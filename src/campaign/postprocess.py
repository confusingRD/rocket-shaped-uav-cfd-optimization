"""OpenFOAM post-processing helpers for production campaign runs."""

from __future__ import annotations

import json
import os
import re
import signal
import subprocess
import time
import threading
from pathlib import Path
from typing import Any

from reporting.force_convergence import merge_force_convergence_into_summary
from reporting.production_db import get_git_commit, get_openfoam_version, utc_now_iso
from campaign.campaign_status import apply_campaign_status
from campaign.solver_config import MAX_ITERATION_BUDGET, infer_termination_reason


def run(
    cmd: list[str],
    cwd: Path,
    log_path: Path,
    env: dict[str, str] | None = None,
    *,
    proc_registry: list[subprocess.Popen] | None = None,
    interrupt_event: threading.Event | None = None,
) -> int:
    log_path.parent.mkdir(parents=True, exist_ok=True)
    with open(log_path, "w", encoding="utf-8") as log:
        proc = subprocess.Popen(
            cmd,
            cwd=str(cwd),
            stdout=log,
            stderr=subprocess.STDOUT,
            env=env,
            text=True,
            start_new_session=True,
        )
        if proc_registry is not None:
            proc_registry.append(proc)
        while proc.poll() is None:
            if interrupt_event is not None and interrupt_event.is_set():
                try:
                    os.killpg(os.getpgid(proc.pid), signal.SIGTERM)
                except (ProcessLookupError, PermissionError, OSError):
                    proc.terminate()
                try:
                    proc.wait(timeout=15)
                except subprocess.TimeoutExpired:
                    try:
                        os.killpg(os.getpgid(proc.pid), signal.SIGKILL)
                    except (ProcessLookupError, PermissionError, OSError):
                        proc.kill()
                return proc.returncode if proc.returncode is not None else 130
            time.sleep(0.25)
        return proc.returncode


def of_env() -> dict:
    script = (
        "source /opt/openfoam13/etc/bashrc >/dev/null 2>&1; "
        "python3 -c 'import os,json; print(json.dumps(dict(os.environ)))'"
    )
    out = subprocess.check_output(["bash", "-lc", script], text=True)
    return json.loads(out)


def parse_yplus(log_text: str) -> dict[str, float | None]:
    out: dict[str, float | None] = {"yplus_min": None, "yplus_max": None, "yplus_avg": None}
    m = re.search(
        r"rocket_wall[^\n]*?min:\s*([0-9eE.+-]+)[^\n]*?max:\s*([0-9eE.+-]+)[^\n]*?average:\s*([0-9eE.+-]+)",
        log_text,
    )
    if m:
        out["yplus_min"] = float(m.group(1))
        out["yplus_max"] = float(m.group(2))
        out["yplus_avg"] = float(m.group(3))
        return out
    for key, pat in (
        ("yplus_min", r"min\s*=\s*([0-9eE.+-]+)"),
        ("yplus_max", r"max\s*=\s*([0-9eE.+-]+)"),
        ("yplus_avg", r"average\s*=\s*([0-9eE.+-]+)"),
    ):
        m2 = re.search(pat, log_text)
        if m2:
            out[key] = float(m2.group(1))
    return out


def parse_foam_log(log_path: Path) -> dict[str, Any]:
    text = log_path.read_text(errors="replace") if log_path.exists() else ""
    result: dict[str, Any] = {
        "converged_residualControl": "SIMPLE solution converged" in text,
        "iterations": None,
        "execution_time_s": None,
        "clock_time_s": None,
        "peak_rss_mb": None,
        "fatal": "FOAM FATAL" in text or "Floating point exception" in text,
        "bounding_messages": len(re.findall(r"bounding\s+\w+", text)),
    }
    m = re.search(r"SIMPLE solution converged in\s+(\d+)\s+iterations", text)
    if m:
        result["iterations"] = int(m.group(1))
    else:
        times = re.findall(r"^Time = (\d+)", text, flags=re.M)
        if times:
            result["iterations"] = int(times[-1])

    all_e = re.findall(r"ExecutionTime\s*=\s*([0-9.]+)\s*s", text)
    if all_e:
        result["execution_time_s"] = float(all_e[-1])
    all_c = re.findall(r"ClockTime\s*=\s*([0-9.]+)\s*s", text)
    if all_c:
        result["clock_time_s"] = float(all_c[-1])

    mem = re.findall(r"Max memory\s*=\s*([0-9.]+)\s*MB", text, flags=re.I)
    if mem:
        result["peak_rss_mb"] = float(mem[-1])
    m = re.search(r"Maximum resident set size \(kbytes\):\s*(\d+)", text)
    if m:
        result["peak_rss_mb"] = float(m.group(1)) / 1024.0
    return result


def parse_forces(force_dat: Path) -> dict[str, Any]:
    if not force_dat.exists():
        return {"Cd": None, "Cl": None, "Cd_history": [], "Cl_history": []}
    cds, cls, times = [], [], []
    for line in force_dat.read_text().splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        parts = line.split()
        if len(parts) < 4:
            continue
        t, _, cd, cl = float(parts[0]), float(parts[1]), float(parts[2]), float(parts[3])
        times.append(t)
        cds.append(cd)
        cls.append(cl)
    out: dict[str, Any] = {
        "Cd": cds[-1] if cds else None,
        "Cl": cls[-1] if cls else None,
        "Cd_history": list(zip(times, cds)),
        "Cl_history": list(zip(times, cls)),
    }
    if len(cds) >= 50:
        window = cds[-50:]
        mean = sum(window) / len(window)
        rel = (max(window) - min(window)) / abs(mean) * 100 if mean != 0 else None
        out["Cd_drift_last50_pct"] = rel
    return out


def write_force_series(results_dir: Path, forces: dict[str, Any]) -> None:
    results_dir.mkdir(parents=True, exist_ok=True)
    with open(results_dir / "force_series.csv", "w", encoding="utf-8") as handle:
        handle.write("time,Cd,Cl\n")
        for (t, cd), (_, cl) in zip(forces["Cd_history"], forces["Cl_history"]):
            handle.write(f"{t},{cd},{cl}\n")


def build_summary(
    *,
    body_id: str,
    simulation_uuid: str,
    campaign_uuid: str,
    case_dir: Path,
    results_dir: Path,
    foam_log: Path,
    yplus_log: Path,
    returncode: int,
    wall_clock_s: float,
    start_time: str,
    config_hash: str,
    fingerprints: dict[str, str | None],
    mesh_level: str,
    turbulence_model: str,
    solver: str,
) -> dict[str, Any]:
    summary = parse_foam_log(foam_log)
    summary.update(
        {
            "body_id": body_id,
            "run_id": simulation_uuid,
            "simulation_uuid": simulation_uuid,
            "campaign_uuid": campaign_uuid,
            "status": "COMPLETED" if returncode == 0 and not summary.get("fatal") else "FAILED",
            "returncode": returncode,
            "wall_clock_s": wall_clock_s,
            "start_time": start_time,
            "completed_at": utc_now_iso(),
            "end_time": utc_now_iso(),
            "mesh_level": mesh_level,
            "turbulence_model": turbulence_model,
            "solver": solver,
            "git_commit": get_git_commit(),
            "openfoam_version": get_openfoam_version(),
            "config_hash": config_hash,
            "config_fingerprints": fingerprints,
            "case_path": str(case_dir),
            "results_path": str(results_dir),
        }
    )
    forces = parse_forces(
        case_dir / "postProcessing" / "forceCoeffsIncompressible" / "0" / "forceCoeffs.dat"
    )
    summary.update({k: forces[k] for k in ("Cd", "Cl", "Cd_drift_last50_pct") if k in forces})
    write_force_series(results_dir, forces)
    yplus = parse_yplus(yplus_log.read_text(errors="replace") if yplus_log.exists() else "")
    summary.update(yplus)
    merge_force_convergence_into_summary(summary, case_dir)

    actual_iterations = summary.get("iterations")
    summary["actual_iterations_run"] = actual_iterations
    summary["termination_reason"] = infer_termination_reason(
        converged_residual_control=bool(summary.get("converged_residualControl")),
        iterations=actual_iterations,
        fatal=bool(summary.get("fatal")),
        returncode=returncode,
        max_iterations=MAX_ITERATION_BUDGET,
    )
    apply_campaign_status(summary)
    return summary
