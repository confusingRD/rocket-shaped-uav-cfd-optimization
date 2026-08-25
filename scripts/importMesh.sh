#!/usr/bin/env bash
# End-to-end Gmsh → OpenFOAM mesh import for wedge rocket cases.
# Usage: bash scripts/importMesh.sh [case_directory] [profile.geo] [profile.msh]
set -e
set -o pipefail
CASE_DIR="${1:-.}"
GEO="${2:-profile.geo}"
MSH="${3:-profile.msh}"
SCRIPT_DIR="$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)"
cd "${CASE_DIR}" || exit 1
. /opt/openfoam13/etc/bashrc 2>/dev/null || true

GMSH="${GMSH:-gmsh}"
echo "=== gmsh: ${GEO} → ${MSH} (MSH 2.2) ==="
"${GMSH}" "${GEO}" -3 -format msh22 -o "${MSH}"

echo "=== gmshToFoam ==="
rm -rf constant/polyMesh
gmshToFoam "${MSH}"

echo "=== patch types ==="
bash "${SCRIPT_DIR}/setWedgePatches.sh" "."

echo "=== checkMesh ==="
checkMesh | tee log.checkMesh
