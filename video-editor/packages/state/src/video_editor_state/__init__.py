"""State management for the video editor engine."""

from video_editor_state.errors import (
    LeaseLostError,
    VEAssetError,
    VEBudgetError,
    VEConfigError,
    VEError,
    VEPipelineError,
    VEProviderError,
)
from video_editor_state.job import JobContext
from video_editor_state.ledger import (
    LedgerConfig,
    LedgerError,
    NullLedger,
    ProjectLedger,
)
from video_editor_state.ledger_hooks import (
    record_cost_line,
    record_issue,
    record_pipeline_complete,
    record_pipeline_failed,
    record_pipeline_start,
    record_stage_done,
    record_stage_failed,
    record_stage_start,
)
from video_editor_state.manifest import JobManifest, OperationLog
from video_editor_state.project_brief import (
    ProjectBrief,
    load_project_brief,
    write_project_brief,
)
from video_editor_state.store import ArtifactStore
from video_editor_state.trust import TrustError, TrustLevel

__all__ = [
    "ArtifactStore",
    "JobContext",
    "JobLockError",
    "JobManifest",
    "LeaseLostError",
    "LedgerConfig",
    "LedgerError",
    "NullLedger",
    "OperationLog",
    "ProjectBrief",
    "ProjectLedger",
    "TrustError",
    "TrustLevel",
    "VEAssetError",
    "VEBudgetError",
    "VEConfigError",
    "VEError",
    "VEPipelineError",
    "VEProviderError",
    "job_lock",
    "load_project_brief",
    "record_cost_line",
    "record_issue",
    "record_pipeline_complete",
    "record_pipeline_failed",
    "record_pipeline_start",
    "record_stage_done",
    "record_stage_failed",
    "record_stage_start",
    "write_project_brief",
]
from video_editor_state.locking import JobLockError, job_lock
