"""Stub Lambda — use Bedrock to detect external dependencies and generate safe stubs."""

import json
import os

import boto3

from src.data.job_store import update_job_stage, append_stage_log

BEDROCK_MODEL_ID = os.environ.get("BEDROCK_MODEL_ID", "global.anthropic.claude-sonnet-4-6")


def _build_file_sections(scanned_files):
    sections = []
    for category in ("workflow_files", "dockerfiles", "package_manifests"):
        for f in scanned_files.get(category, []):
            sections.append(f"### {f['path']}\n```\n{f['content']}\n```")
    return "\n\n".join(sections)


def analyze_and_stub(scanned_files, job_id):
    """Ask Claude to identify external dependencies and generate stubs for them."""
    bedrock = boto3.client("bedrock-runtime", region_name="us-east-1")

    file_sections = _build_file_sections(scanned_files)
    if not file_sections:
        return {"stub_files": [], "flagged_for_review": []}

    prompt = f"""You are an expert at analyzing CI/CD pipeline files and Dockerfiles for external dependencies that need to be stubbed out during an arm64 migration test run.

Here are the repository files:

{file_sections}

Your task: identify every external dependency that would cause a pipeline run to fail if the real service/secret is unavailable, and generate safe stub replacements.

Look for:
1. Secrets and credentials — GitHub Actions secrets (${{{{ secrets.X }}}}), environment variables containing keys/tokens/passwords
2. Database connection strings — postgres://, mysql://, mongodb://, redis://, etc.
3. External service URLs — any HTTP/HTTPS endpoints that are called during the pipeline
4. Cloud service references — AWS, GCP, Azure service endpoints or resource ARNs that need real credentials

For each dependency found, generate a stub file that can be committed to the repo to make the pipeline runnable without real credentials.

Return a JSON object with exactly this structure:
{{
  "stub_files": [
    {{
      "path": "relative/path/to/stub/file",
      "content": "full file content with stub values",
      "annotation": "human-readable description of what was stubbed",
      "replaced_dependency": "what this stub replaces"
    }}
  ],
  "flagged_for_review": [
    {{
      "reason": "why this needs manual review",
      "placeholder_stub": "suggested placeholder value"
    }}
  ]
}}

If there are no external dependencies that need stubbing, return empty arrays.
Return ONLY the JSON object, no explanation."""

    response = bedrock.invoke_model(
        modelId=BEDROCK_MODEL_ID,
        body=json.dumps({
            "anthropic_version": "bedrock-2023-05-31",
            "max_tokens": 4096,
            "messages": [{"role": "user", "content": prompt}],
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

    result = json.loads(text)
    return {
        "stub_files": result.get("stub_files", []),
        "flagged_for_review": result.get("flagged_for_review", []),
    }


def handler(event, context):
    job_id = event["job_id"]
    scanned = event["scanned_files"]

    update_job_stage(job_id, "stub", "in_progress")
    try:
        append_stage_log(job_id, "stub", "Analyzing files for external dependencies to stub...")
        stubs = analyze_and_stub(scanned, job_id)
        stub_count = len(stubs.get("stub_files", []))
        flag_count = len(stubs.get("flagged_for_review", []))
        append_stage_log(job_id, "stub", f"Generated {stub_count} stubs, {flag_count} flagged for review")
        for sf in stubs.get("stub_files", []):
            append_stage_log(job_id, "stub", f"  Stub: {sf['path']} — {sf['annotation']}")
        update_job_stage(job_id, "stub", "completed")
        return {"generated_stubs": stubs}
    except Exception as e:
        append_stage_log(job_id, "stub", f"ERROR: {e}")
        update_job_stage(job_id, "stub", "failed", error=str(e))
        raise
