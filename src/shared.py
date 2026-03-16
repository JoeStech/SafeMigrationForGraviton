"""Shared utilities used across multiple Lambda handlers."""

import json
import time

from src.data.session_store import get_session, delete_session


def json_response(status_code, body):
    """Build an API Gateway v2 JSON response."""
    return {
        "statusCode": status_code,
        "headers": {"Content-Type": "application/json", "Access-Control-Allow-Origin": "*"},
        "body": json.dumps(body, default=str),
    }


def validate_session(session_token):
    """Look up session by token, check expiry, return user context or None."""
    session = get_session(session_token)
    if not session:
        return None

    if int(session.get("expiresAt", 0)) < int(time.time()):
        delete_session(session_token)
        return None

    ctx = {
        "user_id": session["userId"],
        "github_login": session["githubLogin"],
        "github_access_token": session["githubAccessToken"],
    }
    # Include refresh token info if available
    if session.get("refreshToken"):
        ctx["github_refresh_token"] = session["refreshToken"]
    if session.get("tokenExpiresAt"):
        ctx["token_expires_at"] = session["tokenExpiresAt"]
    return ctx


def extract_session_token(event):
    """Extract Bearer token from Authorization header."""
    return event.get("headers", {}).get("authorization", "").replace("Bearer ", "")
