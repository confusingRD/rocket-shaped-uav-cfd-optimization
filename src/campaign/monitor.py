"""Campaign runtime monitor — health sampling, dashboard, and warnings."""

from __future__ import annotations

import time
from pathlib import Path
from typing import Any, TYPE_CHECKING

from campaign.constants import (
    DASHBOARD_REFRESH_INTERVAL_S,
    DEFAULT_DB_PATH,
    HEALTH_SAMPLE_INTERVAL_S,
    MANIFEST_PATH,
    TOTAL_BODIES,
)
from campaign.dashboard import TerminalDashboard
from campaign.health import HEALTH_JSON_PATH, HEALTH_HISTORY_CSV, HealthMonitor
from campaign.manifest import load_manifest
from campaign.retry import sync_retry_to_manifest
from campaign.warnings import WarningMonitor
from reporting.production_db import connect, count_by_status, count_by_termination_reason

if TYPE_CHECKING:
    from campaign.scheduler import CampaignScheduler


class CampaignMonitor:
    """Orchestrate health monitoring, terminal dashboard, and warnings."""

    def __init__(
        self,
        *,
        scheduler: CampaignScheduler | None = None,
        db_path: Path = DEFAULT_DB_PATH,
        manifest_path: Path = MANIFEST_PATH,
        health_interval_s: float = HEALTH_SAMPLE_INTERVAL_S,
        dashboard_interval_s: float = DASHBOARD_REFRESH_INTERVAL_S,
        enable_dashboard: bool = True,
        health_json_path: Path = HEALTH_JSON_PATH,
        history_csv_path: Path = HEALTH_HISTORY_CSV,
        results_root: Path | None = None,
        cases_root: Path | None = None,
    ) -> None:
        self.scheduler = scheduler
        self.db_path = db_path
        self.manifest_path = manifest_path
        self.enable_dashboard = enable_dashboard
        self._results_root = results_root
        self._cases_root = cases_root
        self.warnings = WarningMonitor()
        self.health = HealthMonitor(
            interval_s=health_interval_s,
            state_provider=self._campaign_state,
            on_sample=self._on_health_sample,
            health_json_path=health_json_path,
            history_csv_path=history_csv_path,
        )
        self.dashboard = TerminalDashboard(
            refresh_interval_s=dashboard_interval_s,
            state_provider=self._dashboard_state,
        )

    def start(self) -> None:
        self.health.start()
        if self.enable_dashboard:
            self.dashboard.start()

    def stop(self) -> None:
        self.health.stop()
        if self.enable_dashboard:
            self.dashboard.stop()

    def _campaign_state(self) -> dict[str, Any]:
        manifest = load_manifest(self.manifest_path)
        counts: dict[str, int] = {}
        try:
            conn = connect(self.db_path)
            try:
                counts = count_by_status(conn)
                sync_retry_to_manifest(manifest, conn)
            finally:
                conn.close()
        except Exception:
            pass

        eta = manifest.get("eta") or {}
        worker_util = 0.0
        active_workers = 0
        total_workers = 0
        active_bodies: list[str] = []
        avg_solve_speed = None

        if self.scheduler is not None:
            total_workers = len(self.scheduler.worker_configs)
            active_workers = sum(
                1 for s in self.scheduler._stats.values() if s.active_body
            )
            worker_util = (active_workers / max(total_workers, 1)) * 100.0
            active_bodies = list(self.scheduler._active_bodies.values())
            speeds = []
            for ws in self._worker_states():
                if ws.get("solve_speed_iter_per_s") is not None:
                    speeds.append(ws["solve_speed_iter_per_s"])
            if speeds:
                avg_solve_speed = sum(speeds) / len(speeds)

        return {
            "campaign_status": manifest.get("campaign_status"),
            "completed_bodies": counts.get("COMPLETED", 0),
            "total_bodies": manifest.get("total_bodies", TOTAL_BODIES),
            "retried_bodies": manifest.get("retried_bodies", 0),
            "worker_utilization_pct": round(worker_util, 1),
            "active_workers": active_workers,
            "total_workers": total_workers,
            "active_bodies": active_bodies,
            "avg_runtime_s": eta.get("average_runtime_s"),
            "estimated_remaining_s": eta.get("estimated_remaining_s"),
            "estimated_finish_at": eta.get("estimated_finish_at"),
            "avg_solve_speed_iter_per_s": avg_solve_speed,
        }

    def _dashboard_state(self) -> dict[str, Any]:
        manifest = load_manifest(self.manifest_path)
        counts: dict[str, int] = {}
        termination_counts: dict[str, int] = {}
        try:
            conn = connect(self.db_path)
            try:
                counts = count_by_status(conn)
                termination_counts = count_by_termination_reason(conn)
                sync_retry_to_manifest(manifest, conn)
            finally:
                conn.close()
        except Exception:
            pass

        return {
            "manifest": manifest,
            "counts": counts,
            "termination_counts": termination_counts,
            "eta": manifest.get("eta"),
            "retried_bodies": manifest.get("retried_bodies", 0),
            "workers": self._worker_states(),
            "health": self.health.latest or {},
        }

    def _worker_states(self) -> list[dict[str, Any]]:
        if self.scheduler is None:
            return []
        states: list[dict[str, Any]] = []
        now = time.time()
        for cfg in self.scheduler.worker_configs:
            stats = self.scheduler._stats.get(cfg.worker_id)
            body_id = stats.active_body if stats else None
            stage = None
            elapsed = None
            iteration = None
            residual = None
            solve_speed = None
            eta_s = None
            if body_id:
                from campaign.recovery import read_run_state
                from campaign.warnings import _read_solver_progress

                run_state = read_run_state(
                    body_id,
                    self._results_root,
                ) if self._results_root is not None else read_run_state(body_id)
                stage = run_state.get("stage") if run_state else None
                started = run_state.get("started_at") if run_state else None
                if started:
                    try:
                        from datetime import datetime

                        t0 = datetime.fromisoformat(started.replace("Z", "+00:00"))
                        elapsed = now - t0.timestamp()
                    except (ValueError, TypeError):
                        elapsed = None
                if stage == "run":
                    iteration, residual = (
                        _read_solver_progress(body_id, cases_root=self._cases_root)
                        if self._cases_root is not None
                        else _read_solver_progress(body_id)
                    )
                    if iteration and elapsed and elapsed > 0:
                        solve_speed = iteration / elapsed
                if self.scheduler.estimated_runtime_s and elapsed is not None:
                    eta_s = max(0.0, self.scheduler.estimated_runtime_s - elapsed)
            states.append(
                {
                    "worker_id": cfg.worker_id,
                    "body_id": body_id,
                    "stage": stage,
                    "elapsed_s": elapsed,
                    "iteration": iteration,
                    "residual": residual,
                    "solve_speed_iter_per_s": solve_speed,
                    "eta_s": eta_s,
                }
            )
        return states

    def _on_health_sample(self, snapshot: dict[str, Any]) -> None:
        if self.scheduler is not None:
            self.scheduler.maybe_check_graceful_stop(
                snapshot.get("completed_bodies"),
            )
        events = self.warnings.check_health_snapshot(snapshot)
        if self.scheduler is not None:
            events.extend(self.warnings.check_worker_stalls(self._worker_states()))
        self.warnings.emit(events)

    def notify_worker_crash(self, worker_id: int, body_id: str, error: str) -> None:
        self.warnings.emit([self.warnings.worker_crash(worker_id, body_id, error)], repeat=True)

    def notify_checkpoint_failed(self, error: str) -> None:
        self.warnings.emit([self.warnings.checkpoint_failed(error)], repeat=True)

    def notify_backup_failed(self, error: str) -> None:
        self.warnings.emit([self.warnings.backup_failed(error)], repeat=True)
