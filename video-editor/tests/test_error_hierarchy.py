"""Tests for the consistent error hierarchy (Task J2)."""

from __future__ import annotations

from video_editor_asset_ingestion.budget import BudgetExceededError
from video_editor_asset_ingestion.manager import (
    CircuitBreakerOpenError as ManagerCircuitBreakerOpenError,
)
from video_editor_asset_ingestion.providers.scoring import ProviderSelectionError
from video_editor_cli.main import JobNotFoundError
from video_editor_llm.router import CircuitBreakerOpenError as LLMCircuitBreakerOpenError
from video_editor_state.errors import (
    VEAssetError,
    VEBudgetError,
    VEConfigError,
    VEError,
    VEPipelineError,
    VEProviderError,
)


class TestErrorHierarchy:
    """Verify the error class hierarchy is correct."""

    def test_veeerror_is_runtime_error(self):
        """VEError inherits from RuntimeError."""
        assert issubclass(VEError, RuntimeError)

    def test_veconfigerror_is_veeerror(self):
        """VEConfigError inherits from VEError."""
        assert issubclass(VEConfigError, VEError)

    def test_veprovidererror_is_veeerror(self):
        """VEProviderError inherits from VEError."""
        assert issubclass(VEProviderError, VEError)

    def test_vebudgeterror_is_veeerror(self):
        """VEBudgetError inherits from VEError."""
        assert issubclass(VEBudgetError, VEError)

    def test_vepipelineerror_is_veeerror(self):
        """VEPipelineError inherits from VEError."""
        assert issubclass(VEPipelineError, VEError)

    def test_veasseterror_is_veeerror(self):
        """VEAssetError inherits from VEError."""
        assert issubclass(VEAssetError, VEError)

    def test_budget_exceeded_error_is_vebudgeterror(self):
        """BudgetExceededError inherits from VEBudgetError."""
        assert issubclass(BudgetExceededError, VEBudgetError)
        assert issubclass(BudgetExceededError, VEError)
        assert issubclass(BudgetExceededError, RuntimeError)

    def test_manager_circuit_breaker_is_veprovidererror(self):
        """CircuitBreakerOpenError (manager) inherits from VEProviderError."""
        assert issubclass(ManagerCircuitBreakerOpenError, VEProviderError)
        assert issubclass(ManagerCircuitBreakerOpenError, VEError)
        assert issubclass(ManagerCircuitBreakerOpenError, RuntimeError)

    def test_llm_circuit_breaker_is_veprovidererror(self):
        """CircuitBreakerOpenError (LLM router) inherits from VEProviderError."""
        assert issubclass(LLMCircuitBreakerOpenError, VEProviderError)
        assert issubclass(LLMCircuitBreakerOpenError, VEError)
        assert issubclass(LLMCircuitBreakerOpenError, RuntimeError)

    def test_provider_selection_error_is_veprovidererror(self):
        """ProviderSelectionError inherits from VEProviderError."""
        assert issubclass(ProviderSelectionError, VEProviderError)
        assert issubclass(ProviderSelectionError, VEError)
        assert issubclass(ProviderSelectionError, RuntimeError)

    def test_job_not_found_error_is_vepipelineerror(self):
        """JobNotFoundError inherits from VEPipelineError."""
        assert issubclass(JobNotFoundError, VEPipelineError)
        assert issubclass(JobNotFoundError, VEError)
        assert issubclass(JobNotFoundError, RuntimeError)

    def test_can_catch_by_base_class(self):
        """All concrete exceptions can be caught by their base VEError class."""
        exceptions = [
            BudgetExceededError("test"),
            ManagerCircuitBreakerOpenError("test"),
            LLMCircuitBreakerOpenError("test"),
            ProviderSelectionError("test"),
            JobNotFoundError("test"),
        ]
        for exc in exceptions:
            assert isinstance(exc, VEError)
            assert isinstance(exc, RuntimeError)
