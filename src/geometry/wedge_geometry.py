"""Meridional wedge fluid-domain geometry model for Gmsh export.

Builds a typed topology (points, curves, surface) from a parsed upper-half
profile and rectangular far-field bounds.  The model is consumed by
:class:`geometry.geo_writer.GeoWriter` to emit ASCII ``.geo`` syntax.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from typing import Protocol

from geometry.constants import (
    GEOMETRY_TOLERANCE,
    WALL_BSPLINE_SEGMENT_SIZE,
    WEDGE_ANGLE_DEG,
    compute_axis_radius,
)


class _ProfileLike(Protocol):
    points: tuple[object, ...]
    length: float


class _DomainLike(Protocol):
    x_left: float
    x_right: float
    y_bottom: float
    y_top: float


class CurveRole(Enum):
    """Semantic boundary roles on the meridional plane (y >= 0)."""

    ROCKET_WALL = "rocket_wall"
    AXIS = "axis"
    INLET = "inlet"
    OUTLET = "outlet"
    FARFIELD_TOP = "farfield_top"


@dataclass(frozen=True)
class PointEntity:
    """Gmsh point with assigned tag."""

    tag: int
    x: float
    y: float
    z: float = 0.0


@dataclass(frozen=True)
class BSplineCurve:
    """Open B-spline through wall profile control points."""

    tag: int
    point_tags: tuple[int, ...]
    role: CurveRole = CurveRole.ROCKET_WALL


@dataclass(frozen=True)
class LineCurve:
    """Straight segment between two point tags."""

    tag: int
    start: int
    end: int
    role: CurveRole


@dataclass(frozen=True)
class CurveLoopEntity:
    """Closed loop of oriented curve tags."""

    tag: int
    curve_tags: tuple[int, ...]


@dataclass(frozen=True)
class PlaneSurfaceEntity:
    """Single-loop plane surface (meridional fluid patch)."""

    tag: int
    loop_tag: int
    name: str = "wedge_front"


@dataclass(frozen=True)
class WedgeMeridionalDomain:
    """Complete 2-D meridional fluid domain before wedge extrusion."""

    wall_points: tuple[PointEntity, ...]
    corners: tuple[PointEntity, ...]
    axis_radius: float
    wall_curves: tuple[BSplineCurve, ...]
    axis_left: LineCurve
    axis_right: LineCurve
    inlet: LineCurve
    outlet: LineCurve
    farfield_top: LineCurve
    outer_loop: CurveLoopEntity
    fluid_surface: PlaneSurfaceEntity
    wedge_angle_deg: float

    @property
    def all_curves(self) -> tuple[BSplineCurve | LineCurve, ...]:
        """All boundary curves in loop order."""
        return (
            self.axis_left,
            *self.wall_curves,
            self.axis_right,
            self.outlet,
            self.farfield_top,
            self.inlet,
        )


def _build_wall_bspline_curves(
    wall_points: tuple[PointEntity, ...],
    curve_tag_start: int,
    *,
    segment_size: int = WALL_BSPLINE_SEGMENT_SIZE,
) -> tuple[BSplineCurve, ...]:
    """Chain open BSpline segments through wall control points."""
    if len(wall_points) < 2:
        raise ValueError(
            f"Wall profile must contain at least 2 points; got {len(wall_points)}"
        )

    point_tags = tuple(point.tag for point in wall_points)
    curves: list[BSplineCurve] = []
    tag = curve_tag_start
    start_idx = 0

    while start_idx < len(point_tags) - 1:
        end_idx = min(start_idx + segment_size - 1, len(point_tags) - 1)
        segment_tags = point_tags[start_idx : end_idx + 1]
        curves.append(BSplineCurve(tag=tag, point_tags=segment_tags))
        tag += 1
        if end_idx >= len(point_tags) - 1:
            break
        start_idx = end_idx

    return tuple(curves)


def _offset_wall_points_to_axis(
    profile: _ProfileLike,
    *,
    axis_radius: float,
    wall_start: int,
    tol: float = GEOMETRY_TOLERANCE,
) -> tuple[PointEntity, ...]:
    """Place wall control points; axis endpoints sit at ``axis_radius``."""
    return tuple(
        PointEntity(
            tag=wall_start + index,
            x=point.x,
            y=axis_radius if point.y <= tol else point.y,
        )
        for index, point in enumerate(profile.points)
    )


def build_wedge_meridional_domain(
    profile: _ProfileLike,
    domain: _DomainLike,
    *,
    global_size: float,
    axis_mesh_size: float | None = None,
    wedge_angle_deg: float = WEDGE_ANGLE_DEG,
) -> WedgeMeridionalDomain:
    """Construct meridional wedge topology with consecutive Gmsh entity tags."""
    effective_size = axis_mesh_size if axis_mesh_size is not None else global_size
    axis_radius = compute_axis_radius(effective_size, profile)

    n_wall = len(profile.points)
    wall_start = 1
    corner_start = wall_start + n_wall

    wall_points = _offset_wall_points_to_axis(
        profile,
        axis_radius=axis_radius,
        wall_start=wall_start,
    )

    corner_coords = (
        (domain.x_left, axis_radius),
        (domain.x_right, axis_radius),
        (domain.x_right, domain.y_top),
        (domain.x_left, domain.y_top),
    )
    corners = tuple(
        PointEntity(tag=corner_start + i, x=x, y=y)
        for i, (x, y) in enumerate(corner_coords)
    )

    first_wall = wall_points[0].tag
    last_wall = wall_points[-1].tag
    bl, br, tr, tl = (point.tag for point in corners)

    curve_tag = 1
    wall_curves = _build_wall_bspline_curves(wall_points, curve_tag)
    curve_tag += len(wall_curves)

    axis_left = LineCurve(
        tag=curve_tag,
        start=bl,
        end=first_wall,
        role=CurveRole.AXIS,
    )
    curve_tag += 1

    axis_right = LineCurve(
        tag=curve_tag,
        start=last_wall,
        end=br,
        role=CurveRole.AXIS,
    )
    curve_tag += 1

    outlet = LineCurve(
        tag=curve_tag,
        start=br,
        end=tr,
        role=CurveRole.OUTLET,
    )
    curve_tag += 1

    farfield_top = LineCurve(
        tag=curve_tag,
        start=tr,
        end=tl,
        role=CurveRole.FARFIELD_TOP,
    )
    curve_tag += 1

    inlet = LineCurve(
        tag=curve_tag,
        start=tl,
        end=bl,
        role=CurveRole.INLET,
    )

    loop_curves = (
        axis_left,
        *wall_curves,
        axis_right,
        outlet,
        farfield_top,
        inlet,
    )
    outer_loop = CurveLoopEntity(
        tag=1,
        curve_tags=tuple(curve.tag for curve in loop_curves),
    )

    fluid_surface = PlaneSurfaceEntity(tag=1, loop_tag=outer_loop.tag)

    return WedgeMeridionalDomain(
        wall_points=wall_points,
        corners=corners,
        axis_radius=axis_radius,
        wall_curves=wall_curves,
        axis_left=axis_left,
        axis_right=axis_right,
        inlet=inlet,
        outlet=outlet,
        farfield_top=farfield_top,
        outer_loop=outer_loop,
        fluid_surface=fluid_surface,
        wedge_angle_deg=wedge_angle_deg,
    )
