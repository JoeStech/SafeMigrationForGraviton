"""Tests for DynamoDB session store CRUD operations."""

import os
import time

import boto3
import pytest
from moto import mock_aws

os.environ["SESSIONS_TABLE"] = "SafeMigration-Sessions"
os.environ["AWS_DEFAULT_REGION"] = "us-east-1"
os.environ["AWS_ACCESS_KEY_ID"] = "testing"
os.environ["AWS_SECRET_ACCESS_KEY"] = "testing"
os.environ["AWS_SECURITY_TOKEN"] = "testing"
os.environ["AWS_SESSION_TOKEN"] = "testing"


@pytest.fixture
def dynamodb_sessions_table():
    """Create a mocked DynamoDB Sessions table for testing."""
    with mock_aws():
        dynamodb = boto3.resource("dynamodb", region_name="us-east-1")
        table = dynamodb.create_table(
            TableName="SafeMigration-Sessions",
            KeySchema=[{"AttributeName": "sessionToken", "KeyType": "HASH"}],
            AttributeDefinitions=[
                {"AttributeName": "sessionToken", "AttributeType": "S"},
                {"AttributeName": "userId", "AttributeType": "S"},
            ],
            GlobalSecondaryIndexes=[
                {
                    "IndexName": "userId-index",
                    "KeySchema": [{"AttributeName": "userId", "KeyType": "HASH"}],
                    "Projection": {"ProjectionType": "ALL"},
                }
            ],
            BillingMode="PAY_PER_REQUEST",
        )
        table.meta.client.get_waiter("table_exists").wait(
            TableName="SafeMigration-Sessions"
        )
        yield table


def test_create_session(dynamodb_sessions_table):
    from src.data.session_store import create_session

    expires = int(time.time()) + 86400
    item = create_session("tok-abc", "user-1", "octocat", "enc-token-xyz", expires)

    assert item["sessionToken"] == "tok-abc"
    assert item["userId"] == "user-1"
    assert item["githubLogin"] == "octocat"
    assert item["githubAccessToken"] == "enc-token-xyz"
    assert item["expiresAt"] == expires
    assert item["ttl"] == expires
    assert "createdAt" in item


def test_get_session(dynamodb_sessions_table):
    from src.data.session_store import create_session, get_session

    expires = int(time.time()) + 86400
    create_session("tok-get", "user-2", "devuser", "enc-token-2", expires)

    result = get_session("tok-get")
    assert result is not None
    assert result["sessionToken"] == "tok-get"
    assert result["userId"] == "user-2"
    assert result["githubLogin"] == "devuser"
    assert result["githubAccessToken"] == "enc-token-2"


def test_get_session_not_found(dynamodb_sessions_table):
    from src.data.session_store import get_session

    result = get_session("nonexistent-token")
    assert result is None


def test_delete_session(dynamodb_sessions_table):
    from src.data.session_store import create_session, delete_session, get_session

    expires = int(time.time()) + 86400
    create_session("tok-del", "user-3", "deluser", "enc-token-3", expires)

    delete_session("tok-del")
    result = get_session("tok-del")
    assert result is None


def test_delete_session_nonexistent(dynamodb_sessions_table):
    """Deleting a nonexistent session should not raise."""
    from src.data.session_store import delete_session

    delete_session("does-not-exist")


def test_get_sessions_by_user_id(dynamodb_sessions_table):
    from src.data.session_store import create_session, get_sessions_by_user_id

    expires = int(time.time()) + 86400
    create_session("tok-a", "user-multi", "multiuser", "enc-a", expires)
    create_session("tok-b", "user-multi", "multiuser", "enc-b", expires)
    create_session("tok-c", "other-user", "otheruser", "enc-c", expires)

    results = get_sessions_by_user_id("user-multi")
    assert len(results) == 2
    tokens = {r["sessionToken"] for r in results}
    assert tokens == {"tok-a", "tok-b"}


def test_get_sessions_by_user_id_empty(dynamodb_sessions_table):
    from src.data.session_store import get_sessions_by_user_id

    results = get_sessions_by_user_id("no-such-user")
    assert results == []
