"""PDF export for the final engineering report (matplotlib-based, no pandoc required)."""

from __future__ import annotations

import textwrap
from pathlib import Path
from typing import Any

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt
from matplotlib.backends.backend_pdf import PdfPages

from reporting.production_db import CampaignRow


def _wrap(text: str, width: int = 95) -> str:
    paragraphs = text.strip().split("\n\n")
    out: list[str] = []
    for para in paragraphs:
        if para.startswith("|") or para.startswith("```") or para.startswith("#"):
            out.append(para)
        else:
            out.append(textwrap.fill(para, width=width))
    return "\n\n".join(out)


def _add_text_page(pdf: PdfPages, title: str, body: str, *, fontsize: int = 9) -> None:
    fig = plt.figure(figsize=(8.27, 11.69))  # A4 portrait
    fig.patch.set_facecolor("white")
    fig.text(0.08, 0.94, title, fontsize=14, fontweight="bold", va="top")
    wrapped = _wrap(body, width=100)
    fig.text(0.08, 0.88, wrapped, fontsize=fontsize, va="top", family="monospace")
    pdf.savefig(fig, bbox_inches="tight")
    plt.close(fig)


def _add_figure_page(pdf: PdfPages, fig_path: Path, caption: str) -> None:
    if not fig_path.exists():
        return
    img = plt.imread(fig_path)
    fig = plt.figure(figsize=(11.69, 8.27))  # A4 landscape for figures
    ax = fig.add_axes([0.05, 0.12, 0.9, 0.78])
    ax.imshow(img)
    ax.axis("off")
    fig.text(0.5, 0.04, caption, ha="center", fontsize=10, wrap=True)
    pdf.savefig(fig, bbox_inches="tight")
    plt.close(fig)


def _executive_summary_text(analysis: dict[str, Any], metadata: dict[str, str | None]) -> str:
    counts = analysis["counts"]
    cost = analysis["cost"]
    cd = analysis["cd_stats"]
    best = analysis["best_body"]
    best_cd = f"{best.cd:.6e}" if best and best.cd is not None else "N/A"
    best_id = best.sample_id if best else "N/A"
    return f"""Objective: Minimize axisymmetric drag C_D at U_inf = 138.89 m/s (M ~ 0.41)
via 200-sample CST Latin Hypercube DOE.

Production pipeline: MeshLevel.M4_PRODUCTION + k-omega SST + incompressibleFluid.

Campaign status: {counts['completed']} completed, {counts['failed']} failed, {counts['pending']} pending
(out of {counts['total_samples']} bodies).

Computational cost: {cost['total_cpu_h']:.1f} CPU-hours total, {cost['total_wall_h']:.1f} wall-clock hours,
mean {cost['avg_wall_s']/60:.1f} min/body, peak memory {cost['peak_rss_mb']:.0f} MB.

Cd statistics: mean {cd.mean:.6e}, min {cd.minimum:.6e}, max {cd.maximum:.6e}, std {cd.std:.6e}.

Best design: {best_id} (Cd = {best_cd}).

Git: {metadata.get('git_commit') or 'N/A'}  |  OpenFOAM: {metadata.get('openfoam_version') or 'OpenFOAM-13'}"""


def _statistics_text(analysis: dict[str, Any]) -> str:
    cd = analysis["cd_stats"]
    rt = analysis["runtime_stats"]
    mem = analysis["memory_stats"]
    yp = analysis["yplus_stats"]
    cells = analysis["cell_stats"]
    lines = ["CAMPAIGN STATISTICS", ""]
    for label, stats in [("Cd", cd), ("Runtime [s]", rt), ("Memory [MB]", mem), ("y+", yp), ("Cells", cells)]:
        if stats:
            lines.append(
                f"{label}: mean={stats.mean:.4g}, median={stats.median:.4g}, "
                f"min={stats.minimum:.4g}, max={stats.maximum:.4g}, std={stats.std:.4g}, CoV={stats.cv:.4g}"
            )
    return "\n".join(lines)


def _correlation_text(analysis: dict[str, Any]) -> str:
    corr = analysis["correlations"]
    lines = ["CORRELATION WITH Cd", ""]
    lines.append(f"{'Variable':<10} {'Pearson':>10} {'Spearman':>10} {'Kendall':>10}")
    for var in ("lambda", "w0", "w1", "w2", "w3"):
        lines.append(
            f"{var:<10} {corr['pearson'].get(var, float('nan')):>+10.4f} "
            f"{corr['spearman'].get(var, float('nan')):>+10.4f} "
            f"{corr['kendall'].get(var, float('nan')):>+10.4f}"
        )
    lines.append("")
    lines.append("Sensitivity ranking (|Spearman rho|):")
    for i, (var, rho) in enumerate(analysis["sensitivity_ranking"], start=1):
        lines.append(f"  {i}. {var}: {abs(rho):.4f}")
    return "\n".join(lines)


def _ranking_text(entries: list[dict[str, Any]], title: str) -> str:
    lines = [title, ""]
    lines.append(f"{'Rank':>4} {'Body':>12} {'lambda':>6} {'Cd':>14} {'Runtime':>10}")
    for e in entries:
        lines.append(
            f"{e.get('rank', 0):>4} {e['sample_id']:>12} {e['lambda']:>6.1f} "
            f"{e['cd']:>14.6e} {e.get('runtime_s') or 0:>10.0f}"
        )
    return "\n".join(lines)


def render_pdf(
    rows: list[CampaignRow],
    analysis: dict[str, Any],
    figures: dict[str, Path],
    metadata: dict[str, str | None],
    out_path: Path,
    *,
    environment: dict[str, Any] | None = None,
) -> Path:
    """Write publication-style PDF with text summaries and embedded figures."""
    out_path.parent.mkdir(parents=True, exist_ok=True)

    figure_captions = [
        ("cd_histogram", "Figure 1: Cd histogram across DOE campaign"),
        ("cd_cdf", "Figure 2: Cumulative distribution of Cd"),
        ("cd_vs_lambda", "Figure 3: Cd vs fineness ratio lambda"),
        ("cd_vs_weights", "Figure 4: Cd vs CST Bernstein weights"),
        ("correlation_matrix", "Figure 5: Pearson correlation with Cd"),
        ("correlation_heatmap", "Figure 6: Design variable correlation heatmap"),
        ("scatter_matrix", "Figure 7: Scatter matrix"),
        ("pairplot", "Figure 8: Pair plot coloured by Cd"),
        ("runtime_statistics", "Figure 9: Runtime and memory statistics"),
        ("convergence_statistics", "Figure 10: Convergence statistics"),
        (
            "stopping_iterations_histogram",
            "Figure 11: Distribution of solver stopping iterations",
        ),
        ("ranking_plot", "Figure 12: Top 30 designs by Cd"),
        ("engineering_dashboard", "Figure 13: Engineering dashboard"),
    ]

    section_offset = 1 if environment else 0
    s = section_offset

    with PdfPages(out_path) as pdf:
        # Title page
        fig = plt.figure(figsize=(8.27, 11.69))
        fig.patch.set_facecolor("white")
        fig.text(0.5, 0.62, "Final Engineering Report", ha="center", fontsize=22, fontweight="bold")
        fig.text(0.5, 0.52, "200-Body Axisymmetric Drag DOE", ha="center", fontsize=16)
        fig.text(
            0.5,
            0.42,
            "Rocket-Shaped Quadrotor CFD Automation",
            ha="center",
            fontsize=12,
            color="#444444",
        )
        fig.text(
            0.5,
            0.30,
            f"Generated: {metadata.get('generated_at', '')}\n"
            f"OpenFOAM: {metadata.get('openfoam_version', 'OpenFOAM-13')}",
            ha="center",
            fontsize=10,
            family="monospace",
        )
        pdf.savefig(fig, bbox_inches="tight")
        plt.close(fig)

        _add_text_page(pdf, "1. Executive Summary", _executive_summary_text(analysis, metadata))
        if environment:
            from campaign.environment import environment_plaintext

            _add_text_page(
                pdf,
                "2. Computational Environment",
                environment_plaintext(environment),
            )
        _add_text_page(
            pdf,
            f"{2 + s}. Production CFD Configuration",
            "Geometry: CST Bernstein (w0-w3) + lambda = L/(2R), 200 LHS samples.\n\n"
            "Mesh: MeshLevel.M4_PRODUCTION (mesh-independence gate PASS, |dCd| M4-M5 = 1.21%).\n\n"
            "Turbulence: k-omega SST (SA rejected by 5-body probe).\n\n"
            "Solver: incompressibleFluid (compressible probe: ranking preserved, Spearman rho = 0.90).\n\n"
            "See engineering_archive/legacy/verification/ for full V&V reports.",
        )
        _add_text_page(
            pdf,
            f"{3 + s}. Campaign Statistics",
            _statistics_text(analysis),
        )

        _add_text_page(
            pdf,
            f"{4 + s}. Top 10 Designs",
            _ranking_text(analysis["top_10"], "Best 10 by ascending Cd"),
        )

        _add_text_page(
            pdf,
            f"{5 + s}. Statistical Analysis",
            _correlation_text(analysis),
        )

        for key, caption in figure_captions:
            if key in figures:
                _add_figure_page(pdf, figures[key], caption)

        best = analysis["best_body"]
        if best:
            cl_text = f"{best.cl:.6e}" if best.cl is not None else "N/A"

            runtime = (
                best.wall_clock_s
                if best.wall_clock_s is not None
                else best.execution_time_s
            )
            runtime_text = f"{runtime:.0f} s" if runtime is not None else "N/A"

            best_text = (
                f"Best body: {best.sample_id}\n"
                f"Cd = {best.cd:.6e}, Cl = {cl_text}\n"
                f"lambda = {best.lambda_:.1f}, L = {best.length:.4f} m\n"
                f"weights: w0={best.w0}, w1={best.w1}, w2={best.w2}, w3={best.w3}\n"
                f"Iterations: {best.iterations}, Runtime: {runtime_text}\n"
                f"Cells: {best.cells}, y+ avg: {best.yplus_avg}\n\n"
                f"Remark: {analysis['best_body_remark']}\n\n"
                f"Artifacts: best_body/{best.sample_id}/"
            )
            _add_text_page(pdf, f"{7 + s}. Best Geometry Summary", best_text)

        _add_text_page(
            pdf,
            f"{9 + s}. Conclusions & {10 + s}. Future Work",
            "Conclusions: DOE identifies minimum-drag candidate; production pipeline validated for "
            "relative ranking; absolute Cd unvalidated at M ~ 0.41.\n\n"
            "Future work: higher-Re validation, transient studies, compressible workflow, AMR, "
            "optimization, surrogate/ML models, wind-tunnel validation, fin optimization (Phase 2).",
        )
        _add_text_page(
            pdf,
            f"{11 + s}. Reproducibility",
            f"Database: data/production.db (v{metadata.get('database_version', '1')})\n"
            f"Script: src/reporting/generate.py\n"
            f"Command: python src/reporting/generate.py all\n"
            f"Git: {metadata.get('git_commit') or 'N/A'}",
        )

    return out_path
