"""Production mesh sizing presets for Gmsh wedge export.

All characteristic lengths scale with body length *L* so the same preset
applies across the 200-body DOE (lambda in [3.5, 6.0], fixed R = 0.07 m).

``MeshLevel.M4_PRODUCTION`` is the frozen production mesh selected by the
mesh-independence study (study level M4, ``density_scale = 0.60`` on the
pre-study M1 methodology).  Wall-function-compatible first-layer height
(y+ ~ 30-100), prism boundary-layer inflation on ``rocket_wall``, moderate
local refinement, and a cell count that fits typical workstation RAM.
"""

from __future__ import annotations

import warnings
from dataclasses import dataclass
from enum import Enum
from typing import Final

# Reference flight condition (mission_definition.md) — drives first-layer height.
U_INF: Final[float] = 138.89  # m/s
NU: Final[float] = 1.462e-5  # m^2/s
RHO: Final[float] = 1.225  # kg/m^3
T_INF: Final[float] = 288.15  # K
P_INF: Final[float] = 101_325.0  # Pa

# Anchor length for nondimensional preset definition (Body_0001).
L_REF: Final[float] = 0.63


class MeshLevel(str, Enum):
    """Mesh refinement ladder for V&V."""

    DEBUG = "debug"
    M4_PRODUCTION = "M4_PRODUCTION"
    M2 = "M2"
    M3 = "M3"


# Deprecated CLI alias — study level M1 (coarse) is unrelated; this was the
# old code name for the frozen production preset (study M4).
_DEPRECATED_MESH_LEVEL_ALIASES: Final[dict[str, MeshLevel]] = {
    "M1": MeshLevel.M4_PRODUCTION,
}


def parse_mesh_level(value: str) -> MeshLevel:
    """Resolve a mesh-level string, accepting deprecated aliases."""
    if value in _DEPRECATED_MESH_LEVEL_ALIASES:
        warnings.warn(
            "Mesh level 'M1' is deprecated; use 'M4_PRODUCTION' "
            "(frozen production mesh from mesh-independence study M4).",
            DeprecationWarning,
            stacklevel=2,
        )
        return _DEPRECATED_MESH_LEVEL_ALIASES[value]
    return MeshLevel(value)


@dataclass(frozen=True)
class ProductionMeshSettings:
    """Nondimensional Gmsh sizing parameters scaled by body length *L*."""

    level: MeshLevel
    lc_far_divisor: float
    lc_min_ratio: float
    near_body_extent_fraction: float
    nose_extent_fraction: float
    nose_lc_ratio: float
    wake_extent_fraction: float
    wake_lc_ratio: float
    bl_growth_ratio: float
    bl_thickness_fraction: float
    y_plus_target: float
    # Wall lateral surface (5° revolved strip) needs a much finer size than
    # the near-body volume field; otherwise Gmsh places a single circumferential
    # row of triangles (~O(L/lc_min) faces) on rocket_wall.
    lc_wall_divisor: float
    wall_extent_fraction: float
    skin_friction_coefficient: float = 0.003
    # When > 0, total BL thickness is computed from h0, growth ratio, and N
    # (geometric progression). Otherwise ``bl_thickness_fraction * L`` is used.
    bl_n_layers: int = 0

    @property
    def is_production(self) -> bool:
        return self.level != MeshLevel.DEBUG

    def lc_far(self, length: float) -> float:
        return length / self.lc_far_divisor

    def lc_min(self, length: float) -> float:
        return self.lc_far(length) * self.lc_min_ratio

    def lc_wall(self, length: float) -> float:
        """Target edge length on the revolved rocket_wall strip."""
        return length / self.lc_wall_divisor

    def lc_nose(self, length: float) -> float:
        return self.lc_far(length) * self.nose_lc_ratio

    def lc_wake(self, length: float) -> float:
        return self.lc_far(length) * self.wake_lc_ratio

    def near_body_extent(self, length: float) -> float:
        return self.near_body_extent_fraction * length

    def wall_extent(self, length: float) -> float:
        """Radial distance over which ``lc_wall`` is enforced."""
        return max(3.0 * self.lc_wall(length), self.wall_extent_fraction * length)

    def nose_extent(self, length: float) -> float:
        return self.nose_extent_fraction * length

    def wake_extent(self, length: float) -> float:
        return self.wake_extent_fraction * length

    def first_layer_height(self) -> float:
        """Estimate h0 for target y+ using flat-plate u_tau from Cf."""
        u_tau = (0.5 * self.skin_friction_coefficient * RHO * U_INF**2 / RHO) ** 0.5
        return self.y_plus_target * NU / u_tau

    def bl_thickness(self, length: float) -> float:
        """Total inflation thickness [m].

        Prefer geometric-progression thickness from ``bl_n_layers`` when set:
        δ = h0 · (r^N − 1) / (r − 1). Otherwise use ``bl_thickness_fraction · L``.
        """
        if self.bl_n_layers > 0 and self.bl_growth_ratio > 1.0:
            h0 = self.first_layer_height()
            r = self.bl_growth_ratio
            n = self.bl_n_layers
            return h0 * (r**n - 1.0) / (r - 1.0)
        return self.bl_thickness_fraction * length

    def effective_near_wall_size(self, length: float) -> float:
        """Minimum length scale for axis-radius coupling."""
        candidates = [self.lc_wall(length), self.lc_min(length)]
        if self.bl_thickness(length) > 0.0:
            candidates.append(self.first_layer_height())
        return min(candidates)

    def with_density_scale(self, scale: float) -> ProductionMeshSettings:
        """Uniformly scale volume/surface characteristic lengths.

        ``scale > 1`` → coarser cells; ``scale < 1`` → finer cells.
        Relative size ratios, extent fractions of *L*, and boundary-layer
        parameters (h₀, N, growth) are frozen — only global density changes.

        Intended for mesh-independence studies that must not alter production
        wall-normal methodology.
        """
        if scale <= 0.0:
            raise ValueError(f"density_scale must be positive; got {scale}")
        if abs(scale - 1.0) < 1.0e-15:
            return self
        return ProductionMeshSettings(
            level=self.level,
            lc_far_divisor=self.lc_far_divisor / scale,
            lc_min_ratio=self.lc_min_ratio,
            near_body_extent_fraction=self.near_body_extent_fraction,
            nose_extent_fraction=self.nose_extent_fraction,
            nose_lc_ratio=self.nose_lc_ratio,
            wake_extent_fraction=self.wake_extent_fraction,
            wake_lc_ratio=self.wake_lc_ratio,
            bl_growth_ratio=self.bl_growth_ratio,
            bl_thickness_fraction=self.bl_thickness_fraction,
            y_plus_target=self.y_plus_target,
            lc_wall_divisor=self.lc_wall_divisor / scale,
            wall_extent_fraction=self.wall_extent_fraction,
            skin_friction_coefficient=self.skin_friction_coefficient,
            bl_n_layers=self.bl_n_layers,
        )

    def scaled(self, length: float, *, l_ref: float = L_REF) -> ScaledMeshSizes:
        """Return absolute sizes for a body of length *length*."""
        return ScaledMeshSizes(
            level=self.level,
            length=length,
            lc_far=self.lc_far(length),
            lc_min=self.lc_min(length),
            lc_wall=self.lc_wall(length),
            lc_nose=self.lc_nose(length),
            lc_wake=self.lc_wake(length),
            near_body_extent=self.near_body_extent(length),
            wall_extent=self.wall_extent(length),
            nose_extent=self.nose_extent(length),
            wake_extent=self.wake_extent(length),
            bl_first_layer=self.first_layer_height(),
            bl_growth_ratio=self.bl_growth_ratio,
            bl_thickness=self.bl_thickness(length),
            bl_n_layers=self.bl_n_layers,
            axis_mesh_size=self.effective_near_wall_size(length),
        )


@dataclass(frozen=True)
class ScaledMeshSizes:
    """Absolute mesh sizes for one body length."""

    level: MeshLevel
    length: float
    lc_far: float
    lc_min: float
    lc_wall: float
    lc_nose: float
    lc_wake: float
    near_body_extent: float
    wall_extent: float
    nose_extent: float
    wake_extent: float
    bl_first_layer: float
    bl_growth_ratio: float
    bl_thickness: float
    bl_n_layers: int
    axis_mesh_size: float

    @property
    def bl_enabled(self) -> bool:
        return self.bl_thickness > 0.0


MESH_PRESETS: dict[MeshLevel, ProductionMeshSettings] = {
    MeshLevel.DEBUG: ProductionMeshSettings(
        level=MeshLevel.DEBUG,
        lc_far_divisor=1.0,
        lc_min_ratio=1.0,
        near_body_extent_fraction=0.0,
        nose_extent_fraction=0.0,
        nose_lc_ratio=1.0,
        wake_extent_fraction=0.0,
        wake_lc_ratio=1.0,
        bl_growth_ratio=1.25,
        bl_thickness_fraction=0.0,
        y_plus_target=50.0,
        lc_wall_divisor=1.0,
        wall_extent_fraction=0.0,
        bl_n_layers=0,
    ),
    MeshLevel.M4_PRODUCTION: ProductionMeshSettings(
        level=MeshLevel.M4_PRODUCTION,
        # Frozen Jul 25, 2026 after mesh-independence study (study level M4):
        # former density_scale=0.60 on the pre-study M1 methodology
        # (lc_far L/24, lc_wall L/1200) → divisors ×(1/0.60).
        # BL / extents / size ratios unchanged.
        # Gate: |ΔCd| M4→M5 = 1.21% < 2% on Body_0001 (kOmegaSST).
        lc_far_divisor=40.0,
        lc_min_ratio=0.70,
        near_body_extent_fraction=0.10,
        nose_extent_fraction=0.18,
        nose_lc_ratio=0.80,
        wake_extent_fraction=3.0,
        wake_lc_ratio=0.90,
        # Wall-function BL: h0≈1e-4 m → y+≈37 at Cf=0.003 reference.
        bl_growth_ratio=1.25,
        bl_thickness_fraction=0.0,
        bl_n_layers=14,
        y_plus_target=37.0,
        # L/2000 wall strip (was L/1200 × 0.60 density).
        lc_wall_divisor=2000.0,
        wall_extent_fraction=0.02,
    ),
    MeshLevel.M2: ProductionMeshSettings(
        level=MeshLevel.M2,
        lc_far_divisor=32.0,
        lc_min_ratio=0.60,
        near_body_extent_fraction=0.12,
        nose_extent_fraction=0.20,
        nose_lc_ratio=0.70,
        wake_extent_fraction=4.0,
        wake_lc_ratio=0.80,
        bl_growth_ratio=1.25,
        bl_thickness_fraction=0.0,
        bl_n_layers=14,
        y_plus_target=50.0,
        lc_wall_divisor=1600.0,
        wall_extent_fraction=0.02,
    ),
    MeshLevel.M3: ProductionMeshSettings(
        level=MeshLevel.M3,
        lc_far_divisor=40.0,
        lc_min_ratio=0.55,
        near_body_extent_fraction=0.14,
        nose_extent_fraction=0.22,
        nose_lc_ratio=0.65,
        wake_extent_fraction=5.0,
        wake_lc_ratio=0.75,
        bl_growth_ratio=1.22,
        bl_thickness_fraction=0.0,
        bl_n_layers=16,
        y_plus_target=40.0,
        lc_wall_divisor=2000.0,
        wall_extent_fraction=0.025,
    ),
}


def mesh_level_cli_choices() -> list[str]:
    """CLI choices including deprecated aliases for backward compatibility."""
    return [level.value for level in MeshLevel] + list(_DEPRECATED_MESH_LEVEL_ALIASES)


def get_mesh_settings(level: MeshLevel, *, debug_lc: float = 0.05) -> ProductionMeshSettings:
    """Return preset; DEBUG uses uniform ``debug_lc`` via ``scaled()`` override."""
    preset = MESH_PRESETS[level]
    if level == MeshLevel.DEBUG:
        return ProductionMeshSettings(
            level=MeshLevel.DEBUG,
            lc_far_divisor=1.0 / debug_lc,
            lc_min_ratio=1.0,
            near_body_extent_fraction=0.0,
            nose_extent_fraction=0.0,
            nose_lc_ratio=1.0,
            wake_extent_fraction=0.0,
            wake_lc_ratio=1.0,
            bl_growth_ratio=1.25,
            bl_thickness_fraction=0.0,
            y_plus_target=50.0,
            lc_wall_divisor=1.0 / debug_lc,
            wall_extent_fraction=0.0,
            bl_n_layers=0,
        )
    return preset
