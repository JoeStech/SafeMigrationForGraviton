"""Stub Lambda — generate functional stubs so the migrated repo can run
without real databases, external services, secrets, or cloud credentials.

This goes far beyond placeholder values.  For each detected external
dependency the Lambda produces a *working* stub — an in-memory DB shim,
a lightweight HTTP mock server, a fake credentials provider, etc. — so
that CI pipelines and test suites execute end-to-end on arm64 without
any real infrastructure.
"""

import json
import os

import boto3

from src.data.job_store import update_job_stage, append_stage_log

BEDROCK_MODEL_ID = os.environ.get("BEDROCK_MODEL_ID", "global.anthropic.claude-sonnet-4-6")


# ── Helpers ──────────────────────────────────────────────────────────

def _build_file_sections(scanned_files):
    """Render ALL scanned file categories into prompt text."""
    sections = []
    for category in ("workflow_files", "dockerfiles", "package_manifests",
                      "build_files", "source_files"):
        for f in scanned_files.get(category, []):
            sections.append(f"### {f['path']}\n```\n{f['content']}\n```")
    return "\n\n".join(sections)


def _build_stub_prompt(file_sections):
    """Build the system + user prompt for functional stub generation."""
    system = """\
You are an expert at making codebases fully runnable in isolation — without \
real databases, cloud services, secrets, or external APIs.

You will receive every file in a repository.  Your job is to produce a set of \
*functional* stub / shim files that, when added to the repo, allow the entire \
application and its CI pipeline to execute successfully on arm64 without any \
real external dependencies.

─── WHAT TO DETECT ───
1. DATABASE CONNECTIONS — Postgres, MySQL, MongoDB, Redis, DynamoDB, SQLite, \
   Cassandra, Elasticsearch, etc.
2. SECRETS & CREDENTIALS — GitHub Actions secrets, env-var tokens/keys/passwords, \
   AWS credentials, API keys, OAuth client secrets.
3. EXTERNAL HTTP/GRPC SERVICES — any URL called at runtime or in CI (APIs, \
   webhooks, artifact registries, license servers).
4. CLOUD SDK CALLS — AWS (boto3, SDK), GCP, Azure SDK calls that hit real \
   endpoints.
5. MESSAGE QUEUES / EVENT BUSES — SQS, SNS, Kafka, RabbitMQ, etc.
6. FILE / OBJECT STORAGE — S3, GCS, Azure Blob, MinIO.
7. NATIVE LIBRARY LOADS — dlopen / ctypes / cffi calls to .so files that may \
   not exist on arm64.

─── WHAT TO PRODUCE ───
For each dependency, generate a *working* stub file.  Examples:

• DATABASE → An in-memory implementation.  For SQL databases, use SQLite \
  in-memory.  For Redis, a dict-backed shim.  For DynamoDB, a dict-backed \
  shim with get_item/put_item/query.  The stub must expose the same \
  interface the app imports.

• SECRETS → A .env file or env-setup script that exports every required \
  variable with safe dummy values (e.g. GITHUB_TOKEN=ghp_stub000..., \
  AWS_ACCESS_KEY_ID=AKIASTUBSTUBSTUB).  Values must pass basic format \
  validation (e.g. AWS keys are 20 chars, tokens have correct prefixes).

• HTTP SERVICES → A lightweight mock server (Python http.server, Node \
  express, etc.) that returns canned 200 responses for every endpoint the \
  app calls.  Include a docker-compose.stub.yml or a start script.

• CLOUD SDK → Monkey-patch / mock modules.  For boto3, provide a \
  conftest.py or stub module that patches boto3.client to return \
  in-memory fakes for the services used (S3 → dict store, SQS → list, \
  DynamoDB → dict, etc.).

• MESSAGE QUEUES → In-memory queue (collections.deque) with the same \
  publish/consume interface.

• OBJECT STORAGE → Local filesystem-backed shim (tempdir) with \
  put_object / get_object matching the SDK interface.

• NATIVE LIBS → Provide a Python/ctypes stub .so or a no-op wrapper \
  that satisfies the import without the real native library.

─── OUTPUT FORMAT ───
Return a JSON object:
{
  "stub_files": [
    {
      "path": "stubs/<descriptive_name>",
      "content": "<full working file content>",
      "annotation": "<what this stub does>",
      "replaced_dependency": "<what real dependency it replaces>",
      "integration_instructions": "<how to wire this into the app — \
env var, conftest import, docker-compose override, etc.>"
    }
  ],
  "env_file": {
    "path": ".env.stub",
    "content": "<KEY=VALUE lines for every secret/credential needed>"
  },
  "docker_compose_override": {
    "path": "docker-compose.stub.yml",
    "content": "<compose file that starts any mock servers and wires env>"
  },
  "setup_script": {
    "path": "stubs/setup_stubs.sh",
    "content": "<bash script that activates all stubs — sources .env, \
starts mock servers, patches imports, etc.>"
  },
  "flagged_for_review": [
    {
      "file_path": "<where the dependency was found>",
      "dependency": "<what it is>",
      "reason": "<why it can't be fully stubbed automatically>",
      "suggested_approach": "<best manual workaround>"
    }
  ]
}

RULES:
- Every stub MUST be syntactically valid and runnable.
- Prefer the same language as the project for stubs.
- Stubs must be drop-in: the app should work by setting env vars or \
  adding a conftest — no changes to application source code.
- Include integration_instructions for every stub so a developer knows \
  exactly how to activate it.
- If a dependency truly cannot be stubbed (e.g. hardware dongle), put \
  it in flagged_for_review with a suggested_approach.
- Return ONLY the JSON object, no explanation, no markdown fences."""

    user = (
        "Here are ALL the files in the repository.  Identify every external "
        "dependency and generate functional stubs so the entire application "
        "and CI pipeline can run on arm64 without any real infrastructure.\n\n"
        + file_sections
    )
    return system, user


def analyze_and_stub(scanned_files, job_id):
    """Ask Claude to identify external dependencies and generate functional stubs."""
    bedrock = boto3.client("bedrock-runtime", region_name="us-east-1")

    file_sections = _build_file_sections(scanned_files)
    if not file_sections:
        return {
            "stub_files": [],
            "env_file": None,
            "docker_compose_override": None,
            "setup_script": None,
            "flagged_for_review": [],
        }

    system_prompt, user_prompt = _build_stub_prompt(file_sections)

    response = bedrock.invoke_model(
        modelId=BEDROCK_MODEL_ID,
        body=json.dumps({
            "anthropic_version": "bedrock-2023-05-31",
            "max_tokens": 16384,
            "system": system_prompt,
            "messages": [{"role": "user", "content": user_prompt}],
        }),
        contentType="application/json",
        accept="application/json",
    )
    body = json.loads(response["body"].read())
    text = body["content"][0]["text"].strip()

    # Strip markdown fences if present
    if text.startswith("```"):
        text = text.split("```")[1]
        if text.startswith("json"):
            text = text[4:]
    text = text.strip()
    if text.endswith("```"):
        text = text[:-3].strip()

    result = json.loads(text)

    # Normalise into a consistent shape
    stub_files = result.get("stub_files", [])
    env_file = result.get("env_file")
    compose_override = result.get("docker_compose_override")
    setup_script = result.get("setup_script")
    flagged = result.get("flagged_for_review", [])

    # Promote the env file, compose override, and setup script into
    # stub_files so downstream (PR lambda) can commit them uniformly.
    for extra in (env_file, compose_override, setup_script):
        if extra and extra.get("path") and extra.get("content"):
            stub_files.append({
                "path": extra["path"],
                "content": extra["content"],
                "annotation": f"Auto-generated: {extra['path']}",
                "replaced_dependency": "infrastructure setup",
                "integration_instructions": (
                    extra.get("integration_instructions", "See stubs/setup_stubs.sh")
                ),
            })

    return {
        "stub_files": stub_files,
        "env_file": env_file,
        "docker_compose_override": compose_override,
        "setup_script": setup_script,
        "flagged_for_review": flagged,
    }


def handler(event, context):
    job_id = event["job_id"]
    scanned = event["scanned_files"]

    update_job_stage(job_id, "stub", "in_progress")
    try:
        append_stage_log(job_id, "stub",
                         "Analyzing ALL files for external dependencies to stub...")
        stubs = analyze_and_stub(scanned, job_id)

        stub_count = len(stubs.get("stub_files", []))
        flag_count = len(stubs.get("flagged_for_review", []))
        append_stage_log(job_id, "stub",
                         f"Generated {stub_count} functional stubs, "
                         f"{flag_count} flagged for review")
        for sf in stubs.get("stub_files", []):
            annotation = sf.get("annotation", sf["path"])
            append_stage_log(job_id, "stub", f"  Stub: {sf['path']} — {annotation}")
        for fl in stubs.get("flagged_for_review", []):
            append_stage_log(job_id, "stub",
                             f"  ⚠ Review: {fl.get('dependency', 'unknown')} — "
                             f"{fl.get('reason', '')}")

        update_job_stage(job_id, "stub", "completed")
        return {"generated_stubs": stubs}
    except Exception as e:
        append_stage_log(job_id, "stub", f"ERROR: {e}")
        update_job_stage(job_id, "stub", "failed", error=str(e))
        raise
