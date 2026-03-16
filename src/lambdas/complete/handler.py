"""Complete Lambda — finalize job status in DynamoDB at terminal states."""

from src.data.job_store import complete_job


def handler(event, context):
    """Lambda entry point for Step Functions terminal states."""
    job_id = event["job_id"]
    terminal_status = event.get("terminal_status", "completed")

    kwargs = {"pr_url": None, "pr_number": None, "migration_summary": None, "error_message": None}

    pr_result = event.get("pr_result")
    if pr_result:
        kwargs["pr_url"] = pr_result.get("pr_url")
        kwargs["pr_number"] = pr_result.get("pr_number")

    generate_result = event.get("generate_result")
    stub_result = event.get("stub_result")
    if generate_result or stub_result:
        modified = generate_result.get("modified_files", []) if generate_result else []
        stubs = stub_result.get("generated_stubs", {}) if stub_result else {}
        kwargs["migration_summary"] = {
            "modified_files": len(modified),
            "stubbed_secrets": len(stubs.get("stub_files", [])),
            "stubbed_databases": sum(1 for s in stubs.get("stub_files", []) if "database" in s.get("replaced_dependency", "")),
            "stubbed_services": sum(1 for s in stubs.get("stub_files", []) if "service" in s.get("replaced_dependency", "")),
            "flagged_for_review": len(stubs.get("flagged_for_review", [])),
        }

    error_info = event.get("error")
    if error_info:
        kwargs["error_message"] = str(error_info.get("Cause", error_info)) if isinstance(error_info, dict) else str(error_info)

    complete_job(job_id, terminal_status, **kwargs)

    return {"job_id": job_id, "status": terminal_status}
