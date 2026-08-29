"""Automatic checkpoint system for long-running DOE campaigns."""

from __future__ import annotations

import json
import re
import shutil
from pathlib import Path
from typing import Any

from campaign.backup import backup_database
from campaign.constants import (
    CHECKPOINTS_DIR,
    DEFAULT_DB_PATH,
    MAJOR_CHECKPOINT_MILESTONES,
    MANIFEST_PATH,
    PROGRESS_REPORTS_DIR,
    TOTAL_BODIES,
)
from campaign.eta import compute_eta
from campaign.health import HEALTH_HISTORY_CSV, HEALTH_JSON_PATH
from campaign.manifest import load_manifest, save_manifest, sync_manifest_from_db, update_eta
from campaign.progress_report import generate_progress_report
from campaign.retry import aggregate_retry_statistics
from reporting.analysis import analyze_campaign
from reporting.figures import generate_all_figures
from reporting.production_db import (
    connect,
    count_by_status,
    fetch_campaign_rows,
    prepare_database,
    utc_now_iso,
)


def checkpoint_name(completed_count: int, *, major: bool = False) -> str:
    if major:
        return f"checkpoint_major_{completed_count:03d}"
    return f"checkpoint_{completed_count:04d}"


def is_major_milestone(completed_count: int) -> bool:
    return completed_count in MAJOR_CHECKPOINT_MILESTONES


def create_checkpoint(
    completed_count: int,
    *,
    db_path: Path = DEFAULT_DB_PATH,
    manifest_path: Path = MANIFEST_PATH,
    workers: int = 1,
    checkpoints_dir: Path = CHECKPOINTS_DIR,
    progress_reports_dir: Path = PROGRESS_REPORTS_DIR,
    monitor: Any | None = None,
    results_root: Path | None = None,
    profiles_root: Path | None = None,
    profile_glob: str = "Body_*/metadata.json",
    summary_glob: str = "Body_*/summary.json",
    id_pattern: re.Pattern[str] | None = None,
    cases_root: Path | None = None,
    health_json_path: Path = HEALTH_JSON_PATH,
    history_csv_path: Path = HEALTH_HISTORY_CSV,
    backup_prefix: str = "production",
    data_dir: Path | None = None,
) -> Path:
    """Create a full campaign checkpoint after a body completes."""
    major = is_major_milestone(completed_count)
    name = checkpoint_name(completed_count, major=major)
    dest = checkpoints_dir / name
    if dest.exists():
        shutil.rmtree(dest)
    dest.mkdir(parents=True)

    _results = results_root if results_root is not None else None
    conn = prepare_database(
        db_path,
        sync_results_flag=True,
        import_profiles_flag=False,
        profiles_root=profiles_root,
        results_root=_results,
        profile_glob=profile_glob,
        summary_glob=summary_glob,
        id_pattern=id_pattern,
        cases_root=cases_root,
    )
    try:
        counts = count_by_status(conn)
        rows = fetch_campaign_rows(conn)
        analysis = analyze_campaign(rows)

        manifest = load_manifest(manifest_path)

        eta = compute_eta(
            rows,
            total_bodies=manifest.get("total_bodies", TOTAL_BODIES),
            workers=workers,
        )

        sync_manifest_from_db(manifest, counts)
        update_eta(manifest, eta)
        save_manifest(manifest, manifest_path)

        retry_stats = aggregate_retry_statistics(conn)

        status_payload = {
            "checkpoint_name": name,
            "created_at": utc_now_iso(),
            "completed_count": completed_count,
            "major": major,
            "campaign_status": manifest["campaign_status"],
            "counts": counts,
            "remaining_bodies": manifest["remaining_bodies"],
            "eta": eta,
            "retry_statistics": {
                "total_retries": retry_stats["total_retries"],
                "bodies_retried": retry_stats["bodies_retried"],
                "max_retry_count": retry_stats["max_retry_count"],
                "bodies_with_failures": retry_stats["bodies_with_failures"],
            },
        }
        if monitor is not None and monitor.health.latest:
            status_payload["health"] = monitor.health.latest
        (dest / "campaign_status.json").write_text(
            json.dumps(status_payload, indent=2) + "\n", encoding="utf-8"
        )

        rankings = {
            "top_10": analysis["top_10"],
            "worst_20": analysis["worst_20"],
            "best_body": analysis["best_body"].sample_id if analysis["best_body"] else None,
            "worst_body": analysis["ranked_completed"][-1].sample_id
            if analysis["ranked_completed"]
            else None,
        }
        (dest / "rankings.json").write_text(json.dumps(rankings, indent=2, default=str) + "\n")

        stats = {
            "counts": analysis["counts"],
            "cd_stats": _serialize_stats(analysis["cd_stats"]),
            "runtime_stats": _serialize_stats(analysis["runtime_stats"]),
            "cost": analysis["cost"],
            "retry_statistics": retry_stats,
        }
        (dest / "statistics.json").write_text(json.dumps(stats, indent=2, default=str) + "\n")

        (dest / "retry_statistics.json").write_text(
            json.dumps(retry_stats, indent=2) + "\n", encoding="utf-8"
        )

        if health_json_path.exists():
            shutil.copy2(health_json_path, dest / "health.json")
        if history_csv_path.exists():
            shutil.copy2(history_csv_path, dest / "health_history.csv")

        dashboard_snapshot = ""
        if monitor is not None:
            dashboard_snapshot = monitor.dashboard.last_snapshot
        if dashboard_snapshot:
            (dest / "dashboard_snapshot.txt").write_text(dashboard_snapshot, encoding="utf-8")

        shutil.copy2(manifest_path, dest / "campaign_manifest.json")

        db_backup = backup_database(
            db_path,
            data_dir=data_dir or db_path.parent,
            backup_prefix=backup_prefix,
        )
        if db_backup:
            shutil.copy2(db_backup, dest / db_backup.name)

        figures_dir = dest / "figures"
        figures = generate_all_figures(rows, analysis, figures_dir)

        if major:
            report_path = progress_reports_dir / f"progress_{completed_count:03d}.pdf"
            generate_progress_report(
                rows,
                analysis,
                figures,
                manifest,
                eta,
                report_path,
                completed_count=completed_count,
            )
            shutil.copy2(report_path, dest / report_path.name)

        partial_md = dest / "partial_report.md"
        partial_md.write_text(
            _partial_report_text(manifest, analysis, eta, completed_count),
            encoding="utf-8",
        )

        latest = checkpoints_dir / "latest"
        if latest.exists() or latest.is_symlink():
            latest.unlink()
        latest.symlink_to(dest.name)

        return dest
    finally:
        conn.close()


def _serialize_stats(stats: Any) -> dict[str, Any] | None:
    if stats is None:
        return None
    return {
        "mean": stats.mean,
        "median": stats.median,
        "minimum": stats.minimum,
        "maximum": stats.maximum,
        "std": stats.std,
        "cv": stats.cv,
        "count": stats.count,
    }


def _partial_report_text(
    manifest: dict[str, Any],
    analysis: dict[str, Any],
    eta: dict[str, Any],
    completed_count: int,
) -> str:
    best = analysis["best_body"]
    worst = analysis["ranked_completed"][-1] if analysis["ranked_completed"] else None
    lines = [
        "# Intermediate Campaign Report",
        "",
        f"**INTERMEDIATE RESULTS** — checkpoint at {completed_count} completed bodies",
        "",
        f"Campaign: {manifest.get('campaign_name')} ({manifest.get('campaign_uuid')})",
        f"Status: {manifest.get('campaign_status')}",
        f"Completed: {manifest.get('completed_bodies')} / {manifest.get('total_bodies')}",
        "",
        "## Rankings",
        f"Best body: {best.sample_id if best else 'N/A'} (Cd={best.cd:.6e})" if best else "Best body: N/A",
        f"Worst body: {worst.sample_id if worst else 'N/A'} (Cd={worst.cd:.6e})"
        if worst and worst.cd is not None
        else "Worst body: N/A",
        "",
        "## ETA",
        f"Moving average runtime: {eta.get('moving_average_runtime_s')} s/body",
        f"Estimated finish: {eta.get('estimated_finish_at')}",
        "",
        "See checkpoint figures/ and progress PDF for details.",
    ]
    return "\n".join(lines) + "\n"
