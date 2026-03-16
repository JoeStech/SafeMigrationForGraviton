"""Stub Lambda — detect external dependencies and generate safe mocks."""

import json
import re

from src.data.job_store import update_job_stage, append_stage_log

# Patterns for detecting secrets
SECRET_PATTERNS = [
    (r'\$\{\{\s*secrets\.(\w+)\s*\}\}', "github-secrets"),
    (r'AWS_SECRET_ACCESS_KEY', "env"),
    (r'AWS_ACCESS_KEY_ID', "env"),
    (r'AWS_SESSION_TOKEN', "env"),
    (r'VAULT_TOKEN', "vault"),
    (r'VAULT_ADDR', "vault"),
]

# Patterns for detecting database connections
DB_PATTERNS = [
    (r'postgres(?:ql)?://[^\s"\']+', "postgres"),
    (r'mysql://[^\s"\']+', "mysql"),
    (r'mongodb(?:\+srv)?://[^\s"\']+', "mongodb"),
    (r'redis://[^\s"\']+', "redis"),
    (r'dynamodb://[^\s"\']+', "dynamodb"),
]

# Patterns for detecting external service URLs
SERVICE_PATTERNS = [
    (r'https?://[a-zA-Z0-9.-]+\.(?:com|io|org|net)(?:/[^\s"\']*)?',),
]


def analyze_external_dependencies(scanned_files):
    """Regex-based static analysis to detect external dependencies."""
    secrets = []
    databases = []
    external_services = []

    all_files = []
    for category in ("workflow_files", "dockerfiles", "package_manifests"):
        all_files.extend(scanned_files.get(category, []))

    for f in all_files:
        lines = f["content"].splitlines()
        for line_num, line in enumerate(lines, 1):
            # Check secrets
            for pattern, source in SECRET_PATTERNS:
                for match in re.finditer(pattern, line):
                    secret_name = match.group(1) if match.lastindex else match.group(0)
                    secrets.append({
                        "file_path": f["path"],
                        "line_number": line_num,
                        "secret_name": secret_name,
                        "secret_source": source,
                        "expected_format": "string",
                    })

            # Check databases
            for pattern, db_type in DB_PATTERNS:
                for match in re.finditer(pattern, line):
                    databases.append({
                        "file_path": f["path"],
                        "line_number": line_num,
                        "connection_string": match.group(0),
                        "database_type": db_type,
                    })

            # Check external services (skip github.com and common CDNs)
            for pattern_tuple in SERVICE_PATTERNS:
                pattern = pattern_tuple[0]
                for match in re.finditer(pattern, line):
                    url = match.group(0)
                    if any(skip in url for skip in ["github.com", "githubusercontent.com", "docker.io", "docker.com"]):
                        continue
                    external_services.append({
                        "file_path": f["path"],
                        "line_number": line_num,
                        "service_url": url,
                        "service_type": "http",
                    })

    return {
        "secrets": secrets,
        "databases": databases,
        "external_services": external_services,
    }


def generate_stubs(detected_dependencies):
    """Generate mock values for each detected dependency."""
    stub_files = []
    flagged_for_review = []

    # Stub secrets
    for secret in detected_dependencies.get("secrets", []):
        stub_value = f"STUB_{secret['secret_name'].upper()}_VALUE"
        annotation = f"# STUBBED: Replaced {secret['secret_source']} secret '{secret['secret_name']}' from {secret['file_path']}:{secret['line_number']}"
        stub_files.append({
            "path": f".safemigration/stubs/secrets/{secret['secret_name']}.env",
            "content": f"{annotation}\n{secret['secret_name']}={stub_value}\n",
            "annotation": annotation,
            "replaced_dependency": f"secret:{secret['secret_name']}",
        })

    # Stub databases
    for db in detected_dependencies.get("databases", []):
        db_type = db["database_type"]
        stub_map = {
            "postgres": "postgresql://stub_user:stub_pass@localhost:5432/stub_db",
            "mysql": "mysql://stub_user:stub_pass@localhost:3306/stub_db",
            "mongodb": "mongodb://localhost:27017/stub_db",
            "redis": "redis://localhost:6379/0",
            "dynamodb": "dynamodb://localhost:8000",
        }
        stub_url = stub_map.get(db_type)
        if stub_url:
            annotation = f"# STUBBED: Replaced {db_type} connection from {db['file_path']}:{db['line_number']}"
            stub_files.append({
                "path": f".safemigration/stubs/databases/{db_type}.env",
                "content": f"{annotation}\nDATABASE_URL={stub_url}\n",
                "annotation": annotation,
                "replaced_dependency": f"database:{db_type}",
            })
        else:
            flagged_for_review.append({
                "reference": db,
                "reason": f"Unknown database type: {db_type}",
                "placeholder_stub": f"DATABASE_URL=unknown://{db_type}:stub",
            })

    # Stub external services
    for svc in detected_dependencies.get("external_services", []):
        annotation = f"# STUBBED: Replaced external service {svc['service_url']} from {svc['file_path']}:{svc['line_number']}"
        stub_files.append({
            "path": f".safemigration/stubs/services/mock_service.env",
            "content": f"{annotation}\nSERVICE_URL=http://localhost:8080/mock\n",
            "annotation": annotation,
            "replaced_dependency": f"service:{svc['service_url']}",
        })

    return {
        "stub_files": stub_files,
        "flagged_for_review": flagged_for_review,
    }


def handler(event, context):
    """Lambda entry point for Step Functions invocation."""
    job_id = event["job_id"]
    scanned = event["scanned_files"]

    update_job_stage(job_id, "stub", "in_progress")
    try:
        append_stage_log(job_id, "stub", "Scanning for external dependencies (secrets, databases, services)...")
        detected = analyze_external_dependencies(scanned)
        s_count = len(detected.get("secrets", []))
        d_count = len(detected.get("databases", []))
        svc_count = len(detected.get("external_services", []))
        append_stage_log(job_id, "stub", f"Detected: {s_count} secrets, {d_count} databases, {svc_count} services")
        stubs = generate_stubs(detected)
        stub_count = len(stubs.get("stub_files", []))
        flag_count = len(stubs.get("flagged_for_review", []))
        append_stage_log(job_id, "stub", f"Generated {stub_count} stubs, {flag_count} flagged for review")
        update_job_stage(job_id, "stub", "completed")
        return {
            "detected_dependencies": detected,
            "generated_stubs": stubs,
        }
    except Exception as e:
        append_stage_log(job_id, "stub", f"ERROR: {e}")
        update_job_stage(job_id, "stub", "failed", error=str(e))
        raise
