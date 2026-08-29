"""Automatic CSV export from the production SQLite database."""

from __future__ import annotations

import csv
import json
import math
import sqlite3
from pathlib import Path
from typing import Any

from reporting.analysis import analyze_campaign
from campaign.constants import MANIFEST_PATH, RESULTS_ROOT
from reporting.production_db import (
    CampaignRow,
    connect,
    fetch_campaign_rows,
    get_campaign_metadata,
    utc_now_iso,
)
from reporting.force_convergence import force_convergence_table_rows

CSV_OUTPUT_DIR = RESULTS_ROOT / "csv"

def _fmt_corr(value: float | None) -> str:
    if value is None or (isinstance(value, float) and math.isnan(value)):
        return ""
    return f"{float(value):+.4f}"


def _fmt_sci(value: float | None) -> str:
    if value is None or (isinstance(value, float) and math.isnan(value)):
        return ""
    return f"{float(value):.6e}"


def _fmt_runtime(value: float | None) -> str:
    if value is None or (isinstance(value, float) and math.isnan(value)):
        return ""
    return f"{float(value):.2f}"


def _fmt_pct(value: float | None) -> str:
    if value is None or (isinstance(value, float) and math.isnan(value)):
        return ""
    return f"{float(value):.2f}"


def _fmt_bool(value: bool | None) -> str:
    if value is None:
        return ""
    return "TRUE" if value else "FALSE"


def _fmt_int(value: int | float | None) -> str:
    if value is None:
        return ""
    return str(int(value))


def _write_csv(path: Path, headers: list[str], rows: list[list[str]]) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w", encoding="utf-8", newline="") as handle:
        writer = csv.writer(handle)
        writer.writerow(headers)
        writer.writerows(rows)
    return path


def _fetch_run_metadata(conn: sqlite3.Connection) -> dict[str, dict[str, Any]]:
    """Per-body run metadata not present on CampaignRow (DB-only)."""
    meta: dict[str, dict[str, Any]] = {}
    for row in conn.execute(
        """
        SELECT
            m.sample_id,
            m.retry_count,
            s.run_id,
            s.config_hash,
            s.campaign_uuid
        FROM master_samples m
        LEFT JOIN simulation_runs s ON s.sample_id = m.sample_id
            AND s.run_id = (
                SELECT run_id
                FROM simulation_runs
                WHERE sample_id = m.sample_id
                ORDER BY completed_at DESC, run_id DESC
                LIMIT 1
            )
        ORDER BY m.sample_id
        """
    ):
        meta[row["sample_id"]] = {
            "retry_count": int(row["retry_count"] or 0),
            "run_id": row["run_id"],
            "config_hash": row["config_hash"],
            "campaign_uuid": row["campaign_uuid"],
        }
    return meta


def _resolve_campaign_uuid(
    conn: sqlite3.Connection,
    run_meta: dict[str, dict[str, Any]],
) -> str | None:
    for info in run_meta.values():
        if info.get("campaign_uuid"):
            return str(info["campaign_uuid"])
    manifest_path = MANIFEST_PATH
    if manifest_path.exists():
        try:
            manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
            if manifest.get("campaign_uuid"):
                return str(manifest["campaign_uuid"])
        except (json.JSONDecodeError, OSError):
            pass
    stored = get_campaign_metadata(conn, "campaign_uuid")
    return stored


def _all_results_rows(
    rows: list[CampaignRow],
    run_meta: dict[str, dict[str, Any]],
    campaign_uuid: str | None,
) -> list[list[str]]:
    out: list[list[str]] = []
    for row in rows:
        extra = run_meta.get(row.sample_id, {})
        runtime = row.wall_clock_s if row.wall_clock_s is not None else row.execution_time_s
        out.append(
            [
                row.sample_id,
                row.status,
                _fmt_runtime(row.lambda_),
                _fmt_runtime(row.w0),
                _fmt_runtime(row.w1),
                _fmt_runtime(row.w2),
                _fmt_runtime(row.w3),
                _fmt_runtime(row.length),
                _fmt_runtime(row.r_max),
                _fmt_sci(row.cd),
                _fmt_sci(row.cl),
                _fmt_int(row.iterations),
                _fmt_int(getattr(row, "actual_iterations_run", None) or row.iterations),
                getattr(row, "termination_reason", None) or "",
                _fmt_runtime(runtime),
                _fmt_runtime(row.execution_time_s),
                _fmt_runtime(row.peak_rss_mb),
                _fmt_int(row.cells),
                _fmt_int(row.faces),
                _fmt_int(row.points),
                _fmt_int(row.rocket_wall_faces),
                _fmt_runtime(row.yplus_avg),
                _fmt_runtime(row.yplus_max),
                _fmt_bool(row.converged),
                _fmt_bool(getattr(row, "force_converged", None)),
                _fmt_sci(getattr(row, "cd_mean_last100", None)),
                _fmt_sci(getattr(row, "cd_std_last100", None)),
                _fmt_pct(getattr(row, "cd_variation_percent", None)),
                _fmt_sci(getattr(row, "cd_max_deviation", None)),
                _fmt_pct(getattr(row, "cd_trend_percent", None)),
                _fmt_int(getattr(row, "force_samples", None)),
                row.solver or "",
                row.mesh_level or "",
                extra.get("run_id") or "",
                _fmt_int(extra.get("retry_count")),
                extra.get("campaign_uuid") or campaign_uuid or "",
                extra.get("config_hash") or "",
            ]
        )
    return out


def _ranking_entry_row(
    rank: int,
    entry: dict[str, Any],
    *,
    best_cd: float | None,
) -> list[str]:
    cd = entry.get("cd")
    delta_pct = ""
    if cd is not None and best_cd is not None and best_cd != 0:
        delta_pct = _fmt_pct((float(cd) - best_cd) / abs(best_cd) * 100.0)
    return [
        str(rank),
        entry["sample_id"],
        _fmt_sci(cd),
        delta_pct,
        _fmt_runtime(entry.get("lambda")),
        _fmt_runtime(entry.get("w0")),
        _fmt_runtime(entry.get("w1")),
        _fmt_runtime(entry.get("w2")),
        _fmt_runtime(entry.get("w3")),
        _fmt_runtime(entry.get("runtime_s")),
    ]


def _ranking_rows_from_analysis(analysis: dict[str, Any]) -> list[list[str]]:
    ranked = analysis["ranked_completed"]
    best_cd = ranked[0].cd if ranked else None
    rows: list[list[str]] = []
    for rank, row in enumerate(ranked, start=1):
        entry = {
            "sample_id": row.sample_id,
            "lambda": row.lambda_,
            "w0": row.w0,
            "w1": row.w1,
            "w2": row.w2,
            "w3": row.w3,
            "cd": row.cd,
            "runtime_s": row.wall_clock_s or row.execution_time_s,
        }
        rows.append(_ranking_entry_row(rank, entry, best_cd=best_cd))
    return rows


def _subset_ranking_rows(
    entries: list[dict[str, Any]],
    *,
    best_cd: float | None,
) -> list[list[str]]:
    rows: list[list[str]] = []
    for entry in entries:
        rank = entry.get("rank")
        rows.append(_ranking_entry_row(int(rank) if rank is not None else 0, entry, best_cd=best_cd))
    return rows


def _campaign_statistics_row(
    analysis: dict[str, Any],
    *,
    campaign_uuid: str | None,
    generated_at: str,
) -> list[str]:
    counts = analysis["counts"]
    cd_stats = analysis["cd_stats"]
    runtime_stats = analysis["runtime_stats"]
    iter_stats = analysis["iter_stats"]
    memory_stats = analysis["memory_stats"]
    yplus_stats = analysis["yplus_stats"]
    corr = analysis["correlations"]
    best = analysis["best_body"]
    worst_list = list(reversed(analysis["ranked_completed"]))
    worst = worst_list[0] if worst_list else None

    pearson_val = corr["pearson"].get("lambda")
    spearman_val = corr["spearman"].get("lambda")
    kendall_val = corr["kendall"].get("lambda")

    stopping = analysis.get("stopping_strategy") or {}
    stop_stats = stopping.get("stopping_iteration_stats")

    return [
        campaign_uuid or "",
        generated_at,
        str(counts.get("completed", 0)),
        str(counts.get("failed", 0)),
        _fmt_sci(cd_stats.mean if cd_stats else None),
        _fmt_sci(cd_stats.median if cd_stats else None),
        _fmt_sci(cd_stats.std if cd_stats else None),
        _fmt_sci(cd_stats.minimum if cd_stats else None),
        _fmt_sci(cd_stats.maximum if cd_stats else None),
        _fmt_runtime(runtime_stats.mean if runtime_stats else None),
        _fmt_runtime(iter_stats.mean if iter_stats else None),
        _fmt_runtime(memory_stats.mean if memory_stats else None),
        _fmt_runtime(yplus_stats.mean if yplus_stats else None),
        _fmt_corr(pearson_val),
        _fmt_corr(spearman_val),
        _fmt_corr(kendall_val),
        best.sample_id if best else "",
        worst.sample_id if worst else "",
        str(stopping.get("residual_converged_stops", 0)),
        str(stopping.get("max_iteration_stops", 0)),
        str(stopping.get("solver_crash_stops", 0)),
        _fmt_runtime(stop_stats.mean if stop_stats else None),
        _fmt_runtime(stop_stats.median if stop_stats else None),
        _fmt_pct(stopping.get("pct_over_800_iterations")),
    ]


ALL_RESULTS_HEADERS = [
    "Body_ID",
    "Status",
    "lambda",
    "w0",
    "w1",
    "w2",
    "w3",
    "Length",
    "Radius",
    "Cd",
    "Cl",
    "Iterations",
    "Actual_iterations_run",
    "Termination_reason",
    "Runtime_seconds",
    "CPU_seconds",
    "Peak_RAM_MB",
    "Cells",
    "Faces",
    "Points",
    "Wall_faces",
    "yPlus_average",
    "yPlus_max",
    "Converged",
    "Force_Converged",
    "Cd_mean_last100",
    "Cd_std_last100",
    "Cd_variation_percent",
    "Cd_max_deviation",
    "Cd_trend_percent",
    "Force_samples",
    "Solver",
    "Mesh_Level",
    "Run_UUID",
    "Retry_Count",
    "Campaign_UUID",
    "Configuration_Hash",
]

RANKING_HEADERS = [
    "Rank",
    "Body_ID",
    "Cd",
    "Delta_to_best_percent",
    "lambda",
    "w0",
    "w1",
    "w2",
    "w3",
    "Runtime",
]

CAMPAIGN_STATISTICS_HEADERS = [
    "Campaign_UUID",
    "Generation_date",
    "Completed_bodies",
    "Failed_bodies",
    "Average_Cd",
    "Median_Cd",
    "Std_Cd",
    "Best_Cd",
    "Worst_Cd",
    "Average_runtime",
    "Average_iterations",
    "Average_RAM",
    "Average_yPlus",
    "Pearson_lambda_Cd",
    "Spearman_lambda_Cd",
    "Kendall_lambda_Cd",
    "Best_body",
    "Worst_body",
    "Bodies_stopped_residual_convergence",
    "Bodies_stopped_max_iterations",
    "Bodies_terminated_solver_crash",
    "Average_stopping_iteration",
    "Median_stopping_iteration",
    "Pct_bodies_over_800_iterations",
]

FORCE_CONVERGENCE_HEADERS = [
    "Body_ID",
    "ResidualControl",
    "Force_Converged",
    "Cd_variation_percent",
    "Cd_trend_percent",
    "Cd_mean_last100",
    "Cd_std_last100",
    "Cd_max_deviation",
    "Force_samples",
    "Recommendation",
    ]


def _force_convergence_csv_rows(rows: list[CampaignRow]) -> list[list[str]]:
    row_by_id = {r.sample_id: r for r in rows}
    out: list[list[str]] = []
    for entry in force_convergence_table_rows(rows):
        row = row_by_id.get(entry["sample_id"])
        out.append(
            [
                entry["sample_id"],
                "PASS" if entry["residual_pass"] else "FAIL",
                _fmt_bool(entry["force_pass"]),
                _fmt_pct(entry.get("cd_variation_percent")),
                _fmt_pct(getattr(row, "cd_trend_percent", None) if row else None),
                _fmt_sci(getattr(row, "cd_mean_last100", None) if row else None),
                _fmt_sci(getattr(row, "cd_std_last100", None) if row else None),
                _fmt_sci(getattr(row, "cd_max_deviation", None) if row else None),
                _fmt_int(getattr(row, "force_samples", None) if row else None),
                entry["recommendation"],
            ]
        )
    return out


ENVIRONMENT_HEADERS = [
    "Campaign_UUID",
    "Captured_at",
    "CPU_model",
    "Physical_cores",
    "Logical_CPUs",
    "Host_Physical_RAM_GB",
    "WSL_Memory_Limit_GB",
    "WSL_Available_RAM_GB",
    "Swap_total_GB",
    "Total_RAM_GB",
    "Hostname",
    "Operating_system",
    "Kernel_version",
    "Architecture",
    "Python_version",
    "OpenFOAM_version",
    "MPI_implementation",
    "MPI_version",
    "Gmsh_version",
    "Git_commit",
    "Git_branch",
    "Git_state",
    "Project_version",
    "Working_directory",
    "Workers",
    "MPI_ranks_per_worker",
    "Mesh_level",
    "Solver",
    "Turbulence_model",
    "Campaign_creation_time",
    "Campaign_start_time",
    "Campaign_end_time",
    "Campaign_duration_s",
    "Campaign_duration_human",
]


def _environment_row(environment: dict[str, Any]) -> list[str]:
    machine = environment.get("machine", {})
    software = environment.get("software", {})
    campaign = environment.get("campaign", {})
    return [
        _safe_csv(campaign.get("campaign_uuid")),
        _safe_csv(environment.get("captured_at")),
        _safe_csv(machine.get("cpu_model")),
        _safe_csv(machine.get("physical_cores")),
        _safe_csv(machine.get("logical_cpus")),
        _safe_csv(machine.get("host_physical_ram_gb")),
        _safe_csv(machine.get("wsl_memory_limit_gb")),
        _safe_csv(machine.get("wsl_available_ram_gb")),
        _safe_csv(machine.get("swap_total_gb")),
        _safe_csv(machine.get("total_ram_gb")),
        _safe_csv(machine.get("hostname")),
        _safe_csv(machine.get("operating_system")),
        _safe_csv(machine.get("kernel_version")),
        _safe_csv(machine.get("architecture")),
        _safe_csv(software.get("python_version")),
        _safe_csv(software.get("openfoam_version")),
        _safe_csv(software.get("mpi_implementation")),
        _safe_csv(software.get("mpi_version")),
        _safe_csv(software.get("gmsh_version")),
        _safe_csv(software.get("git_commit")),
        _safe_csv(software.get("git_branch")),
        _safe_csv(software.get("git_state")),
        _safe_csv(software.get("project_version")),
        _safe_csv(software.get("working_directory")),
        _safe_csv(campaign.get("workers")),
        _safe_csv(campaign.get("mpi_ranks_per_worker")),
        _safe_csv(campaign.get("mesh_level")),
        _safe_csv(campaign.get("solver")),
        _safe_csv(campaign.get("turbulence_model")),
        _safe_csv(campaign.get("campaign_creation_time")),
        _safe_csv(campaign.get("campaign_start_time")),
        _safe_csv(campaign.get("campaign_end_time")),
        _safe_csv(campaign.get("campaign_duration_s")),
        _safe_csv(campaign.get("campaign_duration_human")),
    ]


def _safe_csv(value: Any) -> str:
    if value is None:
        return ""
    return str(value)


def export_campaign_csv(
    db_path: Path | None = None,
    *,
    conn: sqlite3.Connection | None = None,
    rows: list[CampaignRow] | None = None,
    analysis: dict[str, Any] | None = None,
    environment: dict[str, Any] | None = None,
    output_dir: Path = CSV_OUTPUT_DIR,
) -> dict[str, Path]:
    """Export campaign results to CSV files under ``results/csv/``.

    Reads from the production database and reuses :func:`analyze_campaign` for
    rankings and statistics. Safe to call from report generation hooks.
    """
    close_conn = False
    if conn is None:
        conn = connect(db_path) if db_path else connect()
        close_conn = True
    try:
        if rows is None:
            rows = fetch_campaign_rows(conn)
        if analysis is None:
            analysis = analyze_campaign(rows)
        if not analysis["ranked_completed"]:
            raise RuntimeError("No completed simulations in database — cannot export CSV.")

        run_meta = _fetch_run_metadata(conn)
        campaign_uuid = _resolve_campaign_uuid(conn, run_meta)
        generated_at = utc_now_iso()
        best_cd = analysis["best_body"].cd if analysis["best_body"] else None

        paths: dict[str, Path] = {}
        paths["all_results"] = _write_csv(
            output_dir / "all_results.csv",
            ALL_RESULTS_HEADERS,
            _all_results_rows(rows, run_meta, campaign_uuid),
        )
        paths["ranking"] = _write_csv(
            output_dir / "ranking.csv",
            RANKING_HEADERS,
            _ranking_rows_from_analysis(analysis),
        )
        paths["top10"] = _write_csv(
            output_dir / "top10.csv",
            RANKING_HEADERS,
            _subset_ranking_rows(analysis["top_10"], best_cd=best_cd),
        )
        paths["top20"] = _write_csv(
            output_dir / "top20.csv",
            RANKING_HEADERS,
            _subset_ranking_rows(analysis["top_20"], best_cd=best_cd),
        )
        paths["worst20"] = _write_csv(
            output_dir / "worst20.csv",
            RANKING_HEADERS,
            _subset_ranking_rows(analysis["worst_20"], best_cd=best_cd),
        )
        fc_rows = _force_convergence_csv_rows(rows)
        if fc_rows:
            paths["force_convergence"] = _write_csv(
                output_dir / "force_convergence.csv",
                FORCE_CONVERGENCE_HEADERS,
                fc_rows,
            )
        paths["campaign_statistics"] = _write_csv(
            output_dir / "campaign_statistics.csv",
            CAMPAIGN_STATISTICS_HEADERS,
            [_campaign_statistics_row(analysis, campaign_uuid=campaign_uuid, generated_at=generated_at)],
        )
        if environment is None:
            from campaign.environment_store import load_environment_for_report
            from campaign.manifest import load_manifest

            manifest = load_manifest(MANIFEST_PATH) if MANIFEST_PATH.exists() else {}
            if manifest:
                environment = load_environment_for_report(conn, manifest)
        if environment:
            paths["environment"] = _write_csv(
                output_dir / "environment.csv",
                ENVIRONMENT_HEADERS,
                [_environment_row(environment)],
            )
        return paths
    finally:
        if close_conn:
            conn.close()
