"""Campaign management constants — paths, states, and checkpoint milestones."""

from __future__ import annotations

from pathlib import Path

from geometry.mesh_settings import MeshLevel


# ---------------------------------------------------------------------------
# Repository paths
# ---------------------------------------------------------------------------

REPO_ROOT = Path(__file__).resolve().parents[2]
SRC_ROOT = REPO_ROOT / "src"

CAMPAIGN_STATE_DIR = REPO_ROOT / "campaign_state"
CONTROL_DIR = CAMPAIGN_STATE_DIR / "control"
GRACEFUL_STOP_PATH = CONTROL_DIR / "graceful_stop.json"
MANIFEST_PATH = CAMPAIGN_STATE_DIR / "campaign_manifest.json"
CHECKPOINTS_DIR = CAMPAIGN_STATE_DIR / "checkpoints"

DATA_DIR = REPO_ROOT / "data"
DEFAULT_DB_PATH = DATA_DIR / "production.db"

PROFILES_ROOT = REPO_ROOT / "profiles"
RESULTS_ROOT = REPO_ROOT / "results"
CASES_ROOT = REPO_ROOT / "cases"

TEMPLATE_CASE = REPO_ROOT / "openfoam" / "template_case"

PROGRESS_REPORTS_DIR = RESULTS_ROOT / "checkpoints"


# ---------------------------------------------------------------------------
# Frozen production configuration
# ---------------------------------------------------------------------------

TOTAL_BODIES = 200
MESH_LEVEL = MeshLevel.M4_PRODUCTION.value
TURBULENCE_MODEL = "kOmegaSST"
SOLVER = "incompressibleFluid"
MAX_ITERATION_BUDGET = 1000

GEOMETRY_VERSION = "CST_BERNSTEIN_V1"
MESH_VERSION = "M4_PRODUCTION_V1"
LHS_BATCH = "LHS_V1"


# ---------------------------------------------------------------------------
# Campaign states
# ---------------------------------------------------------------------------

CAMPAIGN_STATES = frozenset(
    {"READY", "RUNNING", "PAUSED", "INTERRUPTED", "COMPLETED", "FAILED"}
)

SIMULATION_STATES = frozenset(
    {"PENDING", "RUNNING", "COMPLETED", "FAILED", "INTERRUPTED", "SKIPPED"}
)


# ---------------------------------------------------------------------------
# Checkpointing and monitoring
# ---------------------------------------------------------------------------

MAJOR_CHECKPOINT_MILESTONES = (5, 10, 25, 50, 100, 150, 200)
DEFAULT_BACKUP_RETENTION = 10

HEALTH_SAMPLE_INTERVAL_S = 60.0
DASHBOARD_REFRESH_INTERVAL_S = 3.0
SOLVER_STALL_THRESHOLD_S = 300.0
DISK_WARNING_THRESHOLD_GB = 10.0
RAM_WARNING_THRESHOLD_PCT = 90.0