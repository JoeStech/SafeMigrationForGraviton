"""PR Lambda — commit changes and create pull requests.

Uses raw GitHub REST API (requests) instead of PyGithub to avoid
authentication issues with installation tokens.
"""

import json
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


def _verify_token(token, repo_full_name, job_id):
    """Verify the token has the permissions we need before proceeding."""
    h = _headers(token)

    # Check what the token can see
    resp = http.get(f"{API}/repos/{repo_full_name}", headers=h, timeout=10)
    logger.info("Token verification — GET /repos/%s: %s", repo_full_name, resp.status_code)
    if resp.status_code == 200:
        repo_data = resp.json()
        perms = repo_data.get("permissions", {})
        logger.info("Token permissions on %s: %s", repo_full_name, perms)
        append_stage_log(job_id, "create_pr", f"Token permissions: push={perms.get('push')}, admin={perms.get('admin')}")
        if not perms.get("push"):
            append_stage_log(job_id, "create_pr", "WARNING: Token does NOT have push permission on this repo")
    else:
        logger.error("Token cannot access repo %s: %s %s", repo_full_name, resp.status_code, resp.text[:300])
        append_stage_log(job_id, "create_pr", f"WARNING: Token cannot access repo: {resp.status_code}")

    return resp.status_code == 200


def commit_changes(fork_full_name, branch, modified_files, stub_files, token, job_id):
    """Commit modified files and stub files to the migration branch via REST API."""
    h = _headers(token)

    # Get branch ref
    ref_resp = http.get(f"{API}/repos/{fork_full_name}/git/ref/heads/{branch}", headers=h, timeout=10)
    logger.info("GET ref/heads/%s: %s", branch, ref_resp.status_code)
    ref_resp.raise_for_status()
    base_sha = ref_resp.json()["object"]["sha"]
    append_stage_log(job_id, "create_pr", f"Base SHA: {base_sha[:12]}")

    # Build tree elements
    tree_items = []
    for mf in modified_files:
        tree_items.append({"path": mf["path"], "mode": "100644", "type": "blob", "content": mf["modified_content"]})
    for sf in stub_files:
        tree_items.append({"path": sf["path"], "mode": "100644", "type": "blob", "content": sf["content"]})

    if not tree_items:
        append_stage_log(job_id, "create_pr", "No files to commit — skipping")
        return base_sha  # nothing to commit

    append_stage_log(job_id, "create_pr", f"Creating tree with {len(tree_items)} items...")

    # Create tree
    tree_payload = {"base_tree": base_sha, "tree": tree_items}
    tree_resp = http.post(f"{API}/repos/{fork_full_name}/git/trees", headers=h, timeout=30,
                          json=tree_payload)
    logger.info("POST git/trees: %s", tree_resp.status_code)
    if tree_resp.status_code != 201:
        error_body = tree_resp.text[:500]
        logger.error("create_git_tree failed: %s %s", tree_resp.status_code, error_body)
        append_stage_log(job_id, "create_pr", f"ERROR: create_git_tree {tree_resp.status_code}: {error_body}")
        tree_resp.raise_for_status()
    tree_sha = tree_resp.json()["sha"]
    append_stage_log(job_id, "create_pr", f"Tree created: {tree_sha[:12]}")

    # Create commit
    commit_resp = http.post(f"{API}/repos/{fork_full_name}/git/commits", headers=h, timeout=10,
                            json={"message": "SafeMigration: arm64 compatibility changes + dependency stubs",
                                  "tree": tree_sha, "parents": [base_sha]})
    logger.info("POST git/commits: %s", commit_resp.status_code)
    if commit_resp.status_code != 201:
        logger.error("create_git_commit failed: %s %s", commit_resp.status_code, commit_resp.text[:500])
    commit_resp.raise_for_status()
    commit_sha = commit_resp.json()["sha"]
    append_stage_log(job_id, "create_pr", f"Commit created: {commit_sha[:12]}")

    # Update branch ref
    update_resp = http.patch(f"{API}/repos/{fork_full_name}/git/refs/heads/{branch}", headers=h, timeout=10,
                             json={"sha": commit_sha})
    logger.info("PATCH git/refs: %s", update_resp.status_code)
    update_resp.raise_for_status()
    return commit_sha


def _build_pr_body(migration_report, generated_changes, generated_stubs):
    """Build a descriptive PR body."""
    sections = ["## SafeMigration: ARM64 Compatibility Changes\n"]
    sections.append("### Modified Files\n")
    for mf in generated_changes.get("modified_files", []):
        sections.append(f"**{mf['path']}**")
        for change in mf.get("changes", []):
            sections.append(f"- {change['description']}")
            if change.get("rationale"):
                sections.append(f"  - Rationale: {change['rationale']}")
        sections.append("")
    unchanged = generated_changes.get("unchanged_files", [])
    if unchanged:
        sections.append("### Unchanged Files\n")
        for uf in unchanged:
            sections.append(f"- {uf}")
        sections.append("")
    stubs = generated_stubs.get("stub_files", [])
    if stubs:
        sections.append("### Generated Stubs\n")
        for sf in stubs:
            sections.append(f"- `{sf['path']}`: {sf['annotation']}")
        sections.append("")
    flagged = generated_stubs.get("flagged_for_review", [])
    if flagged:
        sections.append("### ⚠️ Flagged for Manual Review\n")
        for item in flagged:
            sections.append(f"- {item['reason']}: `{item['placeholder_stub']}`")
        sections.append("")
    summary = migration_report.get("summary", "")
    if summary:
        sections.append(f"### Analysis Summary\n\n{summary}\n")
    return "\n".join(sections)


def create_pull_request(fork_full_name, branch, base_branch, migration_report, generated_changes, generated_stubs, token):
    """Open a PR via REST API."""
    h = _headers(token)
    title = "SafeMigration: ARM64/Graviton compatibility changes"
    body = _build_pr_body(migration_report, generated_changes, generated_stubs)
    pr_resp = http.post(f"{API}/repos/{fork_full_name}/pulls", headers=h, timeout=10,
                        json={"title": title, "body": body, "head": branch, "base": base_branch})
    pr_resp.raise_for_status()
    pr = pr_resp.json()
    return {"pr_number": pr["number"], "pr_url": pr["html_url"], "title": pr["title"]}


def handler(event, context):
    """Lambda entry point for Step Functions invocation."""
    job_id = event["job_id"]
    fork_full_name = event["fork_full_name"]
    branch = event["migration_branch"]
    base_branch = event["base_branch"]
    github_token = get_effective_token(job_id, fork_full_name)
    report = event["migration_report"]
    changes = event["generated_changes"]
    stubs = event["generated_stubs"]

    update_job_stage(job_id, "create_pr", "in_progress")
    try:
        # Log token type for debugging
        token_prefix = github_token[:4] if github_token else "None"
        append_stage_log(job_id, "create_pr", f"Token type: {token_prefix}... for repo {fork_full_name}")
        logger.info("PR handler using token prefix: %s for %s", token_prefix, fork_full_name)

        # Verify token has push access before proceeding
        _verify_token(github_token, fork_full_name, job_id)

        mod_count = len(changes.get("modified_files", []))
        stub_count = len(stubs.get("stub_files", []))
        append_stage_log(job_id, "create_pr", f"Committing {mod_count} modified files and {stub_count} stubs to {branch}...")
        commit_changes(fork_full_name, branch, changes.get("modified_files", []), stubs.get("stub_files", []), github_token, job_id)
        append_stage_log(job_id, "create_pr", "Changes committed. Creating pull request...")
        result = create_pull_request(fork_full_name, branch, base_branch, report, changes, stubs, github_token)
        append_stage_log(job_id, "create_pr", f"PR #{result['pr_number']} created: {result['pr_url']}")
        update_job_stage(job_id, "create_pr", "completed")
        return result
    except Exception as e:
        append_stage_log(job_id, "create_pr", f"ERROR: {e}")
        update_job_stage(job_id, "create_pr", "failed", error=str(e))
        raise
