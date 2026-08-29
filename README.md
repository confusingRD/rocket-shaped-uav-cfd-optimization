# Rocket-Shaped UAV CFD Optimization

Automated aerodynamic design-space exploration of an axisymmetric rocket-shaped UAV body using **CST parameterization, Gmsh, OpenFOAM 13, Python, and statistical analysis**.

The project develops an end-to-end CFD workflow that generates parameterized geometries, constructs meshes, runs OpenFOAM simulations, monitors numerical quality, stores results, and progressively refines the design space.

A total of **130 CFD configurations** were evaluated across global and local exploration stages.

The best observed configuration was **P45_012**.

---

## Project Overview

The central research question is:

> Can an automated CFD workflow efficiently explore a parameterized rocket-shaped UAV body and identify lower-drag geometries within a constrained design space?

Instead of manually preparing individual CFD cases, the project builds a reusable computational pipeline:

```text
Design variables
      ↓
CST parameterization
      ↓
Geometry generation
      ↓
Gmsh meshing
      ↓
OpenFOAM CFD
      ↓
Quality checks
      ↓
Post-processing
      ↓
SQLite database
      ↓
Statistical analysis
      ↓
Design-space refinement
```

![Automated CFD workflow](figures/workflow/workflow_automation_pipeline.png)

---



## Key Results

The complete study contains:


| Stage       | CFD cases | Purpose                                |
| ----------- | --------- | -------------------------------------- |
| Initial DOE | 100       | Global design-space exploration        |
| Phase 3     | 15        | Local exploration of promising regions |
| Phase 4.5   | 15        | Hypothesis-guided refinement           |
| **Total**   | **130**   | Final ranked dataset                   |


The final best-observed configuration was:

### P45_012


| Parameter      | Value   |
| -------------- | ------- |
| λ              | 3.8     |
| w₀             | 0.5     |
| w₁             | 1.5     |
| w₂             | 1.0     |
| w₃             | 0.5     |
| Maximum radius | 0.070 m |
| Body length    | 0.532 m |


Reported production CFD coefficient:

**C_D = 7.18132545 × 10⁻⁴**

under the 5° wedge coefficient convention used consistently throughout the campaign.

P45_012 improved the observed drag coefficient by approximately **1.39%** relative to the best configuration found in the initial 100-case DOE.

![Selected P45_012 profile](figures/best_design/best_design_profile_P45_012.png)

---



## Final Ranking

The refinement stages successfully concentrated new CFD evaluations in the lower-drag region of the sampled design space.

![Top 20 final configurations](figures/results/results_top20_ranking_130.png)

Among the final top 10 configurations:

- **8** came from Phase 4.5
- **1** came from Phase 3
- **1** came from the Initial DOE

The initial DOE leader, `Body_0004`, finished at rank 6 after the refinement stages.

P45_012 is therefore described as the **best observed configuration within the 130 simulated designs**, rather than as a mathematically proven global optimum.

---



## Design Parameter Trends

Spearman rank analysis was used to examine monotonic relationships between the CST design parameters and drag.

![Final D130 Spearman correlation](figures/results/results_spearman_correlation.png)

Approximate correlations with C_D are:


| Variable | Spearman ρ | Observed tendency             |
| -------- | ---------- | ----------------------------- |
| w₀       | +0.78      | Strong positive association   |
| w₁       | −0.65      | Strong negative association   |
| λ        | +0.50      | Moderate positive association |
| w₂       | −0.31      | Weaker negative association   |
| w₃       | −0.14      | Weak association              |


The results indicate that **w₀ and w₁** show the strongest monotonic relationships with drag within the sampled design space.

However, the CST coefficients jointly define the body geometry, so these relationships should not be interpreted as independent causal sensitivities.

---



## Geometry Parameterization

The body profile is generated using **Class-Shape Transformation (CST)** parameterization.

Five design variables are used:

```text
λ
w₀
w₁
w₂
w₃
```

The maximum radius is fixed at:

```text
R_max = 0.070 m
```

while body length varies with λ.

The geometry implementation is located under:

`src/geometry/`

A lightweight representation of the selected P45_012 geometry is provided under:

`examples/selected_case/P45_012/`

---



## Design-Space Exploration

The research strategy combines broad exploration with progressively more targeted refinement.

![Overall optimization strategy](figures/workflow/workflow_optimization_strategy.png)

### Initial DOE

100 configurations were evaluated to establish broad coverage of the available design space.

### Phase 3

15 additional configurations were sampled within a narrowed region identified from analysis of the initial campaign.

### Phase 4.5

15 targeted configurations were constructed to test engineering hypotheses derived from the previous results.

This final stage produced P45_012 and most of the highest-ranked configurations.

---



## CFD Setup

The production CFD configuration uses:

```text
OpenFOAM 13
Steady incompressible RANS
SIMPLE pressure–velocity coupling
k–ω SST turbulence model
5° axisymmetric wedge domain
Maximum iteration budget: 1000
```

Representative freestream conditions are:


| Quantity          | Value              |
| ----------------- | ------------------ |
| Velocity          | 138.89 m/s         |
| Temperature       | 298.15 K           |
| Pressure          | 101325 Pa          |
| Density           | ≈ 1.184 kg/m³      |
| Dynamic viscosity | ≈ 1.84 × 10⁻⁵ Pa·s |
| Mach number       | ≈ 0.40             |


A 5° wedge representation was used to reduce computational cost while preserving the axisymmetric geometry required for comparative screening.

The reusable OpenFOAM configuration is located at:

`openfoam/template_case/`

---



## Mesh Verification

A mesh-refinement study was performed before selecting the production configuration.

The production mesh is referred to as:

`M4_PRODUCTION`

and contains approximately **685,000 cells** for the reference configuration.

The change in drag coefficient from M3 to M4 satisfied the predefined **2% mesh-independence acceptance criterion**.

![Mesh independence](figures/validation/mesh_independence/mesh_cd_vs_cells.png)

The production mesh uses approximately:

```text
First prism-layer height ≈ 1 × 10⁻⁴ m
Growth ratio             ≈ 1.25
Prism layers             = 14
```

---



## Compressibility Sensitivity

The production campaign uses an incompressible solver at approximately Mach 0.40.

Because compressibility effects are no longer negligible at this operating condition, a five-body comparison was performed using a compressible CFD formulation.

![Compressibility comparison](figures/validation/compressibility/compressibility_cd_scatter.png)

The comparison produced approximately:

**Pearson r ≈ 0.92**

between incompressible and compressible drag coefficients for the tested bodies.

The broad ranking tendency was preserved, although a local rank exchange occurred between two neighboring configurations.

This supports the use of the incompressible formulation for large-scale comparative screening while also demonstrating that it should **not** be treated as an exact absolute-drag model.

---



## Numerical Limitations

The project intentionally distinguishes numerical verification from experimental validation.

Important limitations include:

- mean production y⁺ values of approximately 17–18
- incompressible modeling at Mach ≈ 0.40
- steady RANS assumptions
- turbulence-model sensitivity
- a finite 1000-iteration solver budget
- some cases reaching `MAX_ITERATIONS` without satisfying strict convergence criteria
- axisymmetric wedge assumptions
- no experimental drag validation

For this reason, the dataset is intended primarily for:

**relative aerodynamic ranking under a common CFD configuration**

rather than experimentally validated absolute drag prediction.

Detailed discussion is available in:

`docs/validation.md`

---



## Selected Flow Field

Velocity field around P45_012:

![Velocity contour around P45_012](figures/best_design/best_design_velocity_contour_P45_012.png)

Pressure field around P45_012:

![Pressure contour around P45_012](figures/best_design/best_design_pressure_contour_P45_012.png)

These visualizations support qualitative interpretation of the selected geometry, while the ranking itself is based on drag values extracted consistently through the automated CFD pipeline.

---



## Repository Structure

```text
rocket-shaped-uav-cfd-optimization/
│
├── src/
│   ├── geometry/          CST geometry generation
│   ├── batch/             Design sampling
│   ├── campaign/          CFD campaign automation
│   ├── reporting/         Analysis and reporting utilities
│   └── csv_to_geo.py
│
├── openfoam/
│   └── template_case/     Reusable OpenFOAM production case
│
├── scripts/
│   ├── importMesh.sh
│   ├── runProductionCase.sh
│   ├── setWedgePatches.sh
│   └── regenerate_results_figures.py
│
├── data/
│   ├── README.md
│   └── authoritative_dataset_130.csv
│
├── examples/
│   └── selected_case/
│       ├── README.md
│       └── P45_012/
│
├── figures/
│   ├── best_design/
│   ├── results/
│   ├── validation/
│   └── workflow/
│
├── docs/
│   ├── methodology.md
│   ├── validation.md
│   ├── results.md
│   └── reproducibility.md
│
├── requirements.txt
└── README.md
```

Generated runtime directories such as OpenFOAM meshes, processor folders, solver outputs, campaign state, and runtime databases are intentionally excluded from version control.

---



## Quick Start



### 1. Clone the repository

```bash
git clone https://github.com/confusingRD/rocket-shaped-uav-cfd-optimization.git
cd rocket-shaped-uav-cfd-optimization
```



### 2. Install Python dependencies

```bash
python -m pip install -r requirements.txt
```

The current Python dependencies are:

```text
numpy
matplotlib
distro
```



### 3. Verify the Python source

```bash
python -m compileall -q src
```



### 4. Inspect the campaign CLI

```bash
python src/campaign/cli.py --help
```

Available campaign commands include:

```text
init
validate
status
run
resume
plan
```

---



## Regenerate the Final Result Figures

The principal design-variable plots are generated directly from the authoritative 130-case dataset.

Run:

```bash
python scripts/regenerate_results_figures.py
```

This regenerates:

```text
figures/results/
├── results_cd_vs_lambda.png
├── results_cd_vs_w0.png
├── results_cd_vs_w1.png
├── results_cd_vs_w2.png
├── results_cd_vs_w3.png
└── results_spearman_correlation.png
```

from:

`data/authoritative_dataset_130.csv`

---



## Full CFD Requirements

The Python analysis components can be inspected and executed on Windows, Linux, or WSL.

Full CFD execution additionally requires:

- **OpenFOAM 13**
- **Gmsh**
- **MPI / OpenMPI**
- **Bash**
- **Linux or WSL**

The complete CFD workflow is therefore intended to run in a Linux-compatible environment.

Detailed reproduction instructions are provided in:

[Reproducibility guide](docs/reproducibility.md)

---



## Documentation

More detailed technical documentation is available here:


| Document                                            | Description                                                          |
| --------------------------------------------------- | -------------------------------------------------------------------- |
| [Methodology](docs/methodology.md)                  | CST, DOE strategy, CFD configuration, and refinement methodology     |
| [Numerical Verification](docs/validation.md)        | Mesh independence, y⁺, compressibility, convergence, and limitations |
| [Results](docs/results.md)                          | Final ranking, parameter trends, and P45_012                         |
| [Reproducibility](docs/reproducibility.md)          | Environment requirements and workflow reproduction                   |
| [Selected Case](examples/selected_case/README.md)   | Lightweight P45_012 reproduction example                             |
| [Dataset](data/README.md)                           | Description of the authoritative 130-case dataset                    |


---



## Technology Stack

**Geometry and meshing**

- CST parameterization
- Gmsh

**CFD**

- OpenFOAM 13
- k–ω SST
- steady incompressible RANS
- MPI parallel execution

**Automation and analysis**

- Python
- NumPy
- Matplotlib
- SQLite
- Bash

**Engineering workflow**

- automated case generation
- campaign checkpointing and recovery
- convergence monitoring
- design-space exploration
- statistical sensitivity analysis
- reproducible result generation

---



## What This Project Demonstrates

Beyond the final aerodynamic design itself, the project demonstrates an integrated engineering workflow for computational design exploration:

- parameterized geometry generation
- automated CFD case construction
- batch simulation management
- failure and interruption recovery
- numerical quality monitoring
- structured result storage
- statistical interpretation
- iterative engineering decision-making

The emphasis is therefore not only on obtaining a lower-drag body, but also on building a workflow capable of managing a relatively large CFD campaign in a repeatable and inspectable way.

---



## Dataset

The authoritative final dataset contains all 130 configurations:

[View the authoritative 130-case dataset](data/authoritative_dataset_130.csv)

Each row records information including:

```text
rank
body_id
phase
Cd
lambda
w0
w1
w2
w3
converged_residual
force_converged
runtime_s
termination_reason
```

This preserves both aerodynamic performance and numerical-status information rather than publishing only the final ranking.

---



## Scientific Scope

The primary conclusion of the study is:

> Within the sampled CST design space and the common production CFD setup, P45_012 produced the lowest observed drag coefficient among the 130 simulated configurations.

The project does **not** claim that:

- P45_012 is the mathematical global optimum,
- the reported C_D is experimentally validated,
- incompressible CFD is equivalent to compressible CFD at Mach 0.40, or
- the present numerical setup represents final high-fidelity vehicle certification.

Future work should progressively confirm the highest-ranked candidates using improved near-wall resolution, compressible CFD, selected three-dimensional simulations, and experimental measurements when available.

---



## Project Status

**Phase 1 computational study: complete**

```text
130 CFD simulations
100 Initial DOE
15 Phase 3
15 Phase 4.5
Best observed candidate: P45_012
```

The repository is a curated version of the computational workflow and research outputs used during the study.