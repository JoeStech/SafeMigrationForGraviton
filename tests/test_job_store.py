"""Tests for DynamoDB job store CRUD operations."""

import os
import time

import boto3
import pytest
from moto import mock_aws

os.environ["JOBS_TABLE"] = "SafeMigration-Jobs"
os.environ["AWS_DEFAULT_REGION"] = "us-east-1"
os.environ["AWS_ACCESS_KEY_ID"] = "testing"
os.environ["AWS_SECRET_ACCESS_KEY"] = "testing"
os.environ["AWS_SECURITY_TOKEN"] = "testing"
os.environ["AWS_SESSION_TOKEN"] = "testing"


@pytest.fixture
def dynamodb_jobs_table():
    """Create a mocked DynamoDB Jobs table for testing."""
    with mock_aws():
        dynamodb = boto3.resource("dynamodb", region_name="us-east-1")
        table = dynamodb.create_table(
            TableName="SafeMigration-Jobs",
            KeySchema=[{"AttributeName": "jobId", "KeyType": "HASH"}],
            AttributeDefinitions=[
                {"AttributeName": "jobId", "AttributeType": "S"},
                {"AttributeName": "userId", "AttributeType": "S"},
                {"AttributeName": "createdAt", "AttributeType": "N"},
            ],
            GlobalSecondaryIndexes=[
                {
                    "IndexName": "userId-createdAt-index",
                    "KeySchema": [
                        {"AttributeName": "userId", "KeyType": "HASH"},
                        {"AttributeName": "createdAt", "KeyType": "RANGE"},
                    ],
                    "Projection": {"ProjectionType": "ALL"},
                }
            ],
            BillingMode="PAY_PER_REQUEST",
        )
        table.meta.client.get_waiter("table_exists").wait(
            TableName="SafeMigration-Jobs"
        )
        yield table


def test_create_job(dynamodb_jobs_table):
    from src.data.job_store import create_job

    item = create_job("user-1", "octocat/hello-world")

    assert item["userId"] == "user-1"
    assert item["repoFullName"] == "octocat/hello-world"
    assert item["status"] == "pending"
    assert item["feedbackAttempts"] == 0
    assert "jobId" in item
    assert "createdAt" in item
    assert "updatedAt" in item


def test_get_job(dynamodb_jobs_table):
    from src.data.job_store import create_job, get_job

    created = create_job("user-2", "dev/repo")
    result = get_job(created["jobId"])

    assert result is not None
    assert result["jobId"] == created["jobId"]
    assert result["userId"] == "user-2"
    assert result["repoFullName"] == "dev/repo"


def test_get_job_not_found(dynamodb_jobs_table):
    from src.data.job_store import get_job

    result = get_job("nonexistent-job-id")
    assert result is None


def test_list_jobs_by_user(dynamodb_jobs_table):
    from src.data.job_store import create_job, list_jobs_by_user

    # Create jobs with slight time gaps
    create_job("user-list", "repo/a")
    time.sleep(0.1)
    create_job("user-list", "repo/b")
    create_job("other-user", "repo/c")

    results = list_jobs_by_user("user-list")
    assert len(results) == 2
    # Should be newest first
    repos = [r["repoFullName"] for r in results]
    assert "repo/a" in repos
    assert "repo/b" in repos


def test_list_jobs_by_user_empty(dynamodb_jobs_table):
    from src.data.job_store import list_jobs_by_user

    results = list_jobs_by_user("no-such-user")
    assert results == []


def test_update_job_stage(dynamodb_jobs_table):
    from src.data.job_store import create_job, get_job, update_job_stage

    created = create_job("user-stage", "repo/stage")
    update_job_stage(created["jobId"], "fork", "in_progress")

    result = get_job(created["jobId"])
    assert result["currentStage"] == "fork"
    assert result["status"] == "in_progress"
    assert "fork" in result["stages"]
    assert result["stages"]["fork"]["status"] == "in_progress"


def test_update_job_stage_with_error(dynamodb_jobs_table):
    from src.data.job_store import create_job, get_job, update_job_stage

    created = create_job("user-err", "repo/err")
    update_job_stage(created["jobId"], "analyze", "failed", error="Bedrock timeout")

    result = get_job(created["jobId"])
    assert result["stages"]["analyze"]["status"] == "failed"
    assert result["stages"]["analyze"]["error"] == "Bedrock timeout"
    assert "completedAt" in result["stages"]["analyze"]


def test_complete_job(dynamodb_jobs_table):
    from src.data.job_store import complete_job, create_job, get_job

    created = create_job("user-done", "repo/done")
    summary = {"modified_files": 3, "stubbed_secrets": 2, "stubbed_databases": 1, "stubbed_services": 0, "flagged_for_review": 1}
    complete_job(
        created["jobId"],
        status="completed",
        pr_url="https://github.com/fork/repo/pull/1",
        pr_number=1,
        migration_summary=summary,
    )

    result = get_job(created["jobId"])
    assert result["status"] == "completed"
    assert result["prUrl"] == "https://github.com/fork/repo/pull/1"
    assert result["prNumber"] == 1
    assert result["migrationSummary"]["modified_files"] == 3


def test_complete_job_failed(dynamodb_jobs_table):
    from src.data.job_store import complete_job, create_job, get_job

    created = create_job("user-fail", "repo/fail")
    complete_job(created["jobId"], status="failed", error_message="Fork failed after 3 retries")

    result = get_job(created["jobId"])
    assert result["status"] == "failed"
    assert result["errorMessage"] == "Fork failed after 3 retries"


def test_create_job_with_github_token(dynamodb_jobs_table):
    from src.data.job_store import create_job, get_github_token

    item = create_job("user-tok", "repo/tok", github_access_token="gho_secret123")

    # Token should NOT be in the returned item
    assert "githubAccessToken" not in item
    assert item["userId"] == "user-tok"

    # But should be retrievable via get_github_token
    token = get_github_token(item["jobId"])
    assert token == "gho_secret123"


def test_get_github_token_not_found(dynamodb_jobs_table):
    from src.data.job_store import get_github_token

    token = get_github_token("nonexistent-id")
    assert token is None


def test_create_job_without_github_token(dynamodb_jobs_table):
    from src.data.job_store import create_job, get_github_token

    item = create_job("user-notok", "repo/notok")
    token = get_github_token(item["jobId"])
    assert token is None
