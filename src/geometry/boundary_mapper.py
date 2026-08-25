"""Boundary role mapping for meridional wedge Gmsh export."""

from __future__ import annotations

from dataclasses import dataclass

from geometry.constants import GEOMETRY_TOLERANCE
from geometry.wedge_geometry import CurveRole, LineCurve, WedgeMeridionalDomain


@dataclass(frozen=True)
class ExtrusionPatchRole:
    """Post-extrusion lateral surface patch mapped from parent curves."""

    role: CurveRole
    patch_name: str


POST_EXTRUSION_PATCH_ROLES: tuple[ExtrusionPatchRole, ...] = (
    ExtrusionPatchRole(CurveRole.INLET, "inlet"),
    ExtrusionPatchRole(CurveRole.OUTLET, "outlet"),
    ExtrusionPatchRole(CurveRole.FARFIELD_TOP, "farfield_top"),
    ExtrusionPatchRole(CurveRole.ROCKET_WALL, "rocket_wall"),
    ExtrusionPatchRole(CurveRole.AXIS, "axis"),
)

EXTRUSION_WEDGE_BACK_INDEX = 0
EXTRUSION_VOLUME_INDEX = 1
EXTRUSION_LATERAL_START_INDEX = 2


def curves_for_role(domain: WedgeMeridionalDomain, role: CurveRole) -> tuple[int, ...]:
    """Return curve tags assigned to *role* (supports multi-segment roles)."""
    return tuple(curve.tag for curve in domain.all_curves if curve.role is role)


def _point_index(domain: WedgeMeridionalDomain) -> dict[int, object]:
    return {
        point.tag: point
        for point in (*domain.wall_points, *domain.corners)
    }


def _point_on_rotation_axis(point: object, *, tol: float = GEOMETRY_TOLERANCE) -> bool:
    return abs(point.y) <= tol and abs(point.z) <= tol


def _curve_is_degenerate_extrusion_edge(
    curve: LineCurve,
    domain: WedgeMeridionalDomain,
) -> bool:
    """Return True when Extrude about x does not create a lateral surface for *curve*."""
    points = _point_index(domain)
    start = points[curve.start]
    end = points[curve.end]
    return _point_on_rotation_axis(start) and _point_on_rotation_axis(end)


def extrusion_lateral_index_by_curve_tag(
    domain: WedgeMeridionalDomain,
) -> dict[int, int]:
    """Map meridional boundary curve tags to ``ext[]`` lateral indices."""
    mapping: dict[int, int] = {}
    lateral_index = EXTRUSION_LATERAL_START_INDEX

    for curve_tag in domain.outer_loop.curve_tags:
        curve = next(curve for curve in domain.all_curves if curve.tag == abs(curve_tag))
        if isinstance(curve, LineCurve) and _curve_is_degenerate_extrusion_edge(
            curve, domain
        ):
            continue
        mapping[curve.tag] = lateral_index
        lateral_index += 1

    return mapping


def render_post_extrusion_physical_group_lines(
    domain: WedgeMeridionalDomain,
) -> list[str]:
    """Assign extruded lateral surfaces using ``ext[]`` loop-order indices.

    ``Boundary{ Curve{...}; Volume{ext[1]}; }`` yields a well-resolved Gmsh
    surface mesh but ``gmshToFoam`` cannot match those faces to the tet hull.
    Loop-order ``ext[]`` indices import reliably. Wall lateral resolution is
    enforced by a dedicated ``lc_wall`` Threshold field in :mod:`geo_writer`
    (see engineering_archive/legacy/verification/V0_root_cause_investigation.md).
    """
    lateral_index_by_curve = extrusion_lateral_index_by_curve_tag(domain)
    volume_ref = f"ext[{EXTRUSION_VOLUME_INDEX}]"

    lines: list[str] = [
        "// ext[0]=wedge_back, ext[1]=fluid volume (Gmsh Extrude convention)",
        "// Lateral BC surfaces: ext[] in meridional loop order "
        "(axis lines on r=0 omitted)",
        f"// Lateral surfaces span ext[{EXTRUSION_LATERAL_START_INDEX}:] "
        f"({len(lateral_index_by_curve)} sides)",
    ]

    for patch in POST_EXTRUSION_PATCH_ROLES:
        ext_refs: list[str] = []
        for curve_tag in curves_for_role(domain, patch.role):
            lateral_index = lateral_index_by_curve.get(curve_tag)
            if lateral_index is not None:
                ext_refs.append(f"ext[{lateral_index}]")

        if not ext_refs:
            lines.append(f"{patch.patch_name}_surfs[] = {{}};")
            continue

        joined = ", ".join(ext_refs)
        lines.append(f"{patch.patch_name}_surfs[] = {{{joined}}};")

    lines.append("")
    lines.append(
        f'Physical Surface("wedge_back") = {{ext[{EXTRUSION_WEDGE_BACK_INDEX}]}};'
    )

    for patch in POST_EXTRUSION_PATCH_ROLES:
        lines.append(
            f'Physical Surface("{patch.patch_name}") = {patch.patch_name}_surfs[];'
        )

    lines.append(f'Physical Volume("fluid") = {{{volume_ref}}};')
    return lines
