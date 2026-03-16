"""DynamoDB job CRUD operations for SafeMigration Jobs Table."""

import os
import time
import uuid

import boto3
from boto3.dynamodb.conditions import Key


def _get_table():
    """Get the DynamoDB Jobs table resource."""
    dynamodb = boto3.resource("dynamodb")
    table_name = os.environ.get("JOBS_TABLE", "SafeMigration-Jobs")
    return dynamodb.Table(table_name)


def create_job(user_id, repo_full_name, github_access_token=None, github_refresh_token=None,
               token_expires_at=None):
    """Create a new migration job in DynamoDB.

    Args:
        user_id: GitHub user ID.
        repo_full_name: Full repository name (owner/repo).
        github_access_token: GitHub token to store with the job (for Step Functions Lambdas).
        github_refresh_token: GitHub refresh token for automatic renewal.
        token_expires_at: Unix timestamp when the access token expires.

    Returns:
        dict with the stored job item (token excluded from return).
    """
    table = _get_table()
    now = int(time.time())
    job_id = str(uuid.uuid4())
    item = {
        "jobId": job_id,
        "userId": user_id,
        "repoFullName": repo_full_name,
        "status": "pending",
        "currentStage": None,
        "stages": {},
        "feedbackAttempts": 0,
        "createdAt": now,
        "updatedAt": now,
    }
    if github_access_token:
        item["githubAccessToken"] = github_access_token
    if github_refresh_token:
        item["githubRefreshToken"] = github_refresh_token
    if token_expires_at:
        item["tokenExpiresAt"] = token_expires_at
    table.put_item(Item=item)
    # Don't return tokens to API callers
    safe_item = {k: v for k, v in item.items() if k not in ("githubAccessToken", "githubRefreshToken")}
    return safe_item


def get_github_token(job_id):
    """Retrieve the GitHub access token for a job, refreshing if expired.

    If the token has a known expiry and a refresh token is available,
    this function will automatically refresh the token and update the
    job record before returning.

    Args:
        job_id: The job ID.

    Returns:
        The GitHub access token string, or None.
    """
    table = _get_table()
    response = table.get_item(
        Key={"jobId": job_id},
        ProjectionExpression="githubAccessToken, githubRefreshToken, tokenExpiresAt",
    )
    item = response.get("Item")
    if not item:
        return None

    access_token = item.get("githubAccessToken")
    refresh_token = item.get("githubRefreshToken")
    token_expires_at = item.get("tokenExpiresAt")

    # If we have expiry info and a refresh token, check if refresh is needed
    # Refresh 5 minutes before actual expiry to avoid race conditions
    if token_expires_at and refresh_token:
        now = int(time.time())
        if now >= (int(token_expires_at) - 300):
            new_token = _refresh_github_token(job_id, refresh_token, table)
            if new_token:
                return new_token

    return access_token


def _refresh_github_token(job_id, refresh_token, table):
    """Exchange a refresh token for a new access token via GitHub OAuth.

    Updates the job record with the new tokens on success.

    Returns:
        The new access token string, or None on failure.
    """
    import os
    import requests

    client_id = os.environ.get("GITHUB_CLIENT_ID", "")
    client_secret = os.environ.get("GITHUB_CLIENT_SECRET", "")

    try:
        resp = requests.post(
            "https://github.com/login/oauth/access_token",
            json={
                "client_id": client_id,
                "client_secret": client_secret,
                "grant_type": "refresh_token",
                "refresh_token": refresh_token,
            },
            headers={"Accept": "application/json"},
            timeout=10,
        )
        token_data = resp.json()
    except Exception:
        return None

    new_access_token = token_data.get("access_token")
    if not new_access_token:
        return None

    new_refresh_token = token_data.get("refresh_token", refresh_token)
    expires_in = token_data.get("expires_in")
    new_expires_at = int(time.time()) + int(expires_in) if expires_in else None

    # Update the job record with fresh tokens
    update_expr = "SET githubAccessToken = :token, githubRefreshToken = :refresh, updatedAt = :now"
    expr_values = {
        ":token": new_access_token,
        ":refresh": new_refresh_token,
        ":now": int(time.time()),
    }
    if new_expires_at:
        update_expr += ", tokenExpiresAt = :expires"
        expr_values[":expires"] = new_expires_at

    table.update_item(
        Key={"jobId": job_id},
        UpdateExpression=update_expr,
        ExpressionAttributeValues=expr_values,
    )

    return new_access_token


def get_job(job_id):
    """Retrieve a job by its ID.

    Args:
        job_id: The job ID (PK).

    Returns:
        dict with job data, or None if not found.
    """
    table = _get_table()
    response = table.get_item(Key={"jobId": job_id})
    return response.get("Item")


def list_jobs_by_user(user_id):
    """List jobs for a user, ordered by createdAt descending.

    Args:
        user_id: GitHub user ID.

    Returns:
        List of job dicts ordered by creation time (newest first).
    """
    table = _get_table()
    response = table.query(
        IndexName="userId-createdAt-index",
        KeyConditionExpression=Key("userId").eq(user_id),
        ScanIndexForward=False,
    )
    return response.get("Items", [])


def update_job_stage(job_id, stage, status, error=None):
    """Update the current stage and its status for a job.

    Args:
        job_id: The job ID.
        stage: Pipeline stage name.
        status: Stage status string.
        error: Optional error message.
    """
    table = _get_table()
    now = int(time.time())
    stage_info = {"status": status, "startedAt": now}
    if status in ("completed", "failed"):
        stage_info["completedAt"] = now
    if error:
        stage_info["error"] = error

    table.update_item(
        Key={"jobId": job_id},
        UpdateExpression="SET currentStage = :stage, stages.#s = :info, updatedAt = :now, #st = :job_status",
        ExpressionAttributeNames={"#s": stage, "#st": "status"},
        ExpressionAttributeValues={
            ":stage": stage,
            ":info": stage_info,
            ":now": now,
            ":job_status": "in_progress",
        },
    )

def append_stage_log(job_id, stage, message):
    """Append a log line to a stage's log array in the job record.

    Args:
        job_id: The job ID.
        stage: Pipeline stage name.
        message: Log message string.
    """
    table = _get_table()
    now = int(time.time())
    entry = {"ts": now, "msg": message}
    try:
        table.update_item(
            Key={"jobId": job_id},
            UpdateExpression="SET stageLogs.#s = list_append(if_not_exists(stageLogs.#s, :empty), :entry)",
            ExpressionAttributeNames={"#s": stage},
            ExpressionAttributeValues={":entry": [entry], ":empty": []},
        )
    except Exception:
        # If stageLogs map doesn't exist yet, create it
        try:
            table.update_item(
                Key={"jobId": job_id},
                UpdateExpression="SET stageLogs = :logs",
                ExpressionAttributeValues={":logs": {stage: [entry]}},
                ConditionExpression="attribute_not_exists(stageLogs)",
            )
        except Exception:
            # Race condition — retry the append
            try:
                table.update_item(
                    Key={"jobId": job_id},
                    UpdateExpression="SET stageLogs.#s = list_append(if_not_exists(stageLogs.#s, :empty), :entry)",
                    ExpressionAttributeNames={"#s": stage},
                    ExpressionAttributeValues={":entry": [entry], ":empty": []},
                )
            except Exception:
                pass  # Best-effort logging




def complete_job(job_id, status, pr_url=None, pr_number=None, migration_summary=None, error_message=None):
    """Mark a job as completed or failed.

    Args:
        job_id: The job ID.
        status: Final job status (completed, failed, manual_review).
        pr_url: Optional PR URL.
        pr_number: Optional PR number.
        migration_summary: Optional dict with migration summary data.
        error_message: Optional error message for failed jobs.
    """
    table = _get_table()
    now = int(time.time())
    update_expr = "SET #st = :status, updatedAt = :now"
    expr_names = {"#st": "status"}
    expr_values = {":status": status, ":now": now}

    if pr_url is not None:
        update_expr += ", prUrl = :pr_url"
        expr_values[":pr_url"] = pr_url
    if pr_number is not None:
        update_expr += ", prNumber = :pr_number"
        expr_values[":pr_number"] = pr_number
    if migration_summary is not None:
        update_expr += ", migrationSummary = :summary"
        expr_values[":summary"] = migration_summary
    if error_message is not None:
        update_expr += ", errorMessage = :error"
        expr_values[":error"] = error_message

    table.update_item(
        Key={"jobId": job_id},
        UpdateExpression=update_expr,
        ExpressionAttributeNames=expr_names,
        ExpressionAttributeValues=expr_values,
    )
