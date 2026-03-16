"""Fork Lambda — fork repos and create migration branches."""

import time

from github import Github, GithubException

from src.data.job_store import update_job_stage, append_stage_log
from src.github_app import get_effective_token
from src.models import MAX_FORK_RETRIES


def create_fork(repo_full_name, github_token, job_id):
    """Fork a repo under the authenticated user's account with retry."""
    g = Github(github_token)
    repo = g.get_repo(repo_full_name)

    last_error = None
    for attempt in range(1, MAX_FORK_RETRIES + 1):
        try:
            append_stage_log(job_id, "fork", f"Creating fork (attempt {attempt}/{MAX_FORK_RETRIES})...")
            fork = repo.create_fork()
            append_stage_log(job_id, "fork", f"Fork created: {fork.full_name}")
            # GitHub forks are async — poll until ready
            for poll in range(10):
                try:
                    fork.get_branch(fork.default_branch)
                    break
                except GithubException:
                    time.sleep(2)
            append_stage_log(job_id, "fork", f"Fork ready, default branch: {fork.default_branch}")
            return {
                "fork_full_name": fork.full_name,
                "fork_url": fork.html_url,
                "base_branch": fork.default_branch,
            }
        except GithubException as e:
            last_error = str(e)
            append_stage_log(job_id, "fork", f"Attempt {attempt} failed: {last_error}")
            if attempt < MAX_FORK_RETRIES:
                time.sleep(2 ** attempt)

    raise RuntimeError(f"Fork failed after {MAX_FORK_RETRIES} attempts: {last_error}")


def create_migration_branch(fork_full_name, base_branch, github_token, job_id):
    """Create a dedicated migration branch in the fork."""
    g = Github(github_token)
    fork = g.get_repo(fork_full_name)
    base_ref = fork.get_branch(base_branch)
    branch_name = f"safemigration/arm64-{int(time.time())}"
    fork.create_git_ref(ref=f"refs/heads/{branch_name}", sha=base_ref.commit.sha)
    append_stage_log(job_id, "fork", f"Migration branch created: {branch_name}")
    return branch_name


def handler(event, context):
    """Lambda entry point for Step Functions invocation."""
    job_id = event["job_id"]
    repo_full_name = event["repository"]["full_name"]

    github_token = get_effective_token(job_id, repo_full_name)

    update_job_stage(job_id, "fork", "in_progress")
    append_stage_log(job_id, "fork", f"Starting fork of {repo_full_name}")
    try:
        fork_info = create_fork(repo_full_name, github_token, job_id)
        branch_name = create_migration_branch(
            fork_info["fork_full_name"], fork_info["base_branch"], github_token, job_id
        )
        append_stage_log(job_id, "fork", "Fork stage completed successfully")
        update_job_stage(job_id, "fork", "completed")
        return {
            "fork_full_name": fork_info["fork_full_name"],
            "fork_url": fork_info["fork_url"],
            "base_branch": fork_info["base_branch"],
            "migration_branch": branch_name,
        }
    except Exception as e:
        append_stage_log(job_id, "fork", f"ERROR: {e}")
        update_job_stage(job_id, "fork", "failed", error=str(e))
        raise
