"""Intermediate progress PDF reports with INTERMEDIATE RESULTS watermark."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt
from matplotlib.backends.backend_pdf import PdfPages

from reporting.pdf_export import _add_figure_page, _add_text_page, _ranking_text, _statistics_text
from reporting.production_db import CampaignRow, utc_now_iso


def _watermarked_title_page(
    pdf: PdfPages,
    *,
    completed_count: int,
    manifest: dict[str, Any],
    eta: dict[str, Any],
) -> None:
    fig = plt.figure(figsize=(8.27, 11.69))
    fig.patch.set_facecolor("white")
    fig.text(
        0.5,
        0.72,
        "INTERMEDIATE RESULTS",
        ha="center",
        fontsize=28,
        fontweight="bold",
        color="#cc0000",
        alpha=0.85,
    )
    fig.text(0.5, 0.58, "Campaign Progress Report", ha="center", fontsize=20, fontweight="bold")
    fig.text(
        0.5,
        0.48,
        f"{completed_count} / {manifest.get('total_bodies', 200)} bodies completed",
        ha="center",
        fontsize=14,
    )
    fig.text(
        0.5,
        0.36,
        f"Campaign: {manifest.get('campaign_name', 'DOE')}\n"
        f"UUID: {manifest.get('campaign_uuid', 'N/A')}\n"
        f"Status: {manifest.get('campaign_status', 'N/A')}\n"
        f"Generated: {utc_now_iso()}",
        ha="center",
        fontsize=10,
        family="monospace",
    )
    pct = 100.0 * completed_count / manifest.get("total_bodies", 200)
    fig.text(
        0.5,
        0.22,
        f"Completion: {pct:.1f}%\n"
        f"Avg runtime: {eta.get('average_runtime_s') or 'N/A'} s\n"
        f"Moving avg: {eta.get('moving_average_runtime_s') or 'N/A'} s\n"
        f"Est. finish: {eta.get('estimated_finish_at') or 'N/A'}",
        ha="center",
        fontsize=10,
        family="monospace",
    )
    pdf.savefig(fig, bbox_inches="tight")
    plt.close(fig)


def _progress_summary_text(
    analysis: dict[str, Any],
    manifest: dict[str, Any],
    eta: dict[str, Any],
    completed_count: int,
) -> str:
    counts = analysis["counts"]
    best = analysis["best_body"]
    worst = analysis["ranked_completed"][-1] if analysis["ranked_completed"] else None
    best_cd = f"{best.cd:.6e}" if best and best.cd is not None else "N/A"
    worst_cd = f"{worst.cd:.6e}" if worst and worst.cd is not None else "N/A"
    return f"""INTERMEDIATE RESULTS — not for final engineering sign-off.

Checkpoint: {completed_count} completed bodies ({100.0 * completed_count / manifest.get('total_bodies', 200):.1f}%).

Campaign status: {manifest.get('campaign_status')}
Completed: {counts['completed']}  Failed: {counts['failed']}  Pending: {counts['pending']}  Running: {counts['running']}  Interrupted: {counts['interrupted']}

Best body so far: {best.sample_id if best else 'N/A'} (Cd={best_cd})
Worst body so far: {worst.sample_id if worst else 'N/A'} (Cd={worst_cd})

Runtime: avg {eta.get('average_runtime_s') or 'N/A'} s, moving avg {eta.get('moving_average_runtime_s') or 'N/A'} s
Estimated remaining: {eta.get('estimated_remaining_s') or 'N/A'} s
Estimated finish: {eta.get('estimated_finish_at') or 'N/A'}

Engineering note: Rankings and statistics are provisional until all {manifest.get('total_bodies', 200)} bodies complete."""


def generate_progress_report(
    rows: list[CampaignRow],
    analysis: dict[str, Any],
    figures: dict[str, Path],
    manifest: dict[str, Any],
    eta: dict[str, Any],
    out_path: Path,
    *,
    completed_count: int,
) -> Path:
    out_path.parent.mkdir(parents=True, exist_ok=True)
    figure_keys = [
        ("ranking_plot", "Top designs by Cd (provisional)"),
        ("cd_histogram", "Cd distribution (partial campaign)"),
        ("runtime_statistics", "Runtime statistics"),
        ("engineering_dashboard", "Engineering dashboard"),
    ]

    with PdfPages(out_path) as pdf:
        _watermarked_title_page(
            pdf,
            completed_count=completed_count,
            manifest=manifest,
            eta=eta,
        )
        _add_text_page(
            pdf,
            "Progress Summary",
            _progress_summary_text(analysis, manifest, eta, completed_count),
        )
        _add_text_page(pdf, "Campaign Statistics", _statistics_text(analysis))
        _add_text_page(
            pdf,
            "Current Top 10",
            _ranking_text(analysis["top_10"], "Best 10 by ascending Cd (provisional)"),
        )
        for key, caption in figure_keys:
            if key in figures:
                _add_figure_page(pdf, figures[key], f"INTERMEDIATE — {caption}")

    return out_path
