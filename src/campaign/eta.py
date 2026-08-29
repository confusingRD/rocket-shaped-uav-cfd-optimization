"""ETA prediction from completed simulation runtimes."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Any

from reporting.production_db import CampaignRow


def compute_eta(
    rows: list[CampaignRow],
    *,
    total_bodies: int,
    window: int = 10,
    workers: int = 1,
) -> dict[str, Any]:
    """Estimate remaining campaign runtime from completed bodies."""

    completed = [
        r
        for r in rows
        if r.status == "COMPLETED"
        and (
            r.wall_clock_s is not None
            or r.execution_time_s is not None
        )
    ]

    completed.sort(key=lambda r: r.completed_at or "")

    runtimes = [
        float(
            r.wall_clock_s
            if r.wall_clock_s is not None
            else r.execution_time_s
        )
        for r in completed
    ]

    pending = sum(
        1
        for r in rows
        if r.status in ("PENDING", "INTERRUPTED", "RUNNING")
    )

    unregistered = max(total_bodies - len(rows), 0)
    remaining = pending + unregistered

    if not runtimes:
        return {
            "average_runtime_s": None,
            "moving_average_runtime_s": None,
            "remaining_bodies": remaining,
            "estimated_remaining_s": None,
            "estimated_finish_at": None,
            "completed_for_eta": 0,
        }

    average = sum(runtimes) / len(runtimes)

    recent = (
        runtimes[-window:]
        if len(runtimes) >= window
        else runtimes
    )
    moving_avg = sum(recent) / len(recent)

    worker_count = max(int(workers), 1)

    estimated_remaining_s = (
        moving_avg * remaining
    ) / worker_count

    finish_at = (
        datetime.now(timezone.utc)
        + timedelta(seconds=estimated_remaining_s)
    ).replace(microsecond=0).isoformat()

    return {
        "average_runtime_s": round(average, 1),
        "moving_average_runtime_s": round(moving_avg, 1),
        "remaining_bodies": remaining,
        "estimated_remaining_s": round(estimated_remaining_s, 1),
        "estimated_finish_at": finish_at,
        "completed_for_eta": len(runtimes),
        "workers": worker_count,
    }
