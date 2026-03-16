"""Generate Lambda — produce arm64-compatible file modifications."""

import difflib
import json

from src.data.job_store import update_job_stage, append_stage_log


def generate_changes(migration_report, scanned_files):
    """Generate modified files based on the migration report.

    For each file with identified dependencies, apply the arm64 alternatives
    and produce a diff.
    """
    # Index dependencies by file path
    deps_by_file = {}
    for dep in migration_report.get("dependencies", []):
        fp = dep["file_path"]
        deps_by_file.setdefault(fp, []).append(dep)

    # Build a lookup of all scanned file contents
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

        # Apply replacements line by line
        lines = content.splitlines(keepends=True)
        changes = []

        # Sort deps by line number descending so replacements don't shift indices
        sorted_deps = sorted(file_deps, key=lambda d: d.get("line_number", 0), reverse=True)
        for dep in sorted_deps:
            line_num = dep.get("line_number", 0)
            if line_num < 1 or line_num > len(lines):
                continue
            idx = line_num - 1
            old_line = lines[idx]
            new_line = old_line.replace(dep["current_value"], dep["arm64_alternative"])
            if old_line != new_line:
                lines[idx] = new_line
                changes.append({
                    "line_range": (line_num, line_num),
                    "description": f"Replace {dep['current_value']} with {dep['arm64_alternative']}",
                    "rationale": dep.get("rationale", ""),
                })

        if not changes:
            unchanged_files.append(path)
            continue

        modified_content = "".join(lines)
        diff = "".join(difflib.unified_diff(
            content.splitlines(keepends=True),
            modified_content.splitlines(keepends=True),
            fromfile=f"a/{path}",
            tofile=f"b/{path}",
        ))

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
    """Lambda entry point for Step Functions invocation."""
    job_id = event["job_id"]
    report = event["migration_report"]
    scanned = event["scanned_files"]

    update_job_stage(job_id, "generate", "in_progress")
    try:
        append_stage_log(job_id, "generate", "Generating ARM64-compatible file modifications...")
        result = generate_changes(report, scanned)
        mod_count = len(result.get("modified_files", []))
        unch_count = len(result.get("unchanged_files", []))
        append_stage_log(job_id, "generate", f"Generated changes: {mod_count} files modified, {unch_count} unchanged")
        for mf in result.get("modified_files", []):
            append_stage_log(job_id, "generate", f"  Modified: {mf['path']}")
        update_job_stage(job_id, "generate", "completed")
        return result
    except Exception as e:
        append_stage_log(job_id, "generate", f"ERROR: {e}")
        update_job_stage(job_id, "generate", "failed", error=str(e))
        raise
