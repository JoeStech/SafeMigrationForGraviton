"""Feedback Lambda — capture failures and apply corrective changes."""

import json
import os

import boto3
from github import Github

from src.data.job_store import update_job_stage, append_stage_log
from src.github_app import get_effective_token
from src.models import MAX_FEEDBACK_ATTEMPTS

BEDROCK_MODEL_ID = os.environ.get("BEDROCK_MODEL_ID", "global.anthropic.claude-sonnet-4-6")


def capture_failure_logs(fork_full_name, pr_number, github_token):
    """Fetch pipeline run logs from GitHub Actions API."""
    g = Github(github_token)
    repo = g.get_repo(fork_full_name)

    # Get the latest workflow run associated with the PR
    runs = repo.get_workflow_runs(event="pull_request")
    latest_run = None
    for run in runs:
        if latest_run is None or run.created_at > latest_run.created_at:
            latest_run = run
        break  # runs are already sorted by created_at desc

    if not latest_run:
        return {"run_id": 0, "logs": "", "failed_step": "unknown", "error_summary": "No workflow runs found"}

    # Get failed jobs
    jobs = latest_run.jobs()
    failed_step = "unknown"
    error_lines = []
    for job in jobs:
        if job.conclusion == "failure":
            failed_step = job.name
            for step in job.steps:
                if step.conclusion == "failure":
                    failed_step = step.name
                    break
            break

    return {
        "run_id": latest_run.id,
        "logs": "\n".join(error_lines) if error_lines else f"Job '{failed_step}' failed",
        "failed_step": failed_step,
        "error_summary": f"Pipeline failed at step: {failed_step}",
    }


def analyze_failure(failure_logs, previous_changes):
    """Send failure logs + previous changes to Bedrock for corrective analysis."""
    client = boto3.client("bedrock-runtime")

    prompt = (
        "A CI/CD pipeline failed after ARM64 migration changes were applied.\n\n"
        f"Failed step: {failure_logs['failed_step']}\n"
        f"Error summary: {failure_logs['error_summary']}\n"
        f"Logs:\n{failure_logs['logs']}\n\n"
        "Previous changes applied:\n"
        + json.dumps(previous_changes, indent=2)
        + "\n\nAnalyze the failure and suggest corrective changes. "
        "Return JSON with:\n"
        '- "modified_files": array of {path, original_content, modified_content, diff, changes}\n'
        '- "explanation": what went wrong and how the fix addresses it'
    )

    response = client.invoke_model(
        modelId=BEDROCK_MODEL_ID,
        contentType="application/json",
        accept="application/json",
        body=json.dumps({
            "anthropic_version": "bedrock-2023-05-31",
            "max_tokens": 4096,
            "messages": [{"role": "user", "content": prompt}],
        }),
    )

    result = json.loads(response["body"].read())
    text = result["content"][0]["text"]

    if "```json" in text:
        text = text.split("```json")[1].split("```")[0]
    elif "```" in text:
        text = text.split("```")[1].split("```")[0]

    try:
        corrections = json.loads(text.strip())
    except json.JSONDecodeError:
        corrections = {"modified_files": [], "explanation": "Failed to parse Bedrock response"}

    return corrections


def apply_corrections(fork_full_name, branch, corrections, pr_number, github_token):
    """Commit corrective changes to migration branch and update PR."""
    from github import InputGitTreeElement

    g = Github(github_token)
    repo = g.get_repo(fork_full_name)
    ref = repo.get_git_ref(f"heads/{branch}")
    base_sha = ref.object.sha
    base_tree = repo.get_git_tree(base_sha)

    elements = []
    for mf in corrections.get("modified_files", []):
        elements.append(InputGitTreeElement(
            path=mf["path"], mode="100644", type="blob",
            content=mf["modified_content"],
        ))

    if elements:
        new_tree = repo.create_git_tree(elements, base_tree)
        commit = repo.create_git_commit(
            message=f"SafeMigration: corrective fix - {corrections.get('explanation', 'auto-fix')[:80]}",
            tree=new_tree,
            parents=[repo.get_git_commit(base_sha)],
        )
        ref.edit(sha=commit.sha)

        # Add comment to PR
        pr = repo.get_pull(pr_number)
        pr.create_issue_comment(f"🔧 Corrective changes applied:\n\n{corrections.get('explanation', '')}")


def handler(event, context):
    """Lambda entry point for Step Functions invocation."""
    job_id = event["job_id"]
    fork_full_name = event["fork_full_name"]
    branch = event["migration_branch"]
    pr_number = event["pr_number"]
    github_token = get_effective_token(job_id, fork_full_name)
    previous_changes = event.get("generated_changes", {})
    attempt_number = event.get("feedback_attempts", 0) + 1

    update_job_stage(job_id, "feedback", "in_progress")

    if attempt_number > MAX_FEEDBACK_ATTEMPTS:
        append_stage_log(job_id, "feedback", f"Max feedback attempts ({MAX_FEEDBACK_ATTEMPTS}) reached")
        update_job_stage(job_id, "feedback", "completed")
        return {"status": "max_attempts_reached", "attempt_number": attempt_number}

    try:
        append_stage_log(job_id, "feedback", f"Feedback attempt {attempt_number}/{MAX_FEEDBACK_ATTEMPTS}")
        append_stage_log(job_id, "feedback", "Capturing pipeline failure logs...")
        logs = capture_failure_logs(fork_full_name, pr_number, github_token)
        append_stage_log(job_id, "feedback", f"Failed step: {logs['failed_step']}")
        append_stage_log(job_id, "feedback", "Analyzing failure with Bedrock and generating corrections...")
        corrections = analyze_failure(logs, previous_changes)
        corrections["attempt_number"] = attempt_number
        append_stage_log(job_id, "feedback", f"Applying corrective changes: {corrections.get('explanation', '')[:200]}")
        apply_corrections(fork_full_name, branch, corrections, pr_number, github_token)
        append_stage_log(job_id, "feedback", "Corrections committed and PR updated")
        update_job_stage(job_id, "feedback", "completed")
        return {
            "status": "corrections_applied",
            "attempt_number": attempt_number,
            "explanation": corrections.get("explanation", ""),
        }
    except Exception as e:
        append_stage_log(job_id, "feedback", f"ERROR: {e}")
        update_job_stage(job_id, "feedback", "failed", error=str(e))
        raise
