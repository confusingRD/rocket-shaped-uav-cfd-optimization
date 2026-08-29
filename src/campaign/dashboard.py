"""Live terminal dashboard for production campaign progress."""

from __future__ import annotations

import sys
import threading
import time
from typing import Any, Callable

from campaign.constants import DASHBOARD_REFRESH_INTERVAL_S, TOTAL_BODIES


def _supports_ansi() -> bool:
    return sys.stdout.isatty() and "NO_COLOR" not in __import__("os").environ


def _format_duration(seconds: float | None) -> str:
    if seconds is None:
        return "n/a"
    seconds = max(0.0, float(seconds))
    if seconds >= 3600:
        return f"{seconds / 3600:.1f} h"
    if seconds >= 60:
        return f"{seconds / 60:.1f} min"
    return f"{seconds:.0f} s"


def _stage_label(stage: str | None) -> str:
    labels = {
        "prepare": "Preparing case",
        "mesh": "Preparing mesh",
        "run": "OpenFOAM solving",
        "complete": "Complete",
    }
    return labels.get(stage or "", stage or "Idle")


def render_dashboard(state: dict[str, Any]) -> str:
    """Build the dashboard text block from campaign state."""
    manifest = state.get("manifest") or {}
    counts = state.get("counts") or {}
    termination = state.get("termination_counts") or {}
    workers = state.get("workers") or []
    health = state.get("health") or {}
    eta = manifest.get("eta") or state.get("eta") or {}

    total = manifest.get("total_bodies", TOTAL_BODIES)
    completed = counts.get("COMPLETED", manifest.get("completed_bodies", 0))
    running = counts.get("RUNNING", 0)
    pending = counts.get("PENDING", 0)
    failed = counts.get("FAILED", 0)
    retried = manifest.get("retried_bodies", state.get("retried_bodies", 0))
    interrupted = counts.get("INTERRUPTED", 0)
    skipped = counts.get("SKIPPED", 0)

    lines = [
        "--------------------------------------------------------",
        "",
        "Rocket Drone CFD Production Campaign",
        "",
        f"Campaign: {manifest.get('campaign_name', 'Rocket Drone 200-Body DOE')}",
        f"Completed: {completed}/{total}",
        f"Running: {running}",
        f"Pending: {pending}",
        f"Interrupted: {interrupted}",
        f"Failed: {failed}",
        f"Skipped: {skipped}",
        f"Retried: {retried}",
        "",
        f"Residual converged: {termination.get('RESIDUAL_CONVERGED', 0)}",
        f"Max iterations: {termination.get('MAX_ITERATIONS', 0)}",
        f"Solver crash: {termination.get('SOLVER_CRASH', 0)}",
        "",
    ]

    if not workers:
        lines.append("Workers: idle")
        lines.append("")
    else:
        for ws in workers:
            wid = ws.get("worker_id", "?")
            body = ws.get("body_id") or "—"
            stage = _stage_label(ws.get("stage"))
            lines.append(f"Worker {wid}")
            lines.append(f"  {body}")
            lines.append(f"  {stage}")
            lines.append(f"  Elapsed: {_format_duration(ws.get('elapsed_s'))}")
            if ws.get("stage") == "run":
                if ws.get("residual") is not None:
                    lines.append(f"  Residual: {ws['residual']:.2e}")
                if ws.get("iteration") is not None:
                    lines.append(f"  Iteration: {ws['iteration']}")
            if ws.get("eta_s") is not None:
                lines.append(f"  ETA: {_format_duration(ws['eta_s'])}")
            lines.append("")

    lines.extend(
        [
            f"CPU: {health.get('cpu_percent', 'n/a')}%",
            f"RAM: {health.get('ram_percent', 'n/a')}% "
            f"({health.get('ram_used_mb', '?')}/{health.get('ram_total_mb', '?')} MB)",
            f"Swap: {health.get('swap_used_mb', 0):.0f} MB",
            f"Disk free: {health.get('disk_free_gb', 'n/a')} GB",
            "",
            f"Average body runtime: {_format_duration(eta.get('average_runtime_s'))}",
            f"Moving avg runtime: {_format_duration(eta.get('moving_average_runtime_s'))}",
            f"Campaign ETA: {eta.get('estimated_finish_at') or _format_duration(eta.get('estimated_remaining_s'))}",
            "",
            "--------------------------------------------------------",
        ]
    )
    return "\n".join(lines)


class TerminalDashboard:
    """Continuously refresh an in-place terminal dashboard."""

    def __init__(
        self,
        *,
        refresh_interval_s: float = DASHBOARD_REFRESH_INTERVAL_S,
        state_provider: Callable[[], dict[str, Any]] | None = None,
    ) -> None:
        self.refresh_interval_s = refresh_interval_s
        self._state_provider = state_provider or (lambda: {})
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None
        self._use_ansi = _supports_ansi()
        self._line_count = 0
        self._last_snapshot = ""

    @property
    def last_snapshot(self) -> str:
        return self._last_snapshot

    def render_once(self, state: dict[str, Any] | None = None) -> str:
        text = render_dashboard(state or self._state_provider())
        self._last_snapshot = text
        if self._use_ansi:
            self._render_ansi(text)
        else:
            # Fallback: print at most every refresh interval without flooding.
            sys.stdout.write(text + "\n")
            sys.stdout.flush()
        return text

    def _render_ansi(self, text: str) -> None:
        lines = text.split("\n")
        previous_count = self._line_count

        if previous_count:
            sys.stdout.write(f"\033[{previous_count}A")

        render_count = max(previous_count, len(lines))

        for i in range(render_count):
            line = lines[i] if i < len(lines) else ""
            sys.stdout.write("\033[2K" + line + "\n")

        self._line_count = render_count
        sys.stdout.flush()

    def start(self) -> None:
        if self._thread and self._thread.is_alive():
            return
        self._stop.clear()
        self._thread = threading.Thread(
            target=self._run_loop,
            name="campaign-terminal-dashboard",
            daemon=True,
        )
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()
        if self._thread:
            self._thread.join(timeout=self.refresh_interval_s + 5)
            self._thread = None
        if self._use_ansi and self._line_count:
            sys.stdout.write(f"\033[{self._line_count}A")
            for _ in range(self._line_count):
                sys.stdout.write("\033[2K\n")
            self._line_count = 0
            sys.stdout.flush()

    def _run_loop(self) -> None:
        while not self._stop.is_set():
            try:
                self.render_once()
            except Exception:
                pass
            if self._stop.wait(self.refresh_interval_s):
                break
