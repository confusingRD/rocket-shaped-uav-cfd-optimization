"""Production solver stopping configuration — validation baselines only.

This module defines the approved iteration budget and frozen residualControl
thresholds. It does not modify OpenFOAM numerics; it supports pre-flight
validation and post-run termination reporting.
"""

from __future__ import annotations

import re
from pathlib import Path

from campaign.constants import MAX_ITERATION_BUDGET, TEMPLATE_CASE

PRODUCTION_RESIDUAL_CONTROL: dict[str, float] = {
    "p": 1e-4,
    "U": 1e-4,
    "k": 1e-4,
    "omega": 1e-4,
}

TERMINATION_RESIDUAL_CONVERGED = "RESIDUAL_CONVERGED"
TERMINATION_MAX_ITERATIONS = "MAX_ITERATIONS"
TERMINATION_SOLVER_CRASH = "SOLVER_CRASH"
# Legacy alias retained for backward-compatible reads only.
TERMINATION_FAILED = TERMINATION_SOLVER_CRASH


def parse_control_dict_end_time(path: Path) -> int | None:
    """Return ``endTime`` from an OpenFOAM ``controlDict`` file."""
    if not path.is_file():
        return None
    text = path.read_text(errors="replace")
    match = re.search(r"^\s*endTime\s+(\d+)\s*;", text, flags=re.M)
    return int(match.group(1)) if match else None


def parse_fv_solution_residual_control(path: Path) -> dict[str, float]:
    """Extract SIMPLE ``residualControl`` field tolerances from ``fvSolution``."""
    if not path.is_file():
        return {}
    text = path.read_text(errors="replace")
    block = re.search(
        r"residualControl\s*\{([^}]*)\}",
        text,
        flags=re.S,
    )
    if not block:
        return {}
    controls: dict[str, float] = {}
    for line in block.group(1).splitlines():
        match = re.match(r"^\s*(\w+)\s+([0-9.eE+-]+)\s*;", line.strip())
        if match:
            controls[match.group(1)] = float(match.group(2))
    return controls


def residual_control_matches_production(parsed: dict[str, float]) -> tuple[bool, str]:
    """Compare parsed residualControl against the frozen production baseline."""
    if not parsed:
        return False, "residualControl block missing or empty"
    mismatches: list[str] = []
    for field, expected in PRODUCTION_RESIDUAL_CONTROL.items():
        actual = parsed.get(field)
        if actual is None:
            mismatches.append(f"{field}: missing")
        elif actual != expected:
            mismatches.append(f"{field}: expected {expected:g}, got {actual:g}")
    extra = sorted(set(parsed) - set(PRODUCTION_RESIDUAL_CONTROL))
    if extra:
        mismatches.append(f"unexpected fields: {', '.join(extra)}")
    if mismatches:
        return False, "; ".join(mismatches)
    return True, "all fields match production baseline"


def validate_production_solver_config(
    template_case: Path = TEMPLATE_CASE,
) -> tuple[bool, str]:
    """Validate production template stopping configuration."""
    control_dict = template_case / "system" / "controlDict"
    fv_solution = template_case / "system" / "fvSolution"

    end_time = parse_control_dict_end_time(control_dict)
    if end_time != MAX_ITERATION_BUDGET:
        return False, f"endTime={end_time} (expected {MAX_ITERATION_BUDGET})"

    parsed = parse_fv_solution_residual_control(fv_solution)
    ok, detail = residual_control_matches_production(parsed)
    if not ok:
        return False, f"residualControl mismatch: {detail}"
    return True, f"endTime={MAX_ITERATION_BUDGET}, residualControl unchanged"


def infer_termination_reason(
    *,
    converged_residual_control: bool,
    iterations: int | None,
    fatal: bool = False,
    returncode: int = 0,
    max_iterations: int = MAX_ITERATION_BUDGET,
) -> str:
    """Classify why the solver stopped."""
    if fatal or returncode != 0:
        return TERMINATION_SOLVER_CRASH
    if converged_residual_control:
        return TERMINATION_RESIDUAL_CONVERGED
    if iterations is not None and iterations >= max_iterations:
        return TERMINATION_MAX_ITERATIONS
    return TERMINATION_SOLVER_CRASH
