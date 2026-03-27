"""Analyze Lambda — scan ALL repo files and analyze with Bedrock + Arm MCP.

Uses the Bedrock Converse API with tool use to give Claude access to the
Arm MCP Server's tools (knowledge_base_search, check_image, skopeo,
migrate_ease_scan, etc.) during analysis.  Claude drives the tool calls
in an agentic loop — the Lambda relays each tool_use request to the MCP
server and feeds the result back until Claude produces a final answer.

Scans the ENTIRE repository — not just CI files — so that source code
with architecture-specific constructs (AVX/SSE intrinsics, inline asm,
arch-gated #ifdefs, platform-specific Makefiles, etc.) is also caught.
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

# ── File classification patterns ─────────────────────────────────────

WORKFLOW_PATTERNS = [".github/workflows/*.yml", ".github/workflows/*.yaml"]
DOCKERFILE_PATTERNS = ["**/Dockerfile", "**/Dockerfile.*"]
MANIFEST_NAMES = [
    "requirements.txt", "package.json", "go.mod", "Cargo.toml",
    "pom.xml", "build.gradle", "Gemfile", "composer.json",
]

# Source / build files that may contain arch-specific code
SOURCE_EXTENSIONS = {
    ".c", ".h", ".cpp", ".cxx", ".cc", ".hpp", ".hxx",  # C/C++
    ".s", ".S", ".asm",                                   # Assembly
    ".rs",                                                 # Rust
    ".go",                                                 # Go
    ".java",                                               # Java (JNI / native)
    ".py",                                                 # Python (ctypes, cffi, cython)
    ".js", ".ts", ".mjs",                                  # JS/TS (native addons)
    ".rb",                                                 # Ruby (native extensions)
    ".swift", ".m", ".mm",                                 # Swift / Obj-C
}

BUILD_FILE_NAMES = {
    "Makefile", "makefile", "GNUmakefile",
    "CMakeLists.txt", "meson.build", "configure.ac", "configure.in",
    "BUILD", "BUILD.bazel", "WORKSPACE",
    "SConstruct", "SConscript",
    "vcpkg.json", "conanfile.txt", "conanfile.py",
    "setup.py", "setup.cfg", "pyproject.toml",
    "binding.gyp",  # Node native addons
    "build.rs",     # Rust build scripts
}

# Directories to always skip (vendor / generated / test fixtures)
SKIP_DIRS = {
    "node_modules", ".git", "vendor", "third_party", "dist", "build",
    "__pycache__", ".tox", ".mypy_cache", ".pytest_cache",
}

# Max file size we'll pull (256 KB) — avoids blowing up the prompt with
# giant generated files or binaries that slipped through
MAX_FILE_BYTES = 256 * 1024


# ── Repository scanning ──────────────────────────────────────────────

def _classify_file(path):
    """Return a category string for a repo file, or None to skip it."""
    basename = path.split("/")[-1]

    # Skip vendored / generated directories
    parts = path.split("/")
    if any(p in SKIP_DIRS for p in parts):
        return None

    if any(fnmatch.fnmatch(path, p) for p in WORKFLOW_PATTERNS):
        return "workflow_files"
    if "Dockerfile" in basename:
        return "dockerfiles"
    if basename in MANIFEST_NAMES:
        return "package_manifests"
    if basename in BUILD_FILE_NAMES:
        return "build_files"

    ext = os.path.splitext(basename)[1].lower()
    if ext in SOURCE_EXTENSIONS:
        return "source_files"

    return None


def scan_repository(fork_full_name, branch, github_token):
    """Traverse the full repo tree and collect every relevant file."""
    g = Github(github_token)
    repo = g.get_repo(fork_full_name)
    tree = repo.get_git_tree(branch, recursive=True)

    buckets = {
        "workflow_files": [],
        "dockerfiles": [],
        "package_manifests": [],
        "build_files": [],
        "source_files": [],
        "parse_errors": [],
    }

    for item in tree.tree:
        if item.type != "blob":
            continue
        if item.size and item.size > MAX_FILE_BYTES:
            continue

        category = _classify_file(item.path)
        if category is None:
            continue

        try:
            content = repo.get_contents(item.path, ref=branch)
            decoded = content.decoded_content.decode("utf-8")
            buckets[category].append({"path": item.path, "content": decoded})
        except (GithubException, UnicodeDecodeError) as e:
            buckets["parse_errors"].append({"path": item.path, "error": str(e)})

    return buckets


# ── Bedrock Converse + MCP agentic loop ─────────────────────────────

def _build_file_sections(scanned_files):
    """Format scanned files into readable text for the prompt."""
    label_map = {
        "workflow_files": "CI Workflow",
        "dockerfiles": "Dockerfile",
        "package_manifests": "Package manifest",
        "build_files": "Build file",
        "source_files": "Source file",
    }
    sections = []
    for category, label in label_map.items():
        for f in scanned_files.get(category, []):
            sections.append(f"### {label}: {f['path']}\n```\n{f['content']}\n```")
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
   a. Use check_image or skopeo on each base image to verify arm64 support. \
If it doesn't, find an arm64-compatible alternative.
   b. For every package installed via apt-get, apk, yum, or pip, call \
knowledge_base_search for each package individually.

2. REQUIREMENTS / MANIFESTS: For every requirements.txt, package.json, go.mod, \
Cargo.toml, Gemfile, pom.xml, build.gradle, or composer.json:
   a. Go through each dependency line by line.
   b. Call knowledge_base_search for each dependency.
   c. If a package is not compatible, identify the arm64-compatible alternative.

3. WORKFLOW FILES: For every GitHub Actions workflow:
   a. Check runner labels — flag x86-specific runners.
   b. Check for architecture-specific actions or build flags.

4. SOURCE CODE: For every C, C++, assembly, Rust, Go, or other source file:
   a. Identify x86-specific intrinsics (SSE, SSE2, SSE3, SSSE3, SSE4, AVX, \
AVX2, AVX-512, BMI, FMA, AES-NI, PCLMUL, etc.).
   b. Identify inline assembly using x86 mnemonics.
   c. Identify #ifdef / #if guards that check for __x86_64__, __amd64__, \
_M_X64, __SSE__, __AVX__, etc. without corresponding __aarch64__ / __ARM_NEON paths.
   d. For each x86 intrinsic or instruction found, use knowledge_base_search to \
find the Arm NEON / SVE / SVE2 equivalent.
   e. Flag any compiler-specific pragmas or attributes that are x86-only.

5. BUILD FILES: For every Makefile, CMakeLists.txt, meson.build, configure.ac, etc.:
   a. Identify -march=, -mtune=, -msse*, -mavx*, or other x86-specific compiler flags.
   b. Identify architecture-conditional blocks that lack aarch64 paths.
   c. Suggest the equivalent Arm flags (-march=armv8-a+simd, etc.).

6. LANGUAGE SCAN: Run migrate_ease_scan with the appropriate scanner and the \
repository URL if available.

PITFALLS TO AVOID:
- NEON lane indices must be compile-time constants, not variables.
- SVE is scalable — do not assume a fixed vector width.
- Some x86 intrinsics have no 1:1 NEON equivalent; note where multi-instruction \
sequences are needed.
- If unsure about Arm equivalents, use knowledge_base_search.

AFTER completing all tool calls, return your final answer as a JSON object with:
- "dependencies": array of objects for items that REQUIRE an actual change, each with: \
file_path, line_number, current_value, \
dependency_type (runner|base-image|package|action|instruction|intrinsic|asm|\
compiler-flag|build-config|arch-guard), arm64_alternative, \
confidence (high|medium|low), rationale. \
IMPORTANT: Only include entries where current_value != arm64_alternative and a real \
change is needed. Do NOT include entries for things already compatible.
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
        "Below are ALL the relevant files from the repo — CI configs, Dockerfiles, "
        "manifests, build files, AND source code. Follow your instructions step by "
        "step — check EVERY base image, EVERY package, EVERY dependency, EVERY "
        "intrinsic, EVERY build flag using the tools. Do not skip any.\n\n" + file_text
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
        append_stage_log(job_id, "analyze", "ERROR: Failed to parse Claude response as JSON")
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
    append_stage_log(job_id, "analyze", "Starting full repository scan")

    try:
        github_token = get_effective_token(job_id, fork_full_name)
        if not github_token:
            raise RuntimeError("No GitHub token available for job")

        scanned_files = scan_repository(fork_full_name, branch, github_token)
        file_count = sum(
            len(scanned_files[k])
            for k in ("workflow_files", "dockerfiles", "package_manifests",
                       "build_files", "source_files")
        )
        breakdown = ", ".join(
            f"{len(scanned_files[k])} {k.replace('_', ' ')}"
            for k in ("workflow_files", "dockerfiles", "package_manifests",
                       "build_files", "source_files")
            if scanned_files[k]
        )
        append_stage_log(job_id, "analyze", f"Scanned {file_count} files ({breakdown})")

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
