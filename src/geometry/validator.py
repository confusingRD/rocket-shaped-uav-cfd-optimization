"""Validation and debug reporting for meridional wedge geometry."""

from __future__ import annotations

from dataclasses import dataclass, field

from geometry.constants import GEOMETRY_TOLERANCE
from geometry.wedge_geometry import (
    BSplineCurve,
    LineCurve,
    PointEntity,
    WedgeMeridionalDomain,
)


class GeometryValidationError(ValueError):
    """Raised when meridional geometry fails pre-export validation."""


@dataclass
class GeometryDebugReport:
    """Structured geometry diagnostics for DEBUG_GEOMETRY mode."""

    point_numbering: list[str] = field(default_factory=list)
    curve_numbering: list[str] = field(default_factory=list)
    curve_orientations: list[str] = field(default_factory=list)
    curve_connectivity: list[str] = field(default_factory=list)
    signed_loop_area: float | None = None
    topology_problems: list[str] = field(default_factory=list)

    def render(self) -> str:
        """Format the report as plain text."""
        lines = ["=== Geometry Debug Report ===", ""]
        lines.append("--- Point numbering ---")
        lines.extend(self.point_numbering or ["(none)"])
        lines.append("")
        lines.append("--- Curve numbering ---")
        lines.extend(self.curve_numbering or ["(none)"])
        lines.append("")
        lines.append("--- Curve orientation ---")
        lines.extend(self.curve_orientations or ["(none)"])
        lines.append("")
        lines.append("--- Curve connectivity ---")
        lines.extend(self.curve_connectivity or ["(none)"])
        lines.append("")
        area = self.signed_loop_area
        lines.append("--- Signed loop area ---")
        lines.append(f"{area:.16e}" if area is not None else "(not computed)")
        lines.append("")
        lines.append("--- Detected topology problems ---")
        if self.topology_problems:
            lines.extend(f"- {problem}" for problem in self.topology_problems)
        else:
            lines.append("(none)")
        lines.append("")
        return "\n".join(lines)


def _point_index(domain: WedgeMeridionalDomain) -> dict[int, PointEntity]:
    return {
        point.tag: point
        for point in (*domain.wall_points, *domain.corners)
    }


def _points_coincident(
    a: PointEntity,
    b: PointEntity,
    *,
    tol: float = GEOMETRY_TOLERANCE,
) -> bool:
    return abs(a.x - b.x) <= tol and abs(a.y - b.y) <= tol


def _distance(a: PointEntity, b: PointEntity) -> float:
    dx = a.x - b.x
    dy = a.y - b.y
    return (dx * dx + dy * dy) ** 0.5


def _curve_endpoints(
    curve: BSplineCurve | LineCurve,
    points: dict[int, PointEntity],
    *,
    forward: bool = True,
) -> tuple[PointEntity, PointEntity]:
    if isinstance(curve, BSplineCurve):
        start_tag = curve.point_tags[0]
        end_tag = curve.point_tags[-1]
    else:
        start_tag = curve.start
        end_tag = curve.end
    start = points[start_tag]
    end = points[end_tag]
    if forward:
        return start, end
    return end, start


def _curves_in_loop_order(
    domain: WedgeMeridionalDomain,
) -> tuple[tuple[BSplineCurve | LineCurve, bool], ...]:
    curve_by_tag = {curve.tag: curve for curve in domain.all_curves}
    oriented: list[tuple[BSplineCurve | LineCurve, bool]] = []
    for tag in domain.outer_loop.curve_tags:
        oriented.append((curve_by_tag[abs(tag)], tag > 0))
    return tuple(oriented)


def _collect_duplicate_consecutive_wall_points(
    domain: WedgeMeridionalDomain,
    *,
    tol: float = GEOMETRY_TOLERANCE,
) -> list[str]:
    problems: list[str] = []
    for index in range(1, len(domain.wall_points)):
        previous = domain.wall_points[index - 1]
        current = domain.wall_points[index]
        if _points_coincident(previous, current, tol=tol):
            problems.append(
                "Duplicate consecutive wall points at tags "
                f"{previous.tag} and {current.tag}"
            )
    return problems


def _collect_axis_endpoint_problems(
    domain: WedgeMeridionalDomain,
    *,
    tol: float = GEOMETRY_TOLERANCE,
) -> list[str]:
    problems: list[str] = []
    first = domain.wall_points[0]
    last = domain.wall_points[-1]
    axis_radius = domain.axis_radius
    if abs(first.y - axis_radius) > tol:
        problems.append(
            f"First wall point (tag {first.tag}) must lie at axis radius "
            f"y={axis_radius}; got y={first.y}"
        )
    if abs(last.y - axis_radius) > tol:
        problems.append(
            f"Last wall point (tag {last.tag}) must lie at axis radius "
            f"y={axis_radius}; got y={last.y}"
        )
    return problems


def _collect_bspline_junction_problems(
    domain: WedgeMeridionalDomain,
) -> list[str]:
    """Adjacent wall BSpline segments must share a control point at junctions."""
    problems: list[str] = []
    for index in range(len(domain.wall_curves) - 1):
        left = domain.wall_curves[index]
        right = domain.wall_curves[index + 1]
        if left.point_tags[-1] != right.point_tags[0]:
            problems.append(
                "Wall BSpline segments do not chain: curve "
                f"{left.tag} ends at Point({left.point_tags[-1]}) but curve "
                f"{right.tag} starts at Point({right.point_tags[0]})"
            )
    return problems


def _collect_orientation_problems(
    domain: WedgeMeridionalDomain,
    *,
    tol: float = GEOMETRY_TOLERANCE,
) -> list[str]:
    """Outer loop must use forward-oriented curves and positive signed area."""
    problems: list[str] = []
    for tag in domain.outer_loop.curve_tags:
        if tag < 0:
            problems.append(
                f"Outer loop curve {tag} is reversed; "
                "consistent forward orientation required"
            )

    area = compute_signed_loop_area(domain, tol=tol)
    if area <= tol:
        problems.append(
            f"Signed loop area must be positive for CCW fluid orientation; got {area}"
        )
    return problems


def _collect_zero_length_curve_problems(
    domain: WedgeMeridionalDomain,
    points: dict[int, PointEntity],
    *,
    tol: float = GEOMETRY_TOLERANCE,
) -> list[str]:
    problems: list[str] = []
    for curve in domain.all_curves:
        start, end = _curve_endpoints(curve, points)
        if _distance(start, end) <= tol:
            problems.append(f"Zero-length curve {curve.tag}")
        if isinstance(curve, BSplineCurve) and len(curve.point_tags) < 2:
            problems.append(
                f"BSpline curve {curve.tag} must have at least 2 control points"
            )
    return problems


def _collect_connectivity_problems(
    domain: WedgeMeridionalDomain,
    points: dict[int, PointEntity],
    *,
    tol: float = GEOMETRY_TOLERANCE,
) -> list[str]:
    problems: list[str] = []
    loop_curves = _curves_in_loop_order(domain)
    if not loop_curves:
        return ["Outer loop contains no curves"]

    endpoints = [
        _curve_endpoints(curve, points, forward=forward)
        for curve, forward in loop_curves
    ]
    for index in range(len(endpoints) - 1):
        _, current_end = endpoints[index]
        next_start, _ = endpoints[index + 1]
        if not _points_coincident(current_end, next_start, tol=tol):
            current_curve, _ = loop_curves[index]
            next_curve, _ = loop_curves[index + 1]
            problems.append(
                "Curve endpoints do not connect: curve "
                f"{current_curve} end ({current_end.x}, {current_end.y}) != "
                f"curve {next_curve} start ({next_start.x}, {next_start.y})"
            )

    first_start, _ = endpoints[0]
    _, last_end = endpoints[-1]
    if not _points_coincident(last_end, first_start, tol=tol):
        problems.append(
            "Curve loop is not closed: last curve end "
            f"({last_end.x}, {last_end.y}) != first curve start "
            f"({first_start.x}, {first_start.y})"
        )

    return problems


def compute_signed_loop_area(
    domain: WedgeMeridionalDomain,
    *,
    tol: float = GEOMETRY_TOLERANCE,
) -> float:
    """Shoelace signed area from outer-loop curve endpoints."""
    points = _point_index(domain)
    loop_curves = _curves_in_loop_order(domain)
    vertices: list[PointEntity] = []

    for curve, forward in loop_curves:
        start, end = _curve_endpoints(curve, points, forward=forward)
        if not vertices or not _points_coincident(vertices[-1], start, tol=tol):
            vertices.append(start)
        if not _points_coincident(vertices[-1], end, tol=tol):
            vertices.append(end)

    if len(vertices) < 3:
        return 0.0

    area = 0.0
    count = len(vertices)
    for index in range(count):
        next_index = (index + 1) % count
        area += vertices[index].x * vertices[next_index].y
        area -= vertices[next_index].x * vertices[index].y
    return area / 2.0


def build_geometry_debug_report(
    domain: WedgeMeridionalDomain,
    *,
    tol: float = GEOMETRY_TOLERANCE,
) -> GeometryDebugReport:
    """Collect geometry diagnostics without raising."""
    points = _point_index(domain)
    report = GeometryDebugReport()

    for point in (*domain.wall_points, *domain.corners):
        report.point_numbering.append(
            f"Point({point.tag}) = ({point.x}, {point.y}, {point.z})"
        )

    for curve in domain.all_curves:
        if isinstance(curve, BSplineCurve):
            control = ", ".join(str(tag) for tag in curve.point_tags)
            report.curve_numbering.append(
                f"BSpline({curve.tag}) role={curve.role.value} points={{{control}}}"
            )
        else:
            report.curve_numbering.append(
                f"Line({curve.tag}) role={curve.role.value} "
                f"start={curve.start} end={curve.end}"
            )

    loop_curves = _curves_in_loop_order(domain)
    for curve, forward in loop_curves:
        start, end = _curve_endpoints(curve, points, forward=forward)
        orientation = "forward" if forward else "reversed"
        report.curve_orientations.append(
            f"Curve {curve.tag} ({orientation}): "
            f"({start.x}, {start.y}) -> ({end.x}, {end.y})"
        )

    for index, (curve, forward) in enumerate(loop_curves):
        start, end = _curve_endpoints(curve, points, forward=forward)
        loop_tag = domain.outer_loop.curve_tags[index]
        report.curve_connectivity.append(
            f"Loop[{index}] curve {loop_tag}: "
            f"start=Point({start.tag}) end=Point({end.tag})"
        )

    report.signed_loop_area = compute_signed_loop_area(domain, tol=tol)
    report.topology_problems.extend(_collect_duplicate_consecutive_wall_points(domain, tol=tol))
    report.topology_problems.extend(_collect_axis_endpoint_problems(domain, tol=tol))
    report.topology_problems.extend(_collect_bspline_junction_problems(domain))
    report.topology_problems.extend(
        _collect_zero_length_curve_problems(domain, points, tol=tol)
    )
    report.topology_problems.extend(
        _collect_connectivity_problems(domain, points, tol=tol)
    )
    report.topology_problems.extend(_collect_orientation_problems(domain, tol=tol))

    return report


def validate_meridional_domain(
    domain: WedgeMeridionalDomain,
    *,
    tol: float = GEOMETRY_TOLERANCE,
) -> None:
    """Validate geometry before Gmsh export; raise on failure."""
    report = build_geometry_debug_report(domain, tol=tol)
    if report.topology_problems:
        detail = "; ".join(report.topology_problems)
        raise GeometryValidationError(f"Invalid meridional geometry: {detail}")
