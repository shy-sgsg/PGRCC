# Unknown System Error Next Stage Design

> **Status:** Approved by continuation of the active task after the in-chat design proposal. This document defines the next implementation boundary; it does not authorize AI training or production CUDA four-channel STAP.

## Goal

Close the three remaining evidence gaps in the current unknown-system-error line:

1. replace the fixed-work-point geometry deadband with a shared finite-range model and
   a model-validity gate;
2. separate the existing target-assisted servo pilot from a genuine clutter-only
   servo estimator and evaluate it with moving GMTI targets; and
3. isolate pure four-channel spatial degrees-of-freedom benefit with a matched J2/J4
   offline comparison.

After those gates are closed, add velocity true/report separation, then attitude and
real TrackManager/PIPE continuity. The entire sequence remains deterministic and
model-based.

## Non-negotiable constraints

- `ai_training=false` and Router disabled in every new manifest and experiment.
- Do not move production CUDA four-channel STAP ahead of the J2/J4 comparison.
- The blind estimator may consume reported channel geometry, reported platform state,
  reported beam/header metadata, waveform/range metadata, and the current raw IQ file;
  it may not consume true geometry, truth CSV, target truth, ON−OFF target residual,
  known error, or future metrics.
- Known-error correction is evaluator-only and must remain explicitly labeled as an
  upper bound/reference.
- `OFF = C+N`, `ON = S+C+N`, and `TO = S` remain separate files. `OFF` is the only
  calibration input for the clutter-only estimator; `ON` and `TO` are evaluation-only.
- Raw BIN/NPY/large log/runtime trees remain ignored. Only the allowlisted compact
  formal evidence under `outputs/formal_evidence/` is added to Git.
- Existing command paths and historical output directories remain readable. New
  behavior uses new schema names or compatibility wrappers instead of silently
  changing the meaning of the v3 servo artifacts.
- Every formal run records source commit, tracked dirty state, configuration/template
  identity, executable identity, device state where CUDA is used, command, exit code,
  and the exact compact artifact paths.

## Current evidence and known gaps

The current baseline is `origin/master=55c34f7`. Existing evidence shows:

- geometry zero-error estimate about `+0.159 mm` at 9 km, while the nuisance sweep
  gives approximately `−0.496 mm` at 8.8 km and `−0.300 mm` at 9.6 km;
- the existing deadband is therefore a fixed-fixture historical value, not an
  operational global threshold;
- the v3 servo estimator uses a paired target residual and multi-beam target power;
  it is a target-assisted calibration pilot, not clutter-only operational calibration;
- M3 versus M1/M2 combines four-channel spatial freedom with a different offline
  algorithm, so it cannot identify pure spatial-DOF gain;
- Pfa controls already establish the distinction between configured `1e-6` and
  structured-clutter empirical false-hit fraction, but the final IID-H0 closure and
  servo-error clutter controls are still required.

## Architecture

### 1. Evidence governance layer

Create one flat, tracked compact-evidence directory:

```text
outputs/formal_evidence/
```

Add an explicit `.gitignore` exception for this directory and for its allowlisted
`*.csv`/`*.json` files. Do not unignore the existing raw experiment trees.

Add `scripts/collect_formal_evidence.py` with an explicit source-to-destination
mapping. It must refuse missing sources, refuse destinations outside
`outputs/formal_evidence/`, refuse raw extensions (`.bin`, `.npy`, `.log`, `.png`),
and write `outputs/formal_evidence/manifest.json` containing the source path,
destination path, source SHA-256, byte count, producing commit, and collection
timestamp. The collector is the only mechanism used to stage these artifacts.

The initial mapping is:

| Tracked destination | Existing source |
|---|---|
| `geometry_bias_fit_summary.csv` | `unknown_system_error_geometry_bias_audit_20260914_v1/fit_summary.csv` |
| `geometry_extended_matrix_summary.csv` | `unknown_system_error_geometry_matrix_extended_20260914_formal_v2/matrix_summary.csv` |
| `geometry_deadband_decisions.csv` | `unknown_system_error_geometry_deadband_audit_20260914_v2/correction_decisions.csv` |
| `geometry_nuisance_sweep_summary.csv` | `unknown_system_error_geometry_nuisance_sweep_20260914_formal_v1/nuisance_matrix_summary.csv` |
| `target_transfer.csv` | `unknown_system_error_target_transfer_audit_20260914_v2/target_transfer.csv` |
| `pfa_summary.csv` | `unknown_system_error_pfa_audit_20260914_v8/pfa_summary.csv` |
| `servo_estimates.csv` | `unknown_system_error_servo_pilot_20260914_v3/servo_estimates.csv` |
| `servo_decisions.csv` | `unknown_system_error_servo_pilot_20260914_v3/servo_decisions.csv` |
| `information_matched_pairwise_aggregate.csv` | `unknown_system_error_information_matched_baseline_20260914_v1/information_matched_pairwise_aggregate.csv` |
| `geometry_extended_manifest.json` | `unknown_system_error_geometry_matrix_extended_20260914_formal_v2/manifest.json` |
| `geometry_nuisance_manifest.json` | `unknown_system_error_geometry_nuisance_sweep_20260914_formal_v1/manifest.json` |
| `e2e_manifest.json` | `unknown_system_error_end_to_end_20260914_formal_v4/manifest.json` |
| `servo_manifest.json` | `unknown_system_error_servo_pilot_20260914_v3/manifest.json` |

The collector also emits a short `README.md` explaining that these are compact
copies and that the source experiment directories are intentionally not tracked.
All research-document links for these artifacts are changed to the tracked paths;
links to nonexistent GitHub paths are not allowed.

### 2. Shared finite-range geometry model

Add `scripts/finite_range_geometry_model.py` as a pure, dependency-light module.
It is the single implementation used by both estimator and evaluator:

```python
def beam_target_position_m(
    theta_deg: float,
    slant_range_m: float,
    platform_position_m: Sequence[float],
    metadata: Mapping[str, object],
) -> np.ndarray

def finite_range_channel_path_m(
    target_position_m: Sequence[float],
    platform_position_m: Sequence[float],
    channel_position_m: Sequence[float],
) -> float

def finite_range_pair_phase_rad(
    theta_deg: float,
    slant_range_m: float,
    platform_position_m: Sequence[float],
    channel_positions_m: Sequence[Sequence[float]],
    i: int,
    j: int,
    fc_hz: float,
    metadata: Mapping[str, object],
) -> float
```

`beam_target_position_m` uses only reported platform state, the reported beam angle,
the supplied slant/range cell, ground height and the existing coordinate convention.
`finite_range_pair_phase_rad` evaluates common-TX plus channel-specific RX paths and
applies the configured carrier-phase sign. It validates finite positive range, channel
indices, wavelength, and vector dimensions. The existing evaluator functions become
compatibility wrappers around this module; the blind estimator imports the same module
instead of duplicating a formula.

Retain v1 as the current linear LOS estimator. Implement v2 as a one-dimensional
candidate fit: for each candidate baseline offset, perturb only the estimator's
unknown parameter in the reported channel model, evaluate exact finite-range phases,
fit one circular constant intercept per pair, and minimize robust weighted circular
residual over all valid beam/range blocks. Candidate truth never enters this call.
The output records `model_version`, candidate grid/optimizer settings, pair intercepts,
range-block residuals and uncertainty.

The comparison matrix uses:

```text
range: 8.8 / 9.0 / 9.6 km
angle half-span: 10 / 15 / 20 deg
noise power: 0.0001 / 0.001 / 0.01
geometry delta: 0, ±0.5, ±1, ±2.5, ±5 mm
seeds: 101, 202, 303
```

Each row reports v1 and v2 bias, RMSE, bootstrap CI, pair residual, closure, cross-pair
spread, and correction decision. No empirical bias table or fixed subtraction is
permitted. The decision threshold is derived at the current work point from the v2
uncertainty and local phase-to-baseline sensitivity; the historical `0.165 mm` value
is retained only as a comparison column.

### 3. Model-validity gate

Add `scripts/unknown_geometry_model_validity.py` with a pure gate function consuming
the v2 fit and its observations. The same raw case is partitioned into:

- near and far range blocks;
- negative and positive beam-angle subsets;
- odd and even pulse subsets.

Each non-empty subset runs the same blind v2 estimator. The gate records every subset
estimate, uncertainty, valid count, residual RMSE, and reason for exclusion. A fit is
`model_valid` only when at least two independent subset comparisons exist, each paired
estimate difference is no larger than `max(3*sqrt(u_a^2+u_b^2), 2*grid_step)`, pair
spread is below the same uncertainty rule, and closure/residual fields are finite.
Otherwise it returns:

```text
FALLBACK_MODEL_MISMATCH
```

The gate also reports exact-vs-linear predictor disagreement and beam/range residual
trend; those diagnostics cannot be hidden by relabeling the fit. A model-valid fit can
still return `NO_CORRECTION_NEEDED` when the estimate is inside the local uncertainty/
sensitivity threshold. A model-mismatch fit always falls back to Current.

Add tests for clean subsets, deliberate range-dependent disagreement, missing subsets,
non-finite residuals, and the exact decision precedence:

```text
model mismatch -> FALLBACK_MODEL_MISMATCH
unidentifiable -> FALLBACK_UNIDENTIFIABLE
valid and within local threshold -> NO_CORRECTION_NEEDED
valid and outside local threshold -> APPLY_ESTIMATED_CORRECTION
```

### 4. Servo estimator split and rename

Keep `outputs/unknown_system_error_servo_pilot_20260914_v3/` as historical evidence,
but change its documentation and manifest names to `target-assisted servo calibration
pilot` when referenced. Add the canonical entry point
`scripts/run_target_assisted_servo_calibration_pilot.py`; the old script remains a
compatibility wrapper so old commands do not silently change meaning.

Add `scripts/estimate_clutter_only_servo.py`. Its public estimator accepts only:

```python
def estimate_clutter_only_servo(
    off_raw_path: Path,
    reported_geometry: Sequence[Sequence[float]],
    reported_platform: Mapping[str, object],
    reported_scan: Mapping[str, object],
    waveform: Mapping[str, object],
    range_processing: Mapping[str, object],
    seed: int,
) -> dict[str, object]
```

The function must reject or ignore target-related keys rather than accepting them as
hidden inputs. It reads target-free C+N raw IQ only and computes:

1. six-pair clutter phase residual versus reported beam angle;
2. clutter Doppler/ridge displacement versus reported angle;
3. P38/slow-time phase-slope residual;
4. multi-beam clutter power profile as a secondary feature.

Use a deterministic one-dimensional candidate grid over `delta_theta` from `-0.75°`
to `+0.75°` in `0.0025°` increments. Normalize each valid feature by its measured
robust scale, use fixed weights `w_phase=1.0`, `w_ridge=1.0`, `w_p38=0.75`,
`w_power=0.25`, and minimize a Huber-robust sum. Record each component of `J`, the
valid feature count, objective curvature/ambiguity, and the selected candidate. If
fewer than two primary feature families are valid or the objective has no unique
minimum, return `FALLBACK_MODEL_MISMATCH`; do not manufacture a servo estimate.

Run errors `0, ±0.05, ±0.1, ±0.2, ±0.5°` with multiple seeds, clutter textures and
ranges. Compare `S0 Current`, `S1 clutter-only estimated correction`, `S1K known
servo correction`, and `Sassist target-assisted baseline`. Every estimator manifest
must include an input audit proving absence of ON−OFF, target truth, nominal target,
known error and servo truth.

### 5. Servo moving-target E2E and Pfa controls

Add a separate E2E runner that generates target-free OFF, moving-target ON and target-
only TO for three target families: slow near the clutter ridge, medium radial velocity,
and fast radial velocity. Use at least three seeds and at least two clutter/range
scenes per family. The calibration estimator receives OFF only. Production Current,
S1 and S1K run the same preprocessing, CFAR, ROI and evaluation code; TO is used only
for causal target transfer and target-power checks.

The compact result reports servo bias/RMSE, fallback/model-mismatch rate, phase/ridge
residual, clutter cancellation, causal target transfer, Pd, false-hit fraction,
position/velocity errors and branch provenance. It must not report target-assisted
results under the clutter-only estimator name.

Extend the Pfa audit with:

```text
H0 pure IID noise, multiple independent seeds and >=1,000,000 independent CUTs
H1 noise + stationary clutter
H2 geometry-error clutter
H3 corrected geometry clutter
H4 servo-error clutter
H5 corrected servo clutter
```

Verify the denominator from unique valid CUT IDs and report both nominal and effective
sample counts. For H0 label the result `empirical_cell_pfa`; for H1–H5 label it
`empirical_false_hit_fraction_under_structured_clutter`. The configured `1e-6` remains
a configuration field only. Threshold and GO alpha are frozen during the audit.

### 6. Pure spatial-DOF J2/J4 comparison

Extend `scripts/audit_information_matched_baseline.py` with two offline methods:

```text
J2 = two-channel JDL/STAP on pair-fused F1/F2
J4 = native four-channel JDL/STAP
```

Both use the same physical steering, Doppler taps, training support, covariance
estimator, diagonal loading, ROI, CFAR, seed, scene and target-protection rules.
Only channel dimension and the corresponding valid steering vector change. Current
production M0 remains the deployment baseline; M0/M1/M2/M3 remain historical comparison
rows. Add `pure_spatial_dof_delta = J4 - J2` to the compact pairwise output, including
SCNR, target loss, residual tail, Pd and structured-clutter false-hit fraction.
No result is described as a production CUDA STAP advantage unless this offline delta
is stable and material across the prespecified scenes.

### 7. Velocity, attitude and TrackManager sequence

After geometry v2, validity, clutter-only servo, Pfa closure and J2/J4 are complete,
add a simulator `velocity_true`/`velocity_reported` state with default compatibility.
True velocity drives platform trajectory, echo phase, clutter Doppler and target
relative geometry. Reported velocity drives INS/header, CTDR, P38, ridge model,
steering and velocity conversion. Pilot `delta_v` is `0, ±0.05, ±0.1, ±0.2, ±0.5 m/s`
with Current and known-error correction first, followed by a blind deterministic fit
using ridge displacement, P38 slope, CTDR residual and four-channel slow-time phase.

Only after velocity is stable, add yaw first, then pitch/roll as separate experiments.
The design explicitly compares yaw, servo and baseline geometry observables rather than
fitting all attitude dimensions simultaneously.

Finally create a 3–5 period continuous scene and invoke the production TrackManager /
PIPE. Compare Current, blind calibrated and known calibrated branches using track Pd,
false-track rate, continuity, angle/velocity/position RMSE. A proxy 2-of-3 evaluator is
not an acceptance substitute.

## Validation and acceptance gates

Each gate is independently runnable and produces compact evidence before the next gate:

1. evidence collector: all allowlisted files exist, hashes match, links resolve, no raw
   artifact is staged;
2. geometry v2: v1/v2 matrix covers every prescribed range/angle/noise/geometry cell,
   v2 removes or explains range-sign-changing bias, and decisions are local rather than
   fixed-deadband subtraction;
3. model gate: deliberately inconsistent subsets produce `FALLBACK_MODEL_MISMATCH`,
   while valid subsets preserve correction and no-correction semantics;
4. clutter-only servo: input audit passes and no estimator row can be traced to ON/TO,
   target truth, nominal target or servo truth;
5. moving-target E2E: OFF-only calibration, correct Current/S1/S1K provenance and
   multi-scene metrics are present;
6. Pfa: H0 denominator and cell Pfa are reported separately from structured-clutter
   false-hit fraction;
7. J2/J4: pure spatial delta is emitted under common algorithm settings;
8. velocity/attitude/TrackManager: each true/report state is introduced sequentially,
   with no joint confounding hidden in one aggregate score;
9. final documentation and Git: compact evidence is committed, tests/builds pass,
   current source identity is recorded, and AI remains disabled.

## Out of scope for this design

Neural training, Router selection, generic learned complex weights, RD image-to-image
models, production CUDA four-channel STAP migration before J2/J4, empirical correction
tables, and any claim that the current configured Pfa has been achieved are explicitly
out of scope.
