"""Dry-run validation before launching a multi-day DOE campaign."""

from __future__ import annotations

import json
import sqlite3
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from campaign.constants import (
    CASES_ROOT,
    DEFAULT_DB_PATH,
    MANIFEST_PATH,
    MAX_ITERATION_BUDGET,
    MESH_LEVEL,
    PROFILES_ROOT,
    RESULTS_ROOT,
    SOLVER,
    TEMPLATE_CASE,
    TOTAL_BODIES,
    TURBULENCE_MODEL,
)
from campaign.fingerprints import compute_template_fingerprints
from campaign.solver_config import validate_production_solver_config
from campaign.environment import environment_validation_lines
from campaign.environment_store import ensure_campaign_environment
from campaign.manifest import init_manifest, load_manifest, save_manifest
from campaign.resume import build_resume_plan, list_campaign_bodies
from reporting.production_db import connect, prepare_database


@dataclass
class ValidationResult:
    ok: bool = True
    checks: list[dict[str, Any]] = field(default_factory=list)
    environment_lines: list[str] = field(default_factory=list)

    def add(self, name: str, passed: bool, detail: str = "") -> None:
        self.checks.append({"name": name, "passed": passed, "detail": detail})
        if not passed:
            self.ok = False


def _check_directory(path: Path, name: str, result: ValidationResult) -> None:
    result.add(name, path.is_dir(), str(path))


def validate_campaign(
    db_path: Path = DEFAULT_DB_PATH,
    *,
    manifest_path: Path = MANIFEST_PATH,
    expected_bodies: int = TOTAL_BODIES,
    skip_report_pipeline: bool = False,
    workers: int = 2,
    mpi_ranks_per_worker: int = 6,
) -> ValidationResult:
    """Verify campaign integrity before launching production DOE."""
    result = ValidationResult()

    # Required directories
    for label, path in (
        ("profiles_root", PROFILES_ROOT),
        ("results_root", RESULTS_ROOT),
        ("cases_root", CASES_ROOT),
        ("template_case", TEMPLATE_CASE),
    ):
        _check_directory(path, label, result)

    # Manifest
    try:
        if not manifest_path.exists():
            init_manifest(total_bodies=expected_bodies, path=manifest_path)
        manifest = load_manifest(manifest_path)
        required_keys = (
            "campaign_uuid",
            "campaign_name",
            "mesh_level",
            "turbulence_model",
            "solver",
            "total_bodies",
        )
        missing = [k for k in required_keys if k not in manifest]
        result.add(
            "manifest_integrity",
            not missing,
            f"missing keys: {missing}" if missing else manifest_path.as_posix(),
        )
        result.add(
            "manifest_mesh_level",
            manifest.get("mesh_level") == MESH_LEVEL,
            f"expected {MESH_LEVEL}, got {manifest.get('mesh_level')}",
        )
        result.add(
            "manifest_solver",
            manifest.get("solver") == SOLVER and manifest.get("turbulence_model") == TURBULENCE_MODEL,
            f"solver={manifest.get('solver')} turbulence={manifest.get('turbulence_model')}",
        )
    except Exception as exc:
        result.add("manifest_integrity", False, str(exc))

    # Database
    try:
        conn = prepare_database(
            db_path,
            sync_results_flag=False,
        )
        try:
            row = conn.execute(
                "SELECT value FROM schema_info WHERE key = 'schema_version'"
            ).fetchone()
            result.add(
                "database_profiles",
                n_samples == len(bodies),
                f"master_samples={n_samples}, profile folders={len(bodies)}",
            )
            n_samples = conn.execute("SELECT COUNT(*) AS n FROM master_samples").fetchone()["n"]
            bodies = list_campaign_bodies()
            result.add(
                "database_profiles",
                n_samples >= len(bodies) or n_samples == 0,
                f"master_samples={n_samples}, profile folders={len(bodies)}",
            )
            plan = build_resume_plan(conn)
            result.add(
                "resume_pipeline",
                plan.total_bodies == len(bodies),
                f"resume plan covers {plan.total_bodies} bodies",
            )
        finally:
            conn.close()
    except Exception as exc:
        result.add("database_integrity", False, str(exc))

    # Production profile inputs required before case generation
    profile_missing = [
        b
        for b in list_campaign_bodies()
        if not (PROFILES_ROOT / b / "profile.csv").exists()
        or not (PROFILES_ROOT / b / "metadata.json").exists()
    ]

    result.add(
        "production_profiles",
        len(profile_missing) == 0,
        (
            f"missing profile inputs for {len(profile_missing)} bodies"
            if profile_missing
            else "all bodies have profile.csv and metadata.json"
        ),
    )

    # Production solver template
    template_files = (
    "0/U",
    "0/p",
    "0/k",
    "0/omega",
    "0/nut",
    "system/controlDict",
    "system/functions",
    "system/fvSchemes",
    "system/fvSolution",
    "constant/momentumTransport",
    "constant/physicalProperties",
    )
    missing_tpl = [f for f in template_files if not (TEMPLATE_CASE / f).exists()]
    result.add(
        "production_solver",
        not missing_tpl,
        f"missing template files: {missing_tpl}" if missing_tpl else "template_case complete",
    )
    fingerprints = compute_template_fingerprints()
    required_fp = ("fvSchemes", "fvSolution", "controlDict", "momentumTransport", "physicalProperties")
    result.add(
        "template_fingerprints",
        all(fingerprints.get(k) for k in required_fp),
        json.dumps({k: (v[:12] + "..." if v else None) for k, v in fingerprints.items()}),
    )

    stopping_ok, stopping_detail = validate_production_solver_config(TEMPLATE_CASE)
    result.add(
        "solver_stopping_strategy",
        stopping_ok,
        stopping_detail,
    )

    try:
        from campaign.campaign_status import apply_campaign_status, infer_campaign_status
        from campaign.solver_config import TERMINATION_MAX_ITERATIONS

        max_iter_summary = {
            "status": "FAILED",
            "Cd": 0.0012,
            "fatal": False,
            "returncode": 0,
            "converged_residualControl": False,
            "actual_iterations_run": MAX_ITERATION_BUDGET,
            "termination_reason": TERMINATION_MAX_ITERATIONS,
            "error_message": "Residual convergence not reached",
        }
        apply_campaign_status(max_iter_summary)
        status_ok, _ = infer_campaign_status(max_iter_summary)
        result.add(
            "campaign_status_semantics",
            status_ok == "COMPLETED"
            and max_iter_summary["status"] == "COMPLETED"
            and max_iter_summary["termination_reason"] == TERMINATION_MAX_ITERATIONS,
            "MAX_ITERATIONS + valid Cd => COMPLETED (not FAILED)",
        )
    except Exception as exc:
        result.add("campaign_status_semantics", False, str(exc))

    # Checkpoint pipeline directories
    from campaign.constants import CHECKPOINTS_DIR, PROGRESS_REPORTS_DIR

    CHECKPOINTS_DIR.mkdir(parents=True, exist_ok=True)
    PROGRESS_REPORTS_DIR.mkdir(parents=True, exist_ok=True)
    result.add("checkpoint_pipeline", CHECKPOINTS_DIR.is_dir(), str(CHECKPOINTS_DIR))

    # Report pipeline import check (no generation unless bodies exist)
    if not skip_report_pipeline:
        try:
            from reporting.analysis import analyze_campaign
            from reporting.figures import generate_all_figures
            from reporting.markdown_report import render_markdown
            from reporting.pdf_export import render_pdf
            from campaign.progress_report import generate_progress_report
            from campaign.checkpoint import create_checkpoint

            result.add("report_pipeline", True, "reporting modules import OK")
            result.add("figure_pipeline", True, "figures module import OK")
        except Exception as exc:
            result.add("report_pipeline", False, str(exc))

    # Body count
    bodies = list_campaign_bodies()
    result.add(
        "body_count",
        len(bodies) == expected_bodies,
        f"found {len(bodies)} bodies (expected {expected_bodies})",
    )

    # Computational environment (informational — never fails validation)
    try:
        if manifest_path.exists():
            manifest = load_manifest(manifest_path)
        else:
            manifest = init_manifest(total_bodies=expected_bodies, path=manifest_path)
        conn = connect(db_path)
        try:
            environment = ensure_campaign_environment(
                conn,
                manifest,
                manifest_path=manifest_path,
                workers=workers,
                mpi_ranks_per_worker=mpi_ranks_per_worker,
            )
        finally:
            conn.close()
        result.environment_lines = environment_validation_lines(environment)
        result.add("environment_capture", True, "computational environment recorded")
    except Exception as exc:
        result.environment_lines = [f"Environment capture failed: {exc}"]
        result.add("environment_capture", True, f"warning: {exc}")

    return result


def validation_report(result: ValidationResult) -> str:
    lines = ["Campaign validation", ""]
    for check in result.checks:
        mark = "PASS" if check["passed"] else "FAIL"
        lines.append(f"[{mark}] {check['name']}: {check['detail']}")
    env_lines = getattr(result, "environment_lines", None)
    if env_lines:
        lines.extend(env_lines)
    lines.append("")
    lines.append("OVERALL: PASS" if result.ok else "OVERALL: FAIL")
    return "\n".join(lines)
