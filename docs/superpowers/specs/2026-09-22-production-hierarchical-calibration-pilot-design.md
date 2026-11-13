# Production Hierarchical Calibration Pilot — Design Specification

## Status

- Stage: `Production Hierarchical Calibration Pilot`
- Date: 2026-09-22
- Design source: the current user task attachment and the existing production-chain audit at commit `7e1f2da80ed42c1a743aa55914a3d4caf2cd0460`.
- Execution: direct implementation is authorized by the task; the attached design is the approved brief. The implementation will follow SDD and TDD, with one implementer task at a time and focused review after each task.

## Objective and decision boundary

Integrate the already-developed deterministic complex calibration methods into the real production F1/F2 path and run the first production single-error pilot on representative channel delay. The pilot compares the unchanged production Current path, equivalent complex re-calibration, blind physical delay correction, their hierarchical combination, and evaluator-only known-delay upper bounds through the same GO-CFAR, clustering, localization, TrackManager, and PIPE path.

The only allowed delay-stage decision labels are:

- `GO_EQUIVALENT_CALIBRATION_ONLY`
- `GO_PHYSICAL_CALIBRATION_ONLY`
- `GO_HIERARCHICAL_CALIBRATION`
- `NEED_MORE_SINGLE_ERROR_PRODUCTION_EVIDENCE`

`GO_PHYSICS_AI` and the historical mechanism-pilot label `GO_COUPLED_PHYSICAL_STATE_STUDY` are not valid outputs of this stage. AI remains closed: `ai_training=false`, `router_enabled=false`, `native_4ch_stap=false`.

This stage does not implement servo, velocity, decorrelation, coupled-error, M0/M1/M2/M3 future studies, or any native four-channel STAP path. It prepares the semantic/status contracts needed for later stages and runs delay first.

## Semantic corrections before the pilot

1. The historical mechanism-only report is corrected so `M0` is `uncalibrated_subtraction_proxy` and `M1` is `ordinary_complex_subtraction`. Neither is renamed to production Current. Production Current is a separate `C0` branch and is the only baseline used for the production decision.
2. Servo and velocity pilot rows retain their actual status as `SENSOR_PRIOR_ONLY`; they are not reported as blind radar estimators. The status vocabulary is explicit: `RADAR_ESTIMATED`, `SENSOR_PRIOR_ONLY`, `PRIOR_PLUS_RADAR_RESIDUAL`, `KNOWN_TRUTH`, and `NOT_EVALUABLE`.
3. The low-coherence metadata path has a unit test. Zero/low support in the production adapter is fail-closed and reports `NOT_EVALUABLE`; it cannot silently fall back to Current.

## Production integration point and method contract

The adapter is a research-only branch at the shared CUDA CSI entry point:

```text
protocol 4ch IQ
  -> production fusion (1,3)=F1 and (2,4)=F2
  -> pulse compression / coarse channel alignment
  -> slow-time FFT / DBS
  -> production raw P38 and range phase correction
  -> [research calibration tap: final F1/F2]
  -> research calibration branch
  -> production CFAR / clustering / localization / TrackManager / PIPE
```

The default configuration leaves this branch disabled and preserves the current kernel and buffers. The adapter never overwrites production input buffers; it writes the CSI output buffer selected by the existing downstream path. Research execution is explicit in the runtime XML and writes a compact adapter diagnostic CSV in the run output.

The delay pilot method IDs are:

| ID | Role | Input/correction |
|---|---|---|
| `C0` | Production Current | existing production CSI branch; no research adapter |
| `C1` | Ordinary subtraction | final aligned F1/F2 residual `F2-F1` on production support |
| `C2` | SCC | one complex `Gamma` on production support, residual `F2-Gamma*F1` |
| `C3` | Robust DDC | one `Gamma` per Doppler row, robust phase-outlier rejection |
| `C4` | Robust DDC-RB | one `Gamma` per range band and Doppler row, robust phase-outlier rejection |
| `P1` | Blind physical | target-free OFF phase-vs-fast-frequency delay estimate, fractional raw channel correction, then frozen/Current CSI |
| `P2` | Blind physical + robust | the same blind physical correction followed by `C4` |
| `PK` | Known physical upper bound | injected delay used only to create an evaluator reference input, then Current CSI |
| `PKR` | Known physical + robust upper bound | known corrected input followed by `C4` |

For C1–C4 the estimator consumes only production F1/F2, a production support mask, and configured method parameters. The support mask is derived from P38/CSI processing support, finite/power/coherence validity, and range/Doppler bounds; it never reads target truth, target position, injected error labels, or known error state. Mode-A estimates Gamma from paired target-free `OFF=C+N` and applies it to `ON=S+C+N`; Mode-B estimates and applies from the online `ON=S+C+N` input. The production delay pilot records both modes where the run can provide the pairs.

The convention is frozen as:

```text
Gamma = sum(F2 * conj(F1)) / sum(|F1|^2)
residual = F2 - Gamma * F1
```

If a group has insufficient support, a non-finite denominator, or low phase coherence, its status is `NOT_EVALUABLE`; a branch that cannot produce its declared estimate is not reported as an unmodified Current run.

## Evidence and metric contract

The formal pilot uses an independent output root. Only the compact tracked evidence directory is committed:

```text
docs/evidence/equivalent_vs_physical_pilot/
  manifest.json
  method_contract.csv
  gamma_recovery.csv
  clutter_metrics.csv
  decision_matrix.csv
  report.md
```

Large simulator/production outputs, raw IQ, BIN, NPY/NPZ/F32, and plots remain outside Git under a named output root. The manifest includes `source_commit`, `dirty`, `config_sha256`, `runner_sha256`, `analysis_sha256`, `seed`, and the exact command. Before the formal pilot, tracked `git status --porcelain` is empty; the manifest must record the frozen commit and `dirty=false`. The runner refuses to overwrite a non-empty output root unless explicitly resuming an auditable run.

The report separates three layers:

1. Channel/clutter: F1/F2 coherence, Gamma magnitude/phase and smoothness, residual power/cancellation, and structured-clutter false hits.
2. Target: paired `OFF=C+N`, `ON=S+C+N`, `TO=S`; causal target contribution is `ON-OFF`, with gain/loss, range/Doppler shift, and target Pd. ON power alone is not a target-preservation claim.
3. System: CFAR valid CUTs and hits, clusters, protocol detections, confirmed current-period matched tracks, confirmation latency, post-confirm continuity, ID switches, and angle/position/velocity RMSE.

The false-alarm waterfall is never collapsed: CFAR cell rate uses `hit_cut_count / valid_cut_count` only when `valid_cut_count > 0`; cluster, protocol-detection, and track false counts retain their own denominators and statuses. Existing `cfar_geometry_diagnostics.csv` and TrackManager debug files are consumed read-only. If a TrackManager ID switch cannot be classified from existing audit fields, the result is `NOT_IDENTIFIABLE_FROM_CURRENT_DEBUG` and the algorithm is unchanged.

The delay pilot starts with `0`, `+2`, and `-2` ns at one seed/velocity/SNR for a quick CUDA path check, then uses at least three seeds and two velocities for the representative formal comparison; `+/-4` ns is included in the first production sweep and `+/-8` ns is optional. A full Cartesian matrix is explicitly out of scope for this first pilot.

## Decision rules

The analyzer reports the fixed four-condition residual closure before interpreting any estimator:

```text
A0 Ideal/no-error
PK known physical correction
PKR known physical + Robust DDC
```

If `PK` does not approach `A0`, the report attributes the gap to an observed residual such as fractional-delay boundary behavior, pair fusion, physical-correction implementation, P38/range correction, or DDC residual. It does not call the blind estimator wrong until that closure is explained.

The delay decision follows only the measured production layers:

- Case A: equivalent and physical branches are comparable on clutter and downstream metrics — return `GO_EQUIVALENT_CALIBRATION_ONLY`.
- Case B: DDC improves clutter but worsens downstream metrics while physical correction is safe — return `GO_PHYSICAL_CALIBRATION_ONLY`.
- Case C: physical plus robust DDC improves over both single branches without target/false-alarm/track regression — return `GO_HIERARCHICAL_CALIBRATION`.
- Missing denominators, missing causal target controls, missing production TrackManager evidence, unresolved A0/PK closure, or insufficient representative cases — return `NEED_MORE_SINGLE_ERROR_PRODUCTION_EVIDENCE`.

No threshold is invented in this spec. The decision matrix records the configured comparison tolerances, actual denominators, evidence paths, and any `NOT_EVALUABLE` limitation.

## Required tests and verification

The implementation plan must add or update tests for:

- production Current unchanged when research mode is disabled;
- Gamma=1 producing the ordinary residual without changing the input buffers;
- Mode-A reading only OFF and Mode-B not reading target truth;
- zero/low support returning `NOT_EVALUABLE`;
- DDC sign/conjugation and range-band boundary handling;
- research output not overwriting F1/F2 production buffers;
- known-truth correction being evaluator-only;
- prior-only servo/velocity status not becoming `RADAR_ESTIMATED`;
- corrected M0/M1 names;
- production CFAR denominator and TrackManager audit classification contracts;
- compact evidence manifest refusing dirty source identity for formal runs.

Final verification is proportional to the change: Release CUDA build, CTest, Python tests, syntax/JSON checks, `git diff --check`, a GPU `nvidia-smi` record, a targeted CUDA production pilot, compact evidence inspection, and normal commit/push to `origin/codex/unknown-system-error-next-stage`. No force push, dependency installation, or native four-channel STAP is allowed.

