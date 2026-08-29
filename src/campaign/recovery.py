"""Crash recovery — detect and classify interrupted simulations."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from campaign.constants import RESULTS_ROOT, SIMULATION_STATES
from reporting.production_db import utc_now_iso


def run_state_path(body_id: str, results_root: Path = RESULTS_ROOT) -> Path:
    return results_root / body_id / "run_state.json"


def write_run_state(
    body_id: str,
    *,
    simulation_uuid: str,
    campaign_uuid: str,
    status: str = "RUNNING",
    stage: str = "prepare",
    results_root: Path = RESULTS_ROOT,
    extra: dict[str, Any] | None = None,
) -> Path:
    if status not in SIMULATION_STATES:
        raise ValueError(f"Invalid simulation status: {status}")

    path = run_state_path(body_id, results_root)
    path.parent.mkdir(parents=True, exist_ok=True)

    now = utc_now_iso()
    started_at = now

    if path.exists():
        try:
            previous = json.loads(path.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            previous = {}

        if previous.get("simulation_uuid") == simulation_uuid:
            started_at = previous.get("started_at") or now

    payload = {
        "body_id": body_id,
        "simulation_uuid": simulation_uuid,
        "campaign_uuid": campaign_uuid,
        "status": status,
        "stage": stage,
        "started_at": started_at,
        "updated_at": now,
        **(extra or {}),
    }

    path.write_text(
        json.dumps(payload, indent=2) + "\n",
        encoding="utf-8",
    )
    return path


def read_run_state(body_id: str, results_root: Path = RESULTS_ROOT) -> dict[str, Any] | None:
    path = run_state_path(body_id, results_root)
    if not path.exists():
        return None
    return json.loads(path.read_text(encoding="utf-8"))


def mark_interrupted(
    body_id: str,
    *,
    reason: str,
    stage: str | None = None,
    results_root: Path = RESULTS_ROOT,
) -> dict[str, Any] | None:
    """Mark an in-flight body as INTERRUPTED (power loss, CTRL+C, crash)."""
    state = read_run_state(body_id, results_root)
    if state is None:
        state = {
            "body_id": body_id,
            "simulation_uuid": None,
            "campaign_uuid": None,
            "started_at": utc_now_iso(),
        }
    state["status"] = "INTERRUPTED"
    state["stage"] = stage or state.get("stage", "unknown")
    state["interrupted_at"] = utc_now_iso()
    state["updated_at"] = utc_now_iso()
    state["interrupt_reason"] = reason
    path = run_state_path(body_id, results_root)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(state, indent=2) + "\n",
        encoding="utf-8",
    )

    summary_path = results_root / body_id / "summary.json"
    if summary_path.exists():
        summary = json.loads(summary_path.read_text(encoding="utf-8"))
    else:
        summary = {"body_id": body_id}
    summary["status"] = "INTERRUPTED"
    summary["error_message"] = reason
    summary["interrupted_at"] = state["interrupted_at"]
    summary_path.parent.mkdir(parents=True, exist_ok=True)
    summary_path.write_text(json.dumps(summary, indent=2) + "\n", encoding="utf-8")
    return state


def mark_failed(
    body_id: str,
    *,
    reason: str,
    stage: str | None = None,
    results_root: Path = RESULTS_ROOT,
) -> dict[str, Any]:
    """Mark a body as FAILED after mesh/solver/workflow error (not an interrupt)."""
    state = read_run_state(body_id, results_root)
    if state is None:
        state = {
            "body_id": body_id,
            "simulation_uuid": None,
            "campaign_uuid": None,
            "started_at": utc_now_iso(),
        }
    state["status"] = "FAILED"
    state["stage"] = stage or state.get("stage", "unknown")
    state["failed_at"] = utc_now_iso()
    state["updated_at"] = utc_now_iso()
    state["error_message"] = reason
    path = run_state_path(body_id, results_root)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(state, indent=2) + "\n",
        encoding="utf-8",
    )

    summary_path = results_root / body_id / "summary.json"
    if summary_path.exists():
        summary = json.loads(summary_path.read_text(encoding="utf-8"))
    else:
        summary = {"body_id": body_id}
    summary["status"] = "FAILED"
    summary["error_message"] = reason
    summary["failed_at"] = state["failed_at"]
    summary_path.parent.mkdir(parents=True, exist_ok=True)
    summary_path.write_text(json.dumps(summary, indent=2) + "\n", encoding="utf-8")
    return state


def detect_interrupted_bodies(
    results_root: Path = RESULTS_ROOT,
    *,
    run_state_glob: str = "Body_*/run_state.json",
) -> list[str]:
    """Find bodies with run_state RUNNING/INTERRUPTED but no successful completion."""
    interrupted: list[str] = []
    for state_path in sorted(results_root.glob(run_state_glob)):
        body_id = state_path.parent.name
        state = json.loads(state_path.read_text(encoding="utf-8"))
        status = state.get("status")
        summary_path = state_path.parent / "summary.json"
        if status in ("RUNNING", "INTERRUPTED"):
            if summary_path.exists():
                summary = json.loads(summary_path.read_text(encoding="utf-8"))
                if summary.get("status") == "COMPLETED" and summary.get("Cd") is not None:
                    continue
            interrupted.append(body_id)
    return interrupted


def classify_body_for_resume(
    body_id: str,
    *,
    db_status: str | None,
    results_root: Path = RESULTS_ROOT,
    retry_failed: bool = False,
) -> str:
    """Return action: ``skip``, ``restart``, or ``run``."""
    summary_path = results_root / body_id / "summary.json"
    if summary_path.exists():
        summary = json.loads(summary_path.read_text(encoding="utf-8"))
        if summary.get("status") == "COMPLETED" and summary.get("Cd") is not None:
            return "skip"
        if summary.get("status") == "SKIPPED":
            return "skip"
        if summary.get("status") == "FAILED" and not retry_failed:
            return "skip"

    if db_status == "COMPLETED":
        return "skip"
    if db_status == "SKIPPED":
        return "skip"

    state = read_run_state(body_id, results_root)
    if state and state.get("status") == "INTERRUPTED":
        return "restart"
    if state and state.get("status") == "RUNNING":
        return "restart"
    if db_status == "INTERRUPTED":
        return "restart"
    if db_status == "FAILED":
        return "restart" if retry_failed else "skip"
    return "run"
