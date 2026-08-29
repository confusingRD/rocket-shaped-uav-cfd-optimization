"""Production worker scheduler — two simultaneous OpenFOAM jobs by default."""

from __future__ import annotations

import os
import queue
import signal
import threading
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable

from campaign.backup import backup_database
from campaign.checkpoint import create_checkpoint
from campaign.constants import DEFAULT_DB_PATH, GRACEFUL_STOP_PATH, MANIFEST_PATH, PROGRESS_REPORTS_DIR, CHECKPOINTS_DIR, TOTAL_BODIES
from campaign.control import (
    graceful_stop_conditions_met,
    load_graceful_stop_config,
)
from campaign.db_sync import db_write_lock
from campaign.eta import compute_eta
from campaign.manifest import load_manifest, save_manifest, set_campaign_status, sync_manifest_from_db, update_eta
from campaign.openfoam_parallel import format_cpu_set
from campaign.resume import ResumePlan, resume_plan_summary
from campaign.retry import sync_retry_to_manifest
from reporting.generate import on_doe_batch_complete
from reporting.production_db import connect, count_by_status, fetch_campaign_rows, utc_now_iso

RunBodyFn = Callable[..., dict[str, Any]]
BatchCompleteFn = Callable[[Path], dict[str, Path] | None]


@dataclass(frozen=True)
class WorkerConfig:
    worker_id: int
    cpu_affinity: tuple[int, ...]
    mpi_procs: int


@dataclass
class WorkerStats:
    worker_id: int
    bodies_started: int = 0
    bodies_completed: int = 0
    bodies_failed: int = 0
    active_body: str | None = None
    last_runtime_s: float | None = None
    last_cd: float | None = None


@dataclass
class CampaignScheduler:
    """Coordinate multiple independent OpenFOAM workers over a shared body queue."""

    queue_bodies: list[str]
    restart_bodies: set[str]
    campaign_uuid: str
    db_path: Path = DEFAULT_DB_PATH
    manifest_path: Path = MANIFEST_PATH
    worker_configs: list[WorkerConfig] = field(default_factory=list)
    run_body_fn: RunBodyFn | None = None
    plan: ResumePlan | None = None
    estimated_runtime_s: float | None = None
    quiet_logging: bool = False
    stop_after_completed: int | None = None
    required_bodies: frozenset[str] = field(default_factory=frozenset)
    control_path: Path = GRACEFUL_STOP_PATH
    on_batch_complete: BatchCompleteFn | None = None
    checkpoints_dir: Path = CHECKPOINTS_DIR
    progress_reports_dir: Path = PROGRESS_REPORTS_DIR
    backup_prefix: str = "production"
    checkpoint_kwargs: dict[str, Any] = field(default_factory=dict)

    _interrupt: threading.Event = field(default_factory=threading.Event, init=False, repr=False)
    _accept_new_work: bool = field(default=True, init=False, repr=False)
    _graceful_stop_requested: bool = field(default=False, init=False, repr=False)
    _graceful_stop_reason: str = field(default="", init=False, repr=False)
    _monitor: Any = field(default=None, init=False, repr=False)
    _proc_registry: list[Any] = field(default_factory=list, init=False, repr=False)
    _active_bodies: dict[int, str] = field(default_factory=dict, init=False, repr=False)
    _stats: dict[int, WorkerStats] = field(default_factory=dict, init=False, repr=False)
    _stats_lock: threading.Lock = field(default_factory=threading.Lock, init=False, repr=False)
    _completed_count: int = field(default=0, init=False, repr=False)
    _results: list[dict[str, Any]] = field(default_factory=list, init=False, repr=False)
    _results_lock: threading.Lock = field(default_factory=threading.Lock, init=False, repr=False)

    def __post_init__(self) -> None:
        if not self.worker_configs:
            self.worker_configs = default_worker_configs()
        for cfg in self.worker_configs:
            self._stats[cfg.worker_id] = WorkerStats(worker_id=cfg.worker_id)

    def request_shutdown(self) -> None:
        self._interrupt.set()
        self._terminate_active_processes()

    def request_graceful_stop(self, reason: str = "") -> None:
        """Stop accepting new bodies without terminating active OpenFOAM processes."""
        if self._graceful_stop_requested:
            return
        self._graceful_stop_requested = True
        self._accept_new_work = False
        self._graceful_stop_reason = reason
        self._drain_pending_queue()
        if not self.quiet_logging:
            print(
                "[campaign graceful stop]\n"
                f"Reason: {reason or 'milestone reached'}\n"
                "Active workers will finish current bodies; no new bodies will start.\n"
                "-----------------------",
                flush=True,
            )

    @property
    def graceful_stop_requested(self) -> bool:
        return self._graceful_stop_requested

    def maybe_check_graceful_stop(self, completed_count: int | None = None) -> bool:
        """Evaluate milestone/control-file stop conditions."""
        if self._graceful_stop_requested or self.interrupted:
            return self._graceful_stop_requested

        if completed_count is None:
            with db_write_lock():
                conn = connect(self.db_path)
                try:
                    completed_count = count_by_status(conn).get("COMPLETED", 0)
                finally:
                    conn.close()

        config = load_graceful_stop_config(self.control_path)
        if graceful_stop_conditions_met(
            completed_count,
            stop_after_completed=self.stop_after_completed,
            required_bodies=tuple(sorted(self.required_bodies)),
            config=config,
        ):
            reason = config.reason if config and config.enabled else (
                f"Completed {completed_count} bodies"
                + (
                    f" ({', '.join(sorted(self.required_bodies))} satisfied)"
                    if self.required_bodies
                    else ""
                )
            )
            self.request_graceful_stop(reason=reason)
            return True
        return False

    def _drain_pending_queue(self) -> None:
        body_queue: queue.Queue[str | None] | None = getattr(self, "_body_queue", None)
        if body_queue is None:
            return
        while True:
            try:
                body_id = body_queue.get_nowait()
            except queue.Empty:
                break
            if body_id is not None:
                body_queue.task_done()

    def _should_stop_worker(self) -> bool:
        return self.interrupted or not self._accept_new_work

    def _body_number(self, body_id: str) -> int | None:
        if not body_id.startswith("Body_"):
            return None
        try:
            return int(body_id.split("_", 1)[1])
        except (IndexError, ValueError):
            return None

    def _should_accept_body(self, body_id: str, completed_count: int | None = None) -> bool:
        if self._should_stop_worker():
            return False
        if completed_count is None:
            with db_write_lock():
                conn = connect(self.db_path)
                try:
                    completed_count = count_by_status(conn).get("COMPLETED", 0)
                finally:
                    conn.close()
        config = load_graceful_stop_config(self.control_path)
        stop_after = self.stop_after_completed
        required = tuple(sorted(self.required_bodies))
        if config and config.enabled:
            stop_after = config.stop_after_completed
            required = config.required_bodies
        if graceful_stop_conditions_met(
            completed_count,
            stop_after_completed=stop_after,
            required_bodies=required,
            config=config,
        ):
            self.request_graceful_stop(
                reason=config.reason if config and config.enabled else f"Completed {completed_count} bodies"
            )
            return False
        body_number = self._body_number(body_id)
        if body_number is not None and stop_after is not None and body_number > stop_after:
            return False
        return True

    @property
    def interrupted(self) -> bool:
        return self._interrupt.is_set()

    def _terminate_active_processes(self) -> None:
        for proc in list(self._proc_registry):
            if proc.poll() is None:
                try:
                    os.killpg(os.getpgid(proc.pid), signal.SIGTERM)
                except (ProcessLookupError, PermissionError, OSError):
                    try:
                        proc.terminate()
                    except OSError:
                        pass
        deadline = time.time() + 15.0
        for proc in list(self._proc_registry):
            if proc.poll() is None and time.time() < deadline:
                try:
                    proc.wait(timeout=max(0.0, deadline - time.time()))
                except Exception:
                    pass
            if proc.poll() is None:
                try:
                    os.killpg(os.getpgid(proc.pid), signal.SIGKILL)
                except (ProcessLookupError, PermissionError, OSError):
                    try:
                        proc.kill()
                    except OSError:
                        pass

    def _worker_loop(self, config: WorkerConfig) -> None:
        body_queue: queue.Queue[str | None] = getattr(self, "_body_queue")
        while not self.interrupted:
            if self._should_stop_worker():
                break
            self.maybe_check_graceful_stop()
            try:
                body_id = body_queue.get(timeout=0.5)
            except queue.Empty:
                continue
            if body_id is None:
                body_queue.task_done()
                break
            if not self._should_accept_body(body_id):
                body_queue.put(body_id)
                continue
            if self._should_stop_worker():
                body_queue.put(body_id)
                break

            clean_restart = body_id in self.restart_bodies
            if not self.quiet_logging:
                self._log_worker_start(config, body_id)
            with self._stats_lock:
                self._stats[config.worker_id].bodies_started += 1
                self._stats[config.worker_id].active_body = body_id
            self._active_bodies[config.worker_id] = body_id

            try:
                summary = self.run_body_fn(  # type: ignore[misc]
                    body_id,
                    campaign_uuid=self.campaign_uuid,
                    db_path=self.db_path,
                    clean_restart=clean_restart,
                    is_retry=clean_restart,
                    worker_id=config.worker_id,
                    mpi_procs=config.mpi_procs,
                    cpu_affinity=config.cpu_affinity,
                    interrupt_event=self._interrupt,
                    proc_registry=self._proc_registry,
                )
                with self._results_lock:
                    self._results.append(summary)
                self._on_body_complete(config, body_id, summary)
            except Exception as exc:
                with self._stats_lock:
                    self._stats[config.worker_id].bodies_failed += 1
                if self._monitor is not None:
                    self._monitor.notify_worker_crash(config.worker_id, body_id, str(exc))
                elif not self.quiet_logging:
                    print(
                        f"[Worker {config.worker_id}]\n"
                        f"Failed {body_id}: {exc}\n"
                        f"-----------------------",
                        flush=True,
                    )
            finally:
                self._active_bodies.pop(config.worker_id, None)
                with self._stats_lock:
                    self._stats[config.worker_id].active_body = None
                body_queue.task_done()
                if self._should_stop_worker():
                    break

    def _on_body_complete(self, config: WorkerConfig, body_id: str, summary: dict[str, Any]) -> None:
        status = summary.get("status")
        runtime = summary.get("wall_clock_s")
        cd = summary.get("Cd")
        with self._stats_lock:
            stats = self._stats[config.worker_id]
            stats.bodies_completed += 1
            stats.last_runtime_s = runtime
            stats.last_cd = cd
            if status == "FAILED":
                stats.bodies_failed += 1

        if not self.quiet_logging:
            print(
                f"[Worker {config.worker_id}]\n"
                f"Completed {body_id}\n"
                f"Cd = {cd}\n"
                f"Runtime = {runtime:.1f} s\n"
                f"Status = {status}\n"
                f"-----------------------",
                flush=True,
            )

        with db_write_lock():
            conn = connect(self.db_path)
            try:
                counts = count_by_status(conn)
                completed = counts.get("COMPLETED", 0)
                self._completed_count = completed
                manifest = load_manifest(self.manifest_path)
                sync_manifest_from_db(manifest, counts)
                sync_retry_to_manifest(manifest, conn)
                rows = fetch_campaign_rows(conn)
                eta = compute_eta(
                    rows,
                    total_bodies=manifest.get("total_bodies", TOTAL_BODIES),
                    workers=len(self.worker_configs),
                )
                update_eta(manifest, eta)
                save_manifest(manifest, self.manifest_path)
                try:
                    create_checkpoint(
                    completed,
                    db_path=self.db_path,
                    manifest_path=self.manifest_path,
                    workers=len(self.worker_configs),
                    checkpoints_dir=self.checkpoints_dir,
                    progress_reports_dir=self.progress_reports_dir,
                    monitor=self._monitor,
                    backup_prefix=self.backup_prefix,
                    data_dir=self.db_path.parent,
                    **self.checkpoint_kwargs,
                )
                except Exception as exc:
                    if self._monitor is not None:
                        self._monitor.notify_checkpoint_failed(str(exc))
                try:
                    backup_database(
                        self.db_path,
                        data_dir=self.db_path.parent,
                        backup_prefix=self.backup_prefix,
                    )
                except Exception as exc:
                    if self._monitor is not None:
                        self._monitor.notify_backup_failed(str(exc))
                if not self.quiet_logging:
                    self._log_campaign_progress(counts, eta)
                self.maybe_check_graceful_stop(completed)
            finally:
                conn.close()

    def _log_worker_start(self, config: WorkerConfig, body_id: str) -> None:
        cpu_text = format_cpu_set(config.cpu_affinity) or "unpinned"
        eta_text = (
            f"{self.estimated_runtime_s / 60.0:.0f} min"
            if self.estimated_runtime_s
            else "unknown"
        )
        print(
            f"[Worker {config.worker_id}]\n"
            f"Starting {body_id}\n"
            f"Using CPUs {cpu_text}\n"
            f"Running OpenFOAM ({config.mpi_procs} MPI)\n"
            f"Estimated {eta_text}\n"
            f"-----------------------",
            flush=True,
        )

    def _log_campaign_progress(self, counts: dict[str, int], eta: dict[str, Any]) -> None:
        manifest = load_manifest(self.manifest_path)
        total = manifest.get("total_bodies", TOTAL_BODIES)
        completed = counts.get("COMPLETED", 0)
        remaining = sum(counts.get(s, 0) for s in ("PENDING", "INTERRUPTED", "RUNNING"))
        failed = counts.get("FAILED", 0)
        active_workers = sum(1 for s in self._stats.values() if s.active_body)
        util = active_workers / max(len(self.worker_configs), 1) * 100.0
        print(
            "[campaign progress]\n"
            f"Completed: {completed}/{total} | Remaining: {remaining} | Failed: {failed}\n"
            f"Worker utilization: {util:.0f}% ({active_workers}/{len(self.worker_configs)} active)\n"
            f"ETA finish: {eta.get('estimated_finish_at') or 'n/a'} "
            f"({eta.get('estimated_remaining_s')} s remaining, {len(self.worker_configs)} workers)\n"
            f"-----------------------",
            flush=True,
        )

    def build_shutdown_summary(self, counts: dict[str, int]) -> dict[str, Any]:
        manifest = load_manifest(self.manifest_path)
        return build_graceful_shutdown_summary(
            counts,
            total_bodies=manifest.get("total_bodies", TOTAL_BODIES),
        )

    def _print_shutdown_summary(self, summary: dict[str, Any]) -> None:
        print_graceful_shutdown_summary(summary)

    def run(self) -> dict[str, Any]:
        if self.run_body_fn is None:
            raise RuntimeError("CampaignScheduler.run_body_fn is not set")

        self._body_queue: queue.Queue[str | None] = queue.Queue()
        threads = []
        for config in self.worker_configs:
            thread = threading.Thread(
                target=self._worker_loop,
                name=f"campaign-worker-{config.worker_id}",
                args=(config,),
                daemon=False,
            )
            threads.append(thread)
            thread.start()

        for body_id in self.queue_bodies:
            self._body_queue.put(body_id)

        self.maybe_check_graceful_stop()

        for _ in self.worker_configs:
            self._body_queue.put(None)

        for thread in threads:
            thread.join()

        counts: dict[str, int] = {}
        with db_write_lock():
            conn = connect(self.db_path)
            try:
                counts = count_by_status(conn)
                manifest = load_manifest(self.manifest_path)
                sync_manifest_from_db(manifest, counts)
                sync_retry_to_manifest(manifest, conn)
                if self.interrupted:
                    set_campaign_status(manifest, "INTERRUPTED")
                elif self._graceful_stop_requested:
                    set_campaign_status(manifest, "PAUSED")
                    manifest["graceful_stop"] = {
                        "reason": self._graceful_stop_reason,
                        "stopped_at": utc_now_iso(),
                        "completed_bodies": counts.get("COMPLETED", 0),
                        "stop_after_completed": self.stop_after_completed,
                        "required_bodies": sorted(self.required_bodies),
                    }
                save_manifest(manifest, self.manifest_path)
            finally:
                conn.close()

        summary = self.build_shutdown_summary(counts)
        if self._graceful_stop_requested and not self.interrupted:
            self._print_shutdown_summary(summary)

        if counts.get("COMPLETED", 0) >= load_manifest(self.manifest_path).get("total_bodies", TOTAL_BODIES):
            complete_fn = self.on_batch_complete or on_doe_batch_complete
            complete_fn(self.db_path)

        plan_summary = resume_plan_summary(self.plan) if self.plan else {}
        return {
            "ran": len(self._results),
            "plan": plan_summary,
            "counts": counts,
            "interrupted": self.interrupted,
            "graceful_stop": self._graceful_stop_requested,
            "shutdown_summary": summary,
            "workers": len(self.worker_configs),
            "worker_stats": {
                wid: {
                    "started": stats.bodies_started,
                    "completed": stats.bodies_completed,
                    "failed": stats.bodies_failed,
                }
                for wid, stats in self._stats.items()
            },
        }


def default_worker_configs(
    *,
    workers: int = 2,
    cores_per_worker: int = 6,
) -> list[WorkerConfig]:
    """Build worker configs with non-overlapping CPU affinity blocks."""
    configs: list[WorkerConfig] = []
    next_cpu = 0
    for worker_id in range(1, workers + 1):
        cpus = tuple(range(next_cpu, next_cpu + cores_per_worker))
        configs.append(
            WorkerConfig(
                worker_id=worker_id,
                cpu_affinity=cpus,
                mpi_procs=cores_per_worker,
            )
        )
        next_cpu += cores_per_worker
    return configs


def active_body_ids(scheduler: CampaignScheduler) -> list[str]:
    """Return body IDs currently assigned to workers (for interrupt handling)."""
    return list(scheduler._active_bodies.values())


def build_graceful_shutdown_summary(counts: dict[str, int], *, total_bodies: int = TOTAL_BODIES) -> dict[str, Any]:
    completed = counts.get("COMPLETED", 0)
    pending = counts.get("PENDING", 0)
    interrupted = counts.get("INTERRUPTED", 0)
    failed = counts.get("FAILED", 0)
    running = counts.get("RUNNING", 0)
    if interrupted > 0 or running > 0:
        campaign_status = "INTERRUPTED" if interrupted > 0 else "RUNNING"
    elif completed >= total_bodies:
        campaign_status = "COMPLETED"
    else:
        campaign_status = "PAUSED"
    return {
        "completed_bodies": completed,
        "pending_bodies": pending,
        "failed_bodies": failed,
        "interrupted_bodies": interrupted,
        "running_bodies": running,
        "total_bodies": total_bodies,
        "campaign_status": campaign_status,
        "simulations_interrupted": interrupted > 0 or running > 0,
    }


def print_graceful_shutdown_summary(summary: dict[str, Any]) -> None:
    interrupted_text = (
        "WARNING: some simulations were still active or interrupted."
        if summary.get("simulations_interrupted")
        else "No simulations were interrupted."
    )
    print(
        "[campaign shutdown summary]\n"
        f"Completed bodies: {summary['completed_bodies']}/{summary['total_bodies']}\n"
        f"Pending bodies: {summary['pending_bodies']}\n"
        f"Failed bodies: {summary['failed_bodies']}\n"
        f"Campaign status: {summary['campaign_status']}\n"
        f"{interrupted_text}\n"
        "-----------------------",
        flush=True,
    )
