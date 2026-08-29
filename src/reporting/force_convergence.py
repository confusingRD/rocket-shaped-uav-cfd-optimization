"""Engineering force-convergence analysis from OpenFOAM forceCoeffs output.

Classifies drag-coefficient stationarity independently of OpenFOAM residualControl.
Does not modify solver settings or iteration limits.
"""

from __future__ import annotations

import statistics
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Sequence



@dataclass(frozen=True)
class ForceConvergenceConfig:
    """Configurable engineering convergence criteria."""

    window_size: int = 100
    min_samples: int = 20
    max_variation_pct: float = 0.5
    max_trend_pct: float = 0.25

    def as_dict(self) -> dict[str, float | int]:
        return {
            "window_size": self.window_size,
            "min_samples": self.min_samples,
            "max_variation_pct": self.max_variation_pct,
            "max_trend_pct": self.max_trend_pct,
        }


DEFAULT_FORCE_CONVERGENCE_CONFIG = ForceConvergenceConfig()


def find_force_coeffs_dat(case_dir: Path) -> Path | None:
    """Locate reconstructed forceCoeffs output for a production case."""
    candidates = [
        case_dir
        / "postProcessing"
        / "forceCoeffsIncompressible"
        / "0"
        / "forceCoeffs.dat",
        case_dir / "postProcessing" / "forceCoeffs" / "0" / "coefficient.dat",
        case_dir / "postProcessing" / "forceCoeffs" / "0" / "forceCoeffs.dat",
    ]
    for path in candidates:
        if path.is_file():
            return path
    return None


def parse_force_coeffs_cd(path: Path) -> list[float]:
    """Return Cd samples in file order (final reconstruction time series)."""
    cds: list[float] = []
    if not path.is_file():
        return cds
    for line in path.read_text(encoding="utf-8", errors="replace").splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        parts = line.split()
        # Time  Cm  Cd  Cl  ...
        if len(parts) >= 4:
            try:
                cds.append(float(parts[2]))
            except ValueError:
                continue
    return cds


def _linear_trend_pct(values: Sequence[float]) -> float:
    """Total linear drift over the window as % of mean |Cd|."""
    n = len(values)
    if n < 2:
        return 0.0
    mean = sum(values) / n
    if mean == 0:
        return float("inf")
    x_mean = (n - 1) / 2.0
    y_mean = mean
    num = sum((i - x_mean) * (values[i] - y_mean) for i in range(n))
    den = sum((i - x_mean) ** 2 for i in range(n))
    slope = num / den if den else 0.0
    total_change = slope * (n - 1)
    return abs(total_change) / abs(mean) * 100.0


def analyze_force_convergence(
    cd_series: Sequence[float],
    *,
    config: ForceConvergenceConfig = DEFAULT_FORCE_CONVERGENCE_CONFIG,
) -> dict[str, Any]:
    """Compute force-based convergence metrics and classification."""
    cfg = config
    total = len(cd_series)
    if total == 0:
        return {
            "force_converged": False,
            "cd_mean_last100": None,
            "cd_std_last100": None,
            "cd_variation_percent": None,
            "cd_max_deviation": None,
            "cd_trend_percent": None,
            "force_samples": 0,
            "force_convergence_window": cfg.window_size,
            "force_convergence_reason": "No forceCoeffs Cd samples",
            "force_convergence_config": cfg.as_dict(),
        }

    window = list(cd_series[-cfg.window_size :])
    n = len(window)
    if n < cfg.min_samples:
        return {
            "force_converged": False,
            "cd_mean_last100": None,
            "cd_std_last100": None,
            "cd_variation_percent": None,
            "cd_max_deviation": None,
            "cd_trend_percent": None,
            "force_samples": n,
            "force_convergence_window": cfg.window_size,
            "force_convergence_reason": (
                f"Insufficient samples ({n} < {cfg.min_samples})"
            ),
            "force_convergence_config": cfg.as_dict(),
        }

    mean_cd = sum(window) / n
    if mean_cd == 0:
        std_cd = statistics.pstdev(window) if n > 1 else 0.0
        variation_pct = float("inf")
        max_dev = max(abs(x - mean_cd) for x in window)
        trend_pct = _linear_trend_pct(window)
        force_converged = False
        reason = "Mean Cd is zero"
    else:
        std_cd = statistics.pstdev(window) if n > 1 else 0.0
        max_dev = max(abs(x - mean_cd) for x in window)
        variation_pct = std_cd / abs(mean_cd) * 100.0
        trend_pct = _linear_trend_pct(window)

        variation_ok = variation_pct < cfg.max_variation_pct
        trend_ok = trend_pct < cfg.max_trend_pct
        force_converged = variation_ok and trend_ok
        reasons: list[str] = []
        if not variation_ok:
            reasons.append(
                f"Cd variation {variation_pct:.3f}% >= {cfg.max_variation_pct}%"
            )
        if not trend_ok:
            reasons.append(f"Cd trend {trend_pct:.3f}% >= {cfg.max_trend_pct}%")
        reason = "; ".join(reasons) if reasons else "Force convergence criteria met"

    return {
        "force_converged": force_converged,
        "cd_mean_last100": mean_cd,
        "cd_std_last100": std_cd,
        "cd_variation_percent": variation_pct,
        "cd_max_deviation": max_dev,
        "cd_trend_percent": trend_pct,
        "force_samples": n,
        "force_convergence_window": min(cfg.window_size, total),
        "force_convergence_reason": reason,
        "force_convergence_config": cfg.as_dict(),
    }


def analyze_case_force_convergence(
    case_dir: Path,
    *,
    config: ForceConvergenceConfig = DEFAULT_FORCE_CONVERGENCE_CONFIG,
) -> dict[str, Any]:
    """Analyze force convergence from a case directory."""
    path = find_force_coeffs_dat(case_dir)
    if path is None:
        return analyze_force_convergence([], config=config)
    return analyze_force_convergence(parse_force_coeffs_cd(path), config=config)


def merge_force_convergence_into_summary(
    summary: dict[str, Any],
    case_dir: Path,
    *,
    config: ForceConvergenceConfig = DEFAULT_FORCE_CONVERGENCE_CONFIG,
) -> dict[str, Any]:
    """Append force-convergence fields to a summary dict (in place)."""
    metrics = analyze_case_force_convergence(case_dir, config=config)
    summary.update(metrics)
    return summary


def residual_control_passed(summary: dict[str, Any]) -> bool:
    """True when OpenFOAM residualControl reported convergence."""
    return bool(summary.get("converged_residualControl"))


def doe_convergence_recommendation(
    *,
    residual_pass: bool,
    force_pass: bool,
    variation_pct: float | None,
) -> str:
    """Human-readable DOE action recommendation."""
    if residual_pass and force_pass:
        return "Accept"
    if force_pass:
        return "Accept for DOE ranking"
    if variation_pct is not None and variation_pct < 1.0:
        return "Accept for DOE ranking"
    return "Review before DOE"


def analyze_force_convergence_campaign(
    rows: list[Any],
    *,
    config: ForceConvergenceConfig = DEFAULT_FORCE_CONVERGENCE_CONFIG,
) -> dict[str, Any]:
    """Aggregate residual vs force convergence statistics for reporting."""
    from reporting.production_db import CampaignRow

    simulated = [
        r
        for r in rows
        if isinstance(r, CampaignRow)
        and r.status in ("COMPLETED", "FAILED")
        and r.cd is not None
    ]

    def _residual_pass(row: CampaignRow) -> bool:
        return row.converged is True

    def _force_pass(row: CampaignRow) -> bool:
        return getattr(row, "force_converged", None) is True

    both_pass = sum(1 for r in simulated if _residual_pass(r) and _force_pass(r))
    residual_fail_force_pass = sum(
        1 for r in simulated if not _residual_pass(r) and _force_pass(r)
    )
    both_fail = sum(
        1 for r in simulated if not _residual_pass(r) and not _force_pass(r)
    )
    residual_pass_force_fail = sum(
        1 for r in simulated if _residual_pass(r) and not _force_pass(r)
    )
    force_unknown = sum(
        1 for r in simulated if getattr(r, "force_converged", None) is None
    )

    force_pass_rows = [r for r in simulated if _force_pass(r)]
    ranking_assessment = _assess_ranking_sufficiency(simulated, force_pass_rows)

    return {
        "config": config.as_dict(),
        "simulated_with_cd": len(simulated),
        "residual_pass_force_pass": both_pass,
        "residual_fail_force_pass": residual_fail_force_pass,
        "residual_pass_force_fail": residual_pass_force_fail,
        "residual_fail_force_fail": both_fail,
        "force_unknown": force_unknown,
        "ranking_assessment": ranking_assessment,
    }


def _assess_ranking_sufficiency(
    all_rows: list[Any],
    force_pass_rows: list[Any],
) -> str:
    """Assess whether the available force histories support relative drag ranking."""
    if len(all_rows) < 3:
        return (
            "Insufficient completed/failed bodies with Cd to assess ranking stability."
        )

    force_pass_frac = len(force_pass_rows) / max(len(all_rows), 1)

    if force_pass_frac >= 0.8:
        return (
            "Most bodies meet force-convergence criteria; the available solver "
            "histories appear sufficient for relative drag ranking."
        )

    if force_pass_frac >= 0.5:
        return (
            "Mixed force convergence — rank correlations should be verified before "
            "committing to the full DOE. Additional iterations may help bodies with "
            "high late-time Cd drift."
        )

    return (
        "Majority of bodies fail force-convergence; the available solver histories "
        "may be insufficient for reliable relative drag ranking. Review before full DOE."
    )


def force_convergence_table_rows(
    rows: list[Any],
) -> list[dict[str, Any]]:
    """Build per-body rows for the final report convergence table."""
    from reporting.production_db import CampaignRow

    out: list[dict[str, Any]] = []
    for row in sorted(rows, key=lambda r: r.sample_id):
        if not isinstance(row, CampaignRow):
            continue
        if row.status not in ("COMPLETED", "FAILED") or row.cd is None:
            continue
        residual = row.converged is True
        force = getattr(row, "force_converged", None)
        variation = getattr(row, "cd_variation_percent", None)
        out.append(
            {
                "sample_id": row.sample_id,
                "residual_pass": residual,
                "force_pass": force,
                "cd_variation_percent": variation,
                "recommendation": doe_convergence_recommendation(
                    residual_pass=residual,
                    force_pass=force is True,
                    variation_pct=variation,
                ),
            }
        )
    return out
