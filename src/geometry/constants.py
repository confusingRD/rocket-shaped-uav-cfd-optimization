"""Shared constants for Gmsh wedge geometry export."""

from __future__ import annotations

import os
from typing import Protocol

COORD_PRECISION = 8
WEDGE_ANGLE_DEG = 5
GEOMETRY_TOLERANCE = 1.0e-12

# Axis-radius sizing: meridional axis boundary sits at r = axis_radius (> 0)
# so Gmsh Extrude about x produces lateral surfaces (lines on r = 0 do not).
AXIS_RADIUS_MESH_FRACTION = 0.25
AXIS_RADIUS_LENGTH_FRACTION = 1.0e-3
AXIS_RADIUS_WALL_FRACTION = 0.15
AXIS_RADIUS_ABSOLUTE_MIN = 1.0e-5

# Keep the wall as one B-spline so OpenCASCADE extrusion produces a single
# lateral ``rocket_wall`` surface (chained segments only yield sliver patches).
WALL_BSPLINE_SEGMENT_SIZE = 128

DEBUG_GEOMETRY = os.environ.get("DEBUG_GEOMETRY", "").lower() in ("1", "true", "yes")


class _ProfileLike(Protocol):
    points: tuple[object, ...]
    length: float


def compute_axis_radius(
    global_size: float,
    profile: _ProfileLike,
) -> float:
    """Return the meridional axis boundary radius for wedge export.

    The value must be large enough for the mesh generator to resolve (several
    cell heights above zero) and small enough to be negligible relative to the
    body scale.  Unlike a fixed 1e-6 sliver, this scales with ``global_size``.
    """
    positive_radii = [
        point.y
        for point in profile.points
        if point.y > GEOMETRY_TOLERANCE
    ]
    min_wall_r = min(positive_radii) if positive_radii else profile.length

    from_mesh = AXIS_RADIUS_MESH_FRACTION * global_size
    from_length = AXIS_RADIUS_LENGTH_FRACTION * profile.length
    from_wall = AXIS_RADIUS_WALL_FRACTION * min_wall_r

    radius = max(from_mesh, from_length, AXIS_RADIUS_ABSOLUTE_MIN)
    return min(radius, from_wall)
