"""Generate Lambda — use Bedrock to rewrite ALL files for arm64 compatibility.

Handles CI configs, Dockerfiles, manifests, build files, AND source code
(C/C++ intrinsics, inline asm, arch guards, compiler flags, etc.).
"""

import difflib
import json
import os

import boto3

from src.data.job_store import update_job_stage, append_stage_log

BEDROCK_MODEL_ID = os.environ.get("BEDROCK_MODEL_ID", "global.anthropic.claude-sonnet-4-6")


# ── Per-file rewrite prompts ────────────────────────────────────────

_SOURCE_CODE_TYPES = {"intrinsic", "asm", "arch-guard"}
_BUILD_TYPES = {"compiler-flag", "build-config"}


def _rewrite_prompt_for(path, original_content, dependencies):
    """Build a rewrite prompt tailored to the file type."""
    dep_descriptions = "\n".join(
        f"- Line {d['line_number']}: replace `{d['current_value']}` with "
        f"`{d['arm64_alternative']}` ({d['dependency_type']}, "
        f"confidence={d['confidence']}): {d['rationale']}"
        for d in dependencies
    )

    dep_types = {d["dependency_type"] for d in dependencies}
    has_source = bool(dep_types & _SOURCE_CODE_TYPES)
    has_build = bool(dep_types & _BUILD_TYPES)

    extra_rules = ""
    if has_source:
        extra_rules += """
SOURCE CODE RULES:
- When replacing x86 intrinsics (SSE/AVX) with Arm NEON equivalents, include the \
correct header (#include <arm_neon.h>).
- NEON lane indices MUST be compile-time constants.
- If an x86 intrinsic has no single NEON equivalent, implement a multi-instruction \
sequence that produces identical results.
- Preserve existing #ifdef __x86_64__ blocks but add a corresponding \
#elif defined(__aarch64__) block with the Arm implementation.
- For inline asm, rewrite to aarch64 assembly or replace with NEON intrinsics.
- Do NOT remove x86 code paths — keep them under their existing arch guards so the \
file remains cross-platform.
"""
    if has_build:
        extra_rules += """
BUILD FILE RULES:
- Replace -march=x86-64 / -mavx* / -msse* flags with appropriate Arm equivalents \
(-march=armv8-a+simd, -march=armv8.2-a+sve, etc.) under an aarch64 conditional.
- Keep the x86 flags under an x86_64 conditional so the build stays cross-platform.
- For CMake, use CMAKE_SYSTEM_PROCESSOR checks; for Makefiles, use $(uname -m) or \
similar.
"""

    return f"""You are an expert at migrating code from x86 to arm64/aarch64 (AWS Graviton).

Here is the file `{path}`:

```
{original_content}
```

Apply ALL of the following arm64 migration changes to this file:

{dep_descriptions}
{extra_rules}
General rules:
- Apply every change listed above
- Preserve all formatting, comments, indentation, and structure not being changed
- Keep the file cross-platform where possible (dual arch guards)
- Return ONLY the complete rewritten file content, no explanation, no markdown fences"""


def _rewrite_file(path, original_content, dependencies, bedrock):
    """Ask Claude to rewrite a single file applying all arm64 changes."""
    prompt = _rewrite_prompt_for(path, original_content, dependencies)

    response = bedrock.invoke_model(
        modelId=BEDROCK_MODEL_ID,
        body=json.dumps({
            "anthropic_version": "bedrock-2023-05-31",
            "max_tokens": 8192,
            "messages": [{"role": "user", "content": prompt}],
        }),
        contentType="application/json",
        accept="application/json",
    )
    body = json.loads(response["body"].read())
    text = body["content"][0]["text"].strip()

    # Strip markdown fences if Claude wrapped the output
    if text.startswith("```"):
        lines = text.split("\n")
        # Remove first line (```lang) and last line (```)
        if lines[-1].strip() == "```":
            text = "\n".join(lines[1:-1])
        else:
            text = "\n".join(lines[1:])

    return text


def generate_changes(migration_report, scanned_files, job_id):
    bedrock = boto3.client("bedrock-runtime", region_name="us-east-1")

    # Index dependencies by file path, skipping no-op entries
    deps_by_file = {}
    for dep in migration_report.get("dependencies", []):
        cur = dep.get("current_value", "")
        alt = dep.get("arm64_alternative", "")
        if cur == alt:
            continue
        no_change_phrases = ("no change needed", "no change required", "(no change")
        if any(p in alt.lower() for p in no_change_phrases):
            continue
        deps_by_file.setdefault(dep["file_path"], []).append(dep)

    # Build file content lookup from ALL scanned categories
    all_files = {}
    for category in ("workflow_files", "dockerfiles", "package_manifests",
                      "build_files", "source_files"):
        for f in scanned_files.get(category, []):
            all_files[f["path"]] = f["content"]

    modified_files = []
    unchanged_files = []

    for path, content in all_files.items():
        file_deps = deps_by_file.get(path, [])
        if not file_deps:
            unchanged_files.append(path)
            continue

        append_stage_log(job_id, "generate",
                         f"Rewriting {path} ({len(file_deps)} changes)...")
        modified_content = _rewrite_file(path, content, file_deps, bedrock)

        diff = "".join(difflib.unified_diff(
            content.splitlines(keepends=True),
            modified_content.splitlines(keepends=True),
            fromfile=f"a/{path}",
            tofile=f"b/{path}",
        ))

        changes = [
            {
                "line_range": (d.get("line_number", 0), d.get("line_number", 0)),
                "description": (f"Replace `{d['current_value']}` with "
                                f"`{d['arm64_alternative']}`"),
                "rationale": d.get("rationale", ""),
            }
            for d in file_deps
        ]

        modified_files.append({
            "path": path,
            "original_content": content,
            "modified_content": modified_content,
            "diff": diff,
            "changes": changes,
        })

    return {
        "modified_files": modified_files,
        "unchanged_files": unchanged_files,
    }


def handler(event, context):
    job_id = event["job_id"]
    report = event["migration_report"]
    scanned = event["scanned_files"]

    update_job_stage(job_id, "generate", "in_progress")
    try:
        append_stage_log(job_id, "generate",
                         "Generating ARM64-compatible file modifications...")
        result = generate_changes(report, scanned, job_id)
        mod_count = len(result.get("modified_files", []))
        unch_count = len(result.get("unchanged_files", []))
        append_stage_log(job_id, "generate",
                         f"Done: {mod_count} files modified, {unch_count} unchanged")
        for mf in result.get("modified_files", []):
            append_stage_log(job_id, "generate", f"  Modified: {mf['path']}")
        update_job_stage(job_id, "generate", "completed")
        return result
    except Exception as e:
        append_stage_log(job_id, "generate", f"ERROR: {e}")
        update_job_stage(job_id, "generate", "failed", error=str(e))
        raise
