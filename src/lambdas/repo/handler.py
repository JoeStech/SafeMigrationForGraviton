"""Repo Lambda — list and validate repositories."""

import fnmatch

from github import Github, BadCredentialsException

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
    """Check repo for all migratable artifacts — CI, Docker, manifests, build files, source."""
    g = Github(github_access_token)
    repo = g.get_repo(f"{owner}/{repo_name}")
    workflow_files = []
    dockerfiles = []
    package_manifests = []
    build_files = []
    source_files = []

    manifest_names = {
        "requirements.txt", "package.json", "go.mod", "Cargo.toml",
        "pom.xml", "build.gradle", "Gemfile", "composer.json",
    }
    build_file_names = {
        "Makefile", "makefile", "GNUmakefile",
        "CMakeLists.txt", "meson.build", "configure.ac", "configure.in",
        "BUILD", "BUILD.bazel", "WORKSPACE",
        "SConstruct", "SConscript",
        "vcpkg.json", "conanfile.txt", "conanfile.py",
        "setup.py", "setup.cfg", "pyproject.toml",
        "binding.gyp", "build.rs",
    }
    source_extensions = {
        ".c", ".h", ".cpp", ".cxx", ".cc", ".hpp", ".hxx",
        ".s", ".S", ".asm", ".rs", ".go", ".java",
    }
    skip_dirs = {
        "node_modules", ".git", "vendor", "third_party", "dist", "build",
        "__pycache__", ".tox",
    }

    tree = repo.get_git_tree(repo.default_branch, recursive=True)
    for item in tree.tree:
        if item.type != "blob":
            continue
        path = item.path
        parts = path.split("/")
        basename = parts[-1]

        if any(p in skip_dirs for p in parts):
            continue

        if fnmatch.fnmatch(path, ".github/workflows/*.yml") or fnmatch.fnmatch(path, ".github/workflows/*.yaml"):
            workflow_files.append(path)
        elif "Dockerfile" in basename:
            dockerfiles.append(path)
        elif basename in manifest_names:
            package_manifests.append(path)
        elif basename in build_file_names:
            build_files.append(path)
        else:
            import os as _os
            ext = _os.path.splitext(basename)[1].lower()
            if ext in source_extensions:
                source_files.append(path)

    total = len(workflow_files) + len(dockerfiles) + len(package_manifests) + len(build_files) + len(source_files)
    is_valid = total > 0
    message = None if is_valid else "No migratable files found in this repository."

    return {
        "is_valid": is_valid,
        "workflow_files": workflow_files,
        "dockerfiles": dockerfiles,
        "package_manifests": package_manifests,
        "build_files": build_files,
        "source_files": source_files,
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
        try:
            repos = list_repositories(user_ctx["github_access_token"])
        except BadCredentialsException:
            return json_response(401, {"error": "GitHub token expired — please log in again"})
        return json_response(200, repos)

    if method == "GET" and "/validate" in path:
        parts = path.strip("/").split("/")
        if len(parts) >= 4:
            owner = parts[1]
            repo_name = parts[2]
            try:
                result = validate_repository(user_ctx["github_access_token"], owner, repo_name)
            except BadCredentialsException:
                return json_response(401, {"error": "GitHub token expired — please log in again"})
            return json_response(200, result)

    return json_response(404, {"error": "Not found"})
