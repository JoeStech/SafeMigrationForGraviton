"""Tests for Auth Lambda handlers."""

import json
import os
import time
from unittest.mock import patch, MagicMock

import boto3
import pytest
from moto import mock_aws

os.environ["SESSIONS_TABLE"] = "SafeMigration-Sessions"
os.environ["AWS_DEFAULT_REGION"] = "us-east-1"
os.environ["AWS_ACCESS_KEY_ID"] = "testing"
os.environ["AWS_SECRET_ACCESS_KEY"] = "testing"
os.environ["AWS_SECURITY_TOKEN"] = "testing"
os.environ["AWS_SESSION_TOKEN"] = "testing"
os.environ["GITHUB_CLIENT_ID"] = "test-client-id"
os.environ["GITHUB_CLIENT_SECRET"] = "test-client-secret"
os.environ["GITHUB_REDIRECT_URI"] = "http://localhost:3000/callback"


@pytest.fixture
def dynamodb_sessions_table():
    with mock_aws():
        dynamodb = boto3.resource("dynamodb", region_name="us-east-1")
        dynamodb.create_table(
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
        yield


def test_get_login_url(dynamodb_sessions_table):
    from src.lambdas.auth.handler import get_login_url

    result = get_login_url()
    body = json.loads(result["body"])
    assert result["statusCode"] == 200
    assert "github.com/login/oauth/authorize" in body["url"]
    assert "test-client-id" in body["url"]


@patch("src.lambdas.auth.handler.requests")
def test_handle_callback_success(mock_requests, dynamodb_sessions_table):
    from src.lambdas.auth.handler import handle_callback

    # Mock token exchange
    token_resp = MagicMock()
    token_resp.json.return_value = {"access_token": "gho_test123"}
    # Mock user profile
    user_resp = MagicMock()
    user_resp.status_code = 200
    user_resp.json.return_value = {"id": 12345, "login": "testuser"}
    mock_requests.post.return_value = token_resp
    mock_requests.get.return_value = user_resp

    result = handle_callback("valid-code")
    body = json.loads(result["body"])
    assert result["statusCode"] == 200
    assert "sessionToken" in body
    assert body["userId"] == "12345"
    assert body["githubLogin"] == "testuser"


@patch("src.lambdas.auth.handler.requests")
def test_handle_callback_invalid_code(mock_requests, dynamodb_sessions_table):
    from src.lambdas.auth.handler import handle_callback

    token_resp = MagicMock()
    token_resp.json.return_value = {"error": "bad_verification_code", "error_description": "The code is invalid"}
    mock_requests.post.return_value = token_resp

    result = handle_callback("bad-code")
    body = json.loads(result["body"])
    assert result["statusCode"] == 400
    assert "error" in body


@patch("src.lambdas.auth.handler.requests")
def test_handle_callback_github_user_failure(mock_requests, dynamodb_sessions_table):
    from src.lambdas.auth.handler import handle_callback

    token_resp = MagicMock()
    token_resp.json.return_value = {"access_token": "gho_test123"}
    user_resp = MagicMock()
    user_resp.status_code = 500
    mock_requests.post.return_value = token_resp
    mock_requests.get.return_value = user_resp

    result = handle_callback("valid-code")
    assert json.loads(result["body"])["error"] == "Failed to fetch GitHub user profile"


def test_logout(dynamodb_sessions_table):
    from src.data.session_store import create_session, get_session
    from src.lambdas.auth.handler import logout

    expires = int(time.time()) + 86400
    create_session("tok-logout", "user-1", "testuser", "token", expires)
    assert get_session("tok-logout") is not None

    result = logout("tok-logout")
    assert json.loads(result["body"])["message"] == "Logged out"
    assert get_session("tok-logout") is None


def test_validate_session_valid(dynamodb_sessions_table):
    from src.data.session_store import create_session
    from src.lambdas.auth.handler import validate_session

    expires = int(time.time()) + 86400
    create_session("tok-valid", "user-1", "testuser", "enc-token", expires)

    ctx = validate_session("tok-valid")
    assert ctx is not None
    assert ctx["user_id"] == "user-1"
    assert ctx["github_login"] == "testuser"
    assert ctx["github_access_token"] == "enc-token"


def test_validate_session_expired(dynamodb_sessions_table):
    from src.data.session_store import create_session
    from src.lambdas.auth.handler import validate_session

    expires = int(time.time()) - 100  # already expired
    create_session("tok-expired", "user-1", "testuser", "enc-token", expires)

    ctx = validate_session("tok-expired")
    assert ctx is None


def test_validate_session_not_found(dynamodb_sessions_table):
    from src.lambdas.auth.handler import validate_session

    ctx = validate_session("nonexistent")
    assert ctx is None


def test_handler_logout_invalid_token(dynamodb_sessions_table):
    from src.lambdas.auth.handler import handler

    event = {
        "requestContext": {"http": {"method": "POST"}},
        "rawPath": "/auth/logout",
        "headers": {"authorization": "Bearer fake-token"},
    }
    result = handler(event, None)
    assert result["statusCode"] == 401
    assert "Invalid or expired session" in json.loads(result["body"])["error"]


def test_handler_routing(dynamodb_sessions_table):
    from src.lambdas.auth.handler import handler

    # Test login route
    event = {"requestContext": {"http": {"method": "GET"}}, "rawPath": "/auth/login", "headers": {}}
    result = handler(event, None)
    assert result["statusCode"] == 200

    # Test 404
    event = {"requestContext": {"http": {"method": "GET"}}, "rawPath": "/unknown", "headers": {}}
    result = handler(event, None)
    assert result["statusCode"] == 404

    # Test missing code on callback
    event = {"requestContext": {"http": {"method": "POST"}}, "rawPath": "/auth/callback", "body": "{}", "headers": {}}
    result = handler(event, None)
    assert result["statusCode"] == 400
