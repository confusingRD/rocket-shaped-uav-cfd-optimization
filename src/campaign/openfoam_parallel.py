"""OpenFOAM domain decomposition helpers for production campaign runs."""

from __future__ import annotations

import shutil
from pathlib import Path


def taskset_available() -> bool:
    return shutil.which("taskset") is not None


def format_cpu_set(cpus: tuple[int, ...]) -> str:
    """Format CPU indices for taskset (e.g. ``0-5`` or ``0,2,4``)."""
    if not cpus:
        return ""
    if len(cpus) == 1:
        return str(cpus[0])
    ordered = sorted(cpus)
    if ordered == list(range(ordered[0], ordered[-1] + 1)):
        return f"{ordered[0]}-{ordered[-1]}"
    return ",".join(str(cpu) for cpu in ordered)


def wrap_with_affinity(cmd: list[str], cpus: tuple[int, ...] | None) -> list[str]:
    """Prefix a command with taskset when affinity is configured and available."""
    if not cpus or not taskset_available():
        return cmd
    return ["taskset", "-c", format_cpu_set(cpus), *cmd]


def write_decompose_par_dict(case_dir: Path, n_procs: int) -> Path:
    """Generate ``system/decomposeParDict`` for scotch decomposition."""
    if n_procs < 1:
        raise ValueError("n_procs must be >= 1")
    path = case_dir / "system" / "decomposeParDict"
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
    location    "system";
    object      decomposeParDict;
}}
// * * * * * * * * * * * * * * * * * * * * * * * * * * * * * * * * * * * * * //

numberOfSubdomains {n_procs};

method          scotch;

// ************************************************************************* //
"""
    path.write_text(text, encoding="utf-8")
    return path


def cleanup_processor_dirs(case_dir: Path) -> None:
    """Remove parallel decomposition artifacts before a clean restart."""
    for proc_dir in case_dir.glob("processor*"):
        if proc_dir.is_dir():
            shutil.rmtree(proc_dir)
