"""Generate Lambda — use Bedrock to rewrite files for arm64 compatibility."""

import difflib
import json
import os

import boto3

from src.data.job_store import update_job_stage, append_stage_log

BEDROCK_MODEL_ID = os.environ.get("BEDROCK_MODEL_ID", "global.anthropic.claude-sonnet-4-6")


def _rewrite_file(path, original_content, dependencies, bedrock):
    """Ask Claude to rewrite a single file applying all arm64 changes."""
    dep_descriptions = "\n".join(
        f"- Line {d['line_number']}: replace `{d['current_value']}` with `{d['arm64_alternative']}` "
        f"({d['dependency_type']}, confidence={d['confidence']}): {d['rationale']}"
        for d in dependencies
    )

    prompt = f"""You are an expert at migrating CI/CD pipeline files and Dockerfiles to arm64/Graviton compatibility.

Here is the file `{path}`:

```
{original_content}
```

Apply ALL of the following arm64 migration changes to this file:

{dep_descriptions}

Rules:
- Apply every change listed above
- Preserve all formatting, comments, indentation, and structure that is not being changed
- Do not add or remove any lines except as required by the changes above
- Return ONLY the complete rewritten file content, no explanation, no markdown code fences"""

    response = bedrock.invoke_model(
        modelId=BEDROCK_MODEL_ID,
        body=json.dumps({
            "anthropic_version": "bedrock-2023-05-31",
            "max_tokens": 4096,
            "messages": [{"role": "user", "content": prompt}],
        }),
        contentType="application/json",
        accept="application/json",
    )
    body = json.loads(response["body"].read())
    return body["content"][0]["text"].strip()


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

    # Build file content lookup
    all_files = {}
    for category in ("workflow_files", "dockerfiles", "package_manifests"):
        for f in scanned_files.get(category, []):
            all_files[f["path"]] = f["content"]

    modified_files = []
    unchanged_files = []

    for path, content in all_files.items():
        file_deps = deps_by_file.get(path, [])
        if not file_deps:
            unchanged_files.append(path)
            continue

        append_stage_log(job_id, "generate", f"Rewriting {path} ({len(file_deps)} changes)...")
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
                "description": f"Replace `{d['current_value']}` with `{d['arm64_alternative']}`",
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
        append_stage_log(job_id, "generate", "Generating ARM64-compatible file modifications...")
        result = generate_changes(report, scanned, job_id)
        mod_count = len(result.get("modified_files", []))
        unch_count = len(result.get("unchanged_files", []))
        append_stage_log(job_id, "generate", f"Done: {mod_count} files modified, {unch_count} unchanged")
        for mf in result.get("modified_files", []):
            append_stage_log(job_id, "generate", f"  Modified: {mf['path']}")
        update_job_stage(job_id, "generate", "completed")
        return result
    except Exception as e:
        append_stage_log(job_id, "generate", f"ERROR: {e}")
        update_job_stage(job_id, "generate", "failed", error=str(e))
        raise
