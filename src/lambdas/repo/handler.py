"""Repo Lambda — list and validate repositories."""

import fnmatch

from github import Github

from src.shared import json_response, validate_session, extract_session_token


def list_repositories(github_access_token):
    """List repos accessible to the authenticated user."""
    g = Github(github_access_token)
    repos = []
    for repo in g.get_user().get_repos(sort="updated"):
        repos.append({
            "owner": repo.owner.login,
            "name": repo.name,
            "full_name": repo.full_name,
            "default_branch": repo.default_branch,
            "is_private": repo.private,
        })
    return repos


def validate_repository(github_access_token, owner, repo_name):
    """Check repo for workflow files, Dockerfiles, and package manifests."""
    g = Github(github_access_token)
    repo = g.get_repo(f"{owner}/{repo_name}")
    workflow_files = []
    dockerfiles = []
    package_manifests = []

    manifest_names = [
        "requirements.txt", "package.json", "go.mod", "Cargo.toml",
        "pom.xml", "build.gradle", "Gemfile", "composer.json",
    ]

    tree = repo.get_git_tree(repo.default_branch, recursive=True)
    for item in tree.tree:
        if item.type != "blob":
            continue
        path = item.path
        basename = path.split("/")[-1]
        if fnmatch.fnmatch(path, ".github/workflows/*.yml") or fnmatch.fnmatch(path, ".github/workflows/*.yaml"):
            workflow_files.append(path)
        if "Dockerfile" in basename:
            dockerfiles.append(path)
        if basename in manifest_names:
            package_manifests.append(path)

    is_valid = len(workflow_files) > 0 or len(dockerfiles) > 0
    message = None if is_valid else "No workflow files or Dockerfiles found in this repository."

    return {
        "is_valid": is_valid,
        "workflow_files": workflow_files,
        "dockerfiles": dockerfiles,
        "package_manifests": package_manifests,
        "message": message,
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

    if method == "GET" and path == "/repos":
        repos = list_repositories(user_ctx["github_access_token"])
        return json_response(200, repos)

    if method == "GET" and "/validate" in path:
        parts = path.strip("/").split("/")
        if len(parts) >= 4:
            owner = parts[1]
            repo_name = parts[2]
            result = validate_repository(user_ctx["github_access_token"], owner, repo_name)
            return json_response(200, result)

    return json_response(404, {"error": "Not found"})
