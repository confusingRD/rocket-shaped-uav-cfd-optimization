"""Campaign manifest — persistent DOE campaign identity and live status."""

from __future__ import annotations

import json
import platform
import socket
import subprocess
import sys
import uuid
from pathlib import Path
from typing import Any

from campaign.constants import (
    CAMPAIGN_STATES,
    GEOMETRY_VERSION,
    LHS_BATCH,
    MANIFEST_PATH,
    MESH_LEVEL,
    MESH_VERSION,
    REPO_ROOT,
    SOLVER,
    TOTAL_BODIES,
    TURBULENCE_MODEL,
)
from reporting.production_db import get_git_commit, get_openfoam_version, utc_now_iso


def _git_branch(root: Path) -> str | None:
    try:
        out = subprocess.run(
            ["git", "-C", str(root), "rev-parse", "--abbrev-ref", "HEAD"],
            capture_output=True,
            text=True,
            check=True,
            timeout=5,
        )
        return out.stdout.strip() or None
    except (subprocess.SubprocessError, FileNotFoundError):
        return None


def _git_dirty(root: Path) -> bool | None:
    try:
        out = subprocess.run(
            ["git", "-C", str(root), "status", "--porcelain"],
            capture_output=True,
            text=True,
            check=True,
            timeout=5,
        )
        return bool(out.stdout.strip())
    except (subprocess.SubprocessError, FileNotFoundError):
        return None


def build_provenance(root: Path | None = None) -> dict[str, Any]:
    root = root or REPO_ROOT
    return {
        "git_commit": get_git_commit(root),
        "git_branch": _git_branch(root),
        "git_dirty": _git_dirty(root),
        "openfoam_version": get_openfoam_version(),
        "python_version": sys.version.split()[0],
        "os": platform.platform(),
        "hostname": socket.gethostname(),
    }


def default_manifest(
    *,
    campaign_name: str = "Rocket Drone 200-Body DOE",
    total_bodies: int = TOTAL_BODIES,
    campaign_uuid: str | None = None,
) -> dict[str, Any]:
    """Build a new manifest dict with provenance and zeroed counters."""
    provenance = build_provenance()
    return {
        "campaign_uuid": campaign_uuid or str(uuid.uuid4()),
        "campaign_name": campaign_name,
        "creation_date": utc_now_iso(),
        "updated_at": utc_now_iso(),
        **provenance,
        "geometry_version": GEOMETRY_VERSION,
        "mesh_version": MESH_VERSION,
        "mesh_level": MESH_LEVEL,
        "turbulence_model": TURBULENCE_MODEL,
        "solver": SOLVER,
        "lhs_batch": LHS_BATCH,
        "total_bodies": total_bodies,
        "completed_bodies": 0,
        "failed_bodies": 0,
        "skipped_bodies": 0,
        "remaining_bodies": total_bodies,
        "interrupted_bodies": 0,
        "retried_bodies": 0,
        "retry_statistics": {
            "total_retries": 0,
            "bodies_retried": 0,
            "max_retry_count": 0,
            "bodies_with_failures": 0,
        },
        "campaign_status": "READY",
        "eta": {
            "average_runtime_s": None,
            "moving_average_runtime_s": None,
            "remaining_bodies": total_bodies,
            "estimated_remaining_s": None,
            "estimated_finish_at": None,
            "completed_for_eta": 0,
        },
    }


def load_manifest(path: Path = MANIFEST_PATH) -> dict[str, Any]:
    if not path.exists():
        raise FileNotFoundError(f"Campaign manifest not found: {path}")
    return json.loads(path.read_text(encoding="utf-8"))


def save_manifest(manifest: dict[str, Any], path: Path = MANIFEST_PATH) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    manifest["updated_at"] = utc_now_iso()
    path.write_text(json.dumps(manifest, indent=2, sort_keys=False) + "\n", encoding="utf-8")
    return path


def init_manifest(
    *,
    campaign_name: str = "Rocket Drone 200-Body DOE",
    total_bodies: int = TOTAL_BODIES,
    path: Path = MANIFEST_PATH,
    overwrite: bool = False,
) -> dict[str, Any]:
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.exists() and not overwrite:
        return load_manifest(path)
    manifest = default_manifest(campaign_name=campaign_name, total_bodies=total_bodies)
    save_manifest(manifest, path)
    return manifest


def set_campaign_status(manifest: dict[str, Any], status: str) -> None:
    if status not in CAMPAIGN_STATES:
        raise ValueError(f"Invalid campaign status: {status}")
    manifest["campaign_status"] = status


def update_body_counts(
    manifest: dict[str, Any],
    *,
    completed: int,
    failed: int,
    skipped: int,
    interrupted: int,
    total: int | None = None,
) -> None:
    total = total if total is not None else manifest.get("total_bodies", TOTAL_BODIES)
    manifest["total_bodies"] = total
    manifest["completed_bodies"] = completed
    manifest["failed_bodies"] = failed
    manifest["skipped_bodies"] = skipped
    manifest["interrupted_bodies"] = interrupted
    manifest["remaining_bodies"] = max(
        0, total - completed - failed - skipped - interrupted
    )


def update_eta(manifest: dict[str, Any], eta: dict[str, Any]) -> None:
    manifest["eta"] = eta


def sync_manifest_from_db(manifest: dict[str, Any], counts: dict[str, int]) -> dict[str, Any]:
    """Refresh manifest counters and status from database status counts."""
    completed = counts.get("COMPLETED", 0)
    failed = counts.get("FAILED", 0)
    skipped = counts.get("SKIPPED", 0)
    interrupted = counts.get("INTERRUPTED", 0)
    pending = counts.get("PENDING", 0)
    running = counts.get("RUNNING", 0)
    total = manifest.get("total_bodies", TOTAL_BODIES)

    update_body_counts(
        manifest,
        completed=completed,
        failed=failed,
        skipped=skipped,
        interrupted=interrupted,
        total=total,
    )

    if completed >= total:
        set_campaign_status(manifest, "COMPLETED")
    elif running > 0:
        set_campaign_status(manifest, "RUNNING")
    elif interrupted > 0:
        set_campaign_status(manifest, "INTERRUPTED")
    elif failed > 0 and pending == 0 and interrupted == 0:
        set_campaign_status(manifest, "FAILED")
    elif completed + failed + skipped > 0:
        set_campaign_status(manifest, "PAUSED")
    else:
        set_campaign_status(manifest, "READY")

    return manifest
