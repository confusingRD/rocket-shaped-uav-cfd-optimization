# OpenFOAM template case — axisymmetric rocket-body wedge flow

This directory contains the reusable OpenFOAM case template used by the CFD automation workflow.

The template represents external flow around an axisymmetric rocket-shaped body using a finite-angle wedge domain. Geometry and mesh generation are handled with Gmsh, while the resulting mesh is converted and prepared for OpenFOAM through the scripts provided in the repository.

## Solver

The production setup uses **OpenFOAM 13** with:

```text
application     foamRun;
solver          incompressibleFluid;
```

This corresponds to a steady incompressible SIMPLE-based RANS workflow, analogous to the legacy `simpleFoam` approach.

The default production run is limited to 1000 solver iterations, with residual-based stopping criteria defined in `system/fvSolution`.

## Mesh workflow

Meshes are generated externally with Gmsh rather than `blockMesh`.

From the repository root, a typical mesh-import workflow is:

```bash
source /opt/openfoam13/etc/bashrc
GMSH=/path/to/gmsh bash scripts/importMesh.sh cases/Body_XXXX
```

The import pipeline is:

```text
Gmsh .geo
    ↓
Gmsh MSH 2.2 mesh
    ↓
gmshToFoam
    ↓
setWedgePatches.sh
    ↓
checkMesh
```

The Gmsh mesh is explicitly written in **MSH 2.2** format for compatibility with `gmshToFoam`.

## Turbulence model

The production RANS model is **k–ω SST**, configured in:

```text
constant/momentumTransport
```

Required initial and boundary-condition fields are:

```text
0/
├── U
├── p
├── k
├── omega
└── nut
```

The rocket surface uses:

- `kqRWallFunction` for turbulent kinetic energy;
- `omegaWallFunction` for specific dissipation rate;
- `nutkWallFunction` for turbulent kinematic viscosity.

The production freestream values are:

```text
k     = 2.89 m²/s²
omega = 70 s⁻¹
```

The turbulence-model choice was examined separately during the CFD verification process; the production dataset presented in this repository uses k–ω SST.

## Boundary conditions

The OpenFOAM patch names correspond directly to the Gmsh Physical Surface names.

The expected patches are:

```text
inlet
outlet
farfield_top
rocket_wall
axis
wedge_front
wedge_back
```

After `gmshToFoam`, `scripts/setWedgePatches.sh` assigns the required OpenFOAM patch types:

```text
wedge_front, wedge_back → wedge
rocket_wall             → wall
axis                    → symmetry
```

No `topoSet` or `createPatch` stage is required for the standard workflow.

## Force coefficients

Aerodynamic force coefficients are evaluated on:

```text
rocket_wall
```

using the configuration in:

```text
system/forceCoeffsIncompressible
```

The template uses:

```text
U∞   = 138.89 m/s
Rmax = 0.07 m
Aref = πRmax² ≈ 0.0153938 m²
```

The computational domain represents a **5° wedge** of the full axisymmetric body. The wedge-based force coefficient is therefore converted to a full-body-equivalent value using the corresponding angular scaling during post-processing.

Geometry-dependent reference quantities such as body length and center of rotation may be updated by the automation workflow for individual cases.

## Template structure

```text
template_case/
├── 0/
│   ├── U
│   ├── p
│   ├── k
│   ├── omega
│   └── nut
│
├── constant/
│   ├── momentumTransport
│   └── physicalProperties
│
└── system/
    ├── controlDict
    ├── forceCoeffsIncompressible
    ├── functions
    ├── fvSchemes
    └── fvSolution
```



## Files intentionally omitted

The following files are generated during case preparation or are not required by this workflow:

- `constant/polyMesh/` — generated during Gmsh mesh import;
- `system/blockMeshDict` — not used because the mesh is generated with Gmsh;
- `0/nuTilda` — not required by the k–ω SST production model;
- solver logs, `postProcessing/`, and decomposed processor directories — generated during CFD execution.

This template is intended to remain minimal and reusable; generated case data and CFD outputs are kept outside the template itself.