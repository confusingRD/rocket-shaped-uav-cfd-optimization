"""Convert rocket profile CSV files into Gmsh ``.geo`` geometry.

Reads axisymmetric body profiles produced by ``batch/batch_generator.py`` and
writes a 3-D axisymmetric wedge fluid-domain ``.geo`` file suitable for
meshing and OpenFOAM conversion with::

    gmsh rocket.geo -3
    gmshToFoam rocket.msh

Only the upper meridional half (y >= 0) of the closed CSV profile is kept.
The meridional section is built in the x–r plane (z = 0) with the symmetry
axis at y = 0, then rotated by ``-WEDGE_ANGLE_DEG/2`` about the x-axis and
extruded by ``+WEDGE_ANGLE_DEG`` so the wedge is centered on the x–z plane.

The script emits plain ASCII Gmsh syntax only (no Gmsh Python API dependency).
"""

from __future__ import annotations

import argparse
import csv
import sys
from dataclasses import dataclass
from pathlib import Path

from geometry.constants import DEBUG_GEOMETRY, WEDGE_ANGLE_DEG
from geometry.geo_writer import GeoWriter
from geometry.mesh_settings import (
    MeshLevel,
    ProductionMeshSettings,
    get_mesh_settings,
    mesh_level_cli_choices,
    parse_mesh_level,
)
from geometry.validator import build_geometry_debug_report, validate_meridional_domain
from geometry.wedge_geometry import build_wedge_meridional_domain

# ---------------------------------------------------------------------------
# Domain padding factors (multiples of body length L = xmax - xmin)
# ---------------------------------------------------------------------------

LEFT_PADDING_FACTOR = 7.5
RIGHT_PADDING_FACTOR = 15.0
VERTICAL_PADDING_FACTOR = 7.5

# ---------------------------------------------------------------------------
# Data containers
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class ProfilePoint:
    """Single (x, y) coordinate on the rocket meridional profile."""

    x: float
    y: float


@dataclass(frozen=True)
class ProfileData:
    """Parsed profile polyline and derived bounding-box metrics."""

    points: tuple[ProfilePoint, ...]
    xmin: float
    xmax: float
    ymin: float
    ymax: float

    @property
    def length(self) -> float:
        """Axial body length L = xmax - xmin."""
        return self.xmax - self.xmin


@dataclass(frozen=True)
class DomainBounds:
    """Rectangular far-field domain enclosing the profile."""

    x_left: float
    x_right: float
    y_bottom: float
    y_top: float


@dataclass(frozen=True)
class MeshSettings:
    """Global mesh controls (extend later for local refinement / BL)."""

    # Coarse size for fast geometry debugging; reduce to ~0.01–0.005 for production CFD.
    global_size: float = 0.05

# ---------------------------------------------------------------------------
# CSV reading
# ---------------------------------------------------------------------------


def read_profile_csv(csv_path: Path) -> ProfileData:
    """Read a two-column ``x,y`` profile CSV and compute bounding-box metrics.

    Parameters
    ----------
    csv_path:
        Path to the input CSV (header row ``x,y`` required).

    Returns
    -------
    ProfileData
        Ordered profile points and min/max extents.
    """
    if not csv_path.is_file():
        raise FileNotFoundError(f"Profile CSV not found: {csv_path}")

    points: list[ProfilePoint] = []

    with csv_path.open(newline="", encoding="utf-8") as handle:
        reader = csv.DictReader(handle)
        if reader.fieldnames is None:
            raise ValueError(f"CSV file is empty or missing a header: {csv_path}")

        normalized_fields = {name.strip().lower(): name for name in reader.fieldnames}
        if "x" not in normalized_fields or "y" not in normalized_fields:
            raise ValueError(
                f"CSV must contain 'x' and 'y' columns; found {reader.fieldnames}"
            )

        x_key = normalized_fields["x"]
        y_key = normalized_fields["y"]

        for row_index, row in enumerate(reader, start=2):
            try:
                x = float(row[x_key])
                y = float(row[y_key])
            except (KeyError, TypeError, ValueError) as exc:
                raise ValueError(
                    f"Invalid numeric data at row {row_index} in {csv_path}"
                ) from exc
            points.append(ProfilePoint(x=x, y=y))

    if len(points) < 4:
        raise ValueError(
            f"Profile must contain at least 4 points for a spline; got {len(points)}"
        )

    xs = [point.x for point in points]
    ys = [point.y for point in points]

    return ProfileData(
        points=tuple(points),
        xmin=min(xs),
        xmax=max(xs),
        ymin=min(ys),
        ymax=max(ys),
    )


def _is_on_symmetry_axis(point: ProfilePoint, tol: float = 1.0e-12) -> bool:
    """Return True when a point lies on the symmetry axis (y = 0)."""
    return abs(point.y) <= tol


def extract_meridional_profile(
    profile: ProfileData,
    tol: float = 1.0e-12,
) -> ProfileData:
    """Reduce a closed full profile to the upper meridional half (y >= 0).

    Points with y < 0 are discarded. Duplicate axis points (y = 0) at the
    same location are collapsed to a single copy while preserving order along
    the body.
    """
    meridional: list[ProfilePoint] = []

    for point in profile.points:
        if point.y < -tol:
            continue

        if (
            meridional
            and _is_on_symmetry_axis(point, tol)
            and _is_on_symmetry_axis(meridional[-1], tol)
            and _points_are_coincident(point, meridional[-1], tol=tol)
        ):
            continue

        meridional.append(point)

    if (
        len(meridional) >= 2
        and _points_are_coincident(meridional[0], meridional[-1], tol=tol)
    ):
        meridional = meridional[:-1]

    if len(meridional) < 2:
        raise ValueError(
            "Meridional profile must contain at least 2 points after "
            f"upper-half extraction; got {len(meridional)}"
        )

    xs = [point.x for point in meridional]
    ys = [point.y for point in meridional]

    return ProfileData(
        points=tuple(meridional),
        xmin=min(xs),
        xmax=max(xs),
        ymin=0.0,
        ymax=max(ys),
    )


# ---------------------------------------------------------------------------
# Geometry generation
# ---------------------------------------------------------------------------


def compute_domain_bounds(profile: ProfileData) -> DomainBounds:
    """Compute rectangular fluid-domain limits from profile extents and L."""
    length = profile.length
    if length <= 0.0:
        raise ValueError(
            f"Body length must be positive (xmax - xmin); got L = {length}"
        )

    pad = VERTICAL_PADDING_FACTOR * length

    return DomainBounds(
        x_left=profile.xmin - LEFT_PADDING_FACTOR * length,
        x_right=profile.xmax + RIGHT_PADDING_FACTOR * length,
        y_bottom=0.0,
        y_top=profile.ymax + pad,
    )


def _points_are_coincident(
    a: ProfilePoint,
    b: ProfilePoint,
    tol: float = 1.0e-12,
) -> bool:
    """Return True when two profile points occupy the same location."""
    return abs(a.x - b.x) <= tol and abs(a.y - b.y) <= tol


# ---------------------------------------------------------------------------
# .geo writing
# ---------------------------------------------------------------------------


def write_geo_file(
    profile: ProfileData,
    domain: DomainBounds,
    mesh: MeshSettings,
    output_path: Path,
    *,
    production_mesh: ProductionMeshSettings | None = None,
) -> Path:
    """Write a complete Gmsh ``.geo`` wedge file for the fluid domain.

    Parameters
    ----------
    profile:
        Rocket wall profile coordinates.
    domain:
        Rectangular far-field boundary limits.
    mesh:
        Global mesh-size settings.
    output_path:
        Destination ``.geo`` path.

    Returns
    -------
    Path
        The written file path.
    """
    axis_mesh_size = mesh.global_size
    if production_mesh is not None and production_mesh.is_production:
        axis_mesh_size = production_mesh.effective_near_wall_size(profile.length)

    geometry = build_wedge_meridional_domain(
        profile,
        domain,
        global_size=mesh.global_size,
        axis_mesh_size=axis_mesh_size,
    )
    validate_meridional_domain(geometry)
    if DEBUG_GEOMETRY:
        debug_path = output_path.with_suffix(output_path.suffix + ".debug.txt")
        debug_path.write_text(
            build_geometry_debug_report(geometry).render(),
            encoding="utf-8",
        )
    writer = GeoWriter(
        geometry,
        mesh.global_size,
        mesh_settings=production_mesh,
        profile_xmin=profile.xmin,
        profile_xmax=profile.xmax,
        profile_ymax=profile.ymax,
    )
    return writer.write_file(output_path)


# ---------------------------------------------------------------------------
# High-level conversion API
# ---------------------------------------------------------------------------


def convert_csv_to_geo(
    csv_path: Path,
    *,
    output_path: Path | None = None,
    global_size: float | None = None,
    mesh_level: MeshLevel = MeshLevel.DEBUG,
    density_scale: float = 1.0,
) -> Path:
    """Convert one profile CSV into a sibling ``.geo`` file.

    Parameters
    ----------
    csv_path:
        Input profile CSV path.
    output_path:
        Optional explicit output path. Defaults to ``<csv_stem>.geo`` beside
        the CSV.
    global_size:
        Optional override for ``MeshSettings.global_size`` (Gmsh ``lc``).
        When omitted, :class:`MeshSettings` defaults are used.
    density_scale:
        Uniform scale on production volume/surface sizes only (``>1`` coarser,
        ``<1`` finer). Boundary-layer parameters remain unchanged.

    Returns
    -------
    Path
        Path to the written ``.geo`` file.
    """
    csv_path = csv_path.resolve()
    profile = extract_meridional_profile(read_profile_csv(csv_path))
    domain = compute_domain_bounds(profile)

    production_mesh = get_mesh_settings(mesh_level, debug_lc=global_size or 0.05)
    if production_mesh.is_production:
        production_mesh = production_mesh.with_density_scale(density_scale)
        scaled = production_mesh.scaled(profile.length)
        mesh = MeshSettings(global_size=scaled.lc_far)
    else:
        mesh = MeshSettings() if global_size is None else MeshSettings(global_size=global_size)

    if mesh.global_size <= 0.0:
        raise ValueError(f"Global mesh size lc must be positive; got {mesh.global_size}")

    geo_path = output_path or csv_path.with_suffix(".geo")
    prod = production_mesh if production_mesh.is_production else None
    return write_geo_file(profile, domain, mesh, geo_path, production_mesh=prod)


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


def _build_arg_parser() -> argparse.ArgumentParser:
    """Configure command-line arguments."""
    parser = argparse.ArgumentParser(
        description=(
            "Convert a rocket profile CSV into a Gmsh .geo "
            f"{WEDGE_ANGLE_DEG}-degree axisymmetric wedge."
        ),
    )
    parser.add_argument(
        "csv_file",
        type=Path,
        help="Path to the profile CSV (columns: x, y).",
    )
    parser.add_argument(
        "--lc",
        type=float,
        default=None,
        help=(
            "Override global mesh size (Gmsh lc). "
            f"Default: MeshSettings.global_size ({MeshSettings.global_size})."
        ),
    )
    parser.add_argument(
        "-o",
        "--output",
        type=Path,
        default=None,
        help="Output .geo path (default: <csv_stem>.geo next to the CSV).",
    )
    parser.add_argument(
        "--mesh-level",
        choices=mesh_level_cli_choices(),
        default=MeshLevel.DEBUG.value,
        help=(
            "Mesh preset: debug (uniform lc), M4_PRODUCTION (frozen DOE mesh), "
            "or alternate V&V levels M2/M3. Legacy alias M1 → M4_PRODUCTION."
        ),
    )
    parser.add_argument(
        "--density-scale",
        type=float,
        default=1.0,
        help=(
            "Uniform scale on production characteristic lengths only "
            "(>1 coarser, <1 finer). Freezes BL / extent fractions. "
            "Default: 1.0 (unchanged preset)."
        ),
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    """Entry point for ``python csv_to_geo.py <profile.csv>``."""
    args = _build_arg_parser().parse_args(argv)

    try:
        geo_path = convert_csv_to_geo(
            args.csv_file,
            output_path=args.output,
            global_size=args.lc,
            mesh_level=parse_mesh_level(args.mesh_level),
            density_scale=args.density_scale,
        )
    except (FileNotFoundError, ValueError) as exc:
        print(f"Error: {exc}", file=sys.stderr)
        return 1

    print(f"Wrote Gmsh geometry to {geo_path.resolve()}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
