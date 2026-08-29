"""Automatic computational environment detection for campaign reproducibility."""

from __future__ import annotations

import json
import os
import platform
import re
import socket
import subprocess
import sys
from pathlib import Path
from typing import Any

from campaign.constants import (
    CAMPAIGN_STATE_DIR,
    MESH_LEVEL,
    REPO_ROOT,
    SOLVER,
    TURBULENCE_MODEL,
)
from reporting.production_db import get_git_commit, get_openfoam_version, utc_now_iso

UNKNOWN = "UNKNOWN"
ENVIRONMENT_OVERRIDE_PATH = CAMPAIGN_STATE_DIR / "environment_override.json"

def _safe_str(value: Any) -> str:
    if value is None:
        return UNKNOWN
    text = str(value).strip()
    return text if text else UNKNOWN


def _run_command(cmd: list[str], *, timeout: float = 10.0) -> str | None:
    try:
        out = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            timeout=timeout,
            errors="replace",
        )
        text = (out.stdout or out.stderr or "").strip()
        return text or None
    except (subprocess.SubprocessError, FileNotFoundError, OSError):
        return None


def _read_proc_cpuinfo() -> dict[str, Any]:
    info: dict[str, Any] = {
        "model_name": None,
        "physical_ids": set(),
        "cpu_cores_per_socket": None,
    }
    try:
        with open("/proc/cpuinfo", encoding="utf-8", errors="replace") as handle:
            for line in handle:
                if line.startswith("model name") and info["model_name"] is None:
                    info["model_name"] = line.split(":", 1)[1].strip()
                elif line.startswith("physical id"):
                    info["physical_ids"].add(int(line.split(":", 1)[1].strip()))
                elif line.startswith("cpu cores"):
                    info["cpu_cores_per_socket"] = int(line.split(":", 1)[1].strip())
    except OSError:
        pass
    return info


def detect_cpu_model() -> str:
    cpuinfo = _read_proc_cpuinfo()
    if cpuinfo.get("model_name"):
        return _safe_str(cpuinfo["model_name"])
    return _safe_str(platform.processor() or platform.machine())


def detect_physical_cores() -> int | str:
    cpuinfo = _read_proc_cpuinfo()
    physical_ids = cpuinfo.get("physical_ids") or set()
    cores_per_socket = cpuinfo.get("cpu_cores_per_socket")
    if physical_ids and cores_per_socket:
        return len(physical_ids) * int(cores_per_socket)
    lscpu = _run_command(["lscpu"])
    if lscpu:
        match = re.search(r"^Core\(s\) per socket:\s*(\d+)", lscpu, flags=re.M)
        sockets = re.search(r"^Socket\(s\):\s*(\d+)", lscpu, flags=re.M)
        if match and sockets:
            return int(match.group(1)) * int(sockets.group(1))
    logical = os.cpu_count()
    return logical if logical else UNKNOWN


def detect_logical_cpus() -> int | str:
    count = os.cpu_count()
    return count if count is not None else UNKNOWN


def _read_meminfo() -> dict[str, int]:
    info: dict[str, int] = {}
    try:
        with open("/proc/meminfo", encoding="utf-8") as handle:
            for line in handle:
                key, value = line.split(":", 1)
                info[key.strip()] = int(value.strip().split()[0])  # kB
    except (OSError, ValueError):
        pass
    return info


def _kb_to_gb(kb: int) -> float:
    return round(kb / (1024 * 1024), 2)


def _meminfo_kb(key: str) -> int | None:
    value = _read_meminfo().get(key)
    return value if value is not None else None


def _is_wsl() -> bool:
    if "microsoft" in platform.uname().release.lower():
        return True
    try:
        return "microsoft" in Path("/proc/version").read_text(encoding="utf-8", errors="replace").lower()
    except OSError:
        return False


def _host_ram_override(root: Path | None = None) -> float | None:
    env_val = os.environ.get("HOST_PHYSICAL_RAM_GB")
    if env_val:
        try:
            return round(float(env_val), 2)
        except ValueError:
            pass
    override_path = (
        ENVIRONMENT_OVERRIDE_PATH
        if root is None
        else root / "campaign_state" / "environment_override.json"
    )    
    if override_path.exists():
        try:
            data = json.loads(override_path.read_text(encoding="utf-8"))
            value = data.get("host_physical_ram_gb")
            if value is not None:
                return round(float(value), 2)
        except (json.JSONDecodeError, OSError, TypeError, ValueError):
            pass
    return None


def _parse_bytes_from_output(text: str) -> int | None:
    for token in text.replace(",", " ").split():
        if token.isdigit() and len(token) >= 9:
            return int(token)
    return None


def _detect_host_physical_ram_from_windows() -> float | None:
    ps_paths = (
        "/mnt/c/Windows/System32/WindowsPowerShell/v1.0/powershell.exe",
        "powershell.exe",
    )
    ps_command = "(Get-CimInstance Win32_ComputerSystem).TotalPhysicalMemory"
    for ps in ps_paths:
        text = _run_command([ps, "-NoProfile", "-Command", ps_command], timeout=15.0)
        if text:
            nbytes = _parse_bytes_from_output(text)
            if nbytes:
                return round(nbytes / (1024**3), 2)

    wmic_paths = (
        "/mnt/c/Windows/System32/wbem/wmic.exe",
        "wmic.exe",
    )
    for wmic in wmic_paths:
        text = _run_command([wmic, "computersystem", "get", "TotalPhysicalMemory"], timeout=15.0)
        if text:
            nbytes = _parse_bytes_from_output(text)
            if nbytes:
                return round(nbytes / (1024**3), 2)
    return None


def detect_wsl_memory_limit_gb() -> float | str:
    """WSL/Linux memory ceiling visible to the campaign (``MemTotal``)."""
    kb = _meminfo_kb("MemTotal")
    return _kb_to_gb(kb) if kb is not None else UNKNOWN


def detect_wsl_available_ram_gb() -> float | str:
    """Free+reclaimable memory inside WSL at detection time (``MemAvailable``)."""
    kb = _meminfo_kb("MemAvailable")
    if kb is None:
        kb = _meminfo_kb("MemFree")
    return _kb_to_gb(kb) if kb is not None else UNKNOWN


def detect_swap_total_gb() -> float | str:
    kb = _meminfo_kb("SwapTotal")
    return _kb_to_gb(kb) if kb is not None else UNKNOWN


def detect_host_physical_ram_gb(*, root: Path | None = None) -> float | str:
    """Physical workstation RAM (Windows host when running under WSL)."""
    override = _host_ram_override(root)
    if override is not None:
        return override
    if _is_wsl():
        host = _detect_host_physical_ram_from_windows()
        return host if host is not None else UNKNOWN
    return detect_wsl_memory_limit_gb()


def detect_total_ram_gb() -> float | str:
    """Backward-compatible alias for WSL/Linux memory limit (``MemTotal``)."""
    return detect_wsl_memory_limit_gb()


def format_ram_gb(value: Any, *, decimals: int = 1) -> str:
    if value in (None, UNKNOWN):
        return UNKNOWN
    try:
        return f"{float(value):.{decimals}f} GB"
    except (TypeError, ValueError):
        return UNKNOWN


def format_ram_gb_compact(value: Any) -> str:
    """Compact RAM display for validation output."""
    if value in (None, UNKNOWN):
        return UNKNOWN
    try:
        gb = float(value)
        if abs(gb - round(gb)) < 0.05:
            return f"{int(round(gb))} GB"
        return f"{gb:.1f} GB"
    except (TypeError, ValueError):
        return UNKNOWN


def detect_operating_system() -> str:
    try:
        import distro  # type: ignore[import-untyped]

        name = distro.name(pretty=True)
        version = distro.version(pretty=True)
        if name and name != UNKNOWN:
            text = f"{name} {version}".strip()
            if "microsoft" in platform.uname().release.lower() or os.path.exists("/proc/version"):
                if "microsoft" in Path("/proc/version").read_text(encoding="utf-8", errors="replace").lower():
                    text += " (WSL2)"
            return _safe_str(text)
    except Exception:
        pass
    base = platform.platform()
    if "WSL" in base or "microsoft" in platform.uname().release.lower():
        return _safe_str(base + " (WSL2)")
    return _safe_str(base)


def detect_mpi() -> tuple[str, str]:
    for impl, cmd in (
        ("OpenMPI", ["mpirun", "--version"]),
        ("MPICH", ["mpirun", "-version"]),
        ("Intel MPI", ["mpiexec", "--version"]),
    ):
        text = _run_command(cmd)
        if text:
            first_line = text.splitlines()[0]
            version_match = re.search(r"(\d+\.\d+(?:\.\d+)*)", first_line)
            version = version_match.group(1) if version_match else first_line
            return impl, _safe_str(version)
    ompi = _run_command(["ompi_info", "--version"])
    if ompi:
        version_match = re.search(r"(\d+\.\d+(?:\.\d+)*)", ompi)
        return "OpenMPI", _safe_str(version_match.group(1) if version_match else ompi.splitlines()[0])
    return UNKNOWN, UNKNOWN


def detect_gmsh_version() -> str:
    gmsh_bin = os.environ.get("GMSH", "gmsh")
    text = _run_command([gmsh_bin, "--version"])
    if text:
        version_match = re.search(r"(\d+\.\d+(?:\.\d+)*)", text)
        if version_match:
            return version_match.group(1)
        return _safe_str(text.splitlines()[0])
    return UNKNOWN


def _git_branch(root: Path) -> str | None:
    try:
        out = subprocess.run(
            ["git", "-C", str(root), "rev-parse", "--abbrev-ref", "HEAD"],
            capture_output=True,
            text=True,
            check=True,
            timeout=5,
        )
        return out.stdout.strip() or None
    except (subprocess.SubprocessError, FileNotFoundError):
        return None


def _git_dirty(root: Path) -> bool | None:
    try:
        out = subprocess.run(
            ["git", "-C", str(root), "status", "--porcelain"],
            capture_output=True,
            text=True,
            check=True,
            timeout=5,
        )
        return bool(out.stdout.strip())
    except (subprocess.SubprocessError, FileNotFoundError):
        return None


def detect_project_version(root: Path = REPO_ROOT) -> str:
    for candidate in (root / "VERSION", root / "version.txt"):
        if candidate.exists():
            text = candidate.read_text(encoding="utf-8").strip()
            if text:
                return text
    tag = _run_command(["git", "-C", str(root), "describe", "--tags", "--always"], timeout=5)
    if tag and not tag.lower().startswith("fatal:"):
        return _safe_str(tag)
    return UNKNOWN


def format_duration(seconds: float | None) -> str:
    if seconds is None:
        return UNKNOWN
    seconds = max(0.0, float(seconds))
    days, rem = divmod(int(seconds), 86400)
    hours, rem = divmod(rem, 3600)
    minutes, secs = divmod(rem, 60)
    parts: list[str] = []
    if days:
        parts.append(f"{days}d")
    if hours:
        parts.append(f"{hours}h")
    if minutes:
        parts.append(f"{minutes}m")
    if secs or not parts:
        parts.append(f"{secs}s")
    return " ".join(parts)


def detect_environment(
    *,
    campaign_uuid: str | None = None,
    campaign_creation_time: str | None = None,
    campaign_start_time: str | None = None,
    campaign_end_time: str | None = None,
    workers: int | None = None,
    mpi_ranks_per_worker: int | None = None,
    mesh_level: str = MESH_LEVEL,
    solver: str = SOLVER,
    turbulence_model: str = TURBULENCE_MODEL,
    root: Path | None = None,
) -> dict[str, Any]:
    """Detect machine, software, and campaign configuration."""
    root = root or REPO_ROOT
    mpi_impl, mpi_version = detect_mpi()
    git_dirty = _git_dirty(root)
    if git_dirty is True:
        git_state = "Dirty"
    elif git_dirty is False:
        git_state = "Clean"
    else:
        git_state = UNKNOWN

    duration_s = None
    duration_human = UNKNOWN
    if campaign_start_time and campaign_end_time:
        try:
            from datetime import datetime

            start = datetime.fromisoformat(campaign_start_time.replace("Z", "+00:00"))
            end = datetime.fromisoformat(campaign_end_time.replace("Z", "+00:00"))
            duration_s = max(0.0, (end - start).total_seconds())
            duration_human = format_duration(duration_s)
        except (ValueError, TypeError):
            duration_s = None
            duration_human = UNKNOWN

    wsl_limit = detect_wsl_memory_limit_gb()
    wsl_available = detect_wsl_available_ram_gb()
    swap_total = detect_swap_total_gb()
    host_ram = detect_host_physical_ram_gb(root=root)

    return {
        "captured_at": utc_now_iso(),
        "machine": {
            "cpu_model": detect_cpu_model(),
            "physical_cores": detect_physical_cores(),
            "logical_cpus": detect_logical_cpus(),
            "host_physical_ram_gb": host_ram,
            "wsl_memory_limit_gb": wsl_limit,
            "wsl_available_ram_gb": wsl_available,
            "swap_total_gb": swap_total,
            "total_ram_gb": wsl_limit,
            "hostname": _safe_str(socket.gethostname()),
            "operating_system": detect_operating_system(),
            "kernel_version": _safe_str(platform.release()),
            "architecture": _safe_str(platform.machine()),
        },
        "software": {
            "python_version": _safe_str(sys.version.split()[0]),
            "openfoam_version": _safe_str(get_openfoam_version()),
            "mpi_implementation": mpi_impl,
            "mpi_version": mpi_version,
            "gmsh_version": detect_gmsh_version(),
            "git_commit": _safe_str(get_git_commit(root)),
            "git_branch": _safe_str(_git_branch(root)),
            "git_dirty": git_dirty,
            "git_state": git_state,
            "project_version": detect_project_version(root),
            "working_directory": _safe_str(str(root.resolve())),
        },
        "campaign": {
            "workers": workers if workers is not None else UNKNOWN,
            "mpi_ranks_per_worker": mpi_ranks_per_worker if mpi_ranks_per_worker is not None else UNKNOWN,
            "mesh_level": _safe_str(mesh_level),
            "solver": _safe_str(solver),
            "turbulence_model": _safe_str(turbulence_model),
            "campaign_uuid": _safe_str(campaign_uuid),
            "campaign_creation_time": _safe_str(campaign_creation_time),
            "campaign_start_time": _safe_str(campaign_start_time) if campaign_start_time else UNKNOWN,
            "campaign_end_time": _safe_str(campaign_end_time) if campaign_end_time else UNKNOWN,
            "campaign_duration_s": duration_s if duration_s is not None else UNKNOWN,
            "campaign_duration_human": duration_human,
        },
    }


def merge_environment(
    existing: dict[str, Any] | None,
    detected: dict[str, Any],
    *,
    preserve_start_time: bool = True,
) -> dict[str, Any]:
    """Merge a fresh detection with stored environment (preserve start times)."""
    if not existing:
        return detected
    merged = {
        "captured_at": detected.get("captured_at"),
        "machine": detected.get("machine", {}),
        "software": detected.get("software", {}),
        "campaign": dict(detected.get("campaign", {})),
    }
    old_campaign = existing.get("campaign", {})
    if preserve_start_time:
        old_start = old_campaign.get("campaign_start_time")
        if old_start and old_start != UNKNOWN:
            merged["campaign"]["campaign_start_time"] = old_start
        old_creation = old_campaign.get("campaign_creation_time")
        if old_creation and old_creation != UNKNOWN:
            merged["campaign"]["campaign_creation_time"] = old_creation
    if merged["campaign"].get("campaign_end_time") in (None, UNKNOWN):
        old_end = old_campaign.get("campaign_end_time")
        if old_end and old_end != UNKNOWN:
            merged["campaign"]["campaign_end_time"] = old_end

    if merged["campaign"].get("campaign_duration_s") in (None, UNKNOWN):
        old_duration_s = old_campaign.get("campaign_duration_s")
        if old_duration_s not in (None, UNKNOWN):
            merged["campaign"]["campaign_duration_s"] = old_duration_s

    if merged["campaign"].get("campaign_duration_human") in (None, UNKNOWN):
        old_duration_human = old_campaign.get("campaign_duration_human")
        if old_duration_human not in (None, UNKNOWN):
            merged["campaign"]["campaign_duration_human"] = old_duration_human

    return merged

def environment_markdown_table(environment: dict[str, Any]) -> str:
    """Render computational environment as markdown tables for the final report."""
    machine = environment.get("machine", {})
    software = environment.get("software", {})
    campaign = environment.get("campaign", {})

    def row(label: str, value: Any) -> str:
        return f"| {label} | {_safe_str(value)} |"

    ram = machine.get("total_ram_gb")
    ram_display = format_ram_gb(ram) if ram not in (None, UNKNOWN) else UNKNOWN

    machine_table = "\n".join(
        [
            "| Item | Value |",
            "|------|-------|",
            row("CPU", machine.get("cpu_model")),
            row("Physical cores", machine.get("physical_cores")),
            row("Logical CPUs", machine.get("logical_cpus")),
            row("Host Physical RAM", format_ram_gb(machine.get("host_physical_ram_gb"))),
            row("WSL Memory Limit", format_ram_gb(machine.get("wsl_memory_limit_gb"))),
            row("Available WSL RAM", format_ram_gb(machine.get("wsl_available_ram_gb"))),
            row("Swap", format_ram_gb(machine.get("swap_total_gb"))),
            row("RAM", ram_display),
            row("OS", machine.get("operating_system")),
            row("Kernel", machine.get("kernel_version")),
            row("Architecture", machine.get("architecture")),
        ]
    )
    software_table = "\n".join(
        [
            "| Item | Value |",
            "|------|-------|",
            row("Python", software.get("python_version")),
            row("OpenFOAM", software.get("openfoam_version")),
            row("MPI", f"{software.get('mpi_implementation')} {software.get('mpi_version')}"),
            row("Gmsh", software.get("gmsh_version")),
            row("Git commit", software.get("git_commit")),
            row("Branch", software.get("git_branch")),
            row("Repository state", software.get("git_state")),
            row("Project version", software.get("project_version")),
        ]
    )
    campaign_table = "\n".join(
        [
            "| Item | Value |",
            "|------|-------|",
            row("Workers", campaign.get("workers")),
            row("MPI ranks / worker", campaign.get("mpi_ranks_per_worker")),
            row("Mesh", campaign.get("mesh_level")),
            row("Solver", campaign.get("solver")),
            row("Turbulence", campaign.get("turbulence_model")),
            row("Campaign UUID", campaign.get("campaign_uuid")),
            row("Campaign creation", campaign.get("campaign_creation_time")),
            row("Campaign start", campaign.get("campaign_start_time")),
            row("Campaign end", campaign.get("campaign_end_time")),
            row("Campaign duration", campaign.get("campaign_duration_human")),
        ]
    )
    return (
        "### Machine\n\n"
        f"{machine_table}\n\n"
        "### Software\n\n"
        f"{software_table}\n\n"
        "### Campaign\n\n"
        f"{campaign_table}\n"
    )


def environment_plaintext(environment: dict[str, Any]) -> str:
    """Plain-text environment block for PDF export."""
    return "\n".join(environment_validation_lines(environment)).replace("=", "").strip()


def environment_validation_lines(
    environment: dict[str, Any],
    *,
    width: int = 14,
) -> list[str]:
    """Format environment as aligned lines for ``validate`` output."""
    machine = environment.get("machine", {})
    software = environment.get("software", {})
    campaign = environment.get("campaign", {})

    def line(label: str, value: Any) -> str:
        text = _safe_str(value)
        return f"{label:<{width}} {text}"

    def ram_block(label: str, value: Any) -> list[str]:
        return [f"{label}:", format_ram_gb_compact(value)]

    lines = [
        "",
        "===== Computational Environment =====",
        line("CPU", machine.get("cpu_model")),
        line("Physical cores", machine.get("physical_cores")),
        line("Logical CPUs", machine.get("logical_cpus")),
        "",
        *ram_block("Host Physical RAM", machine.get("host_physical_ram_gb")),
        "",
        *ram_block("WSL Memory Limit", machine.get("wsl_memory_limit_gb")),
        "",
        *ram_block("Available WSL RAM", machine.get("wsl_available_ram_gb")),
        "",
        *ram_block("Swap", machine.get("swap_total_gb")),
        "",
        line("OS", machine.get("operating_system")),
        line("Kernel", machine.get("kernel_version")),
        line("Architecture", machine.get("architecture")),
        "",
        line("Python", software.get("python_version")),
        line("OpenFOAM", software.get("openfoam_version")),
        line("MPI", f"{software.get('mpi_implementation')} {software.get('mpi_version')}"),
        line("Gmsh", software.get("gmsh_version")),
        line("Git commit", software.get("git_commit")),
        line("Branch", software.get("git_branch")),
        line("Repository", software.get("git_state")),
        line("Project", software.get("project_version")),
        "",
        line("Workers", campaign.get("workers")),
        line("MPI / worker", campaign.get("mpi_ranks_per_worker")),
        line("Mesh", campaign.get("mesh_level")),
        line("Solver", campaign.get("solver")),
        line("Turbulence", campaign.get("turbulence_model")),
        line("Campaign UUID", campaign.get("campaign_uuid")),
        "=====================================",
    ]
    return lines
