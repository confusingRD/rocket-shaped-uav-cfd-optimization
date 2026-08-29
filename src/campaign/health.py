"""Lightweight runtime health monitoring for production campaigns."""

from __future__ import annotations

import csv
import json
import shutil
import threading
import time
from pathlib import Path
from typing import Any, Callable

from campaign.constants import (
    CAMPAIGN_STATE_DIR,
    HEALTH_SAMPLE_INTERVAL_S,
    REPO_ROOT,
    TOTAL_BODIES,
)
from reporting.production_db import utc_now_iso

HEALTH_JSON_PATH = CAMPAIGN_STATE_DIR / "health.json"
HEALTH_HISTORY_CSV = CAMPAIGN_STATE_DIR / "health_history.csv"

_CSV_HEADER = [
    "timestamp",
    "cpu_percent",
    "ram_used_mb",
    "ram_total_mb",
    "ram_percent",
    "swap_used_mb",
    "swap_total_mb",
    "disk_free_gb",
    "worker_utilization_pct",
    "active_workers",
    "total_workers",
    "active_bodies",
    "avg_runtime_s",
    "estimated_remaining_s",
    "avg_solve_speed_iter_per_s",
]


def _read_meminfo() -> dict[str, int]:
    info: dict[str, int] = {}
    try:
        with open("/proc/meminfo", encoding="utf-8") as handle:
            for line in handle:
                key, value = line.split(":", 1)
                info[key.strip()] = int(value.strip().split()[0])  # kB
    except OSError:
        pass
    return info



class _CpuSampler:
    """Track idle/total jiffies between samples for CPU percent."""

    def __init__(self) -> None:
        self._prev_total: int | None = None
        self._prev_idle: int | None = None

    def sample(self) -> float:
        try:
            with open("/proc/stat", encoding="utf-8") as handle:
                parts = handle.readline().split()
            if parts[0] != "cpu" or len(parts) < 5:
                return 0.0
            values = [int(x) for x in parts[1:]]
            total = sum(values)
            idle = values[3] + values[4]
            if self._prev_total is None:
                self._prev_total = total
                self._prev_idle = idle
                return 0.0
            total_delta = total - self._prev_total
            idle_delta = idle - self._prev_idle
            self._prev_total = total
            self._prev_idle = idle
            if total_delta <= 0:
                return 0.0
            return max(0.0, min(100.0, (1.0 - idle_delta / total_delta) * 100.0))
        except OSError:
            return 0.0


def sample_system_resources() -> dict[str, float]:
    """Read CPU, RAM, swap, and disk without external dependencies."""
    cpu_sampler = getattr(sample_system_resources, "_sampler", None)
    if cpu_sampler is None:
        cpu_sampler = _CpuSampler()
        sample_system_resources._sampler = cpu_sampler  # type: ignore[attr-defined]

    mem = _read_meminfo()
    ram_total_kb = mem.get("MemTotal", 0)
    ram_avail_kb = mem.get("MemAvailable", mem.get("MemFree", 0))
    ram_used_kb = max(0, ram_total_kb - ram_avail_kb)
    swap_total_kb = mem.get("SwapTotal", 0)
    swap_free_kb = mem.get("SwapFree", 0)
    swap_used_kb = max(0, swap_total_kb - swap_free_kb)

    disk_free_gb = 0.0
    try:
        usage = shutil.disk_usage(REPO_ROOT)
        disk_free_gb = usage.free / (1024**3)
    except OSError:
        pass

    return {
        "cpu_percent": round(cpu_sampler.sample(), 1),
        "ram_used_mb": round(ram_used_kb / 1024.0, 1),
        "ram_total_mb": round(ram_total_kb / 1024.0, 1),
        "ram_percent": round((ram_used_kb / ram_total_kb) * 100.0, 1) if ram_total_kb else 0.0,
        "swap_used_mb": round(swap_used_kb / 1024.0, 1),
        "swap_total_mb": round(swap_total_kb / 1024.0, 1),
        "disk_free_gb": round(disk_free_gb, 2),
    }


def build_health_snapshot(
    *,
    campaign_state: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Assemble a health snapshot from system probes and campaign state."""
    resources = sample_system_resources()
    state = campaign_state or {}
    snapshot = {
        "timestamp": utc_now_iso(),
        **resources,
        "worker_utilization_pct": state.get("worker_utilization_pct", 0.0),
        "active_workers": state.get("active_workers", 0),
        "total_workers": state.get("total_workers", 0),
        "active_bodies": state.get("active_bodies", []),
        "avg_runtime_s": state.get("avg_runtime_s"),
        "estimated_remaining_s": state.get("estimated_remaining_s"),
        "estimated_finish_at": state.get("estimated_finish_at"),
        "avg_solve_speed_iter_per_s": state.get("avg_solve_speed_iter_per_s"),
        "campaign_status": state.get("campaign_status"),
        "completed_bodies": state.get("completed_bodies"),
        "total_bodies": state.get("total_bodies", TOTAL_BODIES),
        "retried_bodies": state.get("retried_bodies", 0),
    }
    return snapshot


def write_health_files(
    snapshot: dict[str, Any],
    *,
    health_json_path: Path = HEALTH_JSON_PATH,
    history_csv_path: Path = HEALTH_HISTORY_CSV,
) -> None:
    """Write health.json and append one row to health_history.csv."""
    health_json_path.parent.mkdir(parents=True, exist_ok=True)
    health_json_path.write_text(json.dumps(snapshot, indent=2) + "\n", encoding="utf-8")

    write_header = not history_csv_path.exists() or history_csv_path.stat().st_size == 0
    with open(history_csv_path, "a", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=_CSV_HEADER, extrasaction="ignore")
        if write_header:
            writer.writeheader()
        row = {
            "timestamp": snapshot.get("timestamp"),
            "cpu_percent": snapshot.get("cpu_percent"),
            "ram_used_mb": snapshot.get("ram_used_mb"),
            "ram_total_mb": snapshot.get("ram_total_mb"),
            "ram_percent": snapshot.get("ram_percent"),
            "swap_used_mb": snapshot.get("swap_used_mb"),
            "swap_total_mb": snapshot.get("swap_total_mb"),
            "disk_free_gb": snapshot.get("disk_free_gb"),
            "worker_utilization_pct": snapshot.get("worker_utilization_pct"),
            "active_workers": snapshot.get("active_workers"),
            "total_workers": snapshot.get("total_workers"),
            "active_bodies": ",".join(snapshot.get("active_bodies") or []),
            "avg_runtime_s": snapshot.get("avg_runtime_s"),
            "estimated_remaining_s": snapshot.get("estimated_remaining_s"),
            "avg_solve_speed_iter_per_s": snapshot.get("avg_solve_speed_iter_per_s"),
        }
        writer.writerow(row)


StateProvider = Callable[[], dict[str, Any]]


class HealthMonitor:
    """Background thread that samples health at a fixed interval."""

    def __init__(
        self,
        *,
        interval_s: float = HEALTH_SAMPLE_INTERVAL_S,
        state_provider: StateProvider | None = None,
        health_json_path: Path = HEALTH_JSON_PATH,
        history_csv_path: Path = HEALTH_HISTORY_CSV,
        on_sample: Callable[[dict[str, Any]], None] | None = None,
    ) -> None:
        self.interval_s = interval_s
        self._state_provider = state_provider or (lambda: {})
        self._health_json_path = health_json_path
        self._history_csv_path = history_csv_path
        self._on_sample = on_sample
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None
        self._latest: dict[str, Any] | None = None
        self._lock = threading.Lock()

    @property
    def latest(self) -> dict[str, Any] | None:
        with self._lock:
            return dict(self._latest) if self._latest else None

    def sample_once(self) -> dict[str, Any]:
        campaign_state = self._state_provider()
        snapshot = build_health_snapshot(campaign_state=campaign_state)
        write_health_files(
            snapshot,
            health_json_path=self._health_json_path,
            history_csv_path=self._history_csv_path,
        )
        with self._lock:
            self._latest = snapshot
        if self._on_sample:
            self._on_sample(snapshot)
        return snapshot

    def start(self) -> None:
        if self._thread and self._thread.is_alive():
            return
        self._stop.clear()
        self._thread = threading.Thread(
            target=self._run_loop,
            name="campaign-health-monitor",
            daemon=True,
        )
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()
        if self._thread:
            self._thread.join(timeout=self.interval_s + 5)
            self._thread = None

    def _run_loop(self) -> None:
        # Prime CPU sampler so the first reading is meaningful on the second tick.
        sample_system_resources()
        while not self._stop.is_set():
            try:
                self.sample_once()
            except Exception:
                pass
            if self._stop.wait(self.interval_s):
                break
