"""Production DOE campaign runner — mesh, solve, checkpoint, resume."""

from __future__ import annotations

import json
import math
import os
import shutil
import signal
import subprocess
import sys
import threading
import time
import uuid
from pathlib import Path
from typing import Any

from campaign.backup import backup_database
from campaign.checkpoint import create_checkpoint
from campaign.control import graceful_stop_conditions_met, load_graceful_stop_config
from campaign.constants import (
    CASES_ROOT,
    DASHBOARD_REFRESH_INTERVAL_S,
    DEFAULT_DB_PATH,
    HEALTH_SAMPLE_INTERVAL_S,
    MANIFEST_PATH,
    MESH_LEVEL,
    PROFILES_ROOT,
    REPO_ROOT,
    RESULTS_ROOT,
    SOLVER,
    SRC_ROOT,
    TEMPLATE_CASE,
    TOTAL_BODIES,
    TURBULENCE_MODEL,
)
from campaign.db_sync import db_write_lock
from campaign.eta import compute_eta
from campaign.fingerprints import aggregate_config_hash, compute_body_fingerprints
from campaign.manifest import (
    init_manifest,
    load_manifest,
    save_manifest,
    set_campaign_status,
    sync_manifest_from_db,
    update_eta,
)
from campaign.openfoam_parallel import (
    cleanup_processor_dirs,
    wrap_with_affinity,
    write_decompose_par_dict,
)
from campaign.postprocess import build_summary, of_env, run
from campaign.recovery import mark_interrupted, write_run_state
from campaign.environment_store import ensure_campaign_environment
from campaign.retry import (
    merge_retry_into_summary,
    record_attempt_failure,
    record_attempt_start,
    sync_retry_to_manifest,
    write_retry_to_summary,
)
from campaign.resume import build_resume_plan, resume_plan_summary
from campaign.scheduler import (
    CampaignScheduler,
    active_body_ids,
    build_graceful_shutdown_summary,
    default_worker_configs,
    print_graceful_shutdown_summary,
)
from campaign.validate import validate_campaign, validation_report
from reporting.generate import on_doe_batch_complete
from reporting.production_db import (
    connect,
    count_by_status,
    fetch_campaign_rows,
    prepare_database,
    record_config_fingerprints,
    record_simulation_run,
    utc_now_iso,
)

GMSH = os.environ.get("GMSH", "gmsh")

_scheduler: CampaignScheduler | None = None
_interrupt_event = threading.Event()


def _handle_interrupt(signum: int, frame: Any) -> None:
    _interrupt_event.set()
    if _scheduler is not None:
        _scheduler.request_shutdown()
        for body_id in active_body_ids(_scheduler):
            mark_interrupted(body_id, reason=f"Signal {signum} received")


def write_force_coeffs(path: Path, length: float, r_max: float) -> None:
    aref = math.pi * r_max * r_max
    cof = length / 2.0
    text = f"""/*--------------------------------*- C++ -*----------------------------------*\\
  =========                 |
  \\\\      /  F ield         | OpenFOAM: The Open Source CFD Toolbox
   \\\\    /   O peration     | Website:  https://openfoam.org
    \\\\  /    A nd           | Version:  13
     \\\\/     M anipulation  |
\\*---------------------------------------------------------------------------*/
FoamFile
{{
    format      ascii;
    class       dictionary;
    object      forceCoeffsIncompressible;
}}
// * * * * * * * * * * * * * * * * * * * * * * * * * * * * * * * * * * * * * //

#includeEtc "caseDicts/functions/forces/forceCoeffsIncompressible.cfg"

patches     (rocket_wall);

magUInf     138.89;
lRef        {length};
Aref        {aref};

CofR        ({cof} 0 0);
liftDir     (0 0 1);
dragDir     (1 0 0);
pitchAxis   (0 1 0);

// ************************************************************************* //
"""
    path.write_text(text, encoding="utf-8")


def load_body_metadata(body_id: str) -> dict[str, Any]:
    meta_path = PROFILES_ROOT / body_id / "metadata.json"
    return json.loads(meta_path.read_text(encoding="utf-8"))


def prepare_case(body_id: str, *, clean: bool = False) -> Path:
    """Copy frozen production template and apply per-body geometry references only."""
    meta = load_body_metadata(body_id)
    case_dir = CASES_ROOT / body_id
    if clean and case_dir.exists():
        shutil.rmtree(case_dir)
    if not case_dir.exists():
        case_dir.mkdir(parents=True)
        for name in ("0", "constant", "system"):
            shutil.copytree(TEMPLATE_CASE / name, case_dir / name)

    cleanup_processor_dirs(case_dir)
    write_force_coeffs(
        case_dir / "system" / "forceCoeffsIncompressible",
        length=float(meta["length"]),
        r_max=float(meta.get("R", meta.get("r_max", 0.07))),
    )
    return case_dir


def mesh_body(
    body_id: str,
    env: dict[str, str],
    *,
    cpu_affinity: tuple[int, ...] | None = None,
    proc_registry: list | None = None,
    interrupt_event: threading.Event | None = None,
) -> Path:
    case_dir = CASES_ROOT / body_id
    profile_csv = PROFILES_ROOT / body_id / "profile.csv"
    geo = case_dir / "profile.geo"
    subprocess.check_call(
        wrap_with_affinity(
            [
                sys.executable,
                str(SRC_ROOT / "csv_to_geo.py"),
                str(profile_csv),
                "--mesh-level",
                MESH_LEVEL,
                "-o",
                str(geo),
            ],
            cpu_affinity,
        ),
        cwd=str(REPO_ROOT),
    )
    env2 = dict(env)
    env2["GMSH"] = GMSH
    rc = run(
        wrap_with_affinity(
            ["bash", str(REPO_ROOT / "scripts" / "importMesh.sh"), str(case_dir)],
            cpu_affinity,
        ),
        cwd=REPO_ROOT,
        log_path=case_dir / "log.importMesh",
        env=env2,
        proc_registry=proc_registry,
        interrupt_event=interrupt_event,
    )
    if rc != 0:
        raise RuntimeError(f"importMesh failed for {body_id}")
    return case_dir


def run_solver(
    body_id: str,
    env: dict[str, str],
    *,
    mpi_procs: int = 6,
    cpu_affinity: tuple[int, ...] | None = None,
    proc_registry: list | None = None,
    interrupt_event: threading.Event | None = None,
) -> tuple[int, float, Path, Path]:
    case_dir = CASES_ROOT / body_id
    foam_log = case_dir / "log.foamRun"
    time_bin = "/usr/bin/time" if Path("/usr/bin/time").exists() else None
    t0 = time.time()

    if mpi_procs > 1:
        write_decompose_par_dict(case_dir, mpi_procs)
        rc = run(
            wrap_with_affinity(["decomposePar", "-force"], cpu_affinity),
            cwd=case_dir,
            log_path=case_dir / "log.decomposePar",
            env=env,
            proc_registry=proc_registry,
            interrupt_event=interrupt_event,
        )
        if rc != 0:
            wall = time.time() - t0
            return rc, wall, foam_log, case_dir / "log.yPlus"

        solver_cmd = ["mpirun", "-np", str(mpi_procs), "foamRun", "-parallel"]
        if time_bin:
            solver_cmd = [time_bin, "-v", *solver_cmd]
        rc = run(
            wrap_with_affinity(solver_cmd, cpu_affinity),
            cwd=case_dir,
            log_path=foam_log,
            env=env,
            proc_registry=proc_registry,
            interrupt_event=interrupt_event,
        )
        wall = time.time() - t0

        if rc == 0:
            run(
                wrap_with_affinity(["reconstructPar"], cpu_affinity),
                cwd=case_dir,
                log_path=case_dir / "log.reconstructPar",
                env=env,
                proc_registry=proc_registry,
                interrupt_event=interrupt_event,
            )
    else:
        cmd = [time_bin, "-v", "foamRun"] if time_bin else ["foamRun"]
        rc = run(
            wrap_with_affinity(cmd, cpu_affinity),
            cwd=case_dir,
            log_path=foam_log,
            env=env,
            proc_registry=proc_registry,
            interrupt_event=interrupt_event,
        )
        wall = time.time() - t0

    ylog = case_dir / "log.yPlus"
    run(
        wrap_with_affinity(
            [
                "foamPostProcess",
                "-solver",
                SOLVER,
                "-func",
                "yPlus",
                "-latestTime",
            ],
            cpu_affinity,
        ),
        cwd=case_dir,
        log_path=ylog,
        env=env,
        proc_registry=proc_registry,
        interrupt_event=interrupt_event,
    )
    return rc, wall, foam_log, ylog


def run_single_body(
    body_id: str,
    *,
    campaign_uuid: str,
    db_path: Path = DEFAULT_DB_PATH,
    clean_restart: bool = False,
    is_retry: bool | None = None,
    worker_id: int | None = None,
    mpi_procs: int = 6,
    cpu_affinity: tuple[int, ...] | None = None,
    interrupt_event: threading.Event | None = None,
    proc_registry: list | None = None,
) -> dict[str, Any]:
    """Execute one production body end-to-end with UUID tracking and fingerprints."""
    if is_retry is None:
        is_retry = clean_restart
    if interrupt_event is not None and interrupt_event.is_set():
        raise RuntimeError(f"Campaign interrupted before starting {body_id}")

    simulation_uuid = str(uuid.uuid4())
    start_time = utc_now_iso()
    results_dir = RESULTS_ROOT / body_id
    results_dir.mkdir(parents=True, exist_ok=True)

    with db_write_lock():
        conn = connect(db_path)
        try:
            retry_info = record_attempt_start(conn, body_id, is_retry=is_retry)
        finally:
            conn.close()
    write_retry_to_summary(body_id, retry_info)

    write_run_state(
        body_id,
        simulation_uuid=simulation_uuid,
        campaign_uuid=campaign_uuid,
        status="RUNNING",
        stage="prepare",
        extra={"worker_id": worker_id, "mpi_procs": mpi_procs},
    )

    with db_write_lock():
        conn = connect(db_path)
        try:
            conn.execute(
                "UPDATE master_samples SET status = 'RUNNING' WHERE sample_id = ?",
                (body_id,),
            )
            conn.commit()
        finally:
            conn.close()

    env = of_env()
    try:
        prepare_case(body_id, clean=clean_restart)
        write_run_state(
            body_id,
            simulation_uuid=simulation_uuid,
            campaign_uuid=campaign_uuid,
            status="RUNNING",
            stage="mesh",
            extra={"worker_id": worker_id, "mpi_procs": mpi_procs},
        )
        case_dir = mesh_body(
            body_id,
            env,
            cpu_affinity=cpu_affinity,
            proc_registry=proc_registry,
            interrupt_event=interrupt_event,
        )
        write_run_state(
            body_id,
            simulation_uuid=simulation_uuid,
            campaign_uuid=campaign_uuid,
            status="RUNNING",
            stage="run",
            extra={"worker_id": worker_id, "mpi_procs": mpi_procs},
        )
        rc, wall, foam_log, ylog = run_solver(
            body_id,
            env,
            mpi_procs=mpi_procs,
            cpu_affinity=cpu_affinity,
            proc_registry=proc_registry,
            interrupt_event=interrupt_event,
        )

        if interrupt_event is not None and interrupt_event.is_set():
            mark_interrupted(body_id, reason="Campaign interrupted during solver")
            raise RuntimeError(f"Campaign interrupted during {body_id}")

        profile_dir = PROFILES_ROOT / body_id
        fingerprints = compute_body_fingerprints(profile_dir=profile_dir, case_dir=case_dir)
        config_hash = aggregate_config_hash(fingerprints)

        summary = build_summary(
            body_id=body_id,
            simulation_uuid=simulation_uuid,
            campaign_uuid=campaign_uuid,
            case_dir=case_dir,
            results_dir=results_dir,
            foam_log=foam_log,
            yplus_log=ylog,
            returncode=rc,
            wall_clock_s=wall,
            start_time=start_time,
            config_hash=config_hash,
            fingerprints=fingerprints,
            mesh_level=MESH_LEVEL,
            turbulence_model=TURBULENCE_MODEL,
            solver=SOLVER,
        )
        summary["worker_id"] = worker_id
        summary["mpi_procs"] = mpi_procs
        merge_retry_into_summary(summary, retry_info)

        if summary.get("status") == "FAILED":
            reason = summary.get("error_message") or f"Solver exit code {summary.get('returncode')}"
            with db_write_lock():
                conn = connect(db_path)
                try:
                    retry_info = record_attempt_failure(
                        conn,
                        body_id,
                        reason=reason,
                        exit_code=summary.get("returncode"),
                    )
                finally:
                    conn.close()
            merge_retry_into_summary(summary, retry_info)

        (results_dir / "summary.json").write_text(
            json.dumps(summary, indent=2, default=str) + "\n", encoding="utf-8"
        )

        write_run_state(
            body_id,
            simulation_uuid=simulation_uuid,
            campaign_uuid=campaign_uuid,
            status=summary["status"],
            stage="complete",
            extra={"worker_id": worker_id, "mpi_procs": mpi_procs},
        )

        with db_write_lock():
            conn = connect(db_path)
            try:
                record_simulation_run(
                    conn,
                    body_id,
                    summary,
                    case_path=case_dir,
                    results_path=results_dir,
                )
                record_config_fingerprints(conn, simulation_uuid, fingerprints)
            finally:
                conn.close()

        return summary
    except Exception as exc:
        with db_write_lock():
            conn = connect(db_path)
            try:
                retry_info = record_attempt_failure(
                    conn,
                    body_id,
                    reason=str(exc),
                    exit_code=getattr(exc, "returncode", None),
                )
            finally:
                conn.close()
        write_retry_to_summary(body_id, retry_info)
        mark_interrupted(body_id, reason=str(exc))
        with db_write_lock():
            conn = connect(db_path)
            try:
                conn.execute(
                    "UPDATE master_samples SET status = 'INTERRUPTED' WHERE sample_id = ?",
                    (body_id,),
                )
                conn.commit()
            finally:
                conn.close()
        raise


def _estimate_runtime_s(db_path: Path, workers: int) -> float | None:
    conn = connect(db_path)
    try:
        rows = fetch_campaign_rows(conn)
        eta = compute_eta(rows, total_bodies=TOTAL_BODIES, workers=workers)
        return eta.get("moving_average_runtime_s")
    finally:
        conn.close()


def run_campaign(
    db_path: Path = DEFAULT_DB_PATH,
    *,
    manifest_path: Path = MANIFEST_PATH,
    force_bodies: set[str] | None = None,
    max_bodies: int | None = None,
    workers: int = 2,
    cores_per_worker: int = 6,
    retry_failed: bool = False,
    stop_after_completed: int | None = None,
    required_bodies: set[str] | None = None,
    health_interval_s: float = HEALTH_SAMPLE_INTERVAL_S,
    dashboard_interval_s: float = DASHBOARD_REFRESH_INTERVAL_S,
    enable_dashboard: bool = True,
) -> dict[str, Any]:
    """Run or resume the production DOE until complete or interrupted."""
    global _scheduler, _interrupt_event
    _interrupt_event = threading.Event()
    _scheduler = None
    signal.signal(signal.SIGINT, _handle_interrupt)
    signal.signal(signal.SIGTERM, _handle_interrupt)

    validation = validate_campaign(db_path, manifest_path=manifest_path)
    if not validation.ok:
        raise RuntimeError("Campaign validation failed:\n" + validation_report(validation))

    if not manifest_path.exists():
        init_manifest(path=manifest_path)
    manifest = load_manifest(manifest_path)
    set_campaign_status(manifest, "RUNNING")
    manifest["workers"] = workers
    manifest["cores_per_worker"] = cores_per_worker

    conn = prepare_database(db_path, sync_results_flag=True)
    try:
        plan = build_resume_plan(conn, force_bodies=force_bodies, retry_failed=retry_failed)
    finally:
        conn.close()

    queue = plan.to_restart + plan.to_run
    if max_bodies is not None:
        queue = queue[:max_bodies]

    if queue:
        with db_write_lock():
            conn = connect(db_path)
            try:
                ensure_campaign_environment(
                    conn,
                    manifest,
                    manifest_path=manifest_path,
                    workers=workers,
                    mpi_ranks_per_worker=cores_per_worker,
                    set_start_time=True,
                )
                manifest = load_manifest(manifest_path)
            finally:
                conn.close()

    save_manifest(manifest, manifest_path)

    if not queue:
        conn = connect(db_path)
        try:
            counts = count_by_status(conn)
        finally:
            conn.close()
        return {
            "ran": 0,
            "plan": resume_plan_summary(plan),
            "counts": counts,
            "interrupted": False,
            "workers": workers,
        }

    worker_configs = default_worker_configs(workers=workers, cores_per_worker=cores_per_worker)
    estimated_runtime = _estimate_runtime_s(db_path, workers=workers)

    from campaign.monitor import CampaignMonitor

    monitor: CampaignMonitor | None = None

    if workers <= 1:
        results: list[dict[str, Any]] = []
        counts: dict[str, int] = {}
        monitor = CampaignMonitor(
            db_path=db_path,
            manifest_path=manifest_path,
            health_interval_s=health_interval_s,
            dashboard_interval_s=dashboard_interval_s,
            enable_dashboard=enable_dashboard,
        )
        monitor.start()
        try:
            for body_id in queue:
                if _interrupt_event.is_set():
                    manifest = load_manifest(manifest_path)
                    set_campaign_status(manifest, "INTERRUPTED")
                    save_manifest(manifest, manifest_path)
                    break

                clean = body_id in plan.to_restart
                if not enable_dashboard:
                    print(f"[campaign] Running {body_id} (clean_restart={clean})", flush=True)
                summary = run_single_body(
                    body_id,
                    campaign_uuid=manifest["campaign_uuid"],
                    db_path=db_path,
                    clean_restart=clean,
                    is_retry=clean,
                    mpi_procs=cores_per_worker,
                    cpu_affinity=worker_configs[0].cpu_affinity if worker_configs else None,
                    interrupt_event=_interrupt_event,
                )
                results.append(summary)

                with db_write_lock():
                    conn = connect(db_path)
                    try:
                        counts = count_by_status(conn)
                        completed = counts.get("COMPLETED", 0)
                        manifest = load_manifest(manifest_path)
                        sync_manifest_from_db(manifest, counts)
                        sync_retry_to_manifest(manifest, conn)
                        rows = fetch_campaign_rows(conn)
                        eta = compute_eta(
                            rows,
                            total_bodies=manifest.get("total_bodies", TOTAL_BODIES),
                            workers=1,
                        )
                        update_eta(manifest, eta)
                        save_manifest(manifest, manifest_path)
                        try:
                            create_checkpoint(
                                completed,
                                db_path=db_path,
                                manifest_path=manifest_path,
                                monitor=monitor,
                            )
                        except Exception as exc:
                            monitor.notify_checkpoint_failed(str(exc))
                        try:
                            backup_database(db_path)
                        except Exception as exc:
                            monitor.notify_backup_failed(str(exc))
                    finally:
                        conn.close()

                if _interrupt_event.is_set():
                    break

                completed = counts.get("COMPLETED", 0)
                stop_config = load_graceful_stop_config()
                if graceful_stop_conditions_met(
                    completed,
                    stop_after_completed=stop_after_completed,
                    required_bodies=tuple(sorted(required_bodies or ())),
                    config=stop_config,
                ):
                    manifest = load_manifest(manifest_path)
                    set_campaign_status(manifest, "PAUSED")
                    manifest["graceful_stop"] = {
                        "reason": (
                            stop_config.reason
                            if stop_config and stop_config.enabled
                            else f"Completed {completed} bodies"
                        ),
                        "stopped_at": utc_now_iso(),
                        "completed_bodies": completed,
                        "stop_after_completed": stop_after_completed,
                        "required_bodies": sorted(required_bodies or []),
                    }
                    save_manifest(manifest, manifest_path)
                    print_graceful_shutdown_summary(
                        build_graceful_shutdown_summary(
                            counts,
                            total_bodies=manifest.get("total_bodies", TOTAL_BODIES),
                        )
                    )
                    break
        finally:
            monitor.stop()

        with db_write_lock():
            conn = connect(db_path)
            try:
                counts = count_by_status(conn)
                manifest = load_manifest(manifest_path)
                sync_manifest_from_db(manifest, counts)
                sync_retry_to_manifest(manifest, conn)
                save_manifest(manifest, manifest_path)
            finally:
                conn.close()

        if counts.get("COMPLETED", 0) >= manifest.get("total_bodies", TOTAL_BODIES):
            on_doe_batch_complete(db_path)

        return {
            "ran": len(results),
            "plan": resume_plan_summary(plan),
            "counts": counts,
            "interrupted": _interrupt_event.is_set(),
            "workers": 1,
        }

    scheduler = CampaignScheduler(
        queue_bodies=queue,
        restart_bodies=set(plan.to_restart),
        campaign_uuid=manifest["campaign_uuid"],
        db_path=db_path,
        manifest_path=manifest_path,
        worker_configs=worker_configs,
        run_body_fn=run_single_body,
        plan=plan,
        estimated_runtime_s=estimated_runtime,
        quiet_logging=enable_dashboard,
        stop_after_completed=stop_after_completed,
        required_bodies=frozenset(required_bodies or ()),
    )
    scheduler._interrupt = _interrupt_event
    _scheduler = scheduler

    monitor = CampaignMonitor(
        scheduler=scheduler,
        db_path=db_path,
        manifest_path=manifest_path,
        health_interval_s=health_interval_s,
        dashboard_interval_s=dashboard_interval_s,
        enable_dashboard=enable_dashboard,
    )
    scheduler._monitor = monitor
    monitor.start()

    if not enable_dashboard:
        print(
            f"[campaign] Launching {workers} workers x {cores_per_worker} MPI processes "
            f"({len(queue)} bodies queued)",
            flush=True,
        )
    try:
        result = scheduler.run()
    finally:
        monitor.stop()
    _scheduler = None
    return result


def resume_campaign(
    db_path: Path = DEFAULT_DB_PATH,
    *,
    manifest_path: Path = MANIFEST_PATH,
    force_bodies: set[str] | None = None,
    max_bodies: int | None = None,
    workers: int = 2,
    cores_per_worker: int = 6,
    retry_failed: bool = False,
    stop_after_completed: int | None = None,
    required_bodies: set[str] | None = None,
    health_interval_s: float = HEALTH_SAMPLE_INTERVAL_S,
    dashboard_interval_s: float = DASHBOARD_REFRESH_INTERVAL_S,
    enable_dashboard: bool = True,
) -> dict[str, Any]:
    """Resume an interrupted or paused campaign without rerunning completed bodies."""
    from campaign.resume import refresh_campaign_state

    plan = refresh_campaign_state(
        db_path,
        manifest_path=manifest_path,
        force_bodies=force_bodies,
        retry_failed=retry_failed,
        workers=workers,
    )
    print(json.dumps(resume_plan_summary(plan), indent=2))
    if not plan.to_run and not plan.to_restart:
        print("Nothing to resume — all bodies complete or skipped.")
        return {"ran": 0, "plan": resume_plan_summary(plan)}
    return run_campaign(
        db_path,
        manifest_path=manifest_path,
        force_bodies=force_bodies,
        max_bodies=max_bodies,
        workers=workers,
        cores_per_worker=cores_per_worker,
        retry_failed=retry_failed,
        stop_after_completed=stop_after_completed,
        required_bodies=required_bodies,
        health_interval_s=health_interval_s,
        dashboard_interval_s=dashboard_interval_s,
        enable_dashboard=enable_dashboard,
    )
