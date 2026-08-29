"""Main orchestrator for automatic final engineering report generation."""

from __future__ import annotations

import argparse
import json
import shutil
import sys
from datetime import datetime, timezone
from pathlib import Path

SRC_ROOT = Path(__file__).resolve().parents[1]
if str(SRC_ROOT) not in sys.path:
    sys.path.insert(0, str(SRC_ROOT))

from reporting.csv_export import export_campaign_csv
from reporting.analysis import analyze_campaign
from reporting.figures import FIGURES_DIR, generate_all_figures
from reporting.markdown_report import render_markdown
from reporting.pdf_export import render_pdf
from campaign.constants import (
    CASES_ROOT,
    DEFAULT_DB_PATH,
    PROFILES_ROOT,
    REPO_ROOT,
    RESULTS_ROOT,
)

from reporting.production_db import (
    EXPECTED_DOE_BODIES,
    fetch_campaign_rows,
    get_campaign_metadata,
    is_doe_complete,
    prepare_database,
    set_campaign_metadata,
    utc_now_iso,
)

BEST_BODY_ROOT = REPO_ROOT / "best_body"

REPORT_MD = RESULTS_ROOT / "final_report.md"
REPORT_PDF = RESULTS_ROOT / "final_report.pdf"


def _metadata_from_db(conn) -> dict[str, str | None]:
    return {
        "database_version": get_campaign_metadata(conn, "database_version"),
        "git_commit": get_campaign_metadata(conn, "git_commit"),
        "openfoam_version": get_campaign_metadata(conn, "openfoam_version"),
        "generated_at": utc_now_iso(),
    }


def export_best_body(best_id: str) -> Path:
    """Copy optimal design artifacts to ``best_body/Body_XXXX/``."""
    dest = BEST_BODY_ROOT / best_id
    if dest.exists():
        shutil.rmtree(dest)
    dest.mkdir(parents=True)

    profile_src = PROFILES_ROOT / best_id
    if profile_src.is_dir():
        for name in ("profile.csv", "profile.png", "metadata.json"):
            src = profile_src / name
            if src.exists():
                shutil.copy2(src, dest / name)

    results_src = RESULTS_ROOT / best_id
    if results_src.is_dir():
        for name in ("summary.json", "mesh_stats.json", "force_series.csv"):
            src = results_src / name
            if src.exists():
                shutil.copy2(src, dest / name)

    case_src = CASES_ROOT / best_id
    if case_src.is_dir():
        for name in ("log.checkMesh", "log.foamRun"):
            src = case_src / name
            if src.exists():
                shutil.copy2(src, dest / name)

    manifest = {
        "body_id": best_id,
        "exported_at": utc_now_iso(),
        "source_profile": str(profile_src.relative_to(REPO_ROOT)) if profile_src.exists() else None,
        "source_results": str(results_src.relative_to(REPO_ROOT)) if results_src.exists() else None,
    }
    (dest / "manifest.json").write_text(json.dumps(manifest, indent=2) + "\n", encoding="utf-8")
    return dest


def generate_final_report(
    db_path: Path = DEFAULT_DB_PATH,
    *,
    require_complete: bool = True,
    expected_bodies: int = EXPECTED_DOE_BODIES,
    min_completed: int | None = None,
) -> dict[str, Path]:
    """Generate markdown report, PDF, and figures from the production database.

    Called automatically when the 200-body DOE batch completes, or manually via CLI.

    Returns paths to generated artifacts.
    """
    conn = prepare_database(db_path)
    try:
        if require_complete and not is_doe_complete(conn, expected_bodies=expected_bodies):
            completed = conn.execute(
                "SELECT COUNT(*) AS n FROM master_samples WHERE status = 'COMPLETED'"
            ).fetchone()["n"]

            if require_complete and not is_doe_complete(
                conn,
                expected_bodies=expected_bodies,
            ):
                raise RuntimeError(
                    f"DOE incomplete: {completed}/{expected_bodies} bodies COMPLETED. "
                    "Run with --force to generate a partial report."
                )

            if (
                not require_complete
                and min_completed is not None
                and completed < min_completed
            ):
                raise RuntimeError(
                    f"Only {completed}/{expected_bodies} bodies are COMPLETED; "
                    f"minimum requested is {min_completed}."
                )

        rows = fetch_campaign_rows(conn)
        analysis = analyze_campaign(rows)
        if not analysis["ranked_completed"]:
            raise RuntimeError("No completed simulations in database — cannot generate report.")

        figures = generate_all_figures(rows, analysis, FIGURES_DIR)
        metadata = _metadata_from_db(conn)

        from campaign.constants import MANIFEST_PATH
        from campaign.environment_store import (
            finalize_campaign_environment,
            load_environment_for_report,
        )
        from campaign.manifest import load_manifest

        manifest = load_manifest(MANIFEST_PATH) if MANIFEST_PATH.exists() else {}
        if manifest and is_doe_complete(conn, expected_bodies=expected_bodies):
            finalize_campaign_environment(conn, manifest, manifest_path=MANIFEST_PATH)
            manifest = load_manifest(MANIFEST_PATH)
        environment = (
            load_environment_for_report(conn, manifest)
            if manifest
            else None
        )

        md_text = render_markdown(
            rows,
            analysis,
            figures,
            metadata=metadata,
            environment=environment,
        )
        REPORT_MD.parent.mkdir(parents=True, exist_ok=True)
        REPORT_MD.write_text(md_text, encoding="utf-8")

        render_pdf(rows, analysis, figures, metadata, REPORT_PDF, environment=environment)

        csv_paths = export_campaign_csv(
            conn=conn,
            rows=rows,
            analysis=analysis,
            environment=environment,
        )
        for label, csv_path in csv_paths.items():
            print(f"Wrote CSV {label}: {csv_path}")

        best = analysis["best_body"]
        if best:
            export_best_body(best.sample_id)

        set_campaign_metadata(conn, "last_report_generated_at", utc_now_iso())
        set_campaign_metadata(
            conn,
            "last_report_md",
            str(REPORT_MD.relative_to(REPO_ROOT)),
        )
        set_campaign_metadata(conn, "best_body_id", best.sample_id if best else "")
        conn.commit()

        print(f"Wrote {REPORT_MD}")
        print(f"Wrote {REPORT_PDF}")
        print(f"Figures in {FIGURES_DIR}")
        if best:
            print(f"Exported best body to {BEST_BODY_ROOT / best.sample_id}")

        return {
            "markdown": REPORT_MD,
            "pdf": REPORT_PDF,
            "figures_dir": FIGURES_DIR,
            "csv_dir": csv_paths["all_results"].parent,
            "csv_files": csv_paths,
        }
    finally:
        conn.close()


def on_doe_batch_complete(db_path: Path = DEFAULT_DB_PATH) -> dict[str, Path] | None:
    """Hook for batch runner — generate report only when all 200 bodies are COMPLETED."""
    conn = prepare_database(db_path, sync_results_flag=True)
    try:
        if is_doe_complete(conn):
            print("DOE complete — generating final engineering report...")
            conn.close()
            return generate_final_report(db_path, require_complete=True)
        completed = conn.execute(
            "SELECT COUNT(*) AS n FROM master_samples WHERE status = 'COMPLETED'"
        ).fetchone()["n"]
        print(f"DOE not yet complete ({completed}/{EXPECTED_DOE_BODIES}) — skipping report.")
        return None
    finally:
        if conn:
            conn.close()


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Generate final engineering report from production database (Part 13)."
    )
    parser.add_argument("--db", type=Path, default=DEFAULT_DB_PATH, help="Production SQLite path")
    sub = parser.add_subparsers(dest="command", required=True)

    sub.add_parser("init-db", help="Initialize database and import profiles")

    p_sync = sub.add_parser("sync", help="Sync results/ into database")
    p_sync.add_argument("--no-profiles", action="store_true")

    sub.add_parser("figures", help="Generate figures only")
    sub.add_parser("report", help="Generate markdown report only")
    sub.add_parser("pdf", help="Generate PDF only")

    p_all = sub.add_parser("all", help="Full pipeline: sync → figures → report → pdf")
    p_all.add_argument(
        "--force",
        action="store_true",
        help="Generate partial report even if DOE incomplete",
    )
    p_all.add_argument(
        "--min-completed",
        type=int,
        default=None,
        help="Minimum completed bodies required with --force",
    )

    p_seed = sub.add_parser("seed-probe", help="Seed DB from 5-body SST validation probe (smoke test)")
    p_hook = sub.add_parser("check-complete", help="Generate report if DOE is complete (batch hook)")
    p_hook.add_argument("--force", action="store_true")

    args = parser.parse_args(argv)
    db_path: Path = args.db

    if args.command == "seed-probe":
        conn = prepare_database(db_path, sync_results_flag=False)
        from reporting.production_db import seed_from_validation_probe

        n = seed_from_validation_probe(conn)
        conn.close()
        print(f"Seeded {n} probe bodies into {db_path} (smoke-test data)")
        return 0

    if args.command == "init-db":
        conn = prepare_database(db_path, sync_results_flag=False)
        n = conn.execute("SELECT COUNT(*) AS n FROM master_samples").fetchone()["n"]
        conn.close()
        print(f"Initialized {db_path} with {n} master samples")
        return 0

    if args.command == "sync":
        conn = prepare_database(db_path, import_profiles_flag=not args.no_profiles)
        n = conn.execute("SELECT COUNT(*) AS n FROM simulation_runs").fetchone()["n"]
        conn.close()
        print(f"Synced {n} simulation runs into {db_path}")
        return 0

    if args.command in ("figures", "report", "pdf", "all", "check-complete"):
        force = getattr(args, "force", False)
        if args.command == "check-complete":
            if force:
                generate_final_report(db_path, require_complete=False)
            else:
                result = on_doe_batch_complete(db_path)
                if result is None:
                    return 1
            return 0

        require = not force and args.command == "all"
        min_completed = getattr(args, "min_completed", None)

        conn = prepare_database(db_path)
        rows = fetch_campaign_rows(conn)
        analysis = analyze_campaign(rows)
        metadata = _metadata_from_db(conn)
        conn.close()

        if args.command == "figures":
            generate_all_figures(rows, analysis, FIGURES_DIR)
            return 0
        if args.command == "report":
            if not any(FIGURES_DIR.glob("*.png")):
                figures = generate_all_figures(rows, analysis, FIGURES_DIR)
            else:
                figures = {
                    "cd_histogram": FIGURES_DIR / "fig01_cd_histogram.png",
                    "cd_cdf": FIGURES_DIR / "fig02_cd_cdf.png",
                    "cd_vs_lambda": FIGURES_DIR / "fig03_cd_vs_lambda.png",
                    "cd_vs_weights": FIGURES_DIR / "fig04_cd_vs_cst_weights.png",
                    "correlation_matrix": FIGURES_DIR / "fig05_correlation_matrix.png",
                    "correlation_heatmap": FIGURES_DIR / "fig06_correlation_heatmap.png",
                    "scatter_matrix": FIGURES_DIR / "fig07_scatter_matrix.png",
                    "pairplot": FIGURES_DIR / "fig08_pairplot.png",
                    "runtime_statistics": FIGURES_DIR / "fig09_runtime_statistics.png",
                    "convergence_statistics": FIGURES_DIR / "fig10_convergence_statistics.png",
                    "stopping_iterations_histogram": FIGURES_DIR / "fig13_stopping_iterations_histogram.png",
                    "ranking_plot": FIGURES_DIR / "fig11_ranking_plot.png",
                    "engineering_dashboard": FIGURES_DIR / "fig12_engineering_dashboard.png",
                }
            md = render_markdown(rows, analysis, figures, metadata=metadata)
            REPORT_MD.parent.mkdir(parents=True, exist_ok=True)
            REPORT_MD.write_text(md, encoding="utf-8")
            print(f"Wrote {REPORT_MD}")
            return 0
        if args.command == "pdf":
            figures = {
                k: FIGURES_DIR / fn
                for k, fn in {
                    "cd_histogram": "fig01_cd_histogram.png",
                    "cd_cdf": "fig02_cd_cdf.png",
                    "cd_vs_lambda": "fig03_cd_vs_lambda.png",
                    "cd_vs_weights": "fig04_cd_vs_cst_weights.png",
                    "correlation_matrix": "fig05_correlation_matrix.png",
                    "correlation_heatmap": "fig06_correlation_heatmap.png",
                    "scatter_matrix": "fig07_scatter_matrix.png",
                    "pairplot": "fig08_pairplot.png",
                    "runtime_statistics": "fig09_runtime_statistics.png",
                    "convergence_statistics": "fig10_convergence_statistics.png",
                    "stopping_iterations_histogram": "fig13_stopping_iterations_histogram.png",
                    "ranking_plot": "fig11_ranking_plot.png",
                    "engineering_dashboard": "fig12_engineering_dashboard.png",
                }.items()
            }
            render_pdf(rows, analysis, figures, metadata, REPORT_PDF)
            print(f"Wrote {REPORT_PDF}")
            return 0

        generate_final_report(
            db_path,
            require_complete=require,
            min_completed=min_completed,
        )
        return 0

    return 1


if __name__ == "__main__":
    raise SystemExit(main())
