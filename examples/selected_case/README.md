# Selected Case — P45_012

This directory contains the lightweight geometry inputs for **P45_012**, the best observed low-drag configuration identified from the complete 130-case CFD dataset.

P45_012 was generated during the **Phase 4.5 hypothesis-guided refinement** stage and ranked **1st among all 130 simulated configurations**.

## Design Parameters

| Parameter | Value |
|---|---:|
| λ | 3.8 |
| w₀ | 0.5 |
| w₁ | 1.5 |
| w₂ | 1.0 |
| w₃ | 0.5 |
| Maximum radius | 0.070 m |
| Body length | 0.532 m |

The production CFD workflow reported:

**C_D = 7.18132545 × 10⁻⁴**

under the coefficient convention used consistently throughout the design campaign.

## Files

```text
P45_012/
├── metadata.json
├── profile.csv
├── profile.geo
└── profile.png
```

The individual files can be inspected directly:

- [`metadata.json`](P45_012/metadata.json) — CST design parameters and geometric metadata
- [`profile.csv`](P45_012/profile.csv) — discrete meridional coordinates of the axisymmetric body profile
- [`profile.geo`](P45_012/profile.geo) — Gmsh geometry definition for the 5° wedge CFD domain
- [`profile.png`](P45_012/profile.png) — quick visual preview of the generated body profile

## Role in the CFD Workflow

The selected geometry is intended to be used together with the reusable CFD infrastructure provided elsewhere in the repository.

```text
P45_012 geometry
        ↓
Gmsh geometry and mesh generation
        ↓
OpenFOAM template case
        ↓
CFD simulation
        ↓
Post-processing and quality checks
        ↓
Drag-coefficient extraction
```

Related implementation:

- [OpenFOAM template case](../../openfoam/template_case/)
- [CFD automation scripts](../../scripts/)
- [Campaign-management code](../../src/campaign/)
- [`csv_to_geo.py`](../../src/csv_to_geo.py)

## Why the Full CFD Case Is Not Included

Generated OpenFOAM artifacts are intentionally excluded from this example.

These include:

```text
constant/polyMesh/
processor*/
postProcessing/
solver logs
time directories
```

Such files are large and can be regenerated from the published geometry inputs, OpenFOAM template, and automation workflow.

This example therefore contains only the lightweight geometry information needed to inspect and reconstruct the selected design without duplicating generated simulation data.

## Interpretation

P45_012 is the **best observed configuration within the sampled design space**. It should not be interpreted as a mathematically proven global optimum.

The CFD campaign was designed primarily for **relative aerodynamic ranking** under a common numerical setup.

The reported C_D value should therefore be interpreted within the coefficient convention and numerical assumptions used throughout the campaign, rather than as an experimentally validated absolute drag coefficient.

## Related Repository Content

- [Authoritative 130-case dataset](../../data/authoritative_dataset_130.csv)
- [Dataset documentation](../../data/README.md)
- [Methodology](../../docs/methodology.md)
- [Numerical Verification and Validation](../../docs/validation.md)
- [Results](../../docs/results.md)
- [Reproducibility](../../docs/reproducibility.md)