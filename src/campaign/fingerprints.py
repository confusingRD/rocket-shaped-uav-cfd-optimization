"""Configuration fingerprinting — SHA256 hashes for reproducibility auditing."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path

from campaign.constants import TEMPLATE_CASE

FINGERPRINT_KEYS = (
    "geometry",
    "profile_csv",
    "profile_geo",
    "mesh",
    "fvSchemes",
    "fvSolution",
    "controlDict",
    "momentumTransport",
    "transportProperties",
    "physicalProperties",
)


def sha256_file(path: Path) -> str | None:
    if not path.exists() or not path.is_file():
        return None
    digest = hashlib.sha256()
    with open(path, "rb") as handle:
        for chunk in iter(lambda: handle.read(65536), b""):
            digest.update(chunk)
    return digest.hexdigest()


def sha256_directory(path: Path) -> str | None:
    """Hash all files under a directory in sorted order (e.g. polyMesh)."""
    if not path.is_dir():
        return None
    digest = hashlib.sha256()
    for file_path in sorted(path.rglob("*")):
        if file_path.is_file():
            relative_path = file_path.relative_to(path).as_posix()
            digest.update(relative_path.encode("utf-8"))
            with open(file_path, "rb") as handle:
                for chunk in iter(lambda: handle.read(65536), b""):
                    digest.update(chunk)
    return digest.hexdigest()


def compute_body_fingerprints(
    *,
    profile_dir: Path,
    case_dir: Path | None = None,
) -> dict[str, str | None]:
    """Compute per-simulation configuration hashes."""
    if case_dir is None:
        case_dir = profile_dir  # fallback

    geo = case_dir / "profile.geo"

    fingerprints: dict[str, str | None] = {
        "geometry": sha256_file(profile_dir / "metadata.json"),
        "profile_csv": sha256_file(profile_dir / "profile.csv"),
        "profile_geo": sha256_file(geo),
        "mesh": sha256_directory(case_dir / "constant" / "polyMesh") if case_dir else None,
        "fvSchemes": sha256_file(case_dir / "system" / "fvSchemes") if case_dir else None,
        "fvSolution": sha256_file(case_dir / "system" / "fvSolution") if case_dir else None,
        "controlDict": sha256_file(case_dir / "system" / "controlDict") if case_dir else None,
        "momentumTransport": sha256_file(case_dir / "constant" / "momentumTransport")
        if case_dir
        else None,
        "transportProperties": sha256_file(case_dir / "constant" / "transportProperties")
        if case_dir
        else None,
        "physicalProperties": sha256_file(case_dir / "constant" / "physicalProperties")
        if case_dir
        else None,
    }
    return fingerprints


def compute_template_fingerprints() -> dict[str, str | None]:
    """Hash frozen production template dictionaries (no per-body overrides)."""
    return {
        "fvSchemes": sha256_file(TEMPLATE_CASE / "system" / "fvSchemes"),
        "fvSolution": sha256_file(TEMPLATE_CASE / "system" / "fvSolution"),
        "controlDict": sha256_file(TEMPLATE_CASE / "system" / "controlDict"),
        "momentumTransport": sha256_file(TEMPLATE_CASE / "constant" / "momentumTransport"),
        "transportProperties": sha256_file(TEMPLATE_CASE / "constant" / "transportProperties"),
        "physicalProperties": sha256_file(TEMPLATE_CASE / "constant" / "physicalProperties"),
    }


def aggregate_config_hash(fingerprints: dict[str, str | None]) -> str:
    """Single composite hash stored in simulation_runs.config_hash."""
    payload = json.dumps(
        {k: fingerprints.get(k) for k in FINGERPRINT_KEYS},
        sort_keys=True,
        separators=(",", ":"),
    )
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def compare_fingerprints(
    left: dict[str, str | None],
    right: dict[str, str | None],
) -> list[str]:
    """Return keys whose hashes differ between two fingerprint dicts."""
    mismatches: list[str] = []
    for key in FINGERPRINT_KEYS:
        if left.get(key) != right.get(key):
            mismatches.append(key)
    return mismatches
