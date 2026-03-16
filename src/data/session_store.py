"""DynamoDB session CRUD operations for SafeMigration Sessions Table."""

import os
import time

import boto3


def _get_table():
    """Get the DynamoDB Sessions table resource."""
    dynamodb = boto3.resource("dynamodb")
    table_name = os.environ.get("SESSIONS_TABLE", "SafeMigration-Sessions")
    return dynamodb.Table(table_name)


def create_session(session_token, user_id, github_login, github_access_token, expires_at,
                    refresh_token=None, token_expires_at=None):
    """Store a new session in DynamoDB with TTL.

    Args:
        session_token: Unique session identifier (UUID).
        user_id: GitHub user ID.
        github_login: GitHub username.
        github_access_token: GitHub access token.
        expires_at: Unix timestamp when session expires.
        refresh_token: Optional GitHub refresh token for token renewal.
        token_expires_at: Optional Unix timestamp when the access token expires.

    Returns:
        dict with the stored session item.
    """
    table = _get_table()
    now = int(time.time())
    item = {
        "sessionToken": session_token,
        "userId": user_id,
        "githubLogin": github_login,
        "githubAccessToken": github_access_token,
        "createdAt": now,
        "expiresAt": expires_at,
        "ttl": expires_at,
    }
    if refresh_token:
        item["refreshToken"] = refresh_token
    if token_expires_at:
        item["tokenExpiresAt"] = token_expires_at
    table.put_item(Item=item)
    return item


def get_session(session_token):
    """Retrieve a session by its token.

    Args:
        session_token: The session token (PK).

    Returns:
        dict with session data, or None if not found.
    """
    table = _get_table()
    response = table.get_item(Key={"sessionToken": session_token})
    return response.get("Item")


def delete_session(session_token):
    """Delete a session by its token.

    Args:
        session_token: The session token (PK).
    """
    table = _get_table()
    table.delete_item(Key={"sessionToken": session_token})


def get_sessions_by_user_id(user_id):
    """Query sessions by user ID using the GSI.

    Args:
        user_id: GitHub user ID.

    Returns:
        List of session dicts for the given user.
    """
    table = _get_table()
    response = table.query(
        IndexName="userId-index",
        KeyConditionExpression=boto3.dynamodb.conditions.Key("userId").eq(user_id),
    )
    return response.get("Items", [])
