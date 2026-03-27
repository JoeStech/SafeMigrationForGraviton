"""Shared dataclasses, enums, and constants for SafeMigration."""

from dataclasses import dataclass
from enum import Enum
from typing import Optional


# ── Enums ──


class PipelineStage(str, Enum):
    FORK = "fork"
    ANALYZE = "analyze"
    GENERATE = "generate"
    STUB = "stub"
    CREATE_PR = "create_pr"
    WAIT_PIPELINE = "wait_pipeline"
    FEEDBACK = "feedback"


class StageStatus(str, Enum):
    PENDING = "pending"
    IN_PROGRESS = "in_progress"
    COMPLETED = "completed"
    FAILED = "failed"
    SKIPPED = "skipped"


class JobStatus(str, Enum):
    PENDING = "pending"
    IN_PROGRESS = "in_progress"
    COMPLETED = "completed"
    FAILED = "failed"
    MANUAL_REVIEW = "manual_review"


# ── Constants ──

MAX_FORK_RETRIES = 3
MAX_FEEDBACK_ATTEMPTS = 3
SESSION_TTL_SECONDS = 86400  # 24 hours
STAGE_TRANSITION_TIMEOUT_SECONDS = 5


# ── Auth ──


@dataclass
class Session:
    session_token: str
    user_id: str
    github_login: str
    expires_at: int


@dataclass
class UserContext:
    user_id: str
    github_login: str
    github_access_token: str


# ── Repository ──


@dataclass
class Repository:
    owner: str
    name: str
    full_name: str
    default_branch: str
    is_private: bool


@dataclass
class ValidationResult:
    is_valid: bool
    workflow_files: list[str]
    dockerfiles: list[str]
    package_manifests: list[str]
    message: Optional[str] = None


# ── Fork ──


@dataclass
class ForkResult:
    fork_full_name: str
    fork_url: str
    base_branch: str
    migration_branch: str


# ── Analysis ──


@dataclass
class FileContent:
    path: str
    content: str


@dataclass
class ParseError:
    path: str
    error: str


@dataclass
class ScannedFiles:
    workflow_files: list[FileContent]
    dockerfiles: list[FileContent]
    package_manifests: list[FileContent]
    build_files: list[FileContent]
    source_files: list[FileContent]
    parse_errors: list[ParseError]


@dataclass
class IdentifiedDependency:
    file_path: str
    line_number: int
    current_value: str
    dependency_type: str  # "runner" | "base-image" | "package" | "action" | "instruction" |
                          # "intrinsic" | "asm" | "compiler-flag" | "build-config" | "arch-guard"
    arm64_alternative: str
    confidence: str  # "high" | "medium" | "low"
    rationale: str


@dataclass
class MigrationReport:
    dependencies: list[IdentifiedDependency]
    summary: str


# ── Generate ──


@dataclass
class ChangeDescription:
    line_range: tuple[int, int]
    description: str
    rationale: str


@dataclass
class ModifiedFile:
    path: str
    original_content: str
    modified_content: str
    diff: str
    changes: list[ChangeDescription]


@dataclass
class GeneratedChanges:
    modified_files: list[ModifiedFile]
    unchanged_files: list[str]


# ── Stubbing ──


@dataclass
class SecretReference:
    file_path: str
    line_number: int
    secret_name: str
    secret_source: str  # "env" | "github-secrets" | "aws-secrets-manager" | "vault"
    expected_format: Optional[str] = None


@dataclass
class DatabaseReference:
    file_path: str
    line_number: int
    connection_string: str
    database_type: str  # "postgres" | "mysql" | "mongodb" | "redis" | "dynamodb" | "unknown"


@dataclass
class ServiceReference:
    file_path: str
    line_number: int
    service_url: str
    service_type: str


@dataclass
class DetectedDependencies:
    secrets: list[SecretReference]
    databases: list[DatabaseReference]
    external_services: list[ServiceReference]


@dataclass
class StubFile:
    path: str
    content: str
    annotation: str
    replaced_dependency: str
    integration_instructions: Optional[str] = None


@dataclass
class FlaggedDependency:
    file_path: str
    dependency: str
    reason: str
    suggested_approach: str


@dataclass
class GeneratedStubs:
    stub_files: list[StubFile]
    env_file: Optional[StubFile] = None
    docker_compose_override: Optional[StubFile] = None
    setup_script: Optional[StubFile] = None
    flagged_for_review: list[FlaggedDependency] = None


# ── Pull Request ──


@dataclass
class PullRequestResult:
    pr_number: int
    pr_url: str
    title: str


# ── Feedback ──


@dataclass
class FailureLogs:
    run_id: int
    logs: str
    failed_step: str
    error_summary: str


@dataclass
class CorrectiveChanges:
    modified_files: list[ModifiedFile]
    explanation: str
    attempt_number: int


# ── Job / Orchestration ──


@dataclass
class StageInfo:
    status: StageStatus
    started_at: Optional[int] = None
    completed_at: Optional[int] = None
    error: Optional[str] = None


@dataclass
class MigrationSummary:
    modified_files: int
    stubbed_secrets: int
    stubbed_databases: int
    stubbed_services: int
    flagged_for_review: int


@dataclass
class MigrationJob:
    job_id: str
    user_id: str
    repo_full_name: str
    status: JobStatus
    stages: dict[str, StageInfo]
    feedback_attempts: int
    created_at: int
    updated_at: int
    fork_full_name: Optional[str] = None
    migration_branch: Optional[str] = None
    current_stage: Optional[PipelineStage] = None
    pr_url: Optional[str] = None
    pr_number: Optional[int] = None
    migration_summary: Optional[MigrationSummary] = None


@dataclass
class JobOutcome:
    status: JobStatus
    change_summary: MigrationSummary
    feedback_attempts: int
    pr_url: Optional[str] = None
    error_message: Optional[str] = None


@dataclass
class StageOutput:
    job_id: str
    stage: PipelineStage
    status: StageStatus
    data: dict
    error: Optional[str] = None


@dataclass
class StepFunctionsInput:
    job_id: str
    user_id: str
    github_access_token: str
    repository: Repository
