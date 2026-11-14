"""Consistent error hierarchy for the video editor engine."""


class VEError(RuntimeError):
    """Base exception for all video editor engine errors."""


class VEConfigError(VEError):
    """Configuration-related errors."""


class VEProviderError(VEError):
    """Provider-related errors (API failures, auth, rate limits)."""


class VEBudgetError(VEError):
    """Budget-related errors."""


class VEPipelineError(VEError):
    """Pipeline execution errors."""


class VEAssetError(VEError):
    """Asset generation/ingestion errors."""


class LeaseLostError(VEError):
    """Raised when a worker tries to update a job it no longer owns (#298).

    A worker owns a claim only while its ``lease_id`` matches the row's and the
    row is still ``running``. Another worker reclaimed the job if either
    changed, so the old worker must stop mutating it.
    """
