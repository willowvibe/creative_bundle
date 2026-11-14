"""Job contexts and spending limits for work started over MCP (#276, #277).

MCP tool arguments are written by an agent, which may be steered by text it
read (a transcript, a web page, a script). Everything a tool call can turn into
a path or into spend therefore goes through this module:

* job directories live under ``<work_root>/mcp/<job_id>`` and
  ``<output_root>/mcp/<job_id>`` — one shared, inspectable place instead of a
  fresh ``mkdtemp`` per call that nothing ever removed;
* a ``job_id`` is validated by :class:`JobContext` (one directory name);
* caller-supplied roots are accepted only inside the configured data roots;
* every job carries a :class:`BudgetGovernor` capped at
  ``mcp.default_budget_usd``, and one generation estimated above
  ``mcp.per_call_cap_usd`` needs an explicit ``approve_usd``.
"""

from __future__ import annotations

import uuid
from pathlib import Path
from typing import Any

from video_editor_asset_ingestion.budget import BudgetGovernor
from video_editor_asset_ingestion.manager import AssetIngestionManager
from video_editor_asset_ingestion.models import AssetRequest
from video_editor_asset_ingestion.pricing import PricingRegistry
from video_editor_config import McpConfig, ProjectConfig
from video_editor_state.job import JobContext
from video_editor_state.paths import default_output_root, default_work_root
from video_editor_state.trust import TrustError, contained_root

MCP_SUBDIR = "mcp"


class McpRequestError(ValueError):
    """A tool call that is refused before any work or spend happens."""


def mcp_config(config_path: str | Path | None = None) -> McpConfig:
    """Return the ``mcp:`` section of the project config (defaults when absent)."""
    return ProjectConfig.load(config_path).mcp


def data_roots() -> tuple[Path, Path]:
    """The configured work and output roots, resolved."""
    return Path(default_work_root()).resolve(), Path(default_output_root()).resolve()


def _contained_root(value: str, roots: tuple[Path, ...], label: str) -> Path:
    try:
        return contained_root(value, roots, label)
    except TrustError as exc:
        raise McpRequestError(str(exc)) from exc


def contained_input_path(value: str | Path, label: str = "path") -> Path:
    """Resolve an input file that must live inside the configured data roots (#276).

    Job directories are already constrained; this covers the media/transcript
    arguments an agent can otherwise point at any file the server can read.
    """
    path = Path(value).expanduser().resolve()
    if not any(path.is_relative_to(root) for root in data_roots()):
        raise McpRequestError(
            f"{label} must be inside the configured data roots "
            f"{[str(root) for root in data_roots()]}; got {path}"
        )
    return path


def make_job_ctx(
    job_id: str | None = None,
    *,
    prefix: str = "mcp",
    work_root: str | None = None,
    output_root: str | None = None,
) -> JobContext:
    """Build (and create the directories of) a job context for one tool call.

    ``work_root``/``output_root`` let a tool reach a job the CLI built (for
    example ``run_skill`` on ``projects/<job>``); they must sit inside the data
    roots. Without them the job lives under the roots' ``mcp/`` subdirectory.
    """
    jid = job_id or f"{prefix}-{uuid.uuid4().hex[:12]}"
    roots = data_roots()
    wr = _contained_root(work_root, roots, "work_root") if work_root else roots[0] / MCP_SUBDIR
    out = (
        _contained_root(output_root, roots, "output_root") if output_root else roots[1] / MCP_SUBDIR
    )
    try:
        ctx = JobContext(job_id=jid, work_root=wr, output_root=out)
    except ValueError as exc:
        raise McpRequestError(str(exc)) from exc
    ctx.ensure_dirs()
    return ctx


def job_budget(requested: Any = None, *, config: McpConfig | None = None) -> BudgetGovernor:
    """A budget for one MCP job: the configured cap, or a caller's lower one."""
    cfg = config or mcp_config()
    limit = cfg.default_budget_usd
    if requested is not None:
        try:
            asked = float(requested)
        except (TypeError, ValueError) as exc:
            raise McpRequestError(f"budget must be a number, got {requested!r}") from exc
        if asked < 0:
            raise McpRequestError("budget must not be negative")
        limit = min(limit, asked)
    # The registry makes the governor price in USD; without it, it counts
    # legacy credits and the cap would not mean dollars.
    return BudgetGovernor(limit=limit, registry=PricingRegistry.load())


def estimate_request_usd(manager: AssetIngestionManager, request: AssetRequest) -> float | None:
    """Worst-case USD price of ``request`` across the providers it may reach.

    A chain can fall through to its most expensive member, so the estimate is
    the maximum. ``None`` means no candidate could be priced; the job budget
    still bounds such a call.
    """
    probe = request.model_copy(deep=True)
    prices = [
        cost
        for provider in manager._provider_chain(probe)
        if (cost := manager._request_cost(probe, provider)) is not None
    ]
    return max(prices) if prices else None


def check_call_cap(
    estimate: float | None, approve_usd: Any, *, config: McpConfig | None = None
) -> None:
    """Refuse a generation estimated above the per-call cap without approval."""
    if estimate is None:
        return
    cfg = config or mcp_config()
    if estimate <= cfg.per_call_cap_usd:
        return
    try:
        approved = float(approve_usd) if approve_usd is not None else None
    except (TypeError, ValueError) as exc:
        raise McpRequestError(f"approve_usd must be a number, got {approve_usd!r}") from exc
    if approved is None or approved < estimate:
        raise McpRequestError(
            f"estimated cost ${estimate:.4f} is above the per-call cap "
            f"${cfg.per_call_cap_usd:.2f}; pass approve_usd >= {estimate:.4f} to proceed"
        )
