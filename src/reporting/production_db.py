"""SQLite persistence layer for the production DOE campaign.

Per-body JSON artifacts under ``results/`` are ingested into the campaign
database for centralized analysis, reporting, and reproducibility tracking.
"""

from __future__ import annotations

import json
import re
import sqlite3
import subprocess
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from campaign.constants import (
    CASES_ROOT,
    DEFAULT_DB_PATH,
    LHS_BATCH,
    MESH_LEVEL,
    PROFILES_ROOT,
    REPO_ROOT,
    RESULTS_ROOT,
    SOLVER,
    TOTAL_BODIES,
    TURBULENCE_MODEL,
)

SCHEMA_VERSION = 8
EXPECTED_DOE_BODIES = TOTAL_BODIES
BODY_ID_PATTERN = re.compile(r"^Body_(\d{4})$")
P3_ID_PATTERN = re.compile(r"^P3_(\d{3})$")
P45_ID_PATTERN = re.compile(r"^P45_(\d{3})$")

CREATE_STATEMENTS = """
PRAGMA journal_mode=WAL;

CREATE TABLE IF NOT EXISTS schema_info (
    key TEXT PRIMARY KEY,
    value TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS master_samples (
    sample_id TEXT PRIMARY KEY,
    lambda REAL NOT NULL,
    length REAL NOT NULL,
    r_max REAL NOT NULL,
    w0 REAL NOT NULL,
    w1 REAL NOT NULL,
    w2 REAL NOT NULL,
    w3 REAL NOT NULL,
    lhs_batch TEXT NOT NULL DEFAULT 'LHS_V1',
    status TEXT NOT NULL DEFAULT 'PENDING',
    created_at TEXT NOT NULL,
    profile_path TEXT
);

CREATE TABLE IF NOT EXISTS simulation_runs (
    run_id TEXT PRIMARY KEY,
    sample_id TEXT NOT NULL,
    phase INTEGER NOT NULL DEFAULT 1,
    status TEXT NOT NULL,
    mesh_level TEXT,
    turbulence_model TEXT,
    solver TEXT,
    openfoam_version TEXT,
    git_commit TEXT,
    config_hash TEXT,
    cd REAL,
    cl REAL,
    iterations INTEGER,
    execution_time_s REAL,
    wall_clock_s REAL,
    peak_rss_mb REAL,
    converged INTEGER,
    cd_drift_last50_pct REAL,
    yplus_min REAL,
    yplus_max REAL,
    yplus_avg REAL,
    cells INTEGER,
    faces INTEGER,
    points INTEGER,
    hexes INTEGER,
    prisms INTEGER,
    max_non_ortho REAL,
    max_skewness REAL,
    rocket_wall_faces INTEGER,
    case_path TEXT,
    results_path TEXT,
    error_message TEXT,
    completed_at TEXT,
    FOREIGN KEY (sample_id) REFERENCES master_samples(sample_id)
);

CREATE INDEX IF NOT EXISTS idx_simulation_runs_sample ON simulation_runs(sample_id);
CREATE INDEX IF NOT EXISTS idx_simulation_runs_status ON simulation_runs(status);
CREATE INDEX IF NOT EXISTS idx_master_samples_status ON master_samples(status);

CREATE TABLE IF NOT EXISTS campaign_metadata (
    key TEXT PRIMARY KEY,
    value TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS config_fingerprints (
    run_id TEXT PRIMARY KEY,
    geometry_hash TEXT,
    profile_csv_hash TEXT,
    profile_geo_hash TEXT,
    mesh_hash TEXT,
    fvschemes_hash TEXT,
    fvsolution_hash TEXT,
    controldict_hash TEXT,
    momentumtransport_hash TEXT,
    transportproperties_hash TEXT,
    physicalproperties_hash TEXT,
    FOREIGN KEY (run_id) REFERENCES simulation_runs(run_id)
);
"""

MIGRATION_V2_STATEMENTS = """
ALTER TABLE simulation_runs ADD COLUMN campaign_uuid TEXT;
ALTER TABLE simulation_runs ADD COLUMN start_time TEXT;
"""

MIGRATION_V3_STATEMENTS = """
ALTER TABLE master_samples ADD COLUMN retry_count INTEGER NOT NULL DEFAULT 0;
ALTER TABLE master_samples ADD COLUMN first_attempt_time TEXT;
ALTER TABLE master_samples ADD COLUMN last_attempt_time TEXT;
ALTER TABLE master_samples ADD COLUMN last_failure_reason TEXT;
ALTER TABLE master_samples ADD COLUMN last_exit_code INTEGER;
"""

MIGRATION_V4_STATEMENTS = """
CREATE TABLE IF NOT EXISTS campaign_environment (
    campaign_uuid TEXT PRIMARY KEY,
    captured_at TEXT NOT NULL,
    cpu_model TEXT,
    physical_cores TEXT,
    logical_cpus TEXT,
    total_ram_gb TEXT,
    hostname TEXT,
    operating_system TEXT,
    kernel_version TEXT,
    architecture TEXT,
    python_version TEXT,
    openfoam_version TEXT,
    mpi_implementation TEXT,
    mpi_version TEXT,
    gmsh_version TEXT,
    git_commit TEXT,
    git_branch TEXT,
    git_state TEXT,
    project_version TEXT,
    working_directory TEXT,
    workers TEXT,
    mpi_ranks_per_worker TEXT,
    mesh_level TEXT,
    solver TEXT,
    turbulence_model TEXT,
    campaign_creation_time TEXT,
    campaign_start_time TEXT,
    campaign_end_time TEXT,
    campaign_duration_s TEXT,
    campaign_duration_human TEXT,
    environment_json TEXT NOT NULL
);
"""

MIGRATION_V5_STATEMENTS = """
ALTER TABLE campaign_environment ADD COLUMN host_physical_ram_gb TEXT;
ALTER TABLE campaign_environment ADD COLUMN wsl_memory_limit_gb TEXT;
ALTER TABLE campaign_environment ADD COLUMN wsl_available_ram_gb TEXT;
ALTER TABLE campaign_environment ADD COLUMN swap_total_gb TEXT;
"""

MIGRATION_V6_STATEMENTS = """
ALTER TABLE simulation_runs ADD COLUMN force_converged INTEGER;
ALTER TABLE simulation_runs ADD COLUMN cd_mean_last100 REAL;
ALTER TABLE simulation_runs ADD COLUMN cd_std_last100 REAL;
ALTER TABLE simulation_runs ADD COLUMN cd_variation_percent REAL;
ALTER TABLE simulation_runs ADD COLUMN cd_max_deviation REAL;
ALTER TABLE simulation_runs ADD COLUMN cd_trend_percent REAL;
ALTER TABLE simulation_runs ADD COLUMN force_samples INTEGER;
"""

MIGRATION_V7_STATEMENTS = """
ALTER TABLE simulation_runs ADD COLUMN actual_iterations_run INTEGER;
ALTER TABLE simulation_runs ADD COLUMN termination_reason TEXT;
"""

MIGRATION_V8_LEGACY_STATUS = """
UPDATE simulation_runs
SET termination_reason = 'SOLVER_CRASH'
WHERE termination_reason = 'FAILED';

UPDATE simulation_runs
SET status = 'COMPLETED',
    termination_reason = COALESCE(termination_reason, 'MAX_ITERATIONS'),
    converged = 0
WHERE status = 'FAILED'
  AND cd IS NOT NULL
  AND (termination_reason = 'MAX_ITERATIONS'
       OR (termination_reason IS NULL
           AND iterations >= 1000
           AND IFNULL(converged, 0) = 0));

UPDATE master_samples
SET status = 'COMPLETED'
WHERE sample_id IN (
    SELECT sample_id FROM simulation_runs
    WHERE status = 'COMPLETED'
      AND termination_reason = 'MAX_ITERATIONS'
);
"""


@dataclass(frozen=True)
class CampaignRow:
    """Joined design + simulation record for analysis and reporting."""

    sample_id: str
    lambda_: float
    length: float
    r_max: float
    w0: float
    w1: float
    w2: float
    w3: float
    status: str
    cd: float | None
    cl: float | None
    iterations: int | None
    execution_time_s: float | None
    wall_clock_s: float | None
    peak_rss_mb: float | None
    converged: bool | None
    cd_drift_last50_pct: float | None
    yplus_min: float | None
    yplus_max: float | None
    yplus_avg: float | None
    cells: int | None
    faces: int | None
    points: int | None
    hexes: int | None
    prisms: int | None
    max_non_ortho: float | None
    max_skewness: float | None
    rocket_wall_faces: int | None
    mesh_level: str | None
    turbulence_model: str | None
    solver: str | None
    openfoam_version: str | None
    git_commit: str | None
    case_path: str | None
    results_path: str | None
    error_message: str | None
    completed_at: str | None
    force_converged: bool | None = None
    cd_mean_last100: float | None = None
    cd_std_last100: float | None = None
    cd_variation_percent: float | None = None
    cd_max_deviation: float | None = None
    cd_trend_percent: float | None = None
    force_samples: int | None = None
    actual_iterations_run: int | None = None
    termination_reason: str | None = None


def utc_now_iso() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def get_git_commit(root: Path = REPO_ROOT) -> str | None:
    try:
        out = subprocess.run(
            ["git", "-C", str(root), "rev-parse", "HEAD"],
            capture_output=True,
            text=True,
            check=True,
            timeout=5,
        )
        return out.stdout.strip() or None
    except (subprocess.SubprocessError, FileNotFoundError):
        return None


def get_openfoam_version() -> str:
    try:
        out = subprocess.run(
            ["bash", "-lc", ". /opt/openfoam13/etc/bashrc && foamVersion"],
            capture_output=True,
            text=True,
            timeout=15,
        )
        if out.returncode == 0 and out.stdout.strip():
            return out.stdout.strip()
    except (subprocess.SubprocessError, FileNotFoundError):
        pass
    return "OpenFOAM-13"


def connect(db_path: Path = DEFAULT_DB_PATH) -> sqlite3.Connection:
    db_path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys=ON")
    return conn


def _current_schema_version(conn: sqlite3.Connection) -> int:
    row = conn.execute(
        "SELECT value FROM schema_info WHERE key = 'schema_version'"
    ).fetchone()
    if row is None:
        return 0
    return int(row["value"])


def migrate_schema(conn: sqlite3.Connection) -> None:
    """Apply incremental schema migrations."""
    version = _current_schema_version(conn)
    if version < 2:
        for statement in MIGRATION_V2_STATEMENTS.strip().split(";"):
            statement = statement.strip()
            if not statement:
                continue
            try:
                conn.execute(statement)
            except sqlite3.OperationalError as exc:
                if "duplicate column name" not in str(exc).lower():
                    raise
        conn.executescript(
            """
            CREATE TABLE IF NOT EXISTS config_fingerprints (
                run_id TEXT PRIMARY KEY,
                geometry_hash TEXT,
                profile_csv_hash TEXT,
                profile_geo_hash TEXT,
                mesh_hash TEXT,
                fvschemes_hash TEXT,
                fvsolution_hash TEXT,
                controldict_hash TEXT,
                momentumtransport_hash TEXT,
                transportproperties_hash TEXT,
                physicalproperties_hash TEXT,
                FOREIGN KEY (run_id) REFERENCES simulation_runs(run_id)
            );
            """
        )
        conn.execute(
            "INSERT OR REPLACE INTO schema_info(key, value) VALUES (?, ?)",
            ("schema_version", "2"),
        )
        conn.commit()
    if version < 3:
        for statement in MIGRATION_V3_STATEMENTS.strip().split(";"):
            statement = statement.strip()
            if not statement:
                continue
            try:
                conn.execute(statement)
            except sqlite3.OperationalError as exc:
                if "duplicate column name" not in str(exc).lower():
                    raise
        conn.execute(
            "INSERT OR REPLACE INTO schema_info(key, value) VALUES (?, ?)",
            ("schema_version", "3"),
        )
        conn.commit()
    if version < 4:
        conn.executescript(MIGRATION_V4_STATEMENTS)
        conn.execute(
            "INSERT OR REPLACE INTO schema_info(key, value) VALUES (?, ?)",
            ("schema_version", "4"),
        )
        conn.commit()
    if version < 5:
        for statement in MIGRATION_V5_STATEMENTS.strip().split(";"):
            statement = statement.strip()
            if not statement:
                continue
            try:
                conn.execute(statement)
            except sqlite3.OperationalError as exc:
                if "duplicate column name" not in str(exc).lower():
                    raise
        conn.execute(
            "INSERT OR REPLACE INTO schema_info(key, value) VALUES (?, ?)",
            ("schema_version", "5"),
        )
        conn.commit()
    if version < 6:
        for statement in MIGRATION_V6_STATEMENTS.strip().split(";"):
            statement = statement.strip()
            if not statement:
                continue
            try:
                conn.execute(statement)
            except sqlite3.OperationalError as exc:
                if "duplicate column name" not in str(exc).lower():
                    raise
        conn.execute(
            "INSERT OR REPLACE INTO schema_info(key, value) VALUES (?, ?)",
            ("schema_version", "6"),
        )
        conn.commit()
    if version < 7:
        for statement in MIGRATION_V7_STATEMENTS.strip().split(";"):
            statement = statement.strip()
            if not statement:
                continue
            try:
                conn.execute(statement)
            except sqlite3.OperationalError as exc:
                if "duplicate column name" not in str(exc).lower():
                    raise
        conn.execute(
            "INSERT OR REPLACE INTO schema_info(key, value) VALUES (?, ?)",
            ("schema_version", "7"),
        )
        conn.commit()
    if version < 8:
        conn.executescript(MIGRATION_V8_LEGACY_STATUS)
        conn.execute(
            "INSERT OR REPLACE INTO schema_info(key, value) VALUES (?, ?)",
            ("schema_version", "8"),
        )
        conn.commit()


def init_database(db_path: Path = DEFAULT_DB_PATH) -> None:
    with connect(db_path) as conn:
        conn.executescript(CREATE_STATEMENTS)
        migrate_schema(conn)
        conn.execute(
            "INSERT OR REPLACE INTO schema_info(key, value) VALUES (?, ?)",
            ("schema_version", str(SCHEMA_VERSION)),
        )
        conn.commit()


def set_campaign_metadata(conn: sqlite3.Connection, key: str, value: str) -> None:
    conn.execute(
        "INSERT OR REPLACE INTO campaign_metadata(key, value) VALUES (?, ?)",
        (key, value),
    )


def get_campaign_metadata(conn: sqlite3.Connection, key: str) -> str | None:
    row = conn.execute(
        "SELECT value FROM campaign_metadata WHERE key = ?", (key,)
    ).fetchone()
    return row["value"] if row else None


def import_profiles(
    conn: sqlite3.Connection,
    profiles_root: Path = PROFILES_ROOT,
    *,
    lhs_batch: str = LHS_BATCH,
    profile_glob: str = "Body_*/metadata.json",
    project_root: Path = REPO_ROOT,
) -> int:
    """Register profile ``metadata.json`` rows in master_samples."""
    imported = 0
    now = utc_now_iso()
    for meta_path in sorted(profiles_root.glob(profile_glob)):
        meta = json.loads(meta_path.read_text(encoding="utf-8"))
        sample_id = meta["body_id"]
        weights = meta["weights"]
        conn.execute(
            """
            INSERT INTO master_samples (
                sample_id, lambda, length, r_max, w0, w1, w2, w3,
                lhs_batch, status, created_at, profile_path
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(sample_id) DO UPDATE SET
                lambda=excluded.lambda,
                length=excluded.length,
                r_max=excluded.r_max,
                w0=excluded.w0,
                w1=excluded.w1,
                w2=excluded.w2,
                w3=excluded.w3,
                lhs_batch=excluded.lhs_batch,
                profile_path=excluded.profile_path
            """,
            (
                sample_id,
                float(meta["lambda"]),
                float(meta["length"]),
                float(meta.get("R", meta.get("r_max", 0.07))),
                float(weights["w0"]),
                float(weights["w1"]),
                float(weights["w2"]),
                float(weights["w3"]),
                lhs_batch,
                "PENDING",
                now,
                str(meta_path.parent.relative_to(project_root)),
            ),
        )
        imported += 1
    conn.commit()
    return imported


def _parse_checkmesh_stats(checkmesh_path: Path) -> dict[str, Any]:
    if not checkmesh_path.exists():
        return {}
    text = checkmesh_path.read_text(errors="replace")
    stats: dict[str, Any] = {}

    def _int(pattern: str) -> int | None:
        m = re.search(pattern, text)
        return int(m.group(1).replace(",", "")) if m else None

    def _float(pattern: str) -> float | None:
        m = re.search(pattern, text)
        return float(m.group(1)) if m else None

    stats["cells"] = _int(r"cells:\s+(\d+)")
    stats["faces"] = _int(r"faces:\s+(\d+)")
    stats["points"] = _int(r"points:\s+(\d+)")
    stats["hexes"] = _int(r"hexes:\s+(\d+)")
    stats["prisms"] = _int(r"prisms:\s+(\d+)")
    stats["max_non_ortho"] = _float(r"Max non-orthogonality = ([\d.]+)")
    stats["max_skewness"] = _float(r"Max skewness = ([\d.]+)")
    wall = _int(r"rocket_wall\s+(\d+)")
    if wall is None:
        wall = _int(r"patch rocket_wall.*?(\d+)\s+faces", )
    stats["rocket_wall_faces"] = wall
    return stats


def _infer_run_status(summary: dict[str, Any]) -> str:
    from campaign.campaign_status import infer_campaign_status, normalize_summary_for_ingest

    normalized = normalize_summary_for_ingest(summary)
    status, _ = infer_campaign_status(normalized)
    return status


def record_simulation_run(
    conn: sqlite3.Connection,
    sample_id: str,
    summary: dict[str, Any],
    *,
    case_path: Path | None = None,
    results_path: Path | None = None,
    mesh_stats: dict[str, Any] | None = None,
) -> None:
    """Upsert one simulation result (called by batch runner or sync)."""
    status = _infer_run_status(summary)
    run_id = (
        summary.get("simulation_uuid")
        or summary.get("run_id")
        or f"{sample_id}_phase1"
    )
    mesh_stats = mesh_stats or {}
    converged = summary.get("converged")
    if converged is None:
        converged = summary.get("converged_residualControl")
    force_converged = summary.get("force_converged")

    conn.execute(
        """
        INSERT INTO simulation_runs (
            run_id, sample_id, phase, status, mesh_level, turbulence_model, solver,
            openfoam_version, git_commit, config_hash, campaign_uuid, start_time,
            cd, cl, iterations,
            execution_time_s, wall_clock_s, peak_rss_mb, converged,
            cd_drift_last50_pct, yplus_min, yplus_max, yplus_avg,
            cells, faces, points, hexes, prisms, max_non_ortho, max_skewness,
            rocket_wall_faces, case_path, results_path, error_message, completed_at,
            force_converged, cd_mean_last100, cd_std_last100, cd_variation_percent,
            cd_max_deviation, cd_trend_percent, force_samples,
            actual_iterations_run, termination_reason
        ) VALUES (
            ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?,
            ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?
        )
        ON CONFLICT(run_id) DO UPDATE SET
            status=excluded.status,
            mesh_level=excluded.mesh_level,
            turbulence_model=excluded.turbulence_model,
            solver=excluded.solver,
            openfoam_version=excluded.openfoam_version,
            git_commit=excluded.git_commit,
            config_hash=excluded.config_hash,
            campaign_uuid=excluded.campaign_uuid,
            start_time=excluded.start_time,
            cd=excluded.cd,
            cl=excluded.cl,
            iterations=excluded.iterations,
            execution_time_s=excluded.execution_time_s,
            wall_clock_s=excluded.wall_clock_s,
            peak_rss_mb=excluded.peak_rss_mb,
            converged=excluded.converged,
            cd_drift_last50_pct=excluded.cd_drift_last50_pct,
            yplus_min=excluded.yplus_min,
            yplus_max=excluded.yplus_max,
            yplus_avg=excluded.yplus_avg,
            cells=excluded.cells,
            faces=excluded.faces,
            points=excluded.points,
            hexes=excluded.hexes,
            prisms=excluded.prisms,
            max_non_ortho=excluded.max_non_ortho,
            max_skewness=excluded.max_skewness,
            rocket_wall_faces=excluded.rocket_wall_faces,
            case_path=excluded.case_path,
            results_path=excluded.results_path,
            error_message=excluded.error_message,
            completed_at=excluded.completed_at,
            force_converged=excluded.force_converged,
            cd_mean_last100=excluded.cd_mean_last100,
            cd_std_last100=excluded.cd_std_last100,
            cd_variation_percent=excluded.cd_variation_percent,
            cd_max_deviation=excluded.cd_max_deviation,
            cd_trend_percent=excluded.cd_trend_percent,
            force_samples=excluded.force_samples,
            actual_iterations_run=excluded.actual_iterations_run,
            termination_reason=excluded.termination_reason
        """,
        (
            run_id,
            sample_id,
            int(summary.get("phase", 1)),
            status,
            summary.get("mesh_level", MESH_LEVEL),
            summary.get("turbulence_model", TURBULENCE_MODEL),
            summary.get("solver", SOLVER),
            summary.get("openfoam_version") or get_openfoam_version(),
            summary.get("git_commit") or get_git_commit(),
            summary.get("config_hash"),
            summary.get("campaign_uuid"),
            summary.get("start_time"),
            summary.get("Cd"),
            summary.get("Cl"),
            summary.get("iterations"),
            summary.get("execution_time_s"),
            summary.get("wall_clock_s") or summary.get("clock_time_s"),
            summary.get("peak_rss_mb"),
            1 if converged else 0 if converged is not None else None,
            summary.get("Cd_drift_last50_pct"),
            summary.get("yplus_min"),
            summary.get("yplus_max"),
            summary.get("yplus_avg"),
            mesh_stats.get("cells") or summary.get("cells"),
            mesh_stats.get("faces") or summary.get("faces"),
            mesh_stats.get("points") or summary.get("points"),
            mesh_stats.get("hexes") or summary.get("hexes"),
            mesh_stats.get("prisms") or summary.get("prisms"),
            mesh_stats.get("max_non_ortho") or summary.get("max_non_ortho"),
            mesh_stats.get("max_skewness") or summary.get("max_skewness"),
            mesh_stats.get("rocket_wall_faces") or summary.get("rocket_wall_faces"),
            str(case_path.relative_to(REPO_ROOT)) if case_path else summary.get("case_path"),
            str(results_path.relative_to(REPO_ROOT)) if results_path else summary.get("results_path"),
            summary.get("error_message"),
            summary.get("completed_at") or utc_now_iso(),
            1 if force_converged else 0 if force_converged is not None else None,
            summary.get("cd_mean_last100"),
            summary.get("cd_std_last100"),
            summary.get("cd_variation_percent"),
            summary.get("cd_max_deviation"),
            summary.get("cd_trend_percent"),
            summary.get("force_samples"),
            summary.get("actual_iterations_run", summary.get("iterations")),
            summary.get("termination_reason"),
        ),
    )
    conn.execute(
        "UPDATE master_samples SET status = ? WHERE sample_id = ?",
        (status, sample_id),
    )
    conn.commit()


def record_config_fingerprints(
    conn: sqlite3.Connection,
    run_id: str,
    fingerprints: dict[str, str | None],
) -> None:
    """Store per-artifact SHA256 hashes for reproducibility auditing."""
    key_map = {
        "geometry": "geometry_hash",
        "profile_csv": "profile_csv_hash",
        "profile_geo": "profile_geo_hash",
        "mesh": "mesh_hash",
        "fvSchemes": "fvschemes_hash",
        "fvSolution": "fvsolution_hash",
        "controlDict": "controldict_hash",
        "momentumTransport": "momentumtransport_hash",
        "transportProperties": "transportproperties_hash",
        "physicalProperties": "physicalproperties_hash",
    }
    values = {db_col: fingerprints.get(src_key) for src_key, db_col in key_map.items()}
    conn.execute(
        """
        INSERT INTO config_fingerprints (
            run_id, geometry_hash, profile_csv_hash, profile_geo_hash, mesh_hash,
            fvschemes_hash, fvsolution_hash, controldict_hash,
            momentumtransport_hash, transportproperties_hash, physicalproperties_hash
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        ON CONFLICT(run_id) DO UPDATE SET
            geometry_hash=excluded.geometry_hash,
            profile_csv_hash=excluded.profile_csv_hash,
            profile_geo_hash=excluded.profile_geo_hash,
            mesh_hash=excluded.mesh_hash,
            fvschemes_hash=excluded.fvschemes_hash,
            fvsolution_hash=excluded.fvsolution_hash,
            controldict_hash=excluded.controldict_hash,
            momentumtransport_hash=excluded.momentumtransport_hash,
            transportproperties_hash=excluded.transportproperties_hash,
            physicalproperties_hash=excluded.physicalproperties_hash
        """,
        (
            run_id,
            values["geometry_hash"],
            values["profile_csv_hash"],
            values["profile_geo_hash"],
            values["mesh_hash"],
            values["fvschemes_hash"],
            values["fvsolution_hash"],
            values["controldict_hash"],
            values["momentumtransport_hash"],
            values["transportproperties_hash"],
            values["physicalproperties_hash"],
        ),
    )
    conn.commit()


def sync_from_results(
    conn: sqlite3.Connection,
    results_root: Path = RESULTS_ROOT,
    cases_root: Path | None = None,
    *,
    summary_glob: str = "Body_*/summary.json",
    id_pattern: re.Pattern[str] | None = None,
) -> int:
    """Ingest ``results/<sample_id>/summary.json`` into simulation_runs."""
    cases_root = cases_root or CASES_ROOT
    pattern = id_pattern or BODY_ID_PATTERN
    synced = 0
    for summary_path in sorted(results_root.glob(summary_glob)):
        sample_id = summary_path.parent.name
        if not pattern.match(sample_id):
            continue
        summary = json.loads(summary_path.read_text(encoding="utf-8"))
        summary.setdefault("body_id", sample_id)
        from campaign.campaign_status import normalize_summary_for_ingest

        summary = normalize_summary_for_ingest(summary)
        case_path = cases_root / sample_id
        if case_path.exists():
            from reporting.force_convergence import merge_force_convergence_into_summary

            merge_force_convergence_into_summary(summary, case_path)
        summary_path.write_text(
            json.dumps(summary, indent=2, default=str) + "\n", encoding="utf-8"
        )
        mesh_stats_path = summary_path.parent / "mesh_stats.json"
        if mesh_stats_path.exists():
            mesh_stats = json.loads(mesh_stats_path.read_text())
        else:
            mesh_stats = _parse_checkmesh_stats(case_path / "log.checkMesh")
        record_simulation_run(
            conn,
            sample_id,
            summary,
            case_path=case_path if case_path.exists() else None,
            results_path=summary_path.parent,
            mesh_stats=mesh_stats,
        )
        run_id = (
            summary.get("simulation_uuid")
            or summary.get("run_id")
            or f"{sample_id}_phase1"
        )
        fingerprints = summary.get("config_fingerprints")
        if isinstance(fingerprints, dict):
            record_config_fingerprints(conn, run_id, fingerprints)
        from campaign.retry import sync_retry_from_summary

        sync_retry_from_summary(conn, sample_id, summary)
        synced += 1
    return synced


def fetch_campaign_rows(conn: sqlite3.Connection) -> list[CampaignRow]:
    rows = conn.execute(
        """
        SELECT
            m.sample_id, m.lambda AS lambda_, m.length, m.r_max,
            m.w0, m.w1, m.w2, m.w3, m.status,
            s.cd, s.cl, s.iterations, s.execution_time_s, s.wall_clock_s,
            s.peak_rss_mb, s.converged, s.cd_drift_last50_pct,
            s.yplus_min, s.yplus_max, s.yplus_avg,
            s.cells, s.faces, s.points, s.hexes, s.prisms,
            s.max_non_ortho, s.max_skewness, s.rocket_wall_faces,
            s.mesh_level, s.turbulence_model, s.solver,
            s.openfoam_version, s.git_commit,
            s.case_path, s.results_path, s.error_message, s.completed_at,
            s.force_converged, s.cd_mean_last100, s.cd_std_last100,
            s.cd_variation_percent, s.cd_max_deviation, s.cd_trend_percent,
            s.force_samples, s.actual_iterations_run, s.termination_reason
        FROM master_samples m
        LEFT JOIN simulation_runs s ON s.sample_id = m.sample_id
            AND s.run_id = (
                SELECT run_id FROM simulation_runs
                WHERE sample_id = m.sample_id
                ORDER BY completed_at DESC, run_id DESC
                LIMIT 1
            )
        ORDER BY m.sample_id
        """
    ).fetchall()
    result: list[CampaignRow] = []
    for r in rows:
        result.append(
            CampaignRow(
                sample_id=r["sample_id"],
                lambda_=r["lambda_"],
                length=r["length"],
                r_max=r["r_max"],
                w0=r["w0"],
                w1=r["w1"],
                w2=r["w2"],
                w3=r["w3"],
                status=r["status"] or "PENDING",
                cd=r["cd"],
                cl=r["cl"],
                iterations=r["iterations"],
                execution_time_s=r["execution_time_s"],
                wall_clock_s=r["wall_clock_s"],
                peak_rss_mb=r["peak_rss_mb"],
                converged=bool(r["converged"]) if r["converged"] is not None else None,
                cd_drift_last50_pct=r["cd_drift_last50_pct"],
                yplus_min=r["yplus_min"],
                yplus_max=r["yplus_max"],
                yplus_avg=r["yplus_avg"],
                cells=r["cells"],
                faces=r["faces"],
                points=r["points"],
                hexes=r["hexes"],
                prisms=r["prisms"],
                max_non_ortho=r["max_non_ortho"],
                max_skewness=r["max_skewness"],
                rocket_wall_faces=r["rocket_wall_faces"],
                mesh_level=r["mesh_level"],
                turbulence_model=r["turbulence_model"],
                solver=r["solver"],
                openfoam_version=r["openfoam_version"],
                git_commit=r["git_commit"],
                case_path=r["case_path"],
                results_path=r["results_path"],
                error_message=r["error_message"],
                completed_at=r["completed_at"],
                force_converged=bool(r["force_converged"])
                if r["force_converged"] is not None
                else None,
                cd_mean_last100=r["cd_mean_last100"],
                cd_std_last100=r["cd_std_last100"],
                cd_variation_percent=r["cd_variation_percent"],
                cd_max_deviation=r["cd_max_deviation"],
                cd_trend_percent=r["cd_trend_percent"],
                force_samples=r["force_samples"],
                actual_iterations_run=r["actual_iterations_run"],
                termination_reason=r["termination_reason"],
            )
        )
    return result


def count_by_status(conn: sqlite3.Connection) -> dict[str, int]:
    counts: dict[str, int] = {}
    for row in conn.execute(
        "SELECT status, COUNT(*) AS n FROM master_samples GROUP BY status"
    ):
        counts[row["status"]] = row["n"]
    return counts


def count_by_termination_reason(conn: sqlite3.Connection) -> dict[str, int]:
    """Count termination reasons for completed bodies and solver crashes for failures."""
    counts: dict[str, int] = {}
    for row in conn.execute(
        """
        SELECT s.termination_reason, COUNT(*) AS n
        FROM master_samples m
        JOIN simulation_runs s ON s.sample_id = m.sample_id
            AND s.run_id = (
                SELECT run_id FROM simulation_runs
                WHERE sample_id = m.sample_id
                ORDER BY completed_at DESC, run_id DESC
                LIMIT 1
            )
        WHERE m.status = 'COMPLETED' AND s.termination_reason IS NOT NULL
        GROUP BY s.termination_reason
        """
    ):
        reason = row["termination_reason"]
        if reason == "FAILED":
            reason = "SOLVER_CRASH"
        counts[reason] = row["n"]
    crash_failures = conn.execute(
        "SELECT COUNT(*) AS n FROM master_samples WHERE status = 'FAILED'"
    ).fetchone()
    if crash_failures and crash_failures["n"]:
        counts["SOLVER_CRASH"] = crash_failures["n"]
    return counts


def is_doe_complete(
    conn: sqlite3.Connection,
    *,
    expected_bodies: int = EXPECTED_DOE_BODIES,
) -> bool:
    row = conn.execute(
        "SELECT COUNT(*) AS n FROM master_samples WHERE status = 'COMPLETED'"
    ).fetchone()
    return bool(row and row["n"] >= expected_bodies)


def seed_from_validation_probe(
    conn: sqlite3.Connection,
    probe_results: Path | None = None,
) -> int:
    """Import the five-body SST probe results for pipeline smoke testing."""
    probe_results = probe_results or (
    REPO_ROOT / "validation" / "sa_vs_sst_probe" / "results"
    )
    seeded = 0
    for summary_path in sorted(probe_results.glob("Body_*/SST/summary.json")):
        sample_id = summary_path.parent.parent.name
        summary = json.loads(summary_path.read_text(encoding="utf-8"))
        summary.setdefault("body_id", sample_id)
        summary.setdefault("mesh_level", MESH_LEVEL)
        summary.setdefault("turbulence_model", TURBULENCE_MODEL)
        summary.setdefault("solver", SOLVER)
        record_simulation_run(conn, sample_id, summary, results_path=summary_path.parent)
        seeded += 1
    return seeded


def _env_value(value: Any) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    return text if text else None


def save_campaign_environment(conn: sqlite3.Connection, environment: dict[str, Any]) -> None:
    """Upsert full campaign environment linked by campaign UUID."""
    machine = environment.get("machine", {})
    software = environment.get("software", {})
    campaign = environment.get("campaign", {})
    campaign_uuid = campaign.get("campaign_uuid")
    if not campaign_uuid:
        raise ValueError("campaign_uuid is required to store campaign environment")

    conn.execute(
        """
        INSERT INTO campaign_environment (
            campaign_uuid, captured_at,
            cpu_model, physical_cores, logical_cpus, total_ram_gb,
            host_physical_ram_gb, wsl_memory_limit_gb, wsl_available_ram_gb, swap_total_gb,
            hostname, operating_system, kernel_version, architecture,
            python_version, openfoam_version, mpi_implementation, mpi_version,
            gmsh_version, git_commit, git_branch, git_state, project_version,
            working_directory, workers, mpi_ranks_per_worker, mesh_level,
            solver, turbulence_model, campaign_creation_time, campaign_start_time,
            campaign_end_time, campaign_duration_s, campaign_duration_human,
            environment_json
        ) VALUES (
            ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?,
            ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?
        )
        ON CONFLICT(campaign_uuid) DO UPDATE SET
            captured_at=excluded.captured_at,
            cpu_model=excluded.cpu_model,
            physical_cores=excluded.physical_cores,
            logical_cpus=excluded.logical_cpus,
            total_ram_gb=excluded.total_ram_gb,
            host_physical_ram_gb=excluded.host_physical_ram_gb,
            wsl_memory_limit_gb=excluded.wsl_memory_limit_gb,
            wsl_available_ram_gb=excluded.wsl_available_ram_gb,
            swap_total_gb=excluded.swap_total_gb,
            hostname=excluded.hostname,
            operating_system=excluded.operating_system,
            kernel_version=excluded.kernel_version,
            architecture=excluded.architecture,
            python_version=excluded.python_version,
            openfoam_version=excluded.openfoam_version,
            mpi_implementation=excluded.mpi_implementation,
            mpi_version=excluded.mpi_version,
            gmsh_version=excluded.gmsh_version,
            git_commit=excluded.git_commit,
            git_branch=excluded.git_branch,
            git_state=excluded.git_state,
            project_version=excluded.project_version,
            working_directory=excluded.working_directory,
            workers=excluded.workers,
            mpi_ranks_per_worker=excluded.mpi_ranks_per_worker,
            mesh_level=excluded.mesh_level,
            solver=excluded.solver,
            turbulence_model=excluded.turbulence_model,
            campaign_creation_time=excluded.campaign_creation_time,
            campaign_start_time=excluded.campaign_start_time,
            campaign_end_time=excluded.campaign_end_time,
            campaign_duration_s=excluded.campaign_duration_s,
            campaign_duration_human=excluded.campaign_duration_human,
            environment_json=excluded.environment_json
        """,
        (
            campaign_uuid,
            environment.get("captured_at") or utc_now_iso(),
            _env_value(machine.get("cpu_model")),
            _env_value(machine.get("physical_cores")),
            _env_value(machine.get("logical_cpus")),
            _env_value(machine.get("total_ram_gb")),
            _env_value(machine.get("host_physical_ram_gb")),
            _env_value(machine.get("wsl_memory_limit_gb")),
            _env_value(machine.get("wsl_available_ram_gb")),
            _env_value(machine.get("swap_total_gb")),
            _env_value(machine.get("hostname")),
            _env_value(machine.get("operating_system")),
            _env_value(machine.get("kernel_version")),
            _env_value(machine.get("architecture")),
            _env_value(software.get("python_version")),
            _env_value(software.get("openfoam_version")),
            _env_value(software.get("mpi_implementation")),
            _env_value(software.get("mpi_version")),
            _env_value(software.get("gmsh_version")),
            _env_value(software.get("git_commit")),
            _env_value(software.get("git_branch")),
            _env_value(software.get("git_state")),
            _env_value(software.get("project_version")),
            _env_value(software.get("working_directory")),
            _env_value(campaign.get("workers")),
            _env_value(campaign.get("mpi_ranks_per_worker")),
            _env_value(campaign.get("mesh_level")),
            _env_value(campaign.get("solver")),
            _env_value(campaign.get("turbulence_model")),
            _env_value(campaign.get("campaign_creation_time")),
            _env_value(campaign.get("campaign_start_time")),
            _env_value(campaign.get("campaign_end_time")),
            _env_value(campaign.get("campaign_duration_s")),
            _env_value(campaign.get("campaign_duration_human")),
            json.dumps(environment, indent=2),
        ),
    )
    conn.commit()


def fetch_campaign_environment(
    conn: sqlite3.Connection,
    campaign_uuid: str,
) -> dict[str, Any] | None:
    """Load stored campaign environment by UUID."""
    if not campaign_uuid:
        return None
    row = conn.execute(
        "SELECT environment_json FROM campaign_environment WHERE campaign_uuid = ?",
        (campaign_uuid,),
    ).fetchone()
    if row is None:
        return None
    return json.loads(row["environment_json"])


def prepare_database(
    db_path: Path = DEFAULT_DB_PATH,
    *,
    import_profiles_flag: bool = True,
    sync_results_flag: bool = True,
    profiles_root: Path | None = None,
    results_root: Path | None = None,
    profile_glob: str = "Body_*/metadata.json",
    summary_glob: str = "Body_*/summary.json",
    id_pattern: re.Pattern[str] | None = None,
    cases_root: Path | None = None,
    lhs_batch: str = LHS_BATCH,
) -> sqlite3.Connection:
    init_database(db_path)
    conn = connect(db_path)
    _profiles = profiles_root if profiles_root is not None else PROFILES_ROOT
    _results = results_root if results_root is not None else RESULTS_ROOT
    if import_profiles_flag and _profiles.is_dir():
        import_profiles(
            conn,
            _profiles,
            lhs_batch=lhs_batch,
            profile_glob=profile_glob,
        )
    if sync_results_flag and _results.is_dir():
        sync_from_results(
            conn,
            results_root=_results,
            cases_root=cases_root,
            summary_glob=summary_glob,
            id_pattern=id_pattern,
        )
    set_campaign_metadata(conn, "database_version", str(SCHEMA_VERSION))
    set_campaign_metadata(conn, "last_prepared_at", utc_now_iso())
    set_campaign_metadata(conn, "openfoam_version", get_openfoam_version())
    commit = get_git_commit()
    if commit:
        set_campaign_metadata(conn, "git_commit", commit)
    conn.commit()
    return conn
