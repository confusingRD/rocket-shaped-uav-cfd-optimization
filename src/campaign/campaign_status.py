"""Campaign execution status vs solver termination reason.

Campaign completion describes whether the automation workflow produced usable
engineering outputs. Termination reason describes why the OpenFOAM solver stopped.
These are independent concepts.
"""

from __future__ import annotations

from typing import Any

from campaign.solver_config import (
    MAX_ITERATION_BUDGET,
    TERMINATION_MAX_ITERATIONS,
    TERMINATION_RESIDUAL_CONVERGED,
    TERMINATION_SOLVER_CRASH,
    infer_termination_reason,
)

LEGACY_TERMINATION_FAILED = "FAILED"

TERMINATION_REASONS = frozenset(
    {TERMINATION_RESIDUAL_CONVERGED, TERMINATION_MAX_ITERATIONS, TERMINATION_SOLVER_CRASH}
)


def normalize_termination_reason(reason: str | None) -> str | None:
    """Map legacy ``FAILED`` termination labels to ``SOLVER_CRASH``."""
    if reason == LEGACY_TERMINATION_FAILED:
        return TERMINATION_SOLVER_CRASH
    return reason


def resolve_termination_reason(summary: dict[str, Any]) -> str:
    """Return termination reason, inferring when absent on legacy summaries."""
    existing = normalize_termination_reason(summary.get("termination_reason"))
    if existing in TERMINATION_REASONS:
        return existing
    return infer_termination_reason(
        converged_residual_control=bool(summary.get("converged_residualControl")),
        iterations=summary.get("actual_iterations_run", summary.get("iterations")),
        fatal=bool(summary.get("fatal")),
        returncode=int(summary.get("returncode", 0) or 0),
        max_iterations=MAX_ITERATION_BUDGET,
    )


def infer_campaign_status(summary: dict[str, Any]) -> tuple[str, str | None]:
    """Classify campaign execution status from a body summary payload."""
    if summary.get("status") in ("RUNNING", "INTERRUPTED", "SKIPPED", "PENDING"):
        return str(summary["status"]), summary.get("error_message")

    termination_reason = resolve_termination_reason(summary)
    fatal = bool(summary.get("fatal"))
    returncode = summary.get("returncode", 0)
    cd = summary.get("Cd")

    if fatal or (returncode not in (0, None) and int(returncode) != 0):
        message = summary.get("error_message") or f"Solver exit code {returncode}"
        return "FAILED", message
    if cd is None:
        return "FAILED", summary.get("error_message") or "Missing Cd from forceCoeffs"
    if termination_reason == TERMINATION_RESIDUAL_CONVERGED:
        return "COMPLETED", None
    if termination_reason == TERMINATION_MAX_ITERATIONS:
        return "COMPLETED", None
    return "FAILED", summary.get("error_message") or "Solver terminated abnormally"


def apply_campaign_status(summary: dict[str, Any]) -> dict[str, Any]:
    """Set ``status``, ``termination_reason``, and ``converged`` on a summary dict."""
    termination_reason = resolve_termination_reason(summary)
    status, error_message = infer_campaign_status(summary)

    summary["termination_reason"] = termination_reason
    summary["status"] = status
    if error_message:
        summary["error_message"] = error_message
    elif status == "COMPLETED" and summary.get("error_message") in (
        None,
        "",
        "Residual convergence not reached",
    ):
        summary.pop("error_message", None)

    if status == "COMPLETED":
        summary["converged"] = termination_reason == TERMINATION_RESIDUAL_CONVERGED
    return summary


def normalize_summary_for_ingest(summary: dict[str, Any]) -> dict[str, Any]:
    """Backward-compatible normalization before DB ingest or migration."""
    normalized = dict(summary)
    return apply_campaign_status(normalized)


def is_legacy_max_iteration_failure(summary: dict[str, Any]) -> bool:
    """Detect pre-redesign records marked FAILED only for residual non-convergence."""
    if summary.get("status") != "FAILED":
        return False
    if summary.get("Cd") is None:
        return False
    if summary.get("fatal") or summary.get("returncode", 0) not in (0, None):
        return False
    reason = normalize_termination_reason(summary.get("termination_reason"))
    if reason == TERMINATION_MAX_ITERATIONS:
        return True
    iterations = summary.get("actual_iterations_run", summary.get("iterations"))
    return (
        not summary.get("converged_residualControl")
        and iterations is not None
        and int(iterations) >= MAX_ITERATION_BUDGET
    )
