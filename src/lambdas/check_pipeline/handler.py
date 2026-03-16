"""CheckPipeline Lambda — poll GitHub Actions for workflow run status."""

import time

from github import Github

from src.data.job_store import update_job_stage, append_stage_log
from src.github_app import get_effective_token

MAX_POLL_SECONDS = 300
POLL_INTERVAL = 15


def check_pipeline_status(fork_full_name, pr_number, github_token):
    """Poll GitHub Actions for the latest workflow run on the PR branch.

    Returns 'success', 'failure', or 'pending'.
    """
    g = Github(github_token)
    repo = g.get_repo(fork_full_name)

    # Get workflow runs triggered by the PR
    runs = repo.get_workflow_runs(event="pull_request")
    target_run = None
    for run in runs:
        if target_run is None or run.created_at > target_run.created_at:
            target_run = run
        break  # already sorted desc

    if not target_run:
        return "no_runs"

    # If already concluded, return immediately
    if target_run.conclusion:
        return target_run.conclusion  # "success", "failure", "cancelled", etc.

    # Still running — poll until done or timeout
    deadline = time.time() + MAX_POLL_SECONDS
    while time.time() < deadline:
        target_run = repo.get_workflow_run(target_run.id)
        if target_run.conclusion:
            return target_run.conclusion
        time.sleep(POLL_INTERVAL)

    return "timeout"


def handler(event, context):
    """Lambda entry point for Step Functions invocation."""
    job_id = event["job_id"]
    fork_full_name = event["fork_full_name"]
    pr_number = event["pr_number"]
    github_token = get_effective_token(job_id, fork_full_name)

    update_job_stage(job_id, "wait_pipeline", "in_progress")
    try:
        append_stage_log(job_id, "wait_pipeline", f"Checking GitHub Actions status for PR #{pr_number}...")
        status = check_pipeline_status(fork_full_name, pr_number, github_token)
        append_stage_log(job_id, "wait_pipeline", f"Pipeline result: {status}")
        update_job_stage(job_id, "wait_pipeline", "completed")
        return {
            "pipeline_status": status,
        }
    except Exception as e:
        append_stage_log(job_id, "wait_pipeline", f"ERROR: {e}")
        update_job_stage(job_id, "wait_pipeline", "failed", error=str(e))
        raise
