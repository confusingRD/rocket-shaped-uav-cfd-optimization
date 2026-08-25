#!/bin/sh
# Post-gmshToFoam helper: assign OpenFOAM patch types after Gmsh import.
# gmshToFoam preserves Physical Surface names but uses type "patch" for all patches.
set -e
CASE_DIR="${1:-.}"
cd "${CASE_DIR}" || exit 1
BOUNDARY=constant/polyMesh/boundary
if [ ! -f "$BOUNDARY" ]; then
    echo "ERROR: $BOUNDARY not found. Run gmshToFoam first." >&2
    exit 1
fi
for patch in wedge_front wedge_back; do
    if grep -q "^    ${patch}$" "$BOUNDARY"; then
        sed -i "/^    ${patch}$/,/^    }\$/ s/type            patch;/type            wedge;/" "$BOUNDARY"
        sed -i "/^    ${patch}$/,/^    }\$/ s/physicalType    patch;/physicalType    wedge;/" "$BOUNDARY"
        echo "Set ${patch} -> wedge"
    else
        echo "WARNING: patch rocket_wall not found in boundary file" >&2
    fi
done
if grep -q "^    rocket_wall$" "$BOUNDARY"; then
    sed -i "/^    rocket_wall$/,/^    }\$/ s/type            patch;/type            wall;/" "$BOUNDARY"
    echo "Set rocket_wall -> wall"
fi
if grep -q "^    axis$" "$BOUNDARY"; then
    sed -i "/^    axis$/,/^    }\$/ s/type            patch;/type            symmetry;/" "$BOUNDARY"
    sed -i "/^    axis$/,/^    }\$/ s/type            symmetryPlane;/type            symmetry;/" "$BOUNDARY"
    sed -i "/^    axis$/,/^    }\$/ s/physicalType    patch;/physicalType    symmetry;/" "$BOUNDARY"
    sed -i "/^    axis$/,/^    }\$/ s/physicalType    symmetryPlane;/physicalType    symmetry;/" "$BOUNDARY"
    echo "Set axis -> symmetry"
fi
