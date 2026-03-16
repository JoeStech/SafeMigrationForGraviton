"""Auth Lambda — GitHub App OAuth flow. No Cognito."""

import json
import os
import time
import uuid

import requests

from src.data.session_store import create_session, delete_session
from src.models import SESSION_TTL_SECONDS
from src.shared import json_response, validate_session, extract_session_token

GITHUB_CLIENT_ID = os.environ.get("GITHUB_CLIENT_ID", "")
GITHUB_CLIENT_SECRET = os.environ.get("GITHUB_CLIENT_SECRET", "")
GITHUB_REDIRECT_URI = os.environ.get("GITHUB_REDIRECT_URI", "")


def get_login_url():
    """Construct GitHub OAuth authorize URL."""
    url = (
        f"https://github.com/login/oauth/authorize"
        f"?client_id={GITHUB_CLIENT_ID}"
        f"&redirect_uri={GITHUB_REDIRECT_URI}"
        f"&scope=repo,read:user"
    )
    return json_response(200, {"url": url})


def handle_callback(code):
    """Exchange authorization code for access token + refresh token, create session."""
    resp = requests.post(
        "https://github.com/login/oauth/access_token",
        json={
            "client_id": GITHUB_CLIENT_ID,
            "client_secret": GITHUB_CLIENT_SECRET,
            "code": code,
        },
        headers={"Accept": "application/json"},
        timeout=10,
    )
    try:
        token_data = resp.json()
    except Exception:
        return json_response(400, {"error": f"GitHub returned non-JSON response (status {resp.status_code})"})

    if "error" in token_data or "access_token" not in token_data:
        error_desc = token_data.get(
            "error_description", token_data.get("error", "Unknown OAuth error")
        )
        return json_response(400, {"error": error_desc})

    access_token = token_data["access_token"]
    refresh_token = token_data.get("refresh_token")
    # GitHub returns expires_in (seconds) for expiring tokens
    token_expires_in = token_data.get("expires_in")
    token_expires_at = int(time.time()) + int(token_expires_in) if token_expires_in else None

    # Fetch user profile
    user_resp = requests.get(
        "https://api.github.com/user",
        headers={"Authorization": f"Bearer {access_token}", "Accept": "application/json"},
        timeout=10,
    )
    if user_resp.status_code != 200:
        return json_response(502, {"error": "Failed to fetch GitHub user profile"})

    user_data = user_resp.json()
    user_id = str(user_data["id"])
    github_login = user_data["login"]

    session_token = str(uuid.uuid4())
    expires_at = int(time.time()) + SESSION_TTL_SECONDS
    create_session(
        session_token, user_id, github_login, access_token, expires_at,
        refresh_token=refresh_token, token_expires_at=token_expires_at,
    )

    return json_response(200, {
        "sessionToken": session_token,
        "userId": user_id,
        "githubLogin": github_login,
    })


def logout(session_token):
    """Delete session from DynamoDB."""
    delete_session(session_token)
    return json_response(200, {"message": "Logged out"})


def handler(event, context):
    """Lambda entry point — routes by HTTP method and path."""
    method = event.get("requestContext", {}).get("http", {}).get("method", "")
    path = event.get("rawPath", "")

    if method == "GET" and path == "/auth/login":
        return get_login_url()

    if method == "POST" and path == "/auth/callback":
        body = json.loads(event.get("body", "{}"))
        code = body.get("code", "")
        if not code:
            return json_response(400, {"error": "Missing authorization code"})
        return handle_callback(code)

    if method == "POST" and path == "/auth/logout":
        token = extract_session_token(event)
        if not token:
            return json_response(400, {"error": "Missing session token"})
        user_ctx = validate_session(token)
        if not user_ctx:
            return json_response(401, {"error": "Invalid or expired session"})
        return logout(token)

    if method == "GET" and path == "/auth/session":
        token = extract_session_token(event)
        if not token:
            return json_response(401, {"error": "Missing session token"})
        user_ctx = validate_session(token)
        if not user_ctx:
            return json_response(401, {"error": "Invalid or expired session"})
        return json_response(200, {
            "userId": user_ctx["user_id"],
            "githubLogin": user_ctx["github_login"],
        })

    return json_response(404, {"error": "Not found"})
