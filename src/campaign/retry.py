"""Persistent retry accounting for production campaign bodies."""

from __future__ import annotations

import json
import sqlite3
from pathlib import Path
from typing import Any

from campaign.constants import RESULTS_ROOT
from reporting.production_db import utc_now_iso


def _retry_row(conn: sqlite3.Connection, sample_id: str) -> sqlite3.Row | None:
    return conn.execute(
        """
        SELECT retry_count, first_attempt_time, last_attempt_time,
               last_failure_reason, last_exit_code
        FROM master_samples WHERE sample_id = ?
        """,
        (sample_id,),
    ).fetchone()


def get_retry_info(conn: sqlite3.Connection, sample_id: str) -> dict[str, Any]:
    """Return retry accounting fields for one body."""
    row = _retry_row(conn, sample_id)
    if row is None:
        return {
            "retry_count": 0,
            "first_attempt_time": None,
            "last_attempt_time": None,
            "last_failure_reason": None,
            "last_exit_code": None,
        }
    return {
        "retry_count": int(row["retry_count"] or 0),
        "first_attempt_time": row["first_attempt_time"],
        "last_attempt_time": row["last_attempt_time"],
        "last_failure_reason": row["last_failure_reason"],
        "last_exit_code": row["last_exit_code"],
    }


def record_attempt_start(
    conn: sqlite3.Connection,
    sample_id: str,
    *,
    is_retry: bool,
) -> dict[str, Any]:
    """Record the start of a body attempt; increment retry_count on re-runs."""
    now = utc_now_iso()
    info = get_retry_info(conn, sample_id)
    retry_count = info["retry_count"]
    if is_retry:
        retry_count += 1
    first_attempt = info["first_attempt_time"] or now
    conn.execute(
        """
        UPDATE master_samples SET
            retry_count = ?,
            first_attempt_time = ?,
            last_attempt_time = ?
        WHERE sample_id = ?
        """,
        (retry_count, first_attempt, now, sample_id),
    )
    conn.commit()
    return get_retry_info(conn, sample_id)


def record_attempt_failure(
    conn: sqlite3.Connection,
    sample_id: str,
    *,
    reason: str,
    exit_code: int | None,
) -> dict[str, Any]:
    """Persist last failure metadata without changing retry_count."""
    now = utc_now_iso()
    conn.execute(
        """
        UPDATE master_samples SET
            last_failure_reason = ?,
            last_exit_code = ?,
            last_attempt_time = COALESCE(last_attempt_time, ?)
        WHERE sample_id = ?
        """,
        (reason, exit_code, now, sample_id),
    )
    conn.commit()
    return get_retry_info(conn, sample_id)


def merge_retry_into_summary(summary: dict[str, Any], retry_info: dict[str, Any]) -> dict[str, Any]:
    """Attach retry accounting fields to a summary dict."""
    summary.update(retry_info)
    return summary


def write_retry_to_summary(
    body_id: str,
    retry_info: dict[str, Any],
    *,
    results_root: Path = RESULTS_ROOT,
) -> None:
    """Update summary.json with retry fields (creates minimal file if missing)."""
    summary_path = results_root / body_id / "summary.json"
    if summary_path.exists():
        summary = json.loads(summary_path.read_text(encoding="utf-8"))
    else:
        summary = {"body_id": body_id}
    merge_retry_into_summary(summary, retry_info)
    summary_path.parent.mkdir(parents=True, exist_ok=True)
    summary_path.write_text(json.dumps(summary, indent=2) + "\n", encoding="utf-8")


def aggregate_retry_statistics(conn: sqlite3.Connection) -> dict[str, Any]:
    """Campaign-level retry statistics for manifest and checkpoints."""
    row = conn.execute(
        """
        SELECT
            COALESCE(SUM(retry_count), 0) AS total_retries,
            COALESCE(SUM(CASE WHEN retry_count > 0 THEN 1 ELSE 0 END), 0) AS bodies_retried,
            COALESCE(MAX(retry_count), 0) AS max_retry_count,
            COALESCE(SUM(CASE WHEN last_failure_reason IS NOT NULL THEN 1 ELSE 0 END), 0)
                AS bodies_with_failures
        FROM master_samples
        """
    ).fetchone()
    per_body: list[dict[str, Any]] = []
    for r in conn.execute(
        """
        SELECT sample_id, retry_count, first_attempt_time, last_attempt_time,
               last_failure_reason, last_exit_code
        FROM master_samples
        WHERE retry_count > 0 OR last_failure_reason IS NOT NULL
        ORDER BY sample_id
        """
    ):
        per_body.append(
            {
                "sample_id": r["sample_id"],
                "retry_count": int(r["retry_count"] or 0),
                "first_attempt_time": r["first_attempt_time"],
                "last_attempt_time": r["last_attempt_time"],
                "last_failure_reason": r["last_failure_reason"],
                "last_exit_code": r["last_exit_code"],
            }
        )
    return {
        "total_retries": int(row["total_retries"] or 0),
        "bodies_retried": int(row["bodies_retried"] or 0),
        "max_retry_count": int(row["max_retry_count"] or 0),
        "bodies_with_failures": int(row["bodies_with_failures"] or 0),
        "bodies": per_body,
        "generated_at": utc_now_iso(),
    }


def sync_retry_from_summary(
    conn: sqlite3.Connection,
    sample_id: str,
    summary: dict[str, Any],
) -> None:
    """Ingest retry fields from summary.json during results sync."""
    keys = (
        "retry_count",
        "first_attempt_time",
        "last_attempt_time",
        "last_failure_reason",
        "last_exit_code",
    )
    if not any(k in summary for k in keys):
        return
    conn.execute(
        """
        UPDATE master_samples SET
            retry_count = COALESCE(?, retry_count),
            first_attempt_time = COALESCE(?, first_attempt_time),
            last_attempt_time = COALESCE(?, last_attempt_time),
            last_failure_reason = COALESCE(?, last_failure_reason),
            last_exit_code = COALESCE(?, last_exit_code)
        WHERE sample_id = ?
        """,
        (
            summary.get("retry_count"),
            summary.get("first_attempt_time"),
            summary.get("last_attempt_time"),
            summary.get("last_failure_reason"),
            summary.get("last_exit_code"),
            sample_id,
        ),
    )
    conn.commit()


def sync_retry_to_manifest(manifest: dict[str, Any], conn: sqlite3.Connection) -> dict[str, Any]:
    """Refresh manifest retry counters from the database."""
    stats = aggregate_retry_statistics(conn)
    manifest["retried_bodies"] = stats["bodies_retried"]
    manifest["retry_statistics"] = {
        "total_retries": stats["total_retries"],
        "bodies_retried": stats["bodies_retried"],
        "max_retry_count": stats["max_retry_count"],
        "bodies_with_failures": stats["bodies_with_failures"],
    }
    return manifest
