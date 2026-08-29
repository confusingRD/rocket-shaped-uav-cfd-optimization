"""Persist campaign environment to SQLite and manifest."""

from __future__ import annotations

import json
import sqlite3
from pathlib import Path
from typing import Any

from campaign.constants import MANIFEST_PATH, MESH_LEVEL, SOLVER, TURBULENCE_MODEL
from campaign.environment import UNKNOWN, detect_environment, merge_environment
from campaign.manifest import load_manifest, save_manifest
from reporting.production_db import (
    fetch_campaign_environment,
    save_campaign_environment,
    utc_now_iso,
)


def capture_environment_snapshot(
    *,
    campaign_uuid: str,
    campaign_creation_time: str | None = None,
    campaign_start_time: str | None = None,
    campaign_end_time: str | None = None,
    workers: int | None = None,
    mpi_ranks_per_worker: int | None = None,
    existing: dict[str, Any] | None = None,
) -> dict[str, Any]:
    detected = detect_environment(
        campaign_uuid=campaign_uuid,
        campaign_creation_time=campaign_creation_time,
        campaign_start_time=campaign_start_time,
        campaign_end_time=campaign_end_time,
        workers=workers,
        mpi_ranks_per_worker=mpi_ranks_per_worker,
        mesh_level=MESH_LEVEL,
        solver=SOLVER,
        turbulence_model=TURBULENCE_MODEL,
    )
    return merge_environment(existing, detected)


def persist_campaign_environment(
    conn: sqlite3.Connection,
    manifest: dict[str, Any],
    environment: dict[str, Any],
    *,
    manifest_path: Path = MANIFEST_PATH,
) -> dict[str, Any]:
    """Write environment to DB and manifest."""
    save_campaign_environment(conn, environment)
    manifest["environment"] = environment
    save_manifest(manifest, manifest_path)
    return environment


def ensure_campaign_environment(
    conn: sqlite3.Connection,
    manifest: dict[str, Any],
    *,
    manifest_path: Path = MANIFEST_PATH,
    workers: int | None = None,
    mpi_ranks_per_worker: int | None = None,
    set_start_time: bool = False,
) -> dict[str, Any]:
    """Capture or refresh environment at campaign init / run start."""
    campaign_uuid = manifest["campaign_uuid"]
    existing = manifest.get("environment") or fetch_campaign_environment(conn, campaign_uuid)
    start_time = None
    if existing:
        start_time = existing.get("campaign", {}).get("campaign_start_time")
        if start_time == UNKNOWN:
            start_time = None
    if set_start_time and not start_time:
        start_time = utc_now_iso()
    environment = capture_environment_snapshot(
        campaign_uuid=campaign_uuid,
        campaign_creation_time=manifest.get("creation_date"),
        campaign_start_time=start_time,
        campaign_end_time=None,
        workers=workers,
        mpi_ranks_per_worker=mpi_ranks_per_worker,
        existing=existing,
    )
    if set_start_time:
        environment["campaign"]["campaign_start_time"] = start_time or utc_now_iso()
    return persist_campaign_environment(conn, manifest, environment, manifest_path=manifest_path)


def finalize_campaign_environment(
    conn: sqlite3.Connection,
    manifest: dict[str, Any],
    *,
    manifest_path: Path = MANIFEST_PATH,
) -> dict[str, Any]:
    """Record campaign end time and duration after DOE completion."""
    campaign_uuid = manifest["campaign_uuid"]
    existing = manifest.get("environment") or fetch_campaign_environment(conn, campaign_uuid)
    end_time = utc_now_iso()
    start_time = None
    if existing:
        start_time = existing.get("campaign", {}).get("campaign_start_time")
        if start_time == UNKNOWN:
            start_time = None
    campaign_section = existing.get("campaign", {}) if existing else {}
    environment = capture_environment_snapshot(
        campaign_uuid=campaign_uuid,
        campaign_creation_time=manifest.get("creation_date"),
        campaign_start_time=start_time,
        campaign_end_time=end_time,
        workers=campaign_section.get("workers"),
        mpi_ranks_per_worker=campaign_section.get("mpi_ranks_per_worker"),
        existing=existing,
    )
    environment["campaign"]["campaign_end_time"] = end_time
    return persist_campaign_environment(conn, manifest, environment, manifest_path=manifest_path)


def load_environment_for_report(
    conn: sqlite3.Connection,
    manifest: dict[str, Any],
) -> dict[str, Any]:
    """Load environment for reporting, falling back to manifest or detection."""
    if manifest.get("environment"):
        return manifest["environment"]
    stored = fetch_campaign_environment(conn, manifest.get("campaign_uuid", ""))
    if stored:
        return stored
    return detect_environment(
        campaign_uuid=manifest.get("campaign_uuid"),
        campaign_creation_time=manifest.get("creation_date"),
        workers=manifest.get("workers"),
        mpi_ranks_per_worker=manifest.get("cores_per_worker"),
    )
