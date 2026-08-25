"""CST-based axisymmetric body profile generator.



This module implements Class-Shape Transformation (CST) for generating

2-D meridional profiles of axisymmetric bodies (e.g. rocket fuselages).

The profile is defined in normalized axial coordinate xi = x / L, sampled,

mirrored about the centerline, and exported as a closed (x, y) polyline.



References

----------

Kulfan, J. A. (2008). Universal Parametric Geometry Representation Method.

"""



from __future__ import annotations



from dataclasses import dataclass

from pathlib import Path

from typing import Sequence



import matplotlib.pyplot as plt

import numpy as np





# ---------------------------------------------------------------------------

# Parameters

# ---------------------------------------------------------------------------





@dataclass(frozen=True)

class CSTBodyParameters:

    """Physical and shape parameters for a CST axisymmetric body profile.



    Attributes

    ----------

    length : float

        Body length L [same units as output coordinates].

    r_max : float

        Target maximum radius after normalization.

    weights : tuple[float, float, float, float]

        Cubic Bernstein shape-function weights (omega_0 .. omega_3).

    n_samples : int

        Number of sample points along xi in [0, 1] (inclusive endpoints).

    """



    length: float

    r_max: float

    weights: tuple[float, float, float, float] = (1.0, 1.0, 1.0, 1.0)

    n_samples: int = 101



    def __post_init__(self) -> None:

        if self.length <= 0:

            raise ValueError("length must be positive")

        if self.r_max <= 0:

            raise ValueError("r_max must be positive")

        if len(self.weights) != 4:

            raise ValueError("weights must contain exactly four values")

        if self.n_samples < 2:

            raise ValueError("n_samples must be at least 2")





# ---------------------------------------------------------------------------

# CST building blocks

# ---------------------------------------------------------------------------





def class_function(xi: np.ndarray) -> np.ndarray:

    """Axisymmetric class function C(xi) = sqrt(xi) * (1 - xi).



    Parameters

    ----------

    xi : ndarray

        Normalized axial coordinate in [0, 1].



    Returns

    -------

    ndarray

        Class function values; zero at xi = 0 and xi = 1.

    """

    xi = np.asarray(xi, dtype=float)

    return np.sqrt(np.clip(xi, 0.0, None)) * (1.0 - xi)





def bernstein_basis_cubic(xi: np.ndarray) -> np.ndarray:

    """Cubic Bernstein basis functions evaluated at xi.



    Returns an array of shape (n_points, 4) with columns

    B_{0,3}, B_{1,3}, B_{2,3}, B_{3,3}.

    """

    xi = np.asarray(xi, dtype=float)

    one_minus = 1.0 - xi

    return np.column_stack(

        (

            one_minus**3,

            3.0 * xi * one_minus**2,

            3.0 * xi**2 * one_minus,

            xi**3,

        )

    )





def shape_function(xi: np.ndarray, weights: Sequence[float]) -> np.ndarray:

    """Cubic Bernstein shape function S(xi) = sum_k omega_k B_{k,3}(xi).



    Parameters

    ----------

    xi : ndarray

        Normalized axial coordinate in [0, 1].

    weights : sequence of float

        Four Bernstein weights (omega_0 .. omega_3).

    """

    w = np.asarray(weights, dtype=float)

    if w.shape != (4,):

        raise ValueError("weights must contain exactly four values")

    basis = bernstein_basis_cubic(xi)

    return basis @ w





def radius_profile(xi: np.ndarray, weights: Sequence[float]) -> np.ndarray:

    """Meridional radius r(xi) = C(xi) * S(xi) before normalization."""

    return class_function(xi) * shape_function(xi, weights)





def normalize_radius(r: np.ndarray, r_max: float) -> np.ndarray:

    """Scale radius so that max(r_new) == r_max."""

    r = np.asarray(r, dtype=float)

    peak = float(np.max(r))

    if peak <= 0.0:

        raise ValueError("cannot normalize profile: maximum radius is non-positive")

    return r_max * r / peak





# ---------------------------------------------------------------------------

# Profile assembly

# ---------------------------------------------------------------------------





def sample_xi(n_samples: int) -> np.ndarray:

    """Uniformly sample xi in [0, 1], including both endpoints."""

    return np.linspace(0.0, 1.0, n_samples)





def upper_profile(params: CSTBodyParameters) -> tuple[np.ndarray, np.ndarray]:

    """Generate the upper half of the meridional profile (y >= 0).



    Returns

    -------

    x, y : ndarray

        Axial and radial coordinates of the upper curve.

    """

    xi = sample_xi(params.n_samples)

    r = normalize_radius(radius_profile(xi, params.weights), params.r_max)

    x = params.length * xi

    return x, r





def closed_profile(params: CSTBodyParameters) -> tuple[np.ndarray, np.ndarray]:

    """Build a closed profile by mirroring the upper curve about y = 0.



    The polyline runs: nose -> upper surface -> tail -> lower surface -> nose.

    """

    x_upper, y_upper = upper_profile(params)



    x_lower = x_upper[-2:0:-1]

    y_lower = -y_upper[-2:0:-1]



    x = np.concatenate((x_upper, x_lower, [x_upper[0]]))

    y = np.concatenate((y_upper, y_lower, [y_upper[0]]))

    return x, y





# ---------------------------------------------------------------------------

# I/O and visualization

# ---------------------------------------------------------------------------





def export_profile_csv(

    x: np.ndarray,

    y: np.ndarray,

    path: str | Path,

) -> Path:

    """Write closed profile points to CSV with columns x, y."""

    out = Path(path)

    out.parent.mkdir(parents=True, exist_ok=True)

    data = np.column_stack((x, y))

    np.savetxt(out, data, delimiter=",", header="x,y", comments="", fmt="%.8f")

    return out





def plot_profile(

    x: np.ndarray,

    y: np.ndarray,

    *,

    title: str = "CST axisymmetric body profile",

    show: bool = True,

    save_path: str | Path | None = None,

) -> plt.Figure:

    """Plot the closed meridional profile."""

    fig, ax = plt.subplots(figsize=(10, 4))

    ax.plot(x, y, "b-", linewidth=1.5)

    ax.axhline(0.0, color="k", linewidth=0.5, linestyle="--", alpha=0.4)

    ax.set_xlabel("x")

    ax.set_ylabel("y")

    ax.set_title(title)

    ax.set_aspect("equal", adjustable="box")

    ax.grid(True, alpha=0.3)

    fig.tight_layout()



    if save_path is not None:

        fig.savefig(save_path, dpi=150, bbox_inches="tight")



    if show and plt.get_backend().lower() != "agg":

        plt.show()



    return fig





def generate_body_profile(

    params: CSTBodyParameters,

    csv_path: str | Path = "profile.csv",

    *,

    plot: bool = True,

    plot_save_path: str | Path | None = None,

) -> tuple[np.ndarray, np.ndarray, Path]:

    """End-to-end profile generation, export, and optional plotting.



    Returns

    -------

    x, y, csv_path

        Closed profile coordinates and path to the written CSV file.

    """

    x, y = closed_profile(params)

    out = export_profile_csv(x, y, csv_path)



    if plot:

        plot_profile(x, y, save_path=plot_save_path)



    return x, y, out





# ---------------------------------------------------------------------------

# CLI entry point

# ---------------------------------------------------------------------------





def _default_parameters() -> CSTBodyParameters:

    """Example parameters suitable for a generic rocket-like body."""

    return CSTBodyParameters(

        length=1.0,

        r_max=0.1,

        weights=(1.0, 1.0, 0.8, 0.5),

        n_samples=101,

    )





if __name__ == "__main__":

    params = _default_parameters()

    x, y, csv_out = generate_body_profile(params, csv_path="profile.csv")

    print(f"Wrote {len(x)} profile points to {csv_out.resolve()}")


