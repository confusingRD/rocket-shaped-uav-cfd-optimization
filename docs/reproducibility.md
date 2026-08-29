# Reproducibility

This document describes how to inspect, verify, and reproduce the computational workflow provided in this repository.

The repository contains the source code, OpenFOAM template, selected geometry inputs, curated numerical dataset, and automation scripts required to understand and reconstruct the CFD workflow.

Generated meshes, solver time directories, processor directories, logs, and the original runtime database are intentionally excluded because they are large and can be regenerated.

---

## 1. Reproducibility Scope

The repository supports several different levels of reproduction.

### Level 1 — Inspect the published results

The final ranked numerical dataset is available directly at:

`data/authoritative_dataset_130.csv`

No CFD software is required to inspect this file.

### Level 2 — Regenerate the curated result figures

The final statistical figures can be regenerated directly from the authoritative dataset using Python.

No OpenFOAM installation is required for this step.

### Level 3 — Inspect and regenerate geometry

The CST geometry-generation code is included under:

`src/geometry/`

A lightweight representation of the selected P45_012 geometry is provided under:

`examples/selected_case/P45_012/`

### Level 4 — Execute the CFD workflow

Full CFD execution requires a Linux-compatible environment containing Gmsh, OpenFOAM 13, MPI, and Bash.

The repository provides:

- the OpenFOAM template,
- geometry and meshing utilities,
- shell scripts,
- campaign-management code, and
- post-processing infrastructure.

---



## 2. Repository Components

The principal reproducibility-related directories are:

```text
src/
├── geometry/       CST geometry and wedge-domain generation
├── batch/          Design-sample generation
├── campaign/       CFD campaign management and execution
├── reporting/      Reporting and result processing
└── csv_to_geo.py   Profile-to-Gmsh conversion

openfoam/
└── template_case/  Reusable OpenFOAM production configuration

scripts/
├── importMesh.sh
├── runProductionCase.sh
├── setWedgePatches.sh
└── regenerate_results_figures.py

data/
└── authoritative_dataset_130.csv

examples/
└── selected_case/
    └── P45_012/

figures/
└── ...

docs/
└── ...
```

Generated runtime directories such as `profiles/`, `cases/`, `results/`, and `campaign_state/` are excluded from version control.

---



## 3. Python Requirements

The curated Python code uses the dependencies listed in:

`requirements.txt`

The current requirements are:

```text
numpy
matplotlib
distro
```

Python's standard library is also used for functionality such as:

- CSV processing,
- JSON processing,
- filesystem operations,
- subprocess management, and
- SQLite access.

The repository has been tested with Python 3.12.

A Python 3.10+ environment is recommended.

---



## 4. Install the Python Environment

From the repository root:

### Windows / PowerShell

```powershell
python -m pip install -r requirements.txt
```



### Linux / WSL

```bash
python3 -m pip install -r requirements.txt
```

A successful installation should provide NumPy, Matplotlib, and distro together with Matplotlib's required dependencies.

A simple dependency check is:

```powershell
python -c "import numpy, matplotlib, distro; print('Python dependencies OK')"
```

Expected output:

```text
Python dependencies OK
```

---



## 5. Verify the Python Source Tree

The Python source files can be syntax-checked without running OpenFOAM.

From the repository root:

```powershell
python -m compileall -q src
```

A successful run should return without compilation errors.

The campaign command-line interface can also be checked using:

```powershell
python src\campaign\cli.py --help
```

The CLI exposes the following campaign commands:

```text
init
validate
status
run
resume
plan
```

The same package can also be invoked in module form when `src` is included in `PYTHONPATH`.

For PowerShell:

```powershell
$env:PYTHONPATH = "$PWD\src"
python -m campaign.cli --help
```

These checks verify the Python package structure but do not launch a CFD simulation.

---



## 6. External CFD Requirements

The full production workflow requires software that is not installed through `requirements.txt`.

The principal external dependencies are:


| Software      | Purpose                                     |
| ------------- | ------------------------------------------- |
| OpenFOAM 13   | Steady incompressible RANS CFD solver       |
| Gmsh          | Geometry discretization and mesh generation |
| MPI / OpenMPI | Parallel OpenFOAM execution                 |
| Bash          | Shell automation                            |
| Linux or WSL  | Runtime environment for the CFD toolchain   |


The production workflow was developed around OpenFOAM 13.

A typical OpenFOAM environment on Linux or WSL is initialized using the OpenFOAM installation's `bashrc`, for example:

```bash
source /opt/openfoam13/etc/bashrc
```

The exact installation path may vary between systems.

Before attempting a CFD campaign, verify that the required executables are visible:

```bash
which gmsh
which foamRun
which gmshToFoam
which checkMesh
which mpirun
```

---



## 7. Windows vs Linux / WSL

The repository can be inspected and partially executed on Windows.

For example, Windows can be used to:

- install the Python dependencies,
- inspect and generate CST geometry,
- analyze the CSV dataset,
- regenerate result figures,
- inspect the campaign CLI, and
- edit the OpenFOAM configuration files.

However, the complete production CFD pipeline relies on Bash, Gmsh, OpenFOAM, and MPI.

Therefore, full CFD execution should be performed in:

**Linux or WSL**

rather than through native Windows Python alone.

---



## 8. Selected Reproduction Case

The repository includes P45_012 as a lightweight example:

```text
examples/selected_case/P45_012/
├── metadata.json
├── profile.csv
├── profile.geo
└── profile.png
```

P45_012 is the best observed low-drag configuration among the 130 simulated cases.

Its design parameters are:


| Parameter      | Value   |
| -------------- | ------- |
| λ              | 3.8     |
| w₀             | 0.5     |
| w₁             | 1.5     |
| w₂             | 1.0     |
| w₃             | 0.5     |
| Maximum radius | 0.070 m |
| Body length    | 0.532 m |


The directory deliberately contains geometry inputs rather than the complete generated OpenFOAM case.

Additional details are provided in:

`examples/selected_case/README.md`

---



## 9. Geometry Representation

The computational geometry begins with a CST-generated meridional profile.

The conceptual transformation is:

```text
CST parameters
      ↓
meridional profile
      ↓
profile.csv
      ↓
axisymmetric wedge construction
      ↓
profile.geo
      ↓
Gmsh mesh
```

The CST implementation is located under:

`src/geometry/`

The conversion between a profile CSV and the Gmsh wedge-domain definition is implemented in:

`src/csv_to_geo.py`

The selected example already includes both:

```text
profile.csv
profile.geo
```

so the relationship between the numerical profile and the generated Gmsh geometry can be inspected directly.

---



## 10. Regenerating a Gmsh Geometry File

A generated profile can be converted to the production Gmsh geometry definition using `src/csv_to_geo.py`.

The production mesh preset is:

`M4_PRODUCTION`

The conversion follows the form:

```bash
python3 src/csv_to_geo.py <profile.csv> --mesh-level M4_PRODUCTION -o <profile.geo>
```

For example, using the selected-case profile:

```bash
python3 src/csv_to_geo.py \
    examples/selected_case/P45_012/profile.csv \
    --mesh-level M4_PRODUCTION \
    -o examples/selected_case/P45_012/profile_regenerated.geo
```

This step generates the Gmsh geometry description.

It does not execute OpenFOAM.

---



## 11. OpenFOAM Template

The reusable production OpenFOAM configuration is stored under:

```text
openfoam/template_case/
├── 0/
├── constant/
└── system/
```

It contains the field initialization, turbulence configuration, numerical schemes, solver settings, and force-coefficient configuration used by the production workflow.

The principal production setup is:

```text
Solver formulation:  steady incompressible RANS
Pressure–velocity:    SIMPLE
Turbulence model:     k–ω SST
Iteration budget:     1000
Wedge angle:          5°
```

The template is copied and configured by the campaign workflow for individual geometries rather than manually maintaining a complete independent OpenFOAM setup for every design.

---



## 12. Mesh Import Workflow

The shell scripts under `scripts/` handle the interface between the generated Gmsh geometry and OpenFOAM.

The main roles are:


| Script                 | Purpose                                               |
| ---------------------- | ----------------------------------------------------- |
| `importMesh.sh`        | Generate/import the Gmsh mesh and perform mesh checks |
| `setWedgePatches.sh`   | Correct the wedge patch types after mesh import       |
| `runProductionCase.sh` | Execute a prepared production OpenFOAM case           |


Conceptually, the mesh workflow is:

```text
profile.geo
     ↓
Gmsh
     ↓
.msh
     ↓
gmshToFoam
     ↓
setWedgePatches.sh
     ↓
checkMesh
     ↓
OpenFOAM polyMesh
```

The Python campaign runner invokes these utilities as part of automated case preparation.

---



## 13. Campaign Management

The production campaign is managed through:

`src/campaign/`

The command-line interface is:

```bash
python3 src/campaign/cli.py --help
```

Available commands include:

```text
init
validate
status
run
resume
plan
```

Their general purposes are:


| Command    | Purpose                                    |
| ---------- | ------------------------------------------ |
| `init`     | Initialize campaign state and database     |
| `validate` | Perform pre-run checks                     |
| `status`   | Display campaign and database status       |
| `plan`     | Show which cases would be run or resumed   |
| `run`      | Execute the production campaign            |
| `resume`   | Continue an interrupted or paused campaign |


Before launching expensive CFD calculations, the recommended workflow is:

```text
init
  ↓
validate
  ↓
plan
  ↓
run
```

or, after interruption:

```text
status
  ↓
plan
  ↓
resume
```

The campaign system was designed to preserve execution state so that large CFD batches did not need to restart from the beginning after interruption.

---



## 14. Campaign Runtime Directories

During execution, the workflow creates several directories that are intentionally not committed to Git.

These include:

```text
profiles/
cases/
results/
campaign_state/
data/production.db
```

Their general roles are:


| Directory            | Runtime role                                  |
| -------------------- | --------------------------------------------- |
| `profiles/`          | Generated geometry inputs for campaign bodies |
| `cases/`             | Prepared OpenFOAM case directories            |
| `results/`           | Per-case post-processed CFD outputs           |
| `campaign_state/`    | Manifest, checkpoints, and campaign controls  |
| `data/production.db` | Runtime SQLite campaign database              |


These files are reproducible runtime artifacts rather than source files.

They are excluded through `.gitignore`.

---



## 15. CFD Execution Pipeline

At production level, the automated workflow is approximately:

```text
Design vector
     ↓
CST geometry generation
     ↓
profile.csv
     ↓
Gmsh wedge geometry
     ↓
Gmsh mesh generation
     ↓
OpenFOAM mesh import
     ↓
mesh quality checks
     ↓
case configuration
     ↓
parallel CFD execution
     ↓
force and residual processing
     ↓
summary result
     ↓
SQLite database
```

The campaign-management code coordinates this sequence and records the state of each body independently.

This allows completed cases to be preserved while unfinished or interrupted cases are selectively resumed.

---



## 16. Parallel Execution

The production workflow supports parallel OpenFOAM execution.

The campaign CLI exposes options such as:

```text
--workers
--cores-per-worker
```

The default configuration shown by the current CLI is:

```text
workers          = 2
cores-per-worker = 6
```

This corresponds to two simultaneous CFD workers, each using six MPI processes.

These values should not be treated as universal settings.

They should be adjusted according to:

- available CPU cores,
- system memory,
- OpenFOAM installation,
- and desired campaign throughput.

---



## 17. Runtime Database

The production campaign uses SQLite for persistent state and result storage.

The default runtime database is:

`data/production.db`

This database is intentionally excluded from version control.

The curated public result of the completed research campaign is instead provided as:

`data/authoritative_dataset_130.csv`

This separation keeps the repository lightweight while preserving the final numerical evidence used in the published analysis.

---



## 18. Regenerating the Final Statistical Figures

The six principal design-variable result figures can be regenerated directly from the authoritative 130-case dataset.

The script is:

`scripts/regenerate_results_figures.py`

From the repository root:

### Windows

```powershell
python scripts\regenerate_results_figures.py
```



### Linux / WSL

```bash
python3 scripts/regenerate_results_figures.py
```

The script verifies that the source dataset contains exactly 130 configurations and generates:

```text
figures/results/
├── results_cd_vs_lambda.png
├── results_cd_vs_w0.png
├── results_cd_vs_w1.png
├── results_cd_vs_w2.png
├── results_cd_vs_w3.png
└── results_spearman_correlation.png
```

All six plots are therefore derived directly from:

`data/authoritative_dataset_130.csv`

This prevents statistical figures from silently remaining synchronized with an earlier intermediate dataset.

---



## 19. Expected D130 Correlation Check

The regenerated Spearman analysis should produce approximately:


| Variable | Spearman correlation with C_D |
| -------- | ----------------------------- |
| λ        | +0.50                         |
| w₀       | +0.78                         |
| w₁       | −0.65                         |
| w₂       | −0.31                         |
| w₃       | −0.14                         |


Large differences from these values indicate that:

- a different dataset is being used,
- the dataset has been modified, or
- the analysis procedure has changed.

This provides a lightweight numerical sanity check for the final dataset.

---



## 20. Files Intentionally Excluded

A complete production campaign produces a large quantity of generated CFD data.

The Git repository intentionally excludes artifacts such as:

```text
constant/polyMesh/
processor*/
postProcessing/
*.msh
*.foam
solver logs
OpenFOAM time directories
runtime SQLite databases
campaign checkpoints
```

These files are not required to understand the source methodology and would make the repository unnecessarily large.

The repository instead preserves:

- source code,
- solver configuration,
- selected geometry inputs,
- curated final data,
- result figures,
- and technical documentation.

---



## 21. Reproduction Is Not Bitwise Replication

Exact numerical replication may depend on:

- OpenFOAM version,
- Gmsh version,
- MPI implementation,
- compiler and numerical libraries,
- operating system,
- processor count,
- floating-point behavior, and
- stopping behavior.

Therefore, reproduction should be interpreted as reconstructing the same computational methodology and obtaining consistent aerodynamic behavior, rather than requiring every floating-point value to be bit-for-bit identical across different machines.

For comparative ranking, all candidate geometries should be evaluated using the same software and numerical configuration.

---



## 22. Scientific Interpretation

Reproducing the computational workflow does not remove the numerical limitations of the model.

The present methodology includes known limitations associated with:

- incompressible flow at approximately Mach 0.40,
- near-wall y⁺ values around 17–18,
- steady RANS modeling,
- finite iteration limits,
- and the absence of experimental drag validation.

These issues are documented in:

`docs/validation.md`

The repository is therefore intended to reproduce the **design-screening methodology and numerical study**, not to claim experimentally validated absolute aerodynamic performance.

---



## 23. Recommended Reproduction Path

For a new user, the most practical sequence is:

```text
1. Clone the repository
        ↓
2. Install requirements.txt
        ↓
3. Verify Python source and campaign CLI
        ↓
4. Inspect data/authoritative_dataset_130.csv
        ↓
5. Regenerate the statistical figures
        ↓
6. Inspect examples/selected_case/P45_012/
        ↓
7. Inspect or regenerate profile.geo
        ↓
8. Install/configure Gmsh + OpenFOAM 13 + MPI
        ↓
9. Validate the CFD environment
        ↓
10. Run selected or new CFD configurations
```

Users interested primarily in the research results can stop after Step 5.

Users interested in the automation architecture can inspect the campaign code without executing the CFD solver.

Full CFD reproduction requires completing the Linux/WSL environment setup.

---



## Related Documentation

The research methodology is described in:

`docs/methodology.md`

Numerical verification and limitations are documented in:

`docs/validation.md`

The final aerodynamic results are summarized in:

`docs/results.md`

The selected low-drag geometry is provided under:

`examples/selected_case/P45_012/`

The authoritative final dataset is:

`data/authoritative_dataset_130.csv`