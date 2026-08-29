"""Graceful campaign stop control — milestone pauses without interrupting active workers."""

from __future__ import annotations

import json
import sqlite3
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from campaign.constants import CONTROL_DIR, DEFAULT_DB_PATH, GRACEFUL_STOP_PATH, RESULTS_ROOT
from reporting.production_db import utc_now_iso


@dataclass(frozen=True)
class GracefulStopConfig:
    """Configuration for a temporary campaign pause at a milestone."""

    enabled: bool = False
    stop_after_completed: int = 100
    required_bodies: tuple[str, ...] = ()
    reason: str = ""
    resume_from_body: str | None = None
    created_at: str = field(default_factory=utc_now_iso)

    @classmethod
    def from_dict(cls, payload: dict[str, Any]) -> GracefulStopConfig:
        required = payload.get("required_bodies") or ()
        return cls(
            enabled=bool(payload.get("enabled", False)),
            stop_after_completed=int(payload.get("stop_after_completed", 100)),
            required_bodies=tuple(required),
            reason=str(payload.get("reason", "")),
            resume_from_body=payload.get("resume_from_body"),
            created_at=str(payload.get("created_at") or utc_now_iso()),
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "enabled": self.enabled,
            "stop_after_completed": self.stop_after_completed,
            "required_bodies": list(self.required_bodies),
            "reason": self.reason,
            "resume_from_body": self.resume_from_body,
            "created_at": self.created_at,
        }


def load_graceful_stop_config(path: Path = GRACEFUL_STOP_PATH) -> GracefulStopConfig | None:
    if not path.exists():
        return None
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return None
    return GracefulStopConfig.from_dict(payload)


def save_graceful_stop_config(
    config: GracefulStopConfig,
    path: Path = GRACEFUL_STOP_PATH,
) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(config.to_dict(), indent=2) + "\n", encoding="utf-8")
    return path


def body_completed_successfully(
    body_id: str,
    *,
    results_root: Path = RESULTS_ROOT,
) -> bool:
    summary_path = results_root / body_id / "summary.json"
    if not summary_path.exists():
        return False
    try:
        summary = json.loads(summary_path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return False
    return summary.get("status") == "COMPLETED" and summary.get("Cd") is not None


def required_bodies_satisfied(
    required_bodies: tuple[str, ...],
    *,
    results_root: Path = RESULTS_ROOT,
) -> bool:
    if not required_bodies:
        return True
    return all(body_completed_successfully(body_id, results_root=results_root) for body_id in required_bodies)


def graceful_stop_conditions_met(
    completed_count: int,
    *,
    stop_after_completed: int | None,
    required_bodies: tuple[str, ...] = (),
    config: GracefulStopConfig | None = None,
    results_root: Path = RESULTS_ROOT,
) -> bool:
    """Return True when the campaign should stop accepting new bodies."""
    if config is not None and config.enabled:
        if completed_count < config.stop_after_completed:
            return False
        return required_bodies_satisfied(config.required_bodies, results_root=results_root)

    if stop_after_completed is None:
        return False
    if completed_count < stop_after_completed:
        return False
    return required_bodies_satisfied(required_bodies, results_root=results_root)


def count_completed(conn: sqlite3.Connection) -> int:
    row = conn.execute(
        "SELECT COUNT(*) FROM master_samples WHERE status = 'COMPLETED'"
    ).fetchone()
    return int(row[0]) if row else 0


def build_milestone_stop_config(
    *,
    stop_after_completed: int = 100,
    required_bodies: tuple[str, ...] = ("Body_0099", "Body_0100"),
    reason: str = "DOE analysis pause at 100-body milestone",
    resume_from_body: str = "Body_0101",
) -> GracefulStopConfig:
    return GracefulStopConfig(
        enabled=True,
        stop_after_completed=stop_after_completed,
        required_bodies=required_bodies,
        reason=reason,
        resume_from_body=resume_from_body,
    )
