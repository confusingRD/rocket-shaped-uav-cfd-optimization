"""Generate batches of CST-parameterized axisymmetric body profiles.

Creates numbered ``profiles/Body_xxxx/`` folders, each containing
``profile.csv``, ``profile.png``, and ``metadata.json`` for downstream
meshing with Gmsh and CFD analysis with OpenFOAM.
"""

from __future__ import annotations

import json
import re
import shutil
import sys
from pathlib import Path

import matplotlib
import numpy as np

# Non-interactive backend for headless batch plotting.
matplotlib.use("Agg")

# Allow ``python src/batch/batch_generator.py`` from the repository root.
_SRC_ROOT = Path(__file__).resolve().parents[1]
if str(_SRC_ROOT) not in sys.path:
    sys.path.insert(0, str(_SRC_ROOT))

from geometry.geometry_generator import CSTBodyParameters, generate_body_profile

# ---------------------------------------------------------------------------
# User configuration
# ---------------------------------------------------------------------------

N_BODIES = 200
R_MAX = 0.07  # Fixed maximum radius R [m]
N_SAMPLES = 101
LAMBDA_RANGE: tuple[float, float] = (3.5, 6.0)
LAMBDA_STEP = 0.1

# Discrete sampling ranges (min, max) for each CST Bernstein weight.
WEIGHT_RANGES: dict[str, tuple[float, float]] = {
    "w0": (0.5, 1.5),
    "w1": (0.5, 1.5),
    "w2": (0.3, 1.2),
    "w3": (0.2, 1.0),
}
WEIGHT_STEP = 0.1

OUTPUT_ROOT = Path("profiles")
BODY_DIR_PATTERN = re.compile(r"^Body_(\d{4})$")


def build_discrete_grid(
    min_val: float,
    max_val: float,
    step: float = WEIGHT_STEP,
) -> list[float]:
    """Return every discrete candidate from ``min_val`` to ``max_val`` inclusive."""
    n_steps = round((max_val - min_val) / step)
    return [round(min_val + i * step, 1) for i in range(n_steps + 1)]
# Project design variables are discretized to one decimal place.


def build_discrete_weight_grids(
    ranges: dict[str, tuple[float, float]],
    step: float = WEIGHT_STEP,
) -> dict[str, list[float]]:
    """Build the complete discrete candidate list for each CST weight."""
    return {
        name: build_discrete_grid(lo, hi, step)
        for name, (lo, hi) in ranges.items()
    }


def _lhs_discrete_indices(
    n_samples: int,
    grid_size: int,
    rng: np.random.Generator,
) -> np.ndarray:
    """Map Latin Hypercube strata to indices into a 1D discrete grid."""
    perm = rng.permutation(n_samples)
    indices = np.empty(n_samples, dtype=int)
    for i in range(n_samples):
        stratum = perm[i]
        low = stratum * grid_size / n_samples
        high = (stratum + 1) * grid_size / n_samples
        idx = int(rng.uniform(low, high))
        indices[i] = min(max(idx, 0), grid_size - 1)
    return indices


def sample_lambda_values(
    n_samples: int,
    lambda_range: tuple[float, float] = LAMBDA_RANGE,
    *,
    step: float = LAMBDA_STEP,
    rng: np.random.Generator | None = None,
) -> list[float]:
    """Sample fineness ratio lambda = L / (2R) via discrete Latin Hypercube Sampling.

    Lambda is drawn only from the discrete grid ``[3.5, 3.6, ..., 6.0]``.
    """
    if n_samples < 1:
        raise ValueError(f"n_samples must be >= 1, got {n_samples}")

    if rng is None:
        rng = np.random.default_rng()
    lo, hi = lambda_range
    grid = build_discrete_grid(lo, hi, step)
    indices = _lhs_discrete_indices(n_samples, len(grid), rng)
    return [grid[i] for i in indices]


def sample_weight_combinations(
    n_samples: int,
    ranges: dict[str, tuple[float, float]] = WEIGHT_RANGES,
    *,
    step: float = WEIGHT_STEP,
    rng: np.random.Generator | None = None,
) -> list[tuple[float, float, float, float]]:
    """Sample unique CST weight combinations via discrete Latin Hypercube Sampling.

    Each weight is drawn only from its discrete candidate grid (step ``step``).
    Replace this function to plug in a different strategy (e.g. adaptive sampling).
    """
    if n_samples < 1:
        raise ValueError(f"n_samples must be >= 1, got {n_samples}")

    rng = rng or np.random.default_rng()
    grids = build_discrete_weight_grids(ranges, step)
    weight_names = [f"w{k}" for k in range(4)]
    grid_lists = [grids[name] for name in weight_names]

    max_attempts = 100
    for _ in range(max_attempts):
        index_matrix = np.column_stack(
            [_lhs_discrete_indices(n_samples, len(grid), rng) for grid in grid_lists]
        )
        combinations: list[tuple[float, float, float, float]] = []
        seen: set[tuple[float, float, float, float]] = set()
        for row in index_matrix:
            weights = tuple(grid_lists[d][row[d]] for d in range(4))
            if weights in seen:
                break
            seen.add(weights)
            combinations.append(weights)
        if len(combinations) == n_samples:
            return combinations

    raise RuntimeError(
        f"Failed to generate {n_samples} unique LHS combinations after {max_attempts} attempts"
    )


def delete_existing_bodies(profiles_root: Path = OUTPUT_ROOT) -> int:
    """Remove all ``Body_xxxx`` folders under ``profiles_root``."""
    if not profiles_root.is_dir():
        return 0

    deleted = 0
    for entry in profiles_root.iterdir():
        if entry.is_dir() and BODY_DIR_PATTERN.match(entry.name):
            shutil.rmtree(entry)
            deleted += 1
    return deleted


def body_id(index: int) -> str:
    """Format a zero-padded body identifier (e.g. ``Body_0001``)."""
    return f"Body_{index:04d}"


def write_metadata(
    body_dir: Path,
    *,
    body_name: str,
    lambda_: float,
    length: float,
    r_max: float,
    weights: tuple[float, float, float, float],
    n_samples: int,
) -> Path:
    """Write per-body metadata JSON consumed by later pipeline stages."""
    metadata = {
        "body_id": body_name,
        "lambda": lambda_,
        "length": length,
        "R": r_max,
        "r_max": r_max,
        "weights": {
            "w0": weights[0],
            "w1": weights[1],
            "w2": weights[2],
            "w3": weights[3],
        },
        "n_samples": n_samples,
    }
    out = body_dir / "metadata.json"
    out.write_text(json.dumps(metadata, indent=4) + "\n", encoding="utf-8")
    return out


def generate_single_body(
    profiles_root: Path,
    index: int,
    *,
    lambda_: float,
    length: float,
    r_max: float,
    n_samples: int,
    weights: tuple[float, float, float, float],
) -> Path:
    """Generate one body profile folder with CSV, PNG, and metadata."""
    name = body_id(index)
    body_dir = profiles_root / name

    if body_dir.exists():
        raise FileExistsError(
            f"Refusing to overwrite existing folder: {body_dir}"
        )

    body_dir.mkdir(parents=True, exist_ok=False)

    params = CSTBodyParameters(
        length=length,
        r_max=r_max,
        weights=weights,
        n_samples=n_samples,
    )
    generate_body_profile(
        params,
        csv_path=body_dir / "profile.csv",
        plot=True,
        plot_save_path=body_dir / "profile.png",
    )
    write_metadata(
        body_dir,
        body_name=name,
        lambda_=lambda_,
        length=length,
        r_max=r_max,
        weights=weights,
        n_samples=n_samples,
    )
    return body_dir


def generate_batch(
    n_bodies: int = N_BODIES,
    *,
    r_max: float = R_MAX,
    lambda_range: tuple[float, float] = LAMBDA_RANGE,
    n_samples: int = N_SAMPLES,
    weight_ranges: dict[str, tuple[float, float]] = WEIGHT_RANGES,
    output_root: Path = OUTPUT_ROOT,
    start_index: int = 1,
) -> list[Path]:
    """Generate ``n_bodies`` profiles under ``output_root``."""
    created: list[Path] = []
    rng = np.random.default_rng()
    lambda_values = sample_lambda_values(n_bodies, lambda_range, rng=rng)
    weight_combinations = sample_weight_combinations(
        n_bodies,
        weight_ranges,
        rng=rng,
    )

    for offset, (lambda_, weights) in enumerate(
        zip(lambda_values, weight_combinations, strict=True)
    ):
        length = 2.0 * r_max * lambda_
        body_dir = generate_single_body(
            output_root,
            start_index + offset,
            lambda_=lambda_,
            length=length,
            r_max=r_max,
            n_samples=n_samples,
            weights=weights,
        )
        created.append(body_dir)

    return created

if __name__ == "__main__":
    folders = generate_batch()

    print(
        f"Generated {len(folders)} body profile(s) "
        f"under {OUTPUT_ROOT.resolve()}"
    )

    for folder in folders:
        print(f"  {folder.name}/")
