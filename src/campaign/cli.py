"""Campaign management CLI."""

from __future__ import annotations

import os
import sys

_THIS_DIR = os.path.dirname(os.path.abspath(__file__))
_SRC_ROOT = os.path.dirname(_THIS_DIR)

# Direct execution (`python src/campaign/cli.py`) puts src/campaign
# on sys.path. Remove it before importing argparse because
# campaign/warnings.py would otherwise shadow Python's stdlib warnings.
sys.path = [
    entry
    for entry in sys.path
    if os.path.abspath(entry or os.curdir) != _THIS_DIR
]

if _SRC_ROOT not in sys.path:
    sys.path.insert(0, _SRC_ROOT)

import argparse
import json
from pathlib import Path
from typing import Any


from campaign.constants import (
    DASHBOARD_REFRESH_INTERVAL_S,
    DEFAULT_DB_PATH,
    HEALTH_SAMPLE_INTERVAL_S,
    MANIFEST_PATH,
    TOTAL_BODIES,
)
from campaign.manifest import init_manifest, load_manifest, save_manifest
from campaign.resume import refresh_campaign_state, resume_plan_summary
from campaign.runner import resume_campaign, run_campaign
from campaign.scheduler import default_worker_configs
from campaign.validate import validate_campaign, validation_report
from reporting.production_db import count_by_status, prepare_database


def add_monitor_args(parser: argparse.ArgumentParser) -> None:
    parser.add_argument(
        "--health-interval",
        type=float,
        default=HEALTH_SAMPLE_INTERVAL_S,
        help=f"Health sampling interval in seconds (default: {HEALTH_SAMPLE_INTERVAL_S:g})",
    )
    parser.add_argument(
        "--dashboard-interval",
        type=float,
        default=DASHBOARD_REFRESH_INTERVAL_S,
        help=f"Terminal dashboard refresh interval in seconds (default: {DASHBOARD_REFRESH_INTERVAL_S:g})",
    )
    parser.add_argument(
        "--no-dashboard",
        action="store_true",
        help="Disable live terminal dashboard (keep health monitoring)",
    )


def add_worker_args(parser: argparse.ArgumentParser) -> None:
    parser.add_argument(
        "--workers",
        type=int,
        default=2,
        help="Number of simultaneous OpenFOAM workers (default: 2)",
    )
    parser.add_argument(
        "--cores-per-worker",
        type=int,
        default=6,
        help="MPI processes and CPU cores per worker (default: 6)",
    )
    parser.add_argument(
        "--retry-failed",
        action="store_true",
        help="Include FAILED bodies in the resume queue",
    )
    parser.add_argument(
        "--stop-after-completed",
        type=int,
        default=None,
        help="Gracefully pause after N completed bodies (active workers finish first)",
    )
    parser.add_argument(
        "--required-body",
        action="append",
        default=[],
        help="Body IDs that must complete successfully before graceful stop (repeatable)",
    )


def cmd_init(args: argparse.Namespace) -> int:
    from campaign.environment_store import ensure_campaign_environment

    manifest = init_manifest(
        campaign_name=args.name,
        total_bodies=args.total_bodies,
        path=args.manifest,
        overwrite=args.overwrite,
    )
    conn = prepare_database(args.db, import_profiles_flag=True, sync_results_flag=False)
    try:
        ensure_campaign_environment(
            conn,
            manifest,
            manifest_path=args.manifest,
            workers=args.workers,
            mpi_ranks_per_worker=args.cores_per_worker,
        )
    finally:
        conn.close()
    print(f"Initialized campaign manifest at {args.manifest}")
    print(f"Initialized database at {args.db}")
    return 0


def cmd_validate(args: argparse.Namespace) -> int:
    result = validate_campaign(
        args.db,
        manifest_path=args.manifest,
        expected_bodies=args.total_bodies,
        workers=args.workers,
        mpi_ranks_per_worker=args.cores_per_worker,
    )
    print(validation_report(result))
    return 0 if result.ok else 1


def cmd_status(args: argparse.Namespace) -> int:
    manifest = load_manifest(args.manifest)
    conn = prepare_database(args.db, sync_results_flag=True)
    try:
        counts = count_by_status(conn)
    finally:
        conn.close()
    worker_configs = default_worker_configs(
        workers=args.workers,
        cores_per_worker=args.cores_per_worker,
    )
    payload = {
        "manifest": manifest,
        "database_counts": counts,
        "scheduler": {
            "workers": args.workers,
            "cores_per_worker": args.cores_per_worker,
            "worker_cpu_map": {
                cfg.worker_id: list(cfg.cpu_affinity) for cfg in worker_configs
            },
            "retry_failed": args.retry_failed,
        },
    }
    print(json.dumps(payload, indent=2, default=str))
    return 0


def _run_kwargs(args: argparse.Namespace) -> dict[str, Any]:
    required = set(args.required_body) if args.required_body else None
    return {
        "manifest_path": args.manifest,
        "max_bodies": args.max_bodies,
        "workers": args.workers,
        "cores_per_worker": args.cores_per_worker,
        "retry_failed": args.retry_failed,
        "stop_after_completed": args.stop_after_completed,
        "required_bodies": required,
        "health_interval_s": args.health_interval,
        "dashboard_interval_s": args.dashboard_interval,
        "enable_dashboard": not args.no_dashboard,
    }


def cmd_run(args: argparse.Namespace) -> int:
    if not args.manifest.exists():
        init_args = argparse.Namespace(**vars(args))
        init_args.name = "Rocket Drone 200-Body DOE"
        init_args.overwrite = False
        cmd_init(init_args)

    result = run_campaign(args.db, **_run_kwargs(args))
    print(json.dumps(result, indent=2, default=str))
    if result.get("interrupted"):
        return 130
    return 0


def cmd_resume(args: argparse.Namespace) -> int:
    force = set(args.force_body) if args.force_body else None
    kwargs = _run_kwargs(args)
    kwargs["force_bodies"] = force
    result = resume_campaign(args.db, **kwargs)
    print(json.dumps(result, indent=2, default=str))
    return 0 if not result.get("interrupted") else 130


def cmd_plan(args: argparse.Namespace) -> int:
    force = set(args.force_body) if args.force_body else None
    plan = refresh_campaign_state(
        args.db,
        manifest_path=args.manifest,
        force_bodies=force,
        retry_failed=args.retry_failed,
        workers=args.workers,
    )
    summary = resume_plan_summary(plan)
    summary["scheduler"] = {
        "workers": args.workers,
        "cores_per_worker": args.cores_per_worker,
        "retry_failed": args.retry_failed,
    }
    print(json.dumps(summary, indent=2))
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Rocket Drone production DOE campaign manager (parallel worker upgrade)."
    )
    parser.add_argument("--db", type=Path, default=DEFAULT_DB_PATH)
    parser.add_argument("--manifest", type=Path, default=MANIFEST_PATH)
    parser.add_argument("--total-bodies", type=int, default=TOTAL_BODIES)
    add_worker_args(parser)
    add_monitor_args(parser)
    sub = parser.add_subparsers(dest="command", required=True)

    p_init = sub.add_parser("init", help="Create campaign manifest and initialize database")
    p_init.add_argument("--name", default="Rocket Drone 200-Body DOE")
    p_init.add_argument("--overwrite", action="store_true")
    p_init.set_defaults(func=cmd_init)

    p_validate = sub.add_parser("validate", help="Dry-run validation before production launch")
    p_validate.set_defaults(func=cmd_validate)

    p_status = sub.add_parser("status", help="Show manifest and database status")
    p_status.set_defaults(func=cmd_status)

    p_run = sub.add_parser("run", help="Run campaign (auto-resume unfinished bodies)")
    p_run.add_argument("--max-bodies", type=int, default=None)
    p_run.set_defaults(func=cmd_run)

    p_resume = sub.add_parser("resume", help="Resume interrupted or paused campaign")
    p_resume.add_argument("--force-body", action="append", default=[])
    p_resume.add_argument("--max-bodies", type=int, default=None)
    p_resume.set_defaults(func=cmd_resume)

    p_plan = sub.add_parser("plan", help="Show resume plan without running")
    p_plan.add_argument("--force-body", action="append", default=[])
    p_plan.set_defaults(func=cmd_plan)

    args = parser.parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    raise SystemExit(main())
