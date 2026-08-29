"""Statistical analysis for the 200-body DOE final report."""

from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Any

from reporting.production_db import CampaignRow
from campaign.solver_config import (
    TERMINATION_MAX_ITERATIONS,
    TERMINATION_RESIDUAL_CONVERGED,
    TERMINATION_SOLVER_CRASH,
)


DESIGN_VARS = ("lambda", "w0", "w1", "w2", "w3")


@dataclass(frozen=True)
class DescriptiveStats:
    mean: float
    median: float
    minimum: float
    maximum: float
    std: float
    cv: float
    count: int


def _values(rows: list[CampaignRow], attr: str) -> list[float]:
    out: list[float] = []
    for row in rows:
        val = getattr(row, attr, None)
        if val is not None and not (isinstance(val, float) and math.isnan(val)):
            out.append(float(val))
    return out


def descriptive_stats(values: list[float]) -> DescriptiveStats | None:
    if not values:
        return None
    n = len(values)
    mean = sum(values) / n
    sorted_v = sorted(values)
    mid = n // 2
    median = sorted_v[mid] if n % 2 else 0.5 * (sorted_v[mid - 1] + sorted_v[mid])
    minimum = sorted_v[0]
    maximum = sorted_v[-1]
    if n > 1:
        var = sum((v - mean) ** 2 for v in values) / (n - 1)
        std = math.sqrt(var)
    else:
        std = 0.0
    cv = std / abs(mean) if mean else float("nan")
    return DescriptiveStats(mean, median, minimum, maximum, std, cv, n)


def pearson(x: list[float], y: list[float]) -> float:
    n = len(x)
    if n < 2 or len(y) != n:
        return float("nan")
    mx = sum(x) / n
    my = sum(y) / n
    num = sum((a - mx) * (b - my) for a, b in zip(x, y))
    denx = math.sqrt(sum((a - mx) ** 2 for a in x))
    deny = math.sqrt(sum((b - my) ** 2 for b in y))
    if denx == 0 or deny == 0:
        return float("nan")
    return num / (denx * deny)


def spearman(x: list[float], y: list[float]) -> float:
    def rank(v: list[float]) -> list[float]:
        order = sorted(range(len(v)), key=lambda i: v[i])
        r = [0.0] * len(v)
        i = 0
        while i < len(v):
            j = i
            while j + 1 < len(v) and v[order[j + 1]] == v[order[i]]:
                j += 1
            avg = (i + j) / 2.0 + 1.0
            for k in range(i, j + 1):
                r[order[k]] = avg
            i = j + 1
        return r

    return pearson(rank(x), rank(y))


def kendall_tau(x: list[float], y: list[float]) -> float:
    """Kendall tau-b correlation with tie correction."""
    n = len(x)
    if n < 2 or len(y) != n:
        return float("nan")

    concordant = 0
    discordant = 0
    ties_x = 0
    ties_y = 0

    for i in range(n):
        for j in range(i + 1, n):
            dx = x[i] - x[j]
            dy = y[i] - y[j]

            if dx == 0 and dy == 0:
                continue
            if dx == 0:
                ties_x += 1
            elif dy == 0:
                ties_y += 1
            elif dx * dy > 0:
                concordant += 1
            else:
                discordant += 1

    denom = math.sqrt(
        (concordant + discordant + ties_x)
        * (concordant + discordant + ties_y)
    )

    if denom == 0:
        return float("nan")

    return (concordant - discordant) / denom


def correlation_matrix(
    rows: list[CampaignRow],
    *,
    response: str = "cd",
) -> dict[str, dict[str, float]]:
    """Pearson, Spearman, and Kendall correlations between response and design vars."""
    y = _values(rows, response)
    if not y:
        return {"pearson": {}, "spearman": {}, "kendall": {}}

    # Align x vectors to rows with valid response
    aligned: list[CampaignRow] = [
        r for r in rows if r.cd is not None and r.status == "COMPLETED"
    ]
    y = [float(r.cd) for r in aligned]

    pearson_map: dict[str, float] = {}
    spearman_map: dict[str, float] = {}
    kendall_map: dict[str, float] = {}

    for var in DESIGN_VARS:
        attr = "lambda_" if var == "lambda" else var
        x = [float(getattr(r, attr)) for r in aligned]
        pearson_map[var] = pearson(x, y)
        spearman_map[var] = spearman(x, y)
        kendall_map[var] = kendall_tau(x, y)

    return {"pearson": pearson_map, "spearman": spearman_map, "kendall": kendall_map}


def sensitivity_ranking(corr: dict[str, dict[str, float]]) -> list[tuple[str, float]]:
    """Rank design variables by |Spearman rho| with Cd."""
    spearman_map = corr.get("spearman", {})
    ranked = sorted(
        spearman_map.items(),
        key=lambda kv: abs(kv[1]) if not math.isnan(kv[1]) else -1.0,
        reverse=True,
    )
    return ranked


def engineering_remark(row: CampaignRow, *, cd_stats: DescriptiveStats | None, rank: int, n: int) -> str:
    """Auto-generate a short engineering note for a ranked body."""
    parts: list[str] = []
    if row.cd is not None and cd_stats:
        if rank == 1:
            parts.append("Campaign minimum drag design.")
        elif rank <= 10:
            parts.append(f"Top-{rank} performer ({rank}/{n} by ascending Cd).")
        elif rank >= n - 19:
            parts.append(f"Bottom-{n - rank + 1} drag ({rank}/{n}).")
        if row.cd < cd_stats.mean - cd_stats.std:
            parts.append("Cd more than one standard deviation below fleet mean.")
        elif row.cd > cd_stats.mean + cd_stats.std:
            parts.append("Cd more than one standard deviation above fleet mean.")

    if row.lambda_ >= 5.5:
        parts.append("High fineness ratio (λ ≥ 5.5) — slender body.")
    elif row.lambda_ <= 4.0:
        parts.append("Low fineness ratio (λ ≤ 4.0) — blunt body.")

    if row.converged is False and row.termination_reason == TERMINATION_MAX_ITERATIONS:
        parts.append("Completed at 1000-iteration safety cap without residual convergence.")
    elif row.converged is False:
        parts.append("Did not meet residual convergence criterion.")
    elif row.cd_drift_last50_pct is not None and row.cd_drift_last50_pct > 2.0:
        parts.append(f"Late Cd drift {row.cd_drift_last50_pct:.1f}% (>2% threshold).")

    if row.yplus_avg is not None:
        if row.yplus_avg < 30:
            parts.append(f"Mean y+ = {row.yplus_avg:.1f} below wall-function band (30–300).")
        elif row.yplus_avg > 300:
            parts.append(f"Mean y+ = {row.yplus_avg:.1f} above wall-function band.")

    if row.status == "FAILED":
        parts.append(f"Simulation failed: {row.error_message or 'see logs'}.")

    return " ".join(parts) if parts else "Nominal production run within campaign statistics."


def rank_by_cd(rows: list[CampaignRow]) -> list[CampaignRow]:
    completed = [r for r in rows if r.status == "COMPLETED" and r.cd is not None]
    return sorted(completed, key=lambda r: r.cd)


def _stopping_iterations(row: CampaignRow) -> int | None:
    return row.actual_iterations_run if row.actual_iterations_run is not None else row.iterations


def analyze_stopping_strategy(rows: list[CampaignRow]) -> dict[str, Any]:
    """Campaign-level statistics for automatic residual-based stopping."""
    completed = [r for r in rows if r.status == "COMPLETED"]
    simulated = [
        r
        for r in completed
        if r.termination_reason
        or r.iterations is not None
        or r.actual_iterations_run is not None
    ]
    stopping_iters = [_stopping_iterations(r) for r in simulated]
    stopping_iters = [i for i in stopping_iters if i is not None]

    residual_stops = sum(
        1 for r in simulated if r.termination_reason == TERMINATION_RESIDUAL_CONVERGED
    )
    max_iter_stops = sum(
        1 for r in simulated if r.termination_reason == TERMINATION_MAX_ITERATIONS
    )
    solver_crash_stops = sum(
        1
        for r in rows
        if r.status == "FAILED"
        and (
            r.termination_reason in (TERMINATION_SOLVER_CRASH, "FAILED")
            or r.termination_reason is None
        )
    )

    iter_stats = descriptive_stats([float(i) for i in stopping_iters])
    over_800 = sum(1 for i in stopping_iters if i > 800)
    pct_over_800 = (100.0 * over_800 / len(stopping_iters)) if stopping_iters else 0.0

    return {
        "simulated_bodies": len(simulated),
        "residual_converged_stops": residual_stops,
        "max_iteration_stops": max_iter_stops,
        "solver_crash_stops": solver_crash_stops,
        "stopping_iteration_stats": iter_stats,
        "stopping_iterations": stopping_iters,
        "pct_over_800_iterations": pct_over_800,
        "over_800_count": over_800,
    }


def analyze_campaign(rows: list[CampaignRow]) -> dict[str, Any]:
    """Build the analysis payload consumed by markdown and PDF renderers."""
    completed = [r for r in rows if r.status == "COMPLETED" and r.cd is not None]
    failed = [r for r in rows if r.status == "FAILED"]
    pending = [r for r in rows if r.status == "PENDING"]
    running = [r for r in rows if r.status == "RUNNING"]
    skipped = [r for r in rows if r.status == "SKIPPED"]
    interrupted = [r for r in rows if r.status == "INTERRUPTED"]
    ranked = rank_by_cd(completed)
    n_completed = len(completed)

    cd_stats = descriptive_stats(_values(completed, "cd"))
    iter_stats = descriptive_stats(_values(completed, "iterations"))
    runtime_stats = descriptive_stats(
        [r.wall_clock_s or r.execution_time_s for r in completed if (r.wall_clock_s or r.execution_time_s)]
    )
    memory_stats = descriptive_stats(_values(completed, "peak_rss_mb"))
    yplus_stats = descriptive_stats(_values(completed, "yplus_avg"))
    cell_stats = descriptive_stats([float(r.cells) for r in completed if r.cells])

    total_cpu_s = sum(r.execution_time_s or 0.0 for r in completed)
    total_wall_s = sum(r.wall_clock_s or r.execution_time_s or 0.0 for r in completed)
    peak_memory = max((r.peak_rss_mb or 0.0 for r in completed), default=0.0)

    corr = correlation_matrix(completed)
    sensitivity = sensitivity_ranking(corr)

    def body_table(selected: list[CampaignRow]) -> list[dict[str, Any]]:
        table: list[dict[str, Any]] = []
        for i, row in enumerate(selected, start=1):
            rank = ranked.index(row) + 1 if row in ranked else None
            table.append(
                {
                    "rank": rank,
                    "sample_id": row.sample_id,
                    "lambda": row.lambda_,
                    "w0": row.w0,
                    "w1": row.w1,
                    "w2": row.w2,
                    "w3": row.w3,
                    "cd": row.cd,
                    "cl": row.cl,
                    "runtime_s": row.wall_clock_s or row.execution_time_s,
                    "iterations": row.iterations,
                    "cells": row.cells,
                    "yplus_avg": row.yplus_avg,
                    "remark": engineering_remark(row, cd_stats=cd_stats, rank=rank or i, n=n_completed),
                }
            )
        return table

    best = ranked[0] if ranked else None
    worst = list(reversed(ranked))

    from reporting.force_convergence import analyze_force_convergence_campaign

    force_convergence = analyze_force_convergence_campaign(rows)
    stopping_strategy = analyze_stopping_strategy(rows)

    return {
        "counts": {
            "total_samples": len(rows),
            "simulated": len(completed) + len(failed) + len(running) + len(interrupted),
            "completed": n_completed,
            "failed": len(failed),
            "pending": len(pending),
            "running": len(running),
            "interrupted": len(interrupted),
            "skipped": len(skipped),
        },
        "cost": {
            "total_cpu_s": total_cpu_s,
            "total_cpu_h": total_cpu_s / 3600.0,
            "total_wall_s": total_wall_s,
            "total_wall_h": total_wall_s / 3600.0,
            "avg_wall_s": total_wall_s / n_completed if n_completed else 0.0,
            "peak_rss_mb": peak_memory,
        },
        "cd_stats": cd_stats,
        "iter_stats": iter_stats,
        "runtime_stats": runtime_stats,
        "memory_stats": memory_stats,
        "yplus_stats": yplus_stats,
        "cell_stats": cell_stats,
        "correlations": corr,
        "sensitivity_ranking": sensitivity,
        "ranked_completed": ranked,
        "top_10": body_table(ranked[:10]),
        "top_20": body_table(ranked[:20]),
        "worst_20": body_table(worst[:20]),
        "best_body": best,
        "best_body_remark": engineering_remark(best, cd_stats=cd_stats, rank=1, n=n_completed) if best else "",
        "force_convergence": force_convergence,
        "stopping_strategy": stopping_strategy,
    }
