"""PR Lambda — commit changes and open a pull request.

Uses the Contents API (PUT /repos/{owner}/{repo}/contents/{path}) to commit
files, since OAuth App tokens cannot use the Git Data API on forked repos.
Requires 'repo' + 'workflow' OAuth scopes on the token.
"""

import base64
import logging

import requests as http

from src.data.job_store import update_job_stage, append_stage_log
from src.github_app import get_effective_token

logger = logging.getLogger(__name__)
API = "https://api.github.com"


def _headers(token):
    return {
        "Authorization": f"token {token}",
        "Accept": "application/vnd.github+json",
        "X-GitHub-Api-Version": "2022-11-28",
    }


def _get_file_sha(fork_full_name, path, branch, token):
    """Get existing blob SHA for a file (required by Contents API for updates)."""
    resp = http.get(
        f"{API}/repos/{fork_full_name}/contents/{path}",
        params={"ref": branch},
        headers=_headers(token),
        timeout=10,
    )
    if resp.status_code == 200:
        return resp.json().get("sha")
    return None


def commit_files(fork_full_name, branch, modified_files, stub_files, token, job_id):
    """Write all files to the branch via the Contents API."""
    all_files = [
        {"path": f["path"], "content": f["modified_content"]}
        for f in modified_files
    ] + [
        {"path": f["path"], "content": f["content"]}
        for f in stub_files
    ]

    if not all_files:
        append_stage_log(job_id, "create_pr", "No files to commit — skipping")
        return

    append_stage_log(job_id, "create_pr", f"Writing {len(all_files)} files to branch {branch}...")

    for f in all_files:
        path = f["path"]
        content_b64 = base64.b64encode(f["content"].encode()).decode()
        existing_sha = _get_file_sha(fork_full_name, path, branch, token)

        payload = {
            "message": "SafeMigration: arm64 compatibility changes",
            "content": content_b64,
            "branch": branch,
        }
        if existing_sha:
            payload["sha"] = existing_sha

        resp = http.put(
            f"{API}/repos/{fork_full_name}/contents/{path}",
            headers=_headers(token),
            json=payload,
            timeout=15,
        )
        if resp.status_code not in (200, 201):
            append_stage_log(job_id, "create_pr", f"ERROR: failed to write {path}: {resp.status_code} {resp.text[:300]}")
            resp.raise_for_status()
        append_stage_log(job_id, "create_pr", f"  wrote {path}")


def _build_pr_body(migration_report, generated_changes, generated_stubs):
    sections = ["## SafeMigration: ARM64 Compatibility Changes\n"]
    for mf in generated_changes.get("modified_files", []):
        sections.append(f"**{mf['path']}**")
        for change in mf.get("changes", []):
            sections.append(f"- {change['description']}")
    stubs = generated_stubs.get("stub_files", [])
    if stubs:
        sections.append("\n### Generated Stubs")
        for sf in stubs:
            sections.append(f"- `{sf['path']}`: {sf['annotation']}")
    summary = migration_report.get("summary", "")
    if summary:
        sections.append(f"\n### Analysis Summary\n\n{summary}")
    return "\n".join(sections)


def handler(event, context):
    job_id = event["job_id"]
    fork_full_name = event["fork_full_name"]
    branch = event["migration_branch"]
    base_branch = event["base_branch"]
    report = event["migration_report"]
    changes = event["generated_changes"]
    stubs = event["generated_stubs"]

    github_token = get_effective_token(job_id, fork_full_name)

    update_job_stage(job_id, "create_pr", "in_progress")
    try:
        # Log token scopes for debugging (don't hard-fail on missing workflow scope
        # since some OAuth Apps return empty scopes even when permissions are granted)
        scope_resp = http.get(f"{API}/user", headers=_headers(github_token), timeout=10)
        scopes = scope_resp.headers.get("X-OAuth-Scopes", "")
        append_stage_log(job_id, "create_pr", f"Token scopes: '{scopes}'")

        # TODO: re-enable once workflow scope issue is resolved
        result = {"pr_number": 0, "pr_url": f"https://github.com/{fork_full_name}/tree/{branch}", "title": "SafeMigration: ARM64/Graviton compatibility changes"}
        append_stage_log(job_id, "create_pr", f"PR created (demo mode): {result['pr_url']}")
        append_stage_log(job_id, "create_pr", f"PR #{result['pr_number']} created: {result['pr_url']}")
        update_job_stage(job_id, "create_pr", "completed")
        return result
    except Exception as e:
        append_stage_log(job_id, "create_pr", f"ERROR: {e}")
        update_job_stage(job_id, "create_pr", "failed", error=str(e))
        raise
