"""Regenerate final D130 result figures from the authoritative dataset."""

from __future__ import annotations

import csv
from pathlib import Path

import matplotlib.pyplot as plt
import numpy as np


# ---------------------------------------------------------------------------
# Paths
# ---------------------------------------------------------------------------

REPO_ROOT = Path(__file__).resolve().parents[1]

DATA_PATH = REPO_ROOT / "data" / "authoritative_dataset_130.csv"
OUTPUT_DIR = REPO_ROOT / "figures" / "results"


# ---------------------------------------------------------------------------
# Data loading
# ---------------------------------------------------------------------------

REQUIRED_COLUMNS = [
    "body_id",
    "phase",
    "Cd",
    "lambda",
    "w0",
    "w1",
    "w2",
    "w3",
]


def load_dataset(path: Path) -> dict[str, np.ndarray]:
    if not path.exists():
        raise FileNotFoundError(f"Dataset not found: {path}")

    with path.open("r", encoding="utf-8", newline="") as f:
        rows = list(csv.DictReader(f))

    if not rows:
        raise RuntimeError("Dataset is empty.")

    missing = [col for col in REQUIRED_COLUMNS if col not in rows[0]]
    if missing:
        raise RuntimeError(
            "Dataset is missing required columns: "
            + ", ".join(missing)
        )

    if len(rows) != 130:
        raise RuntimeError(
            f"Expected authoritative D130 dataset, but found {len(rows)} rows."
        )

    data = {
        "body_id": np.array([row["body_id"] for row in rows]),
        "phase": np.array([row["phase"] for row in rows]),
        "Cd": np.array([float(row["Cd"]) for row in rows]),
        "lambda": np.array([float(row["lambda"]) for row in rows]),
        "w0": np.array([float(row["w0"]) for row in rows]),
        "w1": np.array([float(row["w1"]) for row in rows]),
        "w2": np.array([float(row["w2"]) for row in rows]),
        "w3": np.array([float(row["w3"]) for row in rows]),
    }

    return data


# ---------------------------------------------------------------------------
# Spearman correlation
# ---------------------------------------------------------------------------

def average_ranks(values: np.ndarray) -> np.ndarray:
    """
    Compute ranks with average ranks assigned to tied values.

    This provides the rank transformation required for Spearman correlation
    without introducing a SciPy dependency.
    """
    values = np.asarray(values, dtype=float)

    order = np.argsort(values, kind="mergesort")
    sorted_values = values[order]

    ranks = np.empty(len(values), dtype=float)

    i = 0
    while i < len(values):
        j = i + 1

        while j < len(values) and sorted_values[j] == sorted_values[i]:
            j += 1

        # Ranks are conventionally 1-based.
        average_rank = ((i + 1) + j) / 2.0

        ranks[order[i:j]] = average_rank
        i = j

    return ranks


def spearman_matrix(arrays: list[np.ndarray]) -> np.ndarray:
    ranked = np.vstack([average_ranks(array) for array in arrays])
    return np.corrcoef(ranked)


# ---------------------------------------------------------------------------
# Scatter plots
# ---------------------------------------------------------------------------

def make_scatter(
    data: dict[str, np.ndarray],
    variable: str,
    xlabel: str,
    filename: str,
) -> None:
    fig, ax = plt.subplots(figsize=(8, 6))

    phases = ["DOE", "Phase3", "Phase45"]
    markers = ["o", "s", "^"]

    for phase, marker in zip(phases, markers):
        mask = data["phase"] == phase

        ax.scatter(
            data[variable][mask],
            data["Cd"][mask],
            marker=marker,
            alpha=0.75,
            label=phase,
        )

    # Highlight the best observed configuration.
    best_index = int(np.argmin(data["Cd"]))

    ax.scatter(
        data[variable][best_index],
        data["Cd"][best_index],
        marker="*",
        s=180,
        edgecolors="black",
        linewidths=0.8,
        label="P45_012",
        zorder=5,
    )

    ax.annotate(
        "P45_012",
        (
            data[variable][best_index],
            data["Cd"][best_index],
        ),
        xytext=(8, 8),
        textcoords="offset points",
    )

    ax.set_xlabel(xlabel)
    ax.set_ylabel("Drag coefficient, C_D")
    ax.set_title(f"C_D vs {xlabel}")

    ax.grid(alpha=0.25)
    ax.legend()

    fig.tight_layout()
    fig.savefig(
        OUTPUT_DIR / filename,
        dpi=200,
        bbox_inches="tight",
    )

    plt.close(fig)


# ---------------------------------------------------------------------------
# Spearman heatmap
# ---------------------------------------------------------------------------

def make_spearman_heatmap(
    data: dict[str, np.ndarray],
) -> np.ndarray:
    variables = [
        data["lambda"],
        data["w0"],
        data["w1"],
        data["w2"],
        data["w3"],
        data["Cd"],
    ]

    labels = [
        "λ",
        "w₀",
        "w₁",
        "w₂",
        "w₃",
        "C_D",
    ]

    corr = spearman_matrix(variables)

    fig, ax = plt.subplots(figsize=(9, 8))

    image = ax.imshow(
        corr,
        vmin=-1.0,
        vmax=1.0,
        cmap="RdBu_r",
    )

    ax.set_xticks(np.arange(len(labels)))
    ax.set_yticks(np.arange(len(labels)))

    ax.set_xticklabels(labels, fontsize=12)
    ax.set_yticklabels(labels, fontsize=12)

    for i in range(len(labels)):
        for j in range(len(labels)):
            ax.text(
                j,
                i,
                f"{corr[i, j]:.2f}",
                ha="center",
                va="center",
                fontsize=11,
            )

    ax.set_title(
        "Spearman Correlation — Final 130-Case Dataset",
        fontsize=16,
        pad=14,
    )

    colorbar = fig.colorbar(image, ax=ax)
    colorbar.set_label("Spearman ρ")

    fig.tight_layout()

    fig.savefig(
        OUTPUT_DIR / "results_spearman_correlation.png",
        dpi=200,
        bbox_inches="tight",
    )

    plt.close(fig)

    return corr


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main() -> None:
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)

    data = load_dataset(DATA_PATH)

    print(f"Loaded authoritative dataset: {DATA_PATH}")
    print(f"Number of configurations: {len(data['Cd'])}")

    make_scatter(
        data,
        variable="lambda",
        xlabel="λ",
        filename="results_cd_vs_lambda.png",
    )

    make_scatter(
        data,
        variable="w0",
        xlabel="w₀",
        filename="results_cd_vs_w0.png",
    )

    make_scatter(
        data,
        variable="w1",
        xlabel="w₁",
        filename="results_cd_vs_w1.png",
    )

    make_scatter(
        data,
        variable="w2",
        xlabel="w₂",
        filename="results_cd_vs_w2.png",
    )

    make_scatter(
        data,
        variable="w3",
        xlabel="w₃",
        filename="results_cd_vs_w3.png",
    )

    corr = make_spearman_heatmap(data)

    print()
    print("Spearman correlation with C_D:")
    print(f"  lambda : {corr[0, 5]:+.6f}")
    print(f"  w0     : {corr[1, 5]:+.6f}")
    print(f"  w1     : {corr[2, 5]:+.6f}")
    print(f"  w2     : {corr[3, 5]:+.6f}")
    print(f"  w3     : {corr[4, 5]:+.6f}")

    print()
    print("Generated:")
    print("  figures/results/results_cd_vs_lambda.png")
    print("  figures/results/results_cd_vs_w0.png")
    print("  figures/results/results_cd_vs_w1.png")
    print("  figures/results/results_cd_vs_w2.png")
    print("  figures/results/results_cd_vs_w3.png")
    print("  figures/results/results_spearman_correlation.png")


if __name__ == "__main__":
    main()