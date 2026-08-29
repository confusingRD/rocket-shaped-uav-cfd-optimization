"""Markdown renderer for the final engineering report."""

from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from reporting.analysis import DescriptiveStats, DESIGN_VARS
from campaign.constants import RESULTS_ROOT
from reporting.production_db import CampaignRow
from reporting.force_convergence import force_convergence_table_rows


def _fmt_cd(v: float | None) -> str:
    return f"{v:.6e}" if v is not None else "—"


def _fmt_f(v: float | None, digits: int = 2) -> str:
    return f"{v:.{digits}f}" if v is not None else "—"


def _stats_table(stats: DescriptiveStats | None, label: str) -> str:
    if not stats:
        return f"*No {label} data available.*\n"
    return (
        f"| Statistic | {label} |\n"
        f"|-----------|------:|\n"
        f"| Mean | {_fmt_f(stats.mean, 6 if label == 'Cd' else 2)} |\n"
        f"| Median | {_fmt_f(stats.median, 6 if label == 'Cd' else 2)} |\n"
        f"| Minimum | {_fmt_f(stats.minimum, 6 if label == 'Cd' else 2)} |\n"
        f"| Maximum | {_fmt_f(stats.maximum, 6 if label == 'Cd' else 2)} |\n"
        f"| Std. dev. | {_fmt_f(stats.std, 6 if label == 'Cd' else 2)} |\n"
        f"| CoV | {_fmt_f(stats.cv, 4)} |\n"
        f"| N | {stats.count} |\n"
    )


def _body_ranking_table(entries: list[dict[str, Any]]) -> str:
    lines = [
        "| Rank | Body ID | λ | w₀ | w₁ | w₂ | w₃ | Cd | Cl | Runtime [s] | Cells | Remark |",
        "|-----:|---------|---:|---:|---:|---:|---:|---:|---:|------------:|------:|--------|",
    ]
    for e in entries:
        lines.append(
            f"| {e.get('rank', '—')} | {e['sample_id']} | {e['lambda']:.1f} | "
            f"{e['w0']:.1f} | {e['w1']:.1f} | {e['w2']:.1f} | {e['w3']:.1f} | "
            f"{_fmt_cd(e['cd'])} | {_fmt_cd(e['cl'])} | {_fmt_f(e.get('runtime_s'), 0)} | "
            f"{e.get('cells') or '—'} | {e['remark']} |"
        )
    return "\n".join(lines) + "\n"


def _figure_block(fig_path: Path, caption: str, label: str) -> str:
    rel = fig_path.relative_to(RESULTS_ROOT)
    return f"![{caption}]({rel})\n\n*Figure {label}: {caption}*\n\n"


def _sensitivity_discussion(analysis: dict[str, Any]) -> str:
    ranked = analysis["sensitivity_ranking"]
    if not ranked:
        return "Insufficient completed runs for sensitivity analysis.\n"
    lines = ["The following design parameters show the strongest monotonic association with $C_D$ "
             "(ranked by |Spearman $\\rho$|):\n"]
    for i, (var, rho) in enumerate(ranked, start=1):
        direction = "increases" if rho > 0 else "decreases"
        name = r"$\lambda$" if var == "lambda" else f"${var.replace('w', 'w_')}$"
        lines.append(f"{i}. **{name}** — Spearman $\\rho = {rho:+.3f}$ "
                     f"(higher values tend to {direction} drag).\n")
    top = ranked[0][0]
    top_name = "fineness ratio λ" if top == "lambda" else f"CST weight {top}"
    lines.append(
        f"\n**Primary driver:** {top_name} exhibits the largest rank correlation with $C_D$ "
        f"across the completed campaign.\n"
    )
    return "\n".join(lines)


def _force_convergence_table(entries: list[dict[str, Any]]) -> str:
    lines = [
        "| Body | ResidualControl | ForceConverged | Cd variation (%) | Recommendation |",
        "|------|:---------------:|:--------------:|-----------------:|----------------|",
    ]
    for e in entries:
        res = "PASS" if e["residual_pass"] else "FAIL"
        force = (
            "PASS"
            if e["force_pass"] is True
            else "FAIL"
            if e["force_pass"] is False
            else "—"
        )
        var = _fmt_f(e.get("cd_variation_percent"), 2) if e.get("cd_variation_percent") is not None else "—"
        lines.append(
            f"| {e['sample_id']} | {res} | {force} | {var} | {e['recommendation']} |"
        )
    return "\n".join(lines) + "\n"


def _stopping_strategy_discussion(analysis: dict[str, Any]) -> str:
    stopping = analysis.get("stopping_strategy") or {}
    if not stopping.get("simulated_bodies"):
        return "Stopping-strategy statistics not available.\n"
    stats = stopping.get("stopping_iteration_stats")
    return f"""Production runs use **automatic residual-based stopping** with a **1000-iteration safety cap**
(`stopAt endTime`; SIMPLE `residualControl` at $10^{{-4}}$ unchanged).

| Metric | Value |
|--------|------:|
| Simulated bodies | {stopping.get('simulated_bodies', 0)} |
| Stopped by residual convergence | {stopping.get('residual_converged_stops', 0)} |
| Stopped by max iterations (1000) | {stopping.get('max_iteration_stops', 0)} |
| Solver crash (execution failure) | {stopping.get('solver_crash_stops', 0)} |
| Mean stopping iteration | {_fmt_f(stats.mean if stats else None, 0)} |
| Median stopping iteration | {_fmt_f(stats.median if stats else None, 0)} |
| Bodies requiring >800 iterations | {stopping.get('over_800_count', 0)} ({_fmt_f(stopping.get('pct_over_800_iterations'), 1)}%) |

The solver terminates when either SIMPLE `residualControl` criteria are satisfied **or** the iteration
budget reaches 1000 — whichever occurs first. **Campaign completion is independent of residual
convergence:** bodies that reach the 1000-iteration cap with valid drag output are recorded as
`COMPLETED` with `termination_reason = MAX_ITERATIONS`, not as campaign failures.
"""


def _force_convergence_discussion(analysis: dict[str, Any]) -> str:
    fc = analysis.get("force_convergence") or {}
    if not fc:
        return "Force-convergence analysis not available.\n"
    return f"""Independent drag stationarity assessment uses the last 100 forceCoeffs samples
(coefficient-of-variation and linear-trend thresholds; see `src/reporting/force_convergence.py`).
This is **independent** of OpenFOAM `residualControl` and does not modify solver settings.

| Category | Count |
|----------|------:|
| Residual PASS + force PASS | {fc.get('residual_pass_force_pass', 0)} |
| Residual FAIL + force PASS | {fc.get('residual_fail_force_pass', 0)} |
| Residual PASS + force FAIL | {fc.get('residual_pass_force_fail', 0)} |
| Residual FAIL + force FAIL | {fc.get('residual_fail_force_fail', 0)} |
| Force status unknown | {fc.get('force_unknown', 0)} |

**Iteration budget assessment:** {fc.get('ranking_assessment', 'Not available.')}
"""


def _engineering_discussion(rows: list[CampaignRow], analysis: dict[str, Any]) -> str:
    corr = analysis["correlations"]
    cd_stats = analysis["cd_stats"]
    completed = analysis["ranked_completed"]
    lambda_vals = [r.lambda_ for r in completed]
    cds = [r.cd for r in completed]

    lambda_trend = ""
    if len(lambda_vals) >= 2:
        from reporting.analysis import pearson
        r_lam = pearson(lambda_vals, cds)
        if r_lam < -0.2:
            lambda_trend = "Higher λ (slender bodies) correlates with lower drag in this campaign."
        elif r_lam > 0.2:
            lambda_trend = "Higher λ correlates with higher drag — investigate nose/wake resolution interaction."
        else:
            lambda_trend = "λ shows weak linear correlation with Cd; nonlinear CST shape effects dominate."

    weight_effects = []
    for w in ("w0", "w1", "w2", "w3"):
        rho = corr["spearman"].get(w, float("nan"))
        if abs(rho) >= 0.15:
            weight_effects.append(f"{w}: Spearman ρ = {rho:+.3f}")

    return f"""### Observed aerodynamic trends

Across {analysis['counts']['completed']} completed axisymmetric bodies at $U_\\infty = 138.89\\,\\mathrm{{m/s}}$,
$C_D$ spans {_fmt_cd(cd_stats.minimum) if cd_stats else '—'} to {_fmt_cd(cd_stats.maximum) if cd_stats else '—'}
(CoV = {_fmt_f(cd_stats.cv, 3) if cd_stats else '—'}). {lambda_trend}

### Influence of λ

Fineness ratio λ = L/(2R) controls body length at fixed R = 0.07 m. {lambda_trend}

### Influence of CST weights

Bernstein weights (w₀…w₃) modulate nose bluntness and aft taper. Notable correlations: {', '.join(weight_effects) or 'none exceed |ρ| = 0.15'}.

### Observed trade-offs

Slender high-λ bodies may reduce pressure drag through gentler axial shaping, but the increased body length can raise wetted area and skin-friction drag.
Blunt low-λ bodies may incur greater pressure drag; the DOE captures this geometric trade-off across the LHS design space.

### Unexpected behaviour

Late-time Cd drift > 2% flags potential numerical non-stationarity despite residual convergence.

### Mesh limitations

Production `MeshLevel.M4_PRODUCTION` satisfied the 2% mesh-independence acceptance criterion on Body_0001 based on the M3-to-M4 Cd change.
Cell counts scale with L; extreme λ bodies may have slightly different near-wall resolution characteristics.

### Model limitations

Steady incompressible k–ω SST RANS at M ≈ 0.41. The five-body compressibility comparison showed strong overall ranking agreement (Pearson r ≈ 0.92), with a local rank swap between neighboring configurations.
Absolute Cd is not experimentally validated; relative ranking is the DOE objective.

### Solver limitations

SIMPLE segregated solver with wall-function treatment. Production mean y+ values were approximately 17–18, below the conventional 30–300 wall-function band and therefore a limitation of the present CFD setup.

### Sources of uncertainty

Wedge axisymmetric approximation (5° sector), wall-function y+ band, incompressible formulation at M ≈ 0.41,
lack of wind-tunnel absolute validation, and single operating point (α = 0°).
"""


def _future_work() -> str:
    return """1. **Higher-Reynolds-number validation** — extend mission-condition sensitivity beyond Re ≈ 6.6×10⁶.
2. **Transient simulations** — assess unsteady wake effects on time-averaged Cd.
3. **Compressible production workflow** — if absolute Cd prediction becomes a requirement.
4. **Adaptive mesh refinement** — local wake refinement for top-10 candidates.
5. **Deterministic optimization** — gradient-free search seeded by DOE best design.
6. **Surrogate modeling** — Gaussian-process or polynomial response surface over (λ, w₀…w₃).
7. **Machine learning** — neural surrogate for rapid design screening (with uncertainty quantification).
8. **Wind-tunnel validation** — absolute Cd anchor for the optimal body.
9. **Fin optimization** — Phase 2 full-3D assembly with parametric fin sweep.
10. **Mesh re-verification on optimum** — confirm M4 independence on the campaign-best geometry.
"""


def render_markdown(
    rows: list[CampaignRow],
    analysis: dict[str, Any],
    figures: dict[str, Path],
    *,
    metadata: dict[str, str | None],
    generated_at: str | None = None,
    environment: dict[str, Any] | None = None,
) -> str:
    generated_at = generated_at or datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
    counts = analysis["counts"]
    cost = analysis["cost"]
    cd_stats = analysis["cd_stats"]
    corr = analysis["correlations"]
    best = analysis["best_body"]

    env_section = ""
    if environment:
        from campaign.environment import environment_markdown_table

        env_section = f"""
---

## 2. Computational Environment

Permanent record of the workstation and software stack that produced this campaign.
Captured automatically at campaign initialization; duration computed at report generation.

{environment_markdown_table(environment)}

"""

    section_offset = 1 if environment else 0
    s = section_offset
    fig = lambda key, cap, num: _figure_block(figures[key], cap, num)

    md = f"""# Final Engineering Report — 200-Body Axisymmetric Drag DOE

**Generated:** {generated_at}  
**Status:** {'Campaign complete' if counts['completed'] >= 200 else f"Partial ({counts['completed']}/200 completed)"}  
**Database:** `data/production.db` (schema v{metadata.get('database_version', '1')})

---

## 1. Executive Summary

**Project objective:** Identify CST-parameterized axisymmetric rocket-body geometries that minimize
drag coefficient $C_D$ at the reference cruise condition ($U_\\infty = 138.89\\,\\mathrm{{m/s}}$, M ≈ 0.41, sea-level ISA)
using a 200-sample Latin Hypercube Design of Experiments (DOE).

**Production CFD configuration:** `MeshLevel.M4_PRODUCTION` wedge mesh, k–ω SST turbulence model,
incompressible SIMPLE (`incompressibleFluid`), supported by mesh-independence and model-sensitivity checks for the relative-ranking objective.

| Metric | Value |
|--------|------:|
| Bodies in DOE | {counts['total_samples']} |
| Simulations attempted | {counts['simulated']} |
| Successful (COMPLETED) | {counts['completed']} |
| Failed | {counts['failed']} |
| Pending | {counts['pending']} |
| Total CPU-hours | {cost['total_cpu_h']:.1f} h |
| Total wall-clock time | {cost['total_wall_h']:.1f} h |
| Mean runtime / body | {cost['avg_wall_s'] / 60:.1f} min |
| Peak memory (campaign) | {cost['peak_rss_mb']:.0f} MB |

{env_section}---

## {2 + s}. Production CFD Configuration

### Geometry generation

CST (Class-Shape-Transformation) Bernstein parameterization with four weights (w₀…w₃) and fineness ratio λ = L/(2R).
200 profiles generated via discrete LHS on λ ∈ [3.5, 6.0] and weight ranges documented in `src/batch/batch_generator.py`.

### Mesh — `MeshLevel.M4_PRODUCTION`

Frozen after the mesh-independence study. `M4_PRODUCTION` was retained after the change in Cd from M3 to M4 remained below the 2% acceptance threshold on Body_0001.
Characteristic lengths scale with body length L; the 14-layer boundary-layer mesh produced mean y+ values of approximately 17–18 in production.

### Turbulence model

**k–ω SST** (`kOmegaSST`) was retained as the production turbulence model after comparison with Spalart–Allmaras. The models produced materially different drag levels, 
so SST remained the reference model for the production campaign.

### Solver

**Incompressible** steady RANS (`incompressibleFluid`, SIMPLE). Force coefficients via `forceCoeffsIncompressible`.

**Stopping strategy (approved 2026-07-26):** Automatic residual-based termination via SIMPLE `residualControl`
($10^{{-4}}$ on p, U, k, ω — unchanged). Hard safety cap: `endTime = 1000` iterations.
The solver stops when residual criteria are met **or** the iteration budget is exhausted.

### Compressibility validation summary

Five-body incompressible vs isothermal compressible comparison showed strong overall ranking agreement (Pearson r ≈ 0.92), with a local rank swap between neighboring configurations. 
The incompressible workflow was retained for the relative-ranking objective.

### Production approval rationale

All internal pre-production numerical checks used for campaign approval were completed (mesh M4, turbulence-model comparison, and compressibility-ranking check). 
The pipeline was retained for the 200-body DOE **relative-ranking objective**; no experimental absolute-drag validation was performed.

---

## {3 + s}. Campaign Statistics

### Drag coefficient

{_stats_table(cd_stats, 'Cd')}

### Iterations

{_stats_table(analysis['iter_stats'], 'Iterations')}

### Runtime [s]

{_stats_table(analysis['runtime_stats'], 'Runtime [s]')}

### Peak memory [MB]

{_stats_table(analysis['memory_stats'], 'Memory [MB]')}

### Mean y+

{_stats_table(analysis['yplus_stats'], 'y+')}

### Cell count

{_stats_table(analysis['cell_stats'], 'Cells')}

### Automatic stopping strategy

{_stopping_strategy_discussion(analysis)}

### Force convergence (Cd stationarity)

{_force_convergence_discussion(analysis)}

{_force_convergence_table(force_convergence_table_rows(rows))}

---

## {4 + s}. Best Performing Designs

### Top 10

{_body_ranking_table(analysis['top_10'])}

### Top 20

{_body_ranking_table(analysis['top_20'])}

### Worst 20

{_body_ranking_table(analysis['worst_20'])}

---

## {5 + s}. Statistical Analysis

### Correlation coefficients with $C_D$

| Variable | Pearson r | Spearman ρ | Kendall τ |
|----------|----------:|-----------:|----------:|
"""
    for var in DESIGN_VARS:
        label = r"$\lambda$" if var == "lambda" else f"${var.replace('w', 'w_')}$"
        md += (
            f"| {label} | {corr['pearson'].get(var, float('nan')):+.4f} | "
            f"{corr['spearman'].get(var, float('nan')):+.4f} | "
            f"{corr['kendall'].get(var, float('nan')):+.4f} |\n"
        )

    md += f"""
### Sensitivity ranking

| Rank | Variable | |Spearman ρ| |
|-----:|----------|------------:|
"""
    for i, (var, rho) in enumerate(analysis["sensitivity_ranking"], start=1):
        label = r"$\lambda$" if var == "lambda" else var
        md += f"| {i} | {label} | {abs(rho):.4f} |\n"

    md += f"""
### Variable importance discussion

{_sensitivity_discussion(analysis)}

---

## {6 + s}. Engineering Figures

{fig('cd_histogram', 'Histogram of drag coefficient across the DOE campaign.', f'{6 + s}.1')}
{fig('cd_cdf', 'Cumulative distribution function of Cd.', f'{6 + s}.2')}
{fig('cd_vs_lambda', 'Cd versus fineness ratio λ.', f'{6 + s}.3')}
{fig('cd_vs_weights', 'Cd versus CST Bernstein weights.', f'{6 + s}.4')}
{fig('correlation_matrix', 'Pearson correlation of design variables with Cd.', f'{6 + s}.5')}
{fig('correlation_heatmap', 'Full design-variable correlation heatmap.', f'{6 + s}.6')}
{fig('scatter_matrix', 'Scatter matrix of design space and Cd.', f'{6 + s}.7')}
{fig('pairplot', 'Pair plot coloured by Cd.', f'{6 + s}.8')}
{fig('runtime_statistics', 'Runtime and peak memory distributions.', f'{6 + s}.9')}
{fig('convergence_statistics', 'Iteration count and late Cd drift.', f'{6 + s}.10')}
{fig('stopping_iterations_histogram', 'Distribution of solver stopping iterations.', f'{6 + s}.11')}
{fig('ranking_plot', 'Top 30 designs ranked by ascending Cd.', f'{6 + s}.12')}
{fig('engineering_dashboard', 'Executive engineering dashboard.', f'{6 + s}.13')}

---

## {7 + s}. Best Geometry Summary

"""
    if best:
        md += f"""**Best body:** `{best.sample_id}` (minimum $C_D$ = {_fmt_cd(best.cd)})

| Parameter | Value |
|-----------|------:|
| λ | {best.lambda_:.1f} |
| L [m] | {best.length:.4f} |
| R [m] | {best.r_max:.4f} |
| w₀ | {best.w0:.1f} |
| w₁ | {best.w1:.1f} |
| w₂ | {best.w2:.1f} |
| w₃ | {best.w3:.1f} |
| $C_L$ | {_fmt_cd(best.cl)} |
| Iterations | {best.iterations or '—'} |
| Runtime [s] | {_fmt_f(best.wall_clock_s or best.execution_time_s, 0)} |
| Cells | {best.cells or '—'} |
| Mean y+ | {_fmt_f(best.yplus_avg, 1)} |
| Cd drift (last 50 iters) | {_fmt_f(best.cd_drift_last50_pct, 2)}% |

**Mesh quality:** max non-orthogonality = {_fmt_f(best.max_non_ortho, 1)}°, max skewness = {_fmt_f(best.max_skewness, 2)}, rocket_wall faces = {best.rocket_wall_faces or '—'}.

**Convergence:** {'PASS' if best.converged is True else 'FAIL' if best.converged is False else 'UNKNOWN'} (residualControl $10^{{-4}}$).

**Engineering interpretation:** {analysis['best_body_remark']}

**Artifacts:** [`best_body/{best.sample_id}/`](../best_body/{best.sample_id}/) — frozen profile, metadata, and simulation summary.

"""
    else:
        md += "*No completed simulation available for best-body summary.*\n\n"

    md += f"""---

## {8 + s}. Engineering Discussion

{_engineering_discussion(rows, analysis)}

---

## {9 + s}. Conclusions

1. **Major finding:** The DOE identifies `{best.sample_id if best else 'TBD'}` as the minimum-drag candidate with $C_D = {_fmt_cd(best.cd if best else None)}$ among {counts['completed']} completed runs.
2. **Numerical verification status:** Production pipeline (M4 + kOmegaSST + incompressible) passed the internal pre-DOE numerical checks; no experimental validation was performed.
3. **DOE objectives:** {'Achieved' if counts['completed'] >= 200 else 'Partially achieved'} — {counts['completed']}/200 bodies completed successfully.
4. **Pipeline suitability:** Approved for **relative geometry ranking**; absolute $C_D$ remains unvalidated at M ≈ 0.41.

---

## {10 + s}. Future Work

{_future_work()}

---

## {11 + s}. Reproducibility

| Item | Value |
|------|-------|
| Git commit | `{metadata.get('git_commit') or 'not available (repo not initialized)'}` |
| OpenFOAM version | {metadata.get('openfoam_version') or 'OpenFOAM-13'} |
| Report generated | {generated_at} |
| Database version | {metadata.get('database_version', '1')} |
| Database path | `data/production.db` |
| Generation script | `src/reporting/generate.py` |
| Figure script | `src/reporting/figures.py` |

### Reproduction commands

```bash
# After all 200 bodies complete:
python src/reporting/generate.py all

# Or step-by-step:
python src/reporting/generate.py init-db
python src/reporting/generate.py sync
python src/reporting/generate.py figures
python src/reporting/generate.py report
python src/reporting/generate.py pdf
```

### Directory structure

```
profiles/Body_XXXX/          # CST geometry (profile.csv, metadata.json)
cases/Body_XXXX/             # OpenFOAM case directories
results/Body_XXXX/           # summary.json, mesh_stats.json per body
data/production.db           # Production SQLite database
results/final_report.md      # This report (markdown)
results/final_report.pdf     # PDF export
results/final_report_figures/  # Auto-generated figures
best_body/Body_XXXX/         # Frozen optimal design artifacts
```

---

*End of report — auto-generated from `data/production.db`. No manual editing required.*
"""
    return md
