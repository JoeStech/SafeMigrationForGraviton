"""Job Lambda — CRUD for migration jobs + Step Functions trigger."""

import json
import os

import boto3

from src.data.job_store import (
    create_job as store_create_job,
    get_job as store_get_job,
    list_jobs_by_user,
)
from src.shared import json_response, validate_session, extract_session_token

STATE_MACHINE_ARN = os.environ.get("STATE_MACHINE_ARN", "")


def create_job(user_context, repo):
    """Create a job record and start Step Functions execution."""
    job = store_create_job(
        user_context["user_id"],
        repo["full_name"],
        github_access_token=user_context["github_access_token"],
        github_refresh_token=user_context.get("github_refresh_token"),
        token_expires_at=user_context.get("token_expires_at"),
    )

    # Start Step Functions — only pass job_id. Each Lambda fetches
    # the github token from the job record, not from state.
    sfn = boto3.client("stepfunctions")
    sfn_input = {
        "job_id": job["jobId"],
        "user_id": user_context["user_id"],
        "repository": repo,
    }
    sfn.start_execution(
        stateMachineArn=STATE_MACHINE_ARN,
        name=f"migration-{job['jobId']}",
        input=json.dumps(sfn_input),
    )
    return job


def get_job(job_id):
    """Get job details, stripping sensitive token fields."""
    job = store_get_job(job_id)
    if not job:
        return None
    # Strip tokens from API response
    for key in ("githubAccessToken", "githubRefreshToken", "tokenExpiresAt"):
        job.pop(key, None)
    return job


def list_jobs(user_id):
    """List user's jobs."""
    return list_jobs_by_user(user_id)


def get_job_status(job_id):
    """Get pipeline stage status for a job."""
    job = store_get_job(job_id)
    if not job:
        return None
    return {
        "job_id": job["jobId"],
        "status": job["status"],
        "current_stage": job.get("currentStage"),
        "stages": job.get("stages", {}),
    }


def handler(event, context):
    """Lambda entry point — routes by path/method."""
    method = event.get("requestContext", {}).get("http", {}).get("method", "")
    path = event.get("rawPath", "")
    token = extract_session_token(event)

    if not token:
        return json_response(401, {"error": "Missing session token"})

    user_ctx = validate_session(token)
    if not user_ctx:
        return json_response(401, {"error": "Invalid or expired session"})

    if method == "POST" and path == "/jobs":
        body = json.loads(event.get("body", "{}"))
        repo = body.get("repository")
        if not repo:
            return json_response(400, {"error": "Missing repository"})
        job = create_job(user_ctx, repo)
        return json_response(201, job)

    if method == "GET" and path == "/jobs":
        jobs = list_jobs(user_ctx["user_id"])
        return json_response(200, jobs)

    if method == "GET" and path.startswith("/jobs/"):
        parts = path.strip("/").split("/")
        job_id = parts[1] if len(parts) >= 2 else None
        if not job_id:
            return json_response(400, {"error": "Missing job ID"})

        if len(parts) >= 3 and parts[2] == "status":
            status = get_job_status(job_id)
            if not status:
                return json_response(404, {"error": "Job not found"})
            return json_response(200, status)

        job = get_job(job_id)
        if not job:
            return json_response(404, {"error": "Job not found"})
        return json_response(200, job)

    return json_response(404, {"error": "Not found"})
