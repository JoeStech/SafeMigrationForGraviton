"""GitHub App authentication — generate installation tokens from the App's private key.

GitHub App user-to-server tokens (ghu_ prefix) don't support all API operations
(e.g. forking, creating trees/commits). This module uses the App's private key to
generate installation tokens that carry the App's full configured permissions.
Falls back gracefully if App credentials aren't configured.
"""

import logging
import os
import time

import jwt
import requests

logger = logging.getLogger(__name__)

# GitHub App configuration — set via environment variables
GITHUB_APP_ID = os.environ.get("GITHUB_APP_ID", "")

# Cache installation tokens (they last 1 hour, we cache for 50 min)
_token_cache = {}
_CACHE_TTL = 50 * 60


def _get_private_key():
    """Return the PEM private key string from file bundled in Lambda zip."""
    pem_path = os.environ.get("GITHUB_APP_PRIVATE_KEY_PATH", "")
    if pem_path and os.path.isfile(pem_path):
        with open(pem_path, "r") as f:
            content = f.read()
        logger.info("Read private key from %s (%d chars)", pem_path, len(content))
        return content

    logger.error("No private key file found at GITHUB_APP_PRIVATE_KEY_PATH=%s", pem_path)
    return None


def _create_jwt():
    """Create a short-lived JWT signed with the App's private key."""
    private_key = _get_private_key()
    if not private_key or not GITHUB_APP_ID:
        logger.error("Cannot create JWT: key=%s, app_id=%s", bool(private_key), bool(GITHUB_APP_ID))
        return None

    now = int(time.time())
    payload = {
        "iat": now - 60,
        "exp": now + (10 * 60),
        "iss": GITHUB_APP_ID,
    }
    return jwt.encode(payload, private_key, algorithm="RS256")


def get_installation_token(owner):
    """Get an installation access token for the repo owner's installation.

    Does NOT scope to specific repos or permissions — uses the App's
    full configured permissions on all repos the installation covers.

    Args:
        owner: The GitHub user or org that installed the App.

    Returns:
        Access token string, or None if unavailable.
    """
    now = time.time()

    cached = _token_cache.get(owner)
    if cached and (now - cached["created_at"]) < _CACHE_TTL:
        return cached["token"]

    token = _create_jwt()
    if not token:
        return None

    try:
        resp = requests.get(
            f"https://api.github.com/users/{owner}/installation",
            headers={
                "Authorization": f"Bearer {token}",
                "Accept": "application/vnd.github+json",
            },
            timeout=10,
        )
        if resp.status_code != 200:
            logger.warning("GET /users/%s/installation: %s %s", owner, resp.status_code, resp.text[:200])
            return None

        installation_id = resp.json()["id"]
        logger.info("Found installation %s for %s", installation_id, owner)

        token_resp = requests.post(
            f"https://api.github.com/app/installations/{installation_id}/access_tokens",
            headers={
                "Authorization": f"Bearer {token}",
                "Accept": "application/vnd.github+json",
            },
            timeout=10,
        )
        if token_resp.status_code != 201:
            logger.error("POST access_tokens: %s %s", token_resp.status_code, token_resp.text[:500])
            return None

        token_data = token_resp.json()
        install_token = token_data["token"]
        logger.info("Installation token created: %s... perms=%s",
                     install_token[:8], token_data.get("permissions", {}))
        _token_cache[owner] = {"token": install_token, "created_at": now}
        return install_token

    except Exception as e:
        logger.exception("Failed to get installation token for %s: %s", owner, e)
        return None


def get_effective_token(job_id, repo_full_name):
    """Get the GitHub token for API operations — uses the user's OAuth token."""
    from src.data.job_store import get_github_token
    return get_github_token(job_id)
