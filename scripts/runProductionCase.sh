#!/usr/bin/env bash
# Generate M4_PRODUCTION mesh, import to OpenFOAM, and run production CFD for one case.
set -euo pipefail

ROOT="$(CDPATH= cd -- "$(dirname -- "$0")/.." && pwd)"
CASE_DIR="${1:-${ROOT}/cases/Body_0001}"
BODY_ID="$(basename "${CASE_DIR}")"
PROFILE_CSV="${ROOT}/profiles/${BODY_ID}/profile.csv"
MESH_LEVEL="${MESH_LEVEL:-M4_PRODUCTION}"

if [ ! -d "${CASE_DIR}" ]; then
    echo "ERROR: case directory not found: ${CASE_DIR}" >&2
    exit 1
fi

if [ ! -f "${PROFILE_CSV}" ]; then
    echo "ERROR: profile CSV not found: ${PROFILE_CSV}" >&2
    exit 1
fi

echo "=== csv_to_geo (${MESH_LEVEL}) ==="
python3 "${ROOT}/src/csv_to_geo.py" "${PROFILE_CSV}" \
  --mesh-level "${MESH_LEVEL}" \
  -o "${CASE_DIR}/profile.geo"

echo "=== importMesh ==="
GMSH="${GMSH:-gmsh}" bash "${ROOT}/scripts/importMesh.sh" "${CASE_DIR}"

echo "=== foamRun ==="
(
  cd "${CASE_DIR}"
  source /opt/openfoam13/etc/bashrc
  foamRun 2>&1 | tee log.foamRun
)

echo "=== yPlus (foamPostProcess) ==="
(
  cd "${CASE_DIR}"
  source /opt/openfoam13/etc/bashrc
  foamPostProcess -func yPlus -latestTime 2>&1 | tee log.yPlus
)
