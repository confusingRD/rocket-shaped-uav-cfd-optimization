"""Terminal warning detection for production campaign runtime."""

from __future__ import annotations

import re
import sys
import time
from dataclasses import dataclass, field
from typing import Any

from campaign.constants import CASES_ROOT

from campaign.constants import (
    CASES_ROOT,
    DISK_WARNING_THRESHOLD_GB,
    RAM_WARNING_THRESHOLD_PCT,
    SOLVER_STALL_THRESHOLD_S,
)


@dataclass
class WarningEvent:
    code: str
    message: str
    details: dict[str, Any] = field(default_factory=dict)


class WarningMonitor:
    """Detect runtime conditions and print non-blocking warning blocks."""

    def __init__(
        self,
        *,
        ram_threshold_pct: float = RAM_WARNING_THRESHOLD_PCT,
        disk_threshold_gb: float = DISK_WARNING_THRESHOLD_GB,
        stall_threshold_s: float = SOLVER_STALL_THRESHOLD_S,
    ) -> None:
        self.ram_threshold_pct = ram_threshold_pct
        self.disk_threshold_gb = disk_threshold_gb
        self.stall_threshold_s = stall_threshold_s
        self._emitted: set[str] = set()
        self._stall_tracker: dict[int, dict[str, Any]] = {}

    def reset_emitted(self, code: str) -> None:
        self._emitted.discard(code)

    def check_health_snapshot(self, snapshot: dict[str, Any]) -> list[WarningEvent]:
        warnings: list[WarningEvent] = []
        ram_pct = float(snapshot.get("ram_percent") or 0.0)
        if ram_pct >= self.ram_threshold_pct:
            warnings.append(
                WarningEvent(
                    code="ram_high",
                    message=f"RAM usage exceeds {self.ram_threshold_pct:.0f}%",
                    details={"ram_percent": ram_pct, "ram_used_mb": snapshot.get("ram_used_mb")},
                )
            )

        swap_used = float(snapshot.get("swap_used_mb") or 0.0)
        if swap_used > 0:
            warnings.append(
                WarningEvent(
                    code="swap_in_use",
                    message="Swap memory is in use",
                    details={
                        "swap_used_mb": swap_used,
                        "swap_total_mb": snapshot.get("swap_total_mb"),
                    },
                )
            )

        disk_free = float(snapshot.get("disk_free_gb") or 0.0)
        if disk_free < self.disk_threshold_gb:
            warnings.append(
                WarningEvent(
                    code="disk_low",
                    message=f"Disk free space below {self.disk_threshold_gb:.0f} GB",
                    details={"disk_free_gb": disk_free},
                )
            )
        return warnings

    def check_worker_stalls(self, worker_states: list[dict[str, Any]]) -> list[WarningEvent]:
        warnings: list[WarningEvent] = []
        now = time.time()
        active_ids = set()

        for ws in worker_states:
            worker_id = int(ws.get("worker_id") or 0)
            body_id = ws.get("body_id")
            stage = ws.get("stage")
            if not body_id or stage != "run":
                self._stall_tracker.pop(worker_id, None)
                continue
            active_ids.add(worker_id)

            iteration = ws.get("iteration")
            residual = ws.get("residual")

            elapsed = ws.get("elapsed_s")
            tracker = self._stall_tracker.get(worker_id)

            if tracker is None or tracker.get("body_id") != body_id:
                self._stall_tracker[worker_id] = {
                    "iteration": iteration,
                    "since": now,
                    "body_id": body_id,
                }
                continue

            if iteration is not None and iteration != tracker.get("iteration"):
                self.reset_emitted(f"stall_worker_{worker_id}_{body_id}")
                tracker["iteration"] = iteration
                tracker["since"] = now
                tracker["body_id"] = body_id
            elif elapsed is not None and (now - tracker["since"]) >= self.stall_threshold_s:
                warnings.append(
                    WarningEvent(
                        code=f"stall_worker_{worker_id}_{body_id}",
                        message=(
                            f"Solver may be stalled on {body_id} "
                            f"(iteration unchanged for {self.stall_threshold_s:.0f}s)"
                        ),
                        details={
                            "worker_id": worker_id,
                            "body_id": body_id,
                            "iteration": iteration,
                            "residual": residual,
                            "elapsed_s": elapsed,
                        },
                    )
                )
                tracker["since"] = now  # avoid spamming every health tick

        for worker_id in list(self._stall_tracker):
            if worker_id not in active_ids:
                self._stall_tracker.pop(worker_id, None)

        return warnings

    def worker_crash(self, worker_id: int, body_id: str, error: str) -> WarningEvent:
        return WarningEvent(
            code=f"worker_crash_{worker_id}_{body_id}",
            message=f"Worker {worker_id} crashed on {body_id}",
            details={"worker_id": worker_id, "body_id": body_id, "error": error},
        )

    def checkpoint_failed(self, error: str) -> WarningEvent:
        return WarningEvent(
            code="checkpoint_failed",
            message="Checkpoint creation failed",
            details={"error": error},
        )

    def backup_failed(self, error: str) -> WarningEvent:
        return WarningEvent(
            code="backup_failed",
            message="Database backup failed",
            details={"error": error},
        )

    def emit(self, events: list[WarningEvent], *, repeat: bool = False) -> None:
        """Print formatted warning blocks to the terminal."""
        for event in events:
            key = event.code
            if not repeat and key in self._emitted:
                continue
            self._emitted.add(key)
            block = (
                "\n"
                "!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!\n"
                f"  WARNING: {event.message}\n"
            )
            for k, v in event.details.items():
                block += f"  {k}: {v}\n"
            block += "!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!\n"
            sys.stderr.write(block)
            sys.stderr.flush()


def _read_solver_progress(
    body_id: str,
    *,
    cases_root: Path | None = None,
) -> tuple[int | None, float | None]:
    """Read the tail of log.foamRun for iteration and residual (cheap tail read)."""
    root = cases_root if cases_root is not None else CASES_ROOT
    log_path = root / body_id / "log.foamRun"
    if not log_path.exists():
        return None, None
    try:
        size = log_path.stat().st_size
        with open(log_path, "rb") as handle:
            handle.seek(max(0, size - 8192))
            text = handle.read().decode(errors="replace")
    except OSError:
        return None, None

    parsed = parse_foam_log_from_tail(text)
    return parsed.get("iterations"), parsed.get("residual")


def parse_foam_log_from_tail(text: str) -> dict[str, Any]:
    """Extract latest iteration and residual from a log tail fragment."""
    result: dict[str, Any] = {"iterations": None, "residual": None}
    times = re.findall(r"^Time = (\d+)", text, flags=re.M)
    if times:
        result["iterations"] = int(times[-1])
    residuals = re.findall(
        r"Solving for (\w+),\s*Initial residual = ([0-9.eE+-]+)",
        text,
    )
    if residuals:
        result["residual"] = float(residuals[-1][1])
    return result
