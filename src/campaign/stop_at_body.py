"""External milestone stop — commit detection and post-stop finalization.

Used when time priority requires stopping a live campaign immediately after a
target body is fully persisted, accepting interruption of in-flight siblings.
No runtime injection; the monitor sends SIGINT to the campaign process.
"""

from __future__ import annotations

import json
import signal
import sqlite3
import time
from pathlib import Path
from typing import Any

from campaign.backup import backup_database
from campaign.constants import (
    CHECKPOINTS_DIR,
    DEFAULT_DB_PATH,
    MANIFEST_PATH,
    TOTAL_BODIES,
)
from campaign.control import body_completed_successfully
from campaign.manifest import load_manifest, save_manifest, set_campaign_status, sync_manifest_from_db
from campaign.recovery import mark_interrupted
from campaign.scheduler import build_graceful_shutdown_summary, print_graceful_shutdown_summary
from reporting.production_db import count_by_status, prepare_database, utc_now_iso


def _body_number(body_id: str) -> int | None:
    if not body_id.startswith("Body_"):
        return None
    try:
        return int(body_id.split("_", 1)[1])
    except (IndexError, ValueError):
        return None


def latest_checkpoint_completed_count(checkpoints_dir: Path = CHECKPOINTS_DIR) -> int | None:
    """Return completed_count from the latest checkpoint snapshot, if present."""
    latest_status = checkpoints_dir / "latest" / "campaign_status.json"
    if latest_status.exists():
        try:
            payload = json.loads(latest_status.read_text(encoding="utf-8"))
            return int(payload.get("completed_count", 0))
        except (json.JSONDecodeError, OSError, TypeError, ValueError):
            pass

    major = checkpoints_dir / "checkpoint_major_100" / "campaign_status.json"
    if major.exists():
        try:
            payload = json.loads(major.read_text(encoding="utf-8"))
            return int(payload.get("completed_count", 0))
        except (json.JSONDecodeError, OSError, TypeError, ValueError):
            pass
    return None


def body_fully_committed(
    conn: sqlite3.Connection,
    body_id: str,
    *,
    stop_after_completed: int,
    checkpoints_dir: Path = CHECKPOINTS_DIR,
) -> bool:
    """True when target body outputs and milestone checkpointing are complete."""
    if not body_completed_successfully(body_id):
        return False

    row = conn.execute(
        "SELECT status FROM master_samples WHERE sample_id = ?",
        (body_id,),
    ).fetchone()
    if row is None or row[0] != "COMPLETED":
        return False

    counts = count_by_status(conn)
    if counts.get("COMPLETED", 0) < stop_after_completed:
        return False

    checkpoint_count = latest_checkpoint_completed_count(checkpoints_dir)
    if checkpoint_count is None or checkpoint_count < stop_after_completed:
        return False

    return True


def campaign_running(pid: int) -> bool:
    try:
        import os

        os.kill(pid, 0)
        return True
    except OSError:
        return False


def resolve_campaign_python_pid(pid: int) -> int:
    """Return run_campaign.py PID (pid file may reference a shell wrapper)."""
    import os

    try:
        with open(f"/proc/{pid}/cmdline", "rb") as handle:
            cmdline = handle.read().decode("utf-8", errors="ignore")
    except OSError:
        return pid
    if "run_campaign.py" in cmdline:
        return pid

    best_pid = pid
    best_start = -1.0
    for entry in Path("/proc").iterdir():
        if not entry.name.isdigit():
            continue
        candidate = int(entry.name)
        try:
            with open(entry / "stat", "r", encoding="utf-8") as handle:
                stat = handle.read().split()
            if int(stat[3]) != pid:
                continue
            with open(entry / "cmdline", "rb") as handle:
                cmd = handle.read().decode("utf-8", errors="ignore")
            if "run_campaign.py" not in cmd:
                continue
            start = float(stat[21])
            if start >= best_start:
                best_start = start
                best_pid = candidate
        except OSError:
            continue
    return best_pid


def request_campaign_stop(pid: int, sig: signal.Signals = signal.SIGINT) -> None:
    import os

    os.kill(pid, sig.value)


def wait_for_campaign_exit(pid: int, *, timeout_s: float = 120.0, poll_interval_s: float = 0.5) -> bool:
    deadline = time.time() + timeout_s
    while time.time() < deadline:
        if not campaign_running(pid):
            return True
        time.sleep(poll_interval_s)
    return not campaign_running(pid)


def finalize_after_hard_stop(
    conn: sqlite3.Connection,
    *,
    target_body: str,
    resume_from_body: str,
    db_path: Path = DEFAULT_DB_PATH,
    manifest_path: Path = MANIFEST_PATH,
    stop_reason: str = "Hard stop at milestone after target body committed",
) -> dict[str, Any]:
    """Ensure DB/manifest consistency after SIGINT shutdown."""
    running_rows = conn.execute(
        "SELECT sample_id FROM master_samples WHERE status = 'RUNNING'"
    ).fetchall()
    for (body_id,) in running_rows:
        if body_id == target_body:
            continue
        mark_interrupted(body_id, reason=stop_reason)
        conn.execute(
            "UPDATE master_samples SET status = 'INTERRUPTED' WHERE sample_id = ?",
            (body_id,),
        )

    conn.commit()

    counts = count_by_status(conn)
    manifest = load_manifest(manifest_path)
    sync_manifest_from_db(manifest, counts)
    if counts.get("INTERRUPTED", 0) > 0:
        set_campaign_status(manifest, "INTERRUPTED")
    elif counts.get("COMPLETED", 0) >= manifest.get("total_bodies", TOTAL_BODIES):
        set_campaign_status(manifest, "COMPLETED")
    else:
        set_campaign_status(manifest, "PAUSED")
    manifest["hard_stop"] = {
        "reason": stop_reason,
        "stopped_at": utc_now_iso(),
        "target_body": target_body,
        "resume_from_body": resume_from_body,
        "completed_bodies": counts.get("COMPLETED", 0),
    }
    save_manifest(manifest, manifest_path)

    try:
        backup_database(db_path)
    except OSError:
        pass

    summary = build_shutdown_summary(
        conn,
        target_body=target_body,
        resume_from_body=resume_from_body,
        manifest_path=manifest_path,
    )
    return summary


def build_shutdown_summary(
    conn: sqlite3.Connection,
    *,
    target_body: str,
    resume_from_body: str,
    manifest_path: Path = MANIFEST_PATH,
) -> dict[str, Any]:
    counts = count_by_status(conn)
    manifest = load_manifest(manifest_path)
    summary = build_graceful_shutdown_summary(
        counts,
        total_bodies=manifest.get("total_bodies", TOTAL_BODIES),
    )

    interrupted_rows = conn.execute(
        "SELECT sample_id FROM master_samples WHERE status = 'INTERRUPTED' ORDER BY sample_id"
    ).fetchall()
    failed_rows = conn.execute(
        "SELECT sample_id FROM master_samples WHERE status = 'FAILED' ORDER BY sample_id"
    ).fetchall()
    resume_row = conn.execute(
        "SELECT status FROM master_samples WHERE sample_id = ?",
        (resume_from_body,),
    ).fetchone()

    summary["target_body"] = target_body
    summary["interrupted_bodies_list"] = [row[0] for row in interrupted_rows]
    summary["failed_bodies_list"] = [row[0] for row in failed_rows]
    summary["resume_from_body"] = resume_from_body
    summary["resume_body_status"] = resume_row[0] if resume_row else "MISSING"
    summary["target_body_committed"] = body_completed_successfully(target_body)
    summary["can_resume"] = summary["target_body_committed"] and (
        summary["resume_body_status"] in ("PENDING", "INTERRUPTED", "FAILED")
    )
    summary["campaign_status"] = manifest.get("campaign_status", summary["campaign_status"])
    return summary


def print_hard_stop_summary(summary: dict[str, Any]) -> None:
    print_graceful_shutdown_summary(summary)
    interrupted = summary.get("interrupted_bodies_list") or []
    failed = summary.get("failed_bodies_list") or []
    interrupted_text = ", ".join(interrupted) if interrupted else "none"
    failed_text = ", ".join(failed) if failed else "none"
    resume_from = summary.get("resume_from_body", "Body_0101")
    resume_status = summary.get("resume_body_status", "unknown")
    can_resume = summary.get("can_resume", False)
    resume_hint = (
        f"Resume with: python3 scripts/run_campaign.py resume --workers 2 --cores-per-worker 6 --retry-failed"
        if can_resume
        else "Manual review required before resume."
    )
    print(
        "[hard stop summary]\n"
        f"Target body committed: {summary.get('target_body')} "
        f"({'yes' if summary.get('target_body_committed') else 'NO'})\n"
        f"Interrupted bodies: {interrupted_text}\n"
        f"Failed bodies: {failed_text}\n"
        f"Next body on resume: {resume_from} (status={resume_status})\n"
        f"{resume_hint}\n"
        "-----------------------",
        flush=True,
    )
