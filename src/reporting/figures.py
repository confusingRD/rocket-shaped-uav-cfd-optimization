"""Publication-quality figures for the final engineering report."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt
import numpy as np

from campaign.constants import RESULTS_ROOT
from reporting.analysis import DESIGN_VARS, DescriptiveStats
from reporting.production_db import CampaignRow

FIGURES_DIR = RESULTS_ROOT / "final_report_figures"

plt.rcParams.update(
    {
        "font.family": "sans-serif",
        "font.sans-serif": ["DejaVu Sans", "Arial", "Helvetica"],
        "font.size": 10,
        "axes.titlesize": 12,
        "axes.labelsize": 10,
        "figure.dpi": 150,
        "savefig.dpi": 300,
        "axes.spines.top": False,
        "axes.spines.right": False,
    }
)

C_PRIMARY = "#2166AC"
C_SECONDARY = "#B2182B"
C_ACCENT = "#762A83"
C_GRID = "#E0E0E0"


def _completed(rows: list[CampaignRow]) -> list[CampaignRow]:
    return [r for r in rows if r.status == "COMPLETED" and r.cd is not None]


def _save(fig: plt.Figure, stem: str, out_dir: Path) -> Path:
    out_dir.mkdir(parents=True, exist_ok=True)
    png = out_dir / f"{stem}.png"
    fig.savefig(png, bbox_inches="tight", facecolor="white")
    plt.close(fig)
    return png


def figure_cd_histogram(rows: list[CampaignRow], out_dir: Path) -> Path:
    cds = [r.cd for r in _completed(rows)]
    fig, ax = plt.subplots(figsize=(7, 4.5))
    ax.hist(cds, bins=25, color=C_PRIMARY, edgecolor="white", alpha=0.85)
    ax.set_xlabel(r"$C_D$")
    ax.set_ylabel("Count")
    ax.set_title(r"Distribution of Drag Coefficient Across DOE Campaign")
    ax.grid(axis="y", alpha=0.3)
    return _save(fig, "fig01_cd_histogram", out_dir)


def figure_cd_cdf(rows: list[CampaignRow], out_dir: Path) -> Path:
    cds = sorted(r.cd for r in _completed(rows))
    y = np.arange(1, len(cds) + 1) / len(cds)
    fig, ax = plt.subplots(figsize=(7, 4.5))
    ax.plot(cds, y, color=C_PRIMARY, lw=2)
    ax.set_xlabel(r"$C_D$")
    ax.set_ylabel("Cumulative fraction")
    ax.set_title(r"Cumulative Distribution of $C_D$")
    ax.grid(alpha=0.3)
    return _save(fig, "fig02_cd_cdf", out_dir)


def figure_cd_vs_lambda(rows: list[CampaignRow], out_dir: Path) -> Path:
    data = _completed(rows)
    x = [r.lambda_ for r in data]
    y = [r.cd for r in data]
    fig, ax = plt.subplots(figsize=(7, 4.5))
    ax.scatter(x, y, s=28, c=C_PRIMARY, alpha=0.7, edgecolors="white", linewidths=0.4)
    if len(x) >= 2:
        coef = np.polyfit(x, y, 1)
        xx = np.linspace(min(x), max(x), 100)
        ax.plot(xx, coef[0] * xx + coef[1], "--", color=C_SECONDARY, lw=1.5, label="Linear fit")
        ax.legend()
    ax.set_xlabel(r"Fineness ratio $\lambda$")
    ax.set_ylabel(r"$C_D$")
    ax.set_title(r"$C_D$ vs Fineness Ratio")
    ax.grid(alpha=0.3)
    return _save(fig, "fig03_cd_vs_lambda", out_dir)


def figure_cd_vs_weights(rows: list[CampaignRow], out_dir: Path) -> Path:
    data = _completed(rows)
    fig, axes = plt.subplots(2, 2, figsize=(9, 7), sharey=True)
    weights = ["w0", "w1", "w2", "w3"]
    titles = [r"$w_0$", r"$w_1$", r"$w_2$", r"$w_3$"]
    for ax, w, title in zip(axes.flat, weights, titles):
        x = [getattr(r, w) for r in data]
        y = [r.cd for r in data]
        ax.scatter(x, y, s=22, c=C_PRIMARY, alpha=0.65, edgecolors="white", linewidths=0.3)
        ax.set_xlabel(title)
        ax.set_ylabel(r"$C_D$")
        ax.grid(alpha=0.3)
    fig.suptitle(r"$C_D$ vs CST Bernstein Weights", y=1.02)
    fig.tight_layout()
    return _save(fig, "fig04_cd_vs_cst_weights", out_dir)


def figure_correlation_matrix(analysis: dict[str, Any], out_dir: Path) -> Path:
    corr = analysis["correlations"]["pearson"]
    vars_ = list(DESIGN_VARS)
    matrix = np.eye(len(vars_))
    cd_row = [corr.get(v, float("nan")) for v in vars_]
    labels = [r"$\lambda$", r"$w_0$", r"$w_1$", r"$w_2$", r"$w_3$"]

    fig, ax = plt.subplots(figsize=(6, 4))
    im = ax.imshow([cd_row], cmap="RdBu_r", vmin=-1, vmax=1, aspect="auto")
    ax.set_xticks(range(len(vars_)))
    ax.set_xticklabels(labels)
    ax.set_yticks([0])
    ax.set_yticklabels([r"$C_D$"])
    ax.set_title("Pearson Correlation with $C_D$")
    for j, val in enumerate(cd_row):
        ax.text(j, 0, f"{val:.2f}", ha="center", va="center", color="black", fontsize=9)
    fig.colorbar(im, ax=ax, fraction=0.046, pad=0.04)
    return _save(fig, "fig05_correlation_matrix", out_dir)


def figure_correlation_heatmap(analysis: dict[str, Any], out_dir: Path) -> Path:
    """Full design-variable correlation heatmap (Pearson on design space + Cd)."""
    rows = analysis["ranked_completed"]
    labels = ["lambda", "w0", "w1", "w2", "w3", "cd"]
    display = [r"$\lambda$", r"$w_0$", r"$w_1$", r"$w_2$", r"$w_3$", r"$C_D$"]
    data = []
    for r in rows:
        data.append([r.lambda_, r.w0, r.w1, r.w2, r.w3, r.cd])
    arr = np.array(data)
    corr = np.corrcoef(arr.T)

    fig, ax = plt.subplots(figsize=(7, 6))
    im = ax.imshow(corr, cmap="RdBu_r", vmin=-1, vmax=1)
    ax.set_xticks(range(len(labels)))
    ax.set_yticks(range(len(labels)))
    ax.set_xticklabels(display, rotation=45, ha="right")
    ax.set_yticklabels(display)
    ax.set_title("Design Variable Correlation Heatmap")
    for i in range(len(labels)):
        for j in range(len(labels)):
            ax.text(j, i, f"{corr[i, j]:.2f}", ha="center", va="center", fontsize=8)
    fig.colorbar(im, ax=ax, fraction=0.046, pad=0.04)
    fig.tight_layout()
    return _save(fig, "fig06_correlation_heatmap", out_dir)


def figure_scatter_matrix(rows: list[CampaignRow], out_dir: Path) -> Path:
    data = _completed(rows)
    fields = ["lambda_", "w0", "w1", "w2", "w3", "cd"]
    labels = [r"$\lambda$", r"$w_0$", r"$w_1$", r"$w_2$", r"$w_3$", r"$C_D$"]
    n = len(fields)
    fig, axes = plt.subplots(n, n, figsize=(11, 11))
    for i in range(n):
        for j in range(n):
            ax = axes[i, j]
            xi = [getattr(r, fields[j]) for r in data]
            yi = [getattr(r, fields[i]) for r in data]
            if i == j:
                ax.hist(xi, bins=15, color=C_PRIMARY, alpha=0.75)
            else:
                ax.scatter(xi, yi, s=8, c=C_PRIMARY, alpha=0.5)
            if i == n - 1:
                ax.set_xlabel(labels[j], fontsize=8)
            if j == 0:
                ax.set_ylabel(labels[i], fontsize=8)
            ax.tick_params(labelsize=6)
    fig.suptitle("Scatter Matrix — Design Space vs $C_D$", y=1.01)
    fig.tight_layout()
    return _save(fig, "fig07_scatter_matrix", out_dir)


def figure_pairplot(rows: list[CampaignRow], out_dir: Path) -> Path:
    """Cd-coloured pair plot of lambda and weights."""
    data = _completed(rows)
    fields = ["lambda_", "w0", "w1", "w2", "w3"]
    labels = [r"$\lambda$", r"$w_0$", r"$w_1$", r"$w_2$", r"$w_3$"]
    cds = np.array([r.cd for r in data])
    n = len(fields)
    fig, axes = plt.subplots(n, n, figsize=(10, 10))
    for i in range(n):
        for j in range(n):
            ax = axes[i, j]
            if i == j:
                ax.hist([getattr(r, fields[i]) for r in data], bins=12, color=C_ACCENT, alpha=0.7)
            else:
                sc = ax.scatter(
                    [getattr(r, fields[j]) for r in data],
                    [getattr(r, fields[i]) for r in data],
                    c=cds,
                    s=14,
                    cmap="viridis_r",
                    alpha=0.75,
                )
                if i == 0 and j == n - 1:
                    fig.colorbar(sc, ax=ax, label=r"$C_D$", fraction=0.046)
            if i == n - 1:
                ax.set_xlabel(labels[j], fontsize=8)
            if j == 0:
                ax.set_ylabel(labels[i], fontsize=8)
            ax.tick_params(labelsize=6)
    fig.suptitle("Pair Plot (colour = $C_D$)", y=1.01)
    fig.tight_layout()
    return _save(fig, "fig08_pairplot", out_dir)


def figure_runtime_statistics(rows: list[CampaignRow], out_dir: Path) -> Path:
    data = _completed(rows)
    runtimes = [
        (r.wall_clock_s if r.wall_clock_s is not None else r.execution_time_s) / 60.0
        for r in data
        if r.wall_clock_s is not None or r.execution_time_s is not None
    ]

    memory = [
        r.peak_rss_mb
        for r in data
        if r.peak_rss_mb is not None
    ]
    fig, (ax1, ax2) = plt.subplots(1, 2, figsize=(10, 4))
    ax1.hist(runtimes, bins=20, color=C_PRIMARY, edgecolor="white", alpha=0.85)
    ax1.set_xlabel("Wall-clock time [min]")
    ax1.set_ylabel("Count")
    ax1.set_title("Runtime Distribution")
    ax2.hist(memory, bins=20, color=C_ACCENT, edgecolor="white", alpha=0.85)
    ax2.set_xlabel("Peak RSS [MB]")
    ax2.set_ylabel("Count")
    ax2.set_title("Peak Memory Distribution")
    fig.tight_layout()
    return _save(fig, "fig09_runtime_statistics", out_dir)


def figure_convergence_statistics(rows: list[CampaignRow], out_dir: Path) -> Path:
    data = _completed(rows)
    iters = [
        r.actual_iterations_run
        if r.actual_iterations_run is not None
        else (r.iterations or 0)
        for r in data
    ]

    drift_points = [
        (
            r.actual_iterations_run
            if r.actual_iterations_run is not None
            else (r.iterations or 0),
            r.cd_drift_last50_pct,
        )
        for r in data
        if r.cd_drift_last50_pct is not None
    ]

    drift_iters = [p[0] for p in drift_points]
    drift = [p[1] for p in drift_points]
    fig, (ax1, ax2) = plt.subplots(1, 2, figsize=(10, 4))
    ax1.hist(iters, bins=20, color=C_PRIMARY, alpha=0.85, edgecolor="white")
    ax1.set_xlabel("Stopping iteration")
    ax1.set_ylabel("Count")
    ax1.set_title("Iteration Count Distribution")
    ax2.scatter(drift_iters, drift, s=20, c=C_SECONDARY, alpha=0.65)
    ax2.axhline(2.0, color="gray", ls="--", lw=1, label="2% drift threshold")
    ax2.set_xlabel("Iterations")
    ax2.set_ylabel(r"Late $C_D$ drift [%]")
    ax2.set_title("Convergence Quality")
    ax2.legend(fontsize=8)
    fig.tight_layout()
    return _save(fig, "fig10_convergence_statistics", out_dir)


def figure_stopping_iterations_histogram(
    rows: list[CampaignRow],
    analysis: dict[str, Any],
    out_dir: Path,
) -> Path:
    """Histogram of stopping iterations across all simulated bodies."""
    stopping = analysis.get("stopping_strategy") or {}
    iters = stopping.get("stopping_iterations") or []
    if not iters:
        simulated = [r for r in rows if r.status == "COMPLETED"]
        iters = [
            r.actual_iterations_run if r.actual_iterations_run is not None else r.iterations
            for r in simulated
        ]
        iters = [i for i in iters if i is not None]

    fig, ax = plt.subplots(figsize=(7, 4.5))
    if iters:
        ax.hist(iters, bins=20, color=C_PRIMARY, edgecolor="white", alpha=0.85)
        median = float(np.median(iters))
        ax.axvline(median, color=C_SECONDARY, ls="--", lw=1.5, label=f"Median = {median:.0f}")
        ax.axvline(800, color=C_ACCENT, ls=":", lw=1.2, label="800 iterations")
        ax.legend(fontsize=8)
    ax.set_xlabel("Stopping iteration")
    ax.set_ylabel("Count")
    ax.set_title("Distribution of Solver Stopping Iterations")
    ax.grid(axis="y", alpha=0.3)
    return _save(fig, "fig13_stopping_iterations_histogram", out_dir)


def figure_ranking_plots(analysis: dict[str, Any], out_dir: Path) -> Path:
    ranked = analysis["ranked_completed"][:30]
    ids = [r.sample_id.replace("Body_", "") for r in ranked]
    cds = [r.cd for r in ranked]
    fig, ax = plt.subplots(figsize=(10, 5))
    bars = ax.bar(range(len(ids)), cds, color=C_PRIMARY, edgecolor="white")
    bars[0].set_color(C_ACCENT)
    ax.set_xticks(range(len(ids)))
    ax.set_xticklabels(ids, rotation=60, ha="right", fontsize=7)
    ax.set_ylabel(r"$C_D$")
    ax.set_xlabel("Body ID (top 30 by ascending $C_D$)")
    ax.set_title("Ranking Plot — Best 30 Designs")
    ax.grid(axis="y", alpha=0.3)
    fig.tight_layout()
    return _save(fig, "fig11_ranking_plot", out_dir)


def figure_engineering_dashboard(
    rows: list[CampaignRow],
    analysis: dict[str, Any],
    out_dir: Path,
) -> Path:
    """Single-page executive dashboard."""
    counts = analysis["counts"]
    cd_stats: DescriptiveStats | None = analysis["cd_stats"]
    cost = analysis["cost"]
    best = analysis["best_body"]

    fig = plt.figure(figsize=(12, 8))
    gs = fig.add_gridspec(3, 3, hspace=0.45, wspace=0.35)

    # Panel A: Cd histogram
    ax_a = fig.add_subplot(gs[0, 0])
    cds = [r.cd for r in _completed(rows)]
    ax_a.hist(cds, bins=20, color=C_PRIMARY, alpha=0.85)
    ax_a.set_title(r"$C_D$ Distribution")
    ax_a.set_xlabel(r"$C_D$")

    # Panel B: lambda vs Cd
    ax_b = fig.add_subplot(gs[0, 1])
    data = _completed(rows)
    ax_b.scatter([r.lambda_ for r in data], [r.cd for r in data], s=12, c=C_PRIMARY, alpha=0.6)
    ax_b.set_title(r"$C_D$ vs $\lambda$")
    ax_b.set_xlabel(r"$\lambda$")

    # Panel C: sensitivity bars
    ax_c = fig.add_subplot(gs[0, 2])
    sens = analysis["sensitivity_ranking"]
    labels = [s[0].replace("lambda", r"$\lambda$") for s in sens]
    vals = [abs(s[1]) for s in sens]
    ax_c.barh(labels, vals, color=C_ACCENT)
    ax_c.set_title("Sensitivity (|Spearman ρ|)")
    ax_c.set_xlim(0, 1)

    # Panel D: runtime
    ax_d = fig.add_subplot(gs[1, 0])
    rt = [
        (r.wall_clock_s if r.wall_clock_s is not None else r.execution_time_s) / 60.0
        for r in data
        if r.wall_clock_s is not None or r.execution_time_s is not None
    ]   
    ax_d.hist(rt, bins=15, color=C_SECONDARY, alpha=0.8)
    ax_d.set_title("Runtime [min]")

    # Panel E: top-10 Cd
    ax_e = fig.add_subplot(gs[1, 1:])
    top10 = analysis["top_10"]
    ax_e.barh(
        [t["sample_id"].replace("Body_", "") for t in reversed(top10)],
        [t["cd"] for t in reversed(top10)],
        color=C_PRIMARY,
    )
    ax_e.set_title("Top 10 Designs by $C_D$")
    ax_e.set_xlabel(r"$C_D$")

    # Panel F: KPI text
    ax_f = fig.add_subplot(gs[2, :])
    ax_f.axis("off")
    kpi_lines = [
        f"DOE Campaign Dashboard — {counts['completed']} / {counts['total_samples']} completed",
        f"Mean Cd: {cd_stats.mean:.6e}  |  Min Cd: {cd_stats.minimum:.6e}  |  σ: {cd_stats.std:.6e}"
        if cd_stats
        else "",
        f"Total CPU-hours: {cost['total_cpu_h']:.1f}  |  Peak memory: {cost['peak_rss_mb']:.0f} MB",
        f"Best design: {best.sample_id if best else '—'}  (λ={best.lambda_ if best else '—'})",
        "Production: M4_PRODUCTION + kOmegaSST + incompressibleFluid",
    ]
    ax_f.text(
        0.02,
        0.85,
        "\n".join(kpi_lines),
        transform=ax_f.transAxes,
        fontsize=11,
        va="top",
        family="monospace",
        bbox=dict(boxstyle="round", facecolor="#F7F7F7", edgecolor=C_GRID),
    )

    fig.suptitle("200-Body DOE — Engineering Dashboard", fontsize=14, fontweight="bold")
    return _save(fig, "fig12_engineering_dashboard", out_dir)


def generate_all_figures(
    rows: list[CampaignRow],
    analysis: dict[str, Any],
    out_dir: Path = FIGURES_DIR,
) -> dict[str, Path]:
    """Generate every figure required by the final report."""
    figures = {
        "cd_histogram": figure_cd_histogram(rows, out_dir),
        "cd_cdf": figure_cd_cdf(rows, out_dir),
        "cd_vs_lambda": figure_cd_vs_lambda(rows, out_dir),
        "cd_vs_weights": figure_cd_vs_weights(rows, out_dir),
        "correlation_matrix": figure_correlation_matrix(analysis, out_dir),
        "correlation_heatmap": figure_correlation_heatmap(analysis, out_dir),
        "scatter_matrix": figure_scatter_matrix(rows, out_dir),
        "pairplot": figure_pairplot(rows, out_dir),
        "runtime_statistics": figure_runtime_statistics(rows, out_dir),
        "convergence_statistics": figure_convergence_statistics(rows, out_dir),
        "stopping_iterations_histogram": figure_stopping_iterations_histogram(
            rows, analysis, out_dir
        ),
        "ranking_plot": figure_ranking_plots(analysis, out_dir),
        "engineering_dashboard": figure_engineering_dashboard(rows, analysis, out_dir),
    }
    return figures
