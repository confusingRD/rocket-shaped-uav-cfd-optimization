"""Geometry package: CST profile generation and Gmsh wedge export."""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from geometry.geometry_generator import CSTBodyParameters, generate_body_profile

__all__ = ["CSTBodyParameters", "generate_body_profile"]


def __getattr__(name: str):
    if name in __all__:
        from geometry.geometry_generator import CSTBodyParameters, generate_body_profile

        return {"CSTBodyParameters": CSTBodyParameters, "generate_body_profile": generate_body_profile}[
            name
        ]
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
