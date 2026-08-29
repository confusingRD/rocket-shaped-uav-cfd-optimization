"""Automatic campaign resume — continue only unfinished bodies."""

from __future__ import annotations

import re
import sqlite3
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from campaign.constants import DEFAULT_DB_PATH, MANIFEST_PATH, PROFILES_ROOT, RESULTS_ROOT, TOTAL_BODIES
from campaign.eta import compute_eta
from campaign.manifest import load_manifest, save_manifest, sync_manifest_from_db, update_eta
from campaign.recovery import classify_body_for_resume, detect_interrupted_bodies
from campaign.retry import sync_retry_to_manifest
from reporting.production_db import (
    BODY_ID_PATTERN,
    count_by_status,
    fetch_campaign_rows,
    prepare_database,
    utc_now_iso,
)


@dataclass(frozen=True)
class ResumePlan:
    campaign_uuid: str
    total_bodies: int
    completed: list[str]
    pending: list[str]
    failed: list[str]
    interrupted: list[str]
    skipped: list[str]
    to_run: list[str]
    to_restart: list[str]
    to_skip: list[str]


def list_campaign_bodies(
    profiles_root: Path = PROFILES_ROOT,
    *,
    profile_glob: str = "Body_*/metadata.json",
    id_pattern: re.Pattern[str] | None = None,
) -> list[str]:
    pattern = id_pattern or BODY_ID_PATTERN
    bodies = []
    for meta in sorted(profiles_root.glob(profile_glob)):
        body_id = meta.parent.name
        if pattern.match(body_id):
            bodies.append(body_id)
    return bodies


def build_resume_plan(
    conn: sqlite3.Connection,
    *,
    profiles_root: Path = PROFILES_ROOT,
    results_root: Path = RESULTS_ROOT,
    manifest_path: Path = MANIFEST_PATH,
    profile_glob: str = "Body_*/metadata.json",
    id_pattern: re.Pattern[str] | None = None,
    run_state_glob: str = "Body_*/run_state.json",
    force_bodies: set[str] | None = None,
    retry_failed: bool = False,
) -> ResumePlan:
    """Determine which bodies to run, restart, or skip."""
    manifest = load_manifest(manifest_path)
    campaign_uuid = manifest["campaign_uuid"]
    bodies = list_campaign_bodies(
        profiles_root,
        profile_glob=profile_glob,
        id_pattern=id_pattern,
    )

    status_map: dict[str, str] = {}
    for row in conn.execute("SELECT sample_id, status FROM master_samples"):
        status_map[row["sample_id"]] = row["status"]

    completed: list[str] = []
    pending: list[str] = []
    failed: list[str] = []
    interrupted: list[str] = []
    skipped: list[str] = []
    to_run: list[str] = []
    to_restart: list[str] = []
    to_skip: list[str] = []

    detected = set(detect_interrupted_bodies(results_root, run_state_glob=run_state_glob))

    for body_id in bodies:
        db_status = status_map.get(body_id, "PENDING")
        if body_id in detected and db_status != "COMPLETED":
            db_status = "INTERRUPTED"

        if force_bodies and body_id in force_bodies:
            action = "restart"
        else:
            action = classify_body_for_resume(
                body_id,
                db_status=db_status,
                results_root=results_root,
                retry_failed=retry_failed,
            )

        if action == "skip":
            to_skip.append(body_id)
            if db_status == "COMPLETED":
                completed.append(body_id)
            elif db_status == "SKIPPED":
                skipped.append(body_id)
            elif db_status == "FAILED":
                failed.append(body_id)
            else:
                completed.append(body_id)
        elif action == "restart":
            to_restart.append(body_id)

            if body_id in detected or db_status == "INTERRUPTED":
                interrupted.append(body_id)
        else:
            to_run.append(body_id)
            pending.append(body_id)

    return ResumePlan(
        campaign_uuid=campaign_uuid,
        total_bodies=len(bodies) or TOTAL_BODIES,
        completed=sorted(completed),
        pending=sorted(pending),
        failed=sorted(failed),
        interrupted=sorted(interrupted),
        skipped=sorted(skipped),
        to_run=sorted(to_run),
        to_restart=sorted(to_restart),
        to_skip=sorted(to_skip),
    )


def refresh_campaign_state(
    db_path: Path = DEFAULT_DB_PATH,
    *,
    manifest_path: Path = MANIFEST_PATH,
    profiles_root: Path = PROFILES_ROOT,
    results_root: Path = RESULTS_ROOT,
    profile_glob: str = "Body_*/metadata.json",
    summary_glob: str = "Body_*/summary.json",
    id_pattern: re.Pattern[str] | None = None,
    cases_root: Path | None = None,
    run_state_glob: str = "Body_*/run_state.json",
    force_bodies: set[str] | None = None,
    retry_failed: bool = False,
    workers: int = 1,
) -> ResumePlan:
    """Read manifest + database and return a resume plan without rerunning completed cases."""
    conn = prepare_database(
        db_path,
        sync_results_flag=True,
        profiles_root=profiles_root,
        results_root=results_root,
        profile_glob=profile_glob,
        summary_glob=summary_glob,
        id_pattern=id_pattern,
        cases_root=cases_root,
    )
    try:
        plan = build_resume_plan(
            conn,
            profiles_root=profiles_root,
            results_root=results_root,
            manifest_path=manifest_path,
            profile_glob=profile_glob,
            id_pattern=id_pattern,
            run_state_glob=run_state_glob,
            force_bodies=force_bodies,
            retry_failed=retry_failed,
        )
        counts = count_by_status(conn)
        manifest = load_manifest(manifest_path)
        sync_manifest_from_db(manifest, counts)
        sync_retry_to_manifest(manifest, conn)
        rows = fetch_campaign_rows(conn)
        eta = compute_eta(
            rows,
            total_bodies=manifest.get("total_bodies", TOTAL_BODIES),
            workers=workers,
        )
        update_eta(manifest, eta)
        save_manifest(manifest, manifest_path)
        return plan
    finally:
        conn.close()


def resume_plan_summary(plan: ResumePlan) -> dict[str, Any]:
    return {
        "campaign_uuid": plan.campaign_uuid,
        "total_bodies": plan.total_bodies,
        "completed": len(plan.completed),
        "pending": len(plan.pending),
        "failed": len(plan.failed),
        "interrupted": len(plan.interrupted),
        "skipped": len(plan.skipped),
        "to_run": plan.to_run,
        "to_restart": plan.to_restart,
        "to_skip_count": len(plan.to_skip),
        "next_body": (plan.to_restart + plan.to_run)[0] if (plan.to_restart + plan.to_run) else None,
        "generated_at": utc_now_iso(),
    }
