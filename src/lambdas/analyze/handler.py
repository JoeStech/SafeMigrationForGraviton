"""Analyze Lambda — scan repo files and analyze with Bedrock + Arm MCP.

Uses the Bedrock Converse API with tool use to give Claude access to the
Arm MCP Server's tools (knowledge_base_search, check_image, skopeo,
migrate_ease_scan, etc.) during analysis.  Claude drives the tool calls
in an agentic loop — the Lambda relays each tool_use request to the MCP
server and feeds the result back until Claude produces a final answer.
"""

import fnmatch
import json
import logging
import os

import boto3
from github import Github, GithubException

from src.data.job_store import update_job_stage, append_stage_log
from src.github_app import get_effective_token
from src.mcp_client import get_mcp_client, mcp_tools_to_bedrock_tools

logger = logging.getLogger(__name__)
logger.setLevel(logging.INFO)

BEDROCK_MODEL_ID = os.environ.get("BEDROCK_MODEL_ID", "us.anthropic.claude-sonnet-4-6-20250514-v1:0")
MAX_TOOL_ROUNDS = 12  # safety cap on agentic loop iterations

WORKFLOW_PATTERNS = [".github/workflows/*.yml", ".github/workflows/*.yaml"]
DOCKERFILE_PATTERNS = ["**/Dockerfile", "**/Dockerfile.*"]
MANIFEST_NAMES = [
    "requirements.txt", "package.json", "go.mod", "Cargo.toml",
    "pom.xml", "build.gradle", "Gemfile", "composer.json",
]


# ── Repository scanning (unchanged) ─────────────────────────────────

def scan_repository(fork_full_name, branch, github_token):
    """Traverse repo file tree and collect relevant files."""
    g = Github(github_token)
    repo = g.get_repo(fork_full_name)
    tree = repo.get_git_tree(branch, recursive=True)

    workflow_files = []
    dockerfiles = []
    package_manifests = []
    parse_errors = []

    for item in tree.tree:
        if item.type != "blob":
            continue
        path = item.path
        basename = path.split("/")[-1]

        is_workflow = any(fnmatch.fnmatch(path, p) for p in WORKFLOW_PATTERNS)
        is_dockerfile = "Dockerfile" in basename
        is_manifest = basename in MANIFEST_NAMES

        if not (is_workflow or is_dockerfile or is_manifest):
            continue

        try:
            content = repo.get_contents(path, ref=branch)
            decoded = content.decoded_content.decode("utf-8")
            entry = {"path": path, "content": decoded}
            if is_workflow:
                workflow_files.append(entry)
            elif is_dockerfile:
                dockerfiles.append(entry)
            elif is_manifest:
                package_manifests.append(entry)
        except (GithubException, UnicodeDecodeError) as e:
            parse_errors.append({"path": path, "error": str(e)})

    return {
        "workflow_files": workflow_files,
        "dockerfiles": dockerfiles,
        "package_manifests": package_manifests,
        "parse_errors": parse_errors,
    }


# ── Bedrock Converse + MCP agentic loop ─────────────────────────────

def _build_file_sections(scanned_files):
    """Format scanned files into readable text for the prompt."""
    sections = []
    for category, files in [
        ("Workflow files", scanned_files["workflow_files"]),
        ("Dockerfiles", scanned_files["dockerfiles"]),
        ("Package manifests", scanned_files["package_manifests"]),
    ]:
        for f in files:
            sections.append(f"### {category}: {f['path']}\n```\n{f['content']}\n```")
    return "\n\n".join(sections)


def _build_system_prompt():
    return (
        "You are an ARM/arm64 migration expert. You have access to the Arm MCP "
        "Server tools — use them to look up Arm compatibility information, check "
        "Docker image architectures, and scan for migration issues.\n\n"
        "When analyzing files:\n"
        "1. Use knowledge_base_search to look up arm64 compatibility for any "
        "packages, base images, or CI runners you find.\n"
        "2. Use check_image or skopeo to verify whether Docker base images "
        "support arm64.\n"
        "3. Combine your own expertise with the tool results to produce a "
        "thorough migration report.\n\n"
        "After using tools, return your final answer as a JSON object with:\n"
        '- "dependencies": array of objects with fields: file_path, line_number, '
        "current_value, dependency_type (runner|base-image|package|action|instruction), "
        "arm64_alternative, confidence (high|medium|low), rationale\n"
        '- "summary": brief summary of findings'
    )


def analyze_dependencies(scanned_files):
    """Run Bedrock Converse agentic loop with Arm MCP tools."""
    bedrock = boto3.client("bedrock-runtime")
    mcp = None

    try:
        # Connect to Arm MCP server and discover tools
        mcp = get_mcp_client()
        mcp_tools = mcp.list_tools()
        logger.info("Discovered %d MCP tools: %s",
                     len(mcp_tools), [t["name"] for t in mcp_tools])
        bedrock_tools = mcp_tools_to_bedrock_tools(mcp_tools)
    except Exception as e:
        logger.warning("Could not connect to Arm MCP server: %s. "
                       "Falling back to analysis without MCP tools.", e)
        mcp = None
        bedrock_tools = []

    file_text = _build_file_sections(scanned_files)
    user_message = (
        "Analyze these CI/CD pipeline files for arm64 migration. "
        "Use your Arm MCP tools to verify compatibility of base images, "
        "packages, and runners.\n\n" + file_text
    )

    messages = [{"role": "user", "content": [{"text": user_message}]}]

    # Converse API params
    converse_kwargs = {
        "modelId": BEDROCK_MODEL_ID,
        "messages": messages,
        "system": [{"text": _build_system_prompt()}],
        "inferenceConfig": {"maxTokens": 4096},
    }
    if bedrock_tools:
        converse_kwargs["toolConfig"] = {"tools": bedrock_tools}

    # Agentic loop: let Claude call tools until it produces a final text answer
    for round_num in range(MAX_TOOL_ROUNDS):
        logger.info("Converse round %d", round_num + 1)
        response = bedrock.converse(**converse_kwargs)

        output = response["output"]["message"]
        stop_reason = response["stopReason"]
        messages.append(output)

        if stop_reason == "end_turn":
            # Claude is done — extract the final text
            break

        if stop_reason == "tool_use":
            # Process each tool_use block in the response
            tool_results = []
            for block in output["content"]:
                if "toolUse" not in block:
                    continue
                tool_use = block["toolUse"]
                tool_name = tool_use["name"]
                tool_input = tool_use["input"]
                tool_use_id = tool_use["toolUseId"]

                logger.info("Tool call: %s(%s)", tool_name, json.dumps(tool_input)[:200])

                # Relay to MCP server
                try:
                    if mcp is None:
                        raise RuntimeError("MCP server not connected")
                    mcp_result = mcp.call_tool(tool_name, tool_input)
                    # MCP returns {"content": [{"type": "text", "text": "..."}]}
                    result_text = ""
                    for item in mcp_result.get("content", []):
                        if item.get("type") == "text":
                            result_text += item["text"]
                    tool_results.append({
                        "toolUseId": tool_use_id,
                        "content": [{"text": result_text or "(no output)"}],
                    })
                except Exception as e:
                    logger.error("MCP tool %s failed: %s", tool_name, e)
                    tool_results.append({
                        "toolUseId": tool_use_id,
                        "content": [{"text": f"Tool error: {e}"}],
                        "status": "error",
                    })

            # Feed tool results back to Claude
            messages.append({
                "role": "user",
                "content": [{"toolResult": tr} for tr in tool_results],
            })
            converse_kwargs["messages"] = messages
            continue

        # Unexpected stop reason
        logger.warning("Unexpected stop reason: %s", stop_reason)
        break

    if mcp:
        mcp.close()

    # Extract final text from the last assistant message
    final_text = ""
    for block in messages[-1].get("content", []):
        if "text" in block:
            final_text += block["text"]

    # Parse JSON from response
    if "```json" in final_text:
        final_text = final_text.split("```json")[1].split("```")[0]
    elif "```" in final_text:
        final_text = final_text.split("```")[1].split("```")[0]

    try:
        report = json.loads(final_text.strip())
    except json.JSONDecodeError:
        logger.error("Failed to parse Bedrock response as JSON: %s", final_text[:500])
        report = {"dependencies": [], "summary": "Failed to parse Bedrock response"}

    return report


# ── Lambda handler ───────────────────────────────────────────────────

def handler(event, context):
    """Step Functions invokes this with job_id, fork_full_name, migration_branch."""
    job_id = event["job_id"]
    fork_full_name = event["fork_full_name"]
    branch = event["migration_branch"]

    update_job_stage(job_id, "analyze", "in_progress")
    append_stage_log(job_id, "analyze", "Starting repository scan")

    try:
        github_token = get_effective_token(job_id, fork_full_name)
        if not github_token:
            raise RuntimeError("No GitHub token available for job")

        scanned_files = scan_repository(fork_full_name, branch, github_token)
        file_count = sum(
            len(scanned_files[k])
            for k in ("workflow_files", "dockerfiles", "package_manifests")
        )
        append_stage_log(job_id, "analyze", f"Scanned {file_count} files")

        if file_count == 0:
            report = {"dependencies": [], "summary": "No relevant files found"}
        else:
            append_stage_log(job_id, "analyze",
                             "Analyzing with Bedrock + Arm MCP tools")
            report = analyze_dependencies(scanned_files)

        dep_count = len(report.get("dependencies", []))
        append_stage_log(job_id, "analyze",
                         f"Analysis complete: {dep_count} dependencies identified")
        update_job_stage(job_id, "analyze", "completed")

        return {
            "migration_report": report,
            "scanned_files": scanned_files,
        }

    except Exception as e:
        logger.exception("Analyze failed: %s", e)
        append_stage_log(job_id, "analyze", f"Error: {e}")
        update_job_stage(job_id, "analyze", "failed")
        raise
