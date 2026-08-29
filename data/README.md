# Data

This directory contains the curated numerical results used to summarize the CFD design-space exploration.

## `authoritative_dataset_130.csv`

Authoritative ranked dataset containing 130 simulated configurations:

- 100 configurations from the initial DOE campaign (`DOE`)
- 15 configurations from the Phase 3 refinement (`Phase3`)
- 15 configurations from the Phase 4.5 refinement (`Phase45`)

The rows are ordered by ascending drag coefficient.

### Columns

| Column | Description |
|---|---|
| `rank` | Overall rank among the 130 configurations |
| `body_id` | Unique configuration identifier |
| `phase` | Campaign phase that produced the configuration |
| `Cd` | Drag coefficient reported by the production CFD workflow |
| `percentile` | Percentile within the complete ranked dataset |
| `lambda` | Body slenderness parameter |
| `w0`–`w3` | CST shape coefficients |
| `converged_residual` | Whether the solver satisfied the residual-convergence criterion |
| `force_converged` | Whether the force-convergence criterion was satisfied |
| `runtime_s` | Simulation runtime in seconds |
| `termination_reason` | Reason the solver stopped |

## Important interpretation

`COMPLETED` campaign status does not necessarily mean residual convergence was reached. Cases that reached the configured iteration limit while still producing a valid drag coefficient were retained for comparative ranking and are identified through the convergence and termination fields.

The dataset is intended primarily for relative aerodynamic ranking within the common CFD setup rather than as experimentally validated absolute drag data.