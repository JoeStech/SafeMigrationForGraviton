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

BEDROCK_MODEL_ID = os.environ.get("BEDROCK_MODEL_ID", "global.anthropic.claude-sonnet-4-6")
MAX_TOOL_ROUNDS = 30  # safety cap — thorough analysis needs many tool calls

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
    return """\
You are an expert at migrating codebases from x86 to Arm architecture. You have \
access to Arm MCP Server tools — you MUST use them extensively. Do NOT guess at \
compatibility; always verify with the tools.

Your goal: analyze the provided repository files and identify every change needed \
for Arm (arm64/aarch64) compatibility, targeting AWS Graviton (Neoverse).

MANDATORY STEPS — follow these in order:

1. DOCKERFILES: For every Dockerfile found:
   a. Use check_image or skopeo on each base image (e.g. "python:3.12-slim") to \
verify it supports arm64. If it doesn't, find an arm64-compatible alternative.
   b. For every package installed via apt-get, apk, yum, or pip in the Dockerfile, \
call knowledge_base_search with "Is [package] compatible with Arm architecture?" \
for each package individually. If incompatible, find a compatible version.

2. REQUIREMENTS FILES: For every requirements.txt, package.json, go.mod, Cargo.toml, \
Gemfile, pom.xml, build.gradle, or composer.json:
   a. Go through each dependency line by line.
   b. Call knowledge_base_search for each dependency: "Is [package_name] compatible \
with Arm architecture?"
   c. If a package is not compatible, identify the arm64-compatible alternative or version.

3. WORKFLOW FILES: For every GitHub Actions workflow:
   a. Check runner labels — if using ubuntu-latest or any x86-specific runner, flag it.
   b. Check for architecture-specific actions or build flags.

4. LANGUAGE SCAN: Determine the primary language of the codebase from the files. \
Run migrate_ease_scan with the appropriate scanner (cpp, python, go, js, java) and \
the repository URL if available. Apply any suggested changes.

PITFALLS TO AVOID:
- Don't confuse a software version with a language wrapper package version. For \
example, when checking the Python Redis client, check "redis" (the Python package) \
not the Redis server version.
- NEON lane indices must be compile-time constants, not variables.
- If unsure about Arm equivalents, use knowledge_base_search to find documentation.

AFTER completing all tool calls, return your final answer as a JSON object with:
- "dependencies": array of objects, each with: file_path, line_number, current_value, \
dependency_type (runner|base-image|package|action|instruction), arm64_alternative, \
confidence (high|medium|low), rationale
- "summary": brief summary of all findings and recommended changes

You MUST call tools before producing the final JSON. Do not skip tool calls."""


def analyze_dependencies(scanned_files, job_id):
    """Run Bedrock Converse agentic loop with Arm MCP tools."""
    bedrock = boto3.client("bedrock-runtime")
    mcp = None

    try:
        append_stage_log(job_id, "analyze", "Starting Arm MCP server...")
        mcp = get_mcp_client()
        mcp_tools = mcp.list_tools()
        tool_names = [t["name"] for t in mcp_tools]
        append_stage_log(job_id, "analyze", f"MCP server ready — tools: {', '.join(tool_names)}")
        bedrock_tools = mcp_tools_to_bedrock_tools(mcp_tools)
    except Exception as e:
        msg = f"ERROR: MCP server failed to start: {e}"
        append_stage_log(job_id, "analyze", msg)
        raise RuntimeError(msg) from e

    file_text = _build_file_sections(scanned_files)
    user_message = (
        "I need to migrate this repository from x86 to Arm (AWS Graviton). "
        "Below are all the relevant files from the repo. Follow your instructions "
        "step by step — check EVERY base image, EVERY package, EVERY dependency "
        "using the tools. Do not skip any.\n\n" + file_text
    )

    messages = [{"role": "user", "content": [{"text": user_message}]}]

    converse_kwargs = {
        "modelId": BEDROCK_MODEL_ID,
        "messages": messages,
        "system": [{"text": _build_system_prompt()}],
        "inferenceConfig": {"maxTokens": 8192},
    }
    if bedrock_tools:
        converse_kwargs["toolConfig"] = {"tools": bedrock_tools}

    append_stage_log(job_id, "analyze", f"Sending files to Claude ({BEDROCK_MODEL_ID})...")

    # Agentic loop: let Claude call tools until it produces a final text answer
    for round_num in range(MAX_TOOL_ROUNDS):
        logger.info("Converse round %d", round_num + 1)
        response = bedrock.converse(**converse_kwargs)

        output = response["output"]["message"]
        stop_reason = response["stopReason"]
        messages.append(output)

        if stop_reason == "end_turn":
            break

        if stop_reason == "tool_use":
            tool_results = []
            for block in output["content"]:
                if "toolUse" not in block:
                    continue
                tool_use = block["toolUse"]
                tool_name = tool_use["name"]
                tool_input = tool_use["input"]
                tool_use_id = tool_use["toolUseId"]

                # Log the tool call to the UI
                input_summary = ", ".join(f"{k}={repr(v)[:60]}" for k, v in tool_input.items())
                append_stage_log(job_id, "analyze", f"→ {tool_name}({input_summary})")
                logger.info("Tool call: %s(%s)", tool_name, json.dumps(tool_input)[:200])

                try:
                    if mcp is None:
                        raise RuntimeError("MCP server not connected")
                    mcp_result = mcp.call_tool(tool_name, tool_input)
                    result_text = ""
                    for item in mcp_result.get("content", []):
                        if item.get("type") == "text":
                            result_text += item["text"]
                    # Log a brief summary of the result
                    result_preview = result_text[:120].replace("\n", " ")
                    append_stage_log(job_id, "analyze", f"  ✓ {result_preview}{'...' if len(result_text) > 120 else ''}")
                    tool_results.append({
                        "toolUseId": tool_use_id,
                        "content": [{"text": result_text or "(no output)"}],
                    })
                except Exception as e:
                    append_stage_log(job_id, "analyze", f"  ERROR: {tool_name} failed: {e}")
                    logger.error("MCP tool %s failed: %s", tool_name, e)
                    tool_results.append({
                        "toolUseId": tool_use_id,
                        "content": [{"text": f"Tool error: {e}"}],
                        "status": "error",
                    })

            messages.append({
                "role": "user",
                "content": [{"toolResult": tr} for tr in tool_results],
            })
            converse_kwargs["messages"] = messages
            continue

        append_stage_log(job_id, "analyze", f"ERROR: Unexpected stop reason from Claude: {stop_reason}")
        logger.warning("Unexpected stop reason: %s", stop_reason)
        break

    if mcp:
        mcp.close()

    # Extract final text from the last assistant message
    final_text = ""
    for block in messages[-1].get("content", []):
        if "text" in block:
            final_text += block["text"]

    if "```json" in final_text:
        final_text = final_text.split("```json")[1].split("```")[0]
    elif "```" in final_text:
        final_text = final_text.split("```")[1].split("```")[0]

    try:
        report = json.loads(final_text.strip())
    except json.JSONDecodeError:
        append_stage_log(job_id, "analyze", f"ERROR: Failed to parse Claude response as JSON")
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
            report = analyze_dependencies(scanned_files, job_id)

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
