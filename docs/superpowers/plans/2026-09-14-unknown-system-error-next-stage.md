# Unknown System Error Next Stage Implementation Plan

> **For the implementer:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

## Scope and operating constraints

This plan implements the approved design in
docs/superpowers/specs/2026-09-14-unknown-system-error-next-stage-design.md
against implementation baseline 55c34f7 plus the approved design commit
6d7f2d7, currently at origin/master. The work is bounded to the user
attachment and the repository AGENTS.md requirements:

- Keep ai_training=false, Router disabled, and all AI paths out of the active
  experiments. Do not make production CUDA four-channel STAP the default.
- Preserve the Current production path and existing command/config/output
  compatibility unless a new opt-in mode is required by the experiment.
- Use the production TrackManager and retained track_debug for the final
  track/PIPE gate; no proxy tracker or two-of-three acceptance rule.
- Treat current untracked outputs as user-owned evidence. Only compact,
  explicitly mapped files under outputs/formal_evidence/ may be added to Git.
- Use test-first changes. Every new estimator or gate starts with a failing
  focused test, then the smallest implementation, then the relevant integration
  run.
- Use a separate worktree for implementation. The plan itself is the only
  change made on the current branch before execution is explicitly selected.

## Target files and interfaces

Create or modify only the following implementation surfaces unless a failing
test proves that an adjacent file is required:

- scripts/finite_range_geometry_model.py: shared exact finite-range geometry
  model and explicit v1 linear comparison helpers.
- scripts/analyze_four_channel_observables.py: v2 geometry observations,
  estimator output, uncertainty propagation, and model-validity fields.
- scripts/unknown_geometry_model_validity.py: reusable residual and consistency
  gate with stable status strings.
- scripts/run_unknown_system_error_matrix.py: v1/v2 matrix orchestration,
  dynamic correction decisions, and compact summary emission.
- scripts/estimate_clutter_only_servo.py: target-free deterministic servo
  estimator and feature extraction.
- scripts/run_unknown_system_error_servo_pilot.py: rename/reframe current v3 as
  target-assisted calibration pilot without changing its assisted baseline.
- new experiment drivers for moving-target servo, Pfa closure, J2/J4,
  velocity split, yaw split, and TrackManager E2E, each with a manifest and
  compact aggregate output.
- scripts/collect_formal_evidence.py: deterministic compact evidence collector.
- tests/test_finite_range_geometry_model.py,
  tests/test_unknown_geometry_model_validity.py,
  tests/test_clutter_only_servo.py,
  tests/test_formal_evidence_collection.py, plus focused additions to existing
  geometry, servo, and Stage2 tests.
- .gitignore: re-include only the new compact formal evidence directory and
  selected manifest/CSV/JSON files beneath it.
- docs/unknown_system_error_*.md and the relevant navigation document: update
  terminology, command lines, evidence links, and limitations after results
  exist.

Use these stable data contracts across the Python stages:

1. Geometry observation record:
   theta_deg, slant_range_m, platform_position_m, beam_id, pair_i, pair_j,
   observed_phase_rad, coherence, noise_sigma_rad, source_id.
2. Geometry estimate record:
   method, estimate_m, uncertainty_m, deadband_m, fit_rmse_rad,
   pair_consistency_rad, closure_residual_rad, exact_linear_disagreement_rad,
   subset_spread_rad, status, action, source_ids.
3. Servo feature record:
   theta_deg, slant_range_m, beam_id, pair_id, clutter_phase_rad,
   doppler_ridge_hz, phase_slope_rad_per_rad, power_db, coherence, source_id.
4. Servo estimate record:
   method, estimate_deg, uncertainty_deg, fit_rmse, phase_rmse,
   ridge_rmse, clutter_cancellation_db, model_mismatch, fallback_reason,
   status, causal_data_sources.
5. Every experiment manifest records source commit, dirty state, command,
   config/overrides, input paths and SHA-256, host/GPU state where applicable,
   output schema, seed list, and explicit limitations.

## Task 1: Add a compact formal-evidence collection path

### Test first

Add tests/test_formal_evidence_collection.py before the collector:

~~~python
def test_collect_formal_evidence_writes_exact_required_files(tmp_path):
    result = collect_formal_evidence(
        repository_root=repo_root,
        output_root=tmp_path / "formal_evidence",
        source_roots=known_formal_source_roots,
    )
    assert result.manifest["source_commit"] == expected_commit
    assert result.manifest["raw_artifacts_excluded"] is True
    assert sorted(p.name for p in (tmp_path / "formal_evidence").iterdir()) == [
        "correction_decisions.csv",
        "information_matched_pairwise_aggregate.csv",
        "manifest.json",
        "pfa_summary.csv",
        "servo_decisions.csv",
        "servo_estimates.csv",
        "summary.csv",
        "target_transfer.csv",
    ]


def test_collect_formal_evidence_rejects_unmapped_or_missing_source(tmp_path):
    with pytest.raises(ValueError, match="explicit source mapping"):
        collect_formal_evidence(
            repository_root=repo_root,
            output_root=tmp_path / "formal_evidence",
            source_roots={"unknown": tmp_path / "does_not_exist"},
        )
~~~

### Implementation

Implement scripts/collect_formal_evidence.py with:

- an explicit source map for the existing geometry formal matrix, geometry
  nuisance sweep, deadband audit, formal E2E, target transfer audit, Pfa audit,
  information-matched pairwise aggregate, and servo pilot;
- named copy/extract functions for each required compact artifact rather than
  recursive copying;
- normalized CSV headers and stable row ordering;
- manifest entries containing source path, source SHA-256, source commit, source
  dirty state, extraction rule, and excluded raw-file count;
- failure on missing source, ambiguous source, schema mismatch, or raw file
  selection;
- idempotent writes to a uniquely named working directory followed by one
  explicit final output path. The collector must never overwrite an existing
  formal evidence directory.

Add a .gitignore exception for outputs/formal_evidence/ and ignore every other
file in that directory by default, then re-include only the eight compact
artifacts above. Do not re-include raw BIN, NPY, PNG, or full logs.

### Verification and commit

Run:

~~~bash
python3 -m pytest -q tests/test_formal_evidence_collection.py
python3 scripts/collect_formal_evidence.py --help
python3 scripts/collect_formal_evidence.py --source-root outputs --output outputs/formal_evidence
python3 -m json.tool outputs/formal_evidence/manifest.json >/dev/null
python3 - <<'PY'
import csv
from pathlib import Path
root = Path("outputs/formal_evidence")
required = {
    "manifest.json", "summary.csv", "correction_decisions.csv",
    "target_transfer.csv", "pfa_summary.csv", "servo_estimates.csv",
    "servo_decisions.csv", "information_matched_pairwise_aggregate.csv",
}
assert {p.name for p in root.iterdir()} == required
for path in root.glob("*.csv"):
    with path.open(newline="") as f:
        assert list(csv.reader(f))
PY
git diff --check
~~~

Review the staged path list so no ignored raw output or unrelated user file is
included. Commit the collector and its tests as:

~~~bash
git add .gitignore scripts/collect_formal_evidence.py tests/test_formal_evidence_collection.py
git commit -m "feat: collect compact formal evidence"
~~~

## Task 2: Introduce one shared finite-range geometry model

### Test first

Add tests/test_finite_range_geometry_model.py:

~~~python
def test_exact_channel_path_matches_explicit_euclidean_path():
    platform = np.array([13.0, -4.0, 2.0])
    channel = np.array([0.23, -0.17, 0.08])
    target = np.array([8800.0, 1400.0, 50.0])
    expected = np.linalg.norm(target - platform) + np.linalg.norm(target - (platform + channel))
    assert finite_range_channel_path_m(target, platform, channel) == pytest.approx(expected)


def test_pair_phase_is_wrapped_and_uses_channel_specific_receive_paths():
    platform = np.zeros(3)
    positions = np.array([[0.0, 0.0, 0.0], [0.23, -0.17, 0.08]])
    theta = 7.0
    radius = 9000.0
    target = beam_target_position_m(theta, radius, platform, metadata)
    paths = [
        np.linalg.norm(target - platform) + np.linalg.norm(target - (platform + p))
        for p in positions
    ]
    expected = np.arctan2(
        np.sin(-2.0 * np.pi * (paths[0] - paths[1]) / (3.0e8 / 16.0e9)),
        np.cos(-2.0 * np.pi * (paths[0] - paths[1]) / (3.0e8 / 16.0e9)),
    )
    actual = finite_range_pair_phase_rad(theta, radius, platform, positions, 0, 1, 16.0e9, metadata)
    assert actual == pytest.approx(expected)


def test_reported_context_changes_target_position_without_hidden_global_state():
    a = beam_target_position_m(0.0, 8800.0, np.zeros(3), metadata)
    b = beam_target_position_m(0.0, 9600.0, np.zeros(3), metadata)
    assert not np.allclose(a, b)
~~~

### Implementation

Implement the following public functions in
scripts/finite_range_geometry_model.py:

- beam_target_position_m(theta_deg, slant_range_m, platform_position_m,
  metadata), using the reported beam angle, finite slant range, platform
  position, and explicit beam/coordinate metadata;
- finite_range_channel_path_m(target_position_m, platform_position_m,
  channel_position_m), using the common transmit path plus the
  channel-specific receive path;
- finite_range_pair_phase_rad(theta_deg, slant_range_m, platform_position_m,
  channel_positions_m, i, j, fc_hz, metadata), with phase wrapping;
- linear_pair_phase_rad(...) for the current v1 approximation;
- compare_exact_linear_phase(...) returning phase disagreement and the
  derivative/sensitivity used by the uncertainty gate.

Validate dimensions, finite values, positive frequency/range, channel indices,
and metadata coordinate conventions. Keep all constants in one module and make
the channel ordering explicit. Refactor the existing observable evaluator and
estimator to call this module; do not leave a second exact-path implementation
in the caller.

### Verification and commit

Run:

~~~bash
python3 -m pytest -q tests/test_finite_range_geometry_model.py tests/test_unknown_system_error_matrix.py
python3 -m py_compile scripts/finite_range_geometry_model.py scripts/analyze_four_channel_observables.py
git diff --check
~~~

Commit:

~~~bash
git add scripts/finite_range_geometry_model.py scripts/analyze_four_channel_observables.py tests/test_finite_range_geometry_model.py tests/test_unknown_system_error_matrix.py
git commit -m "feat: share finite-range geometry model"
~~~

## Task 3: Add the v1 versus v2 geometry matrix and dynamic correction

### Test first

Extend the geometry tests with a synthetic observation builder whose expected
phase is generated from the public exact model and whose estimator input is
only the observation contract from Task 1:

~~~python
def make_observations(method, geometry_bias_m, seed):
    rng = np.random.default_rng(seed)
    rows = []
    for radius in (8800.0, 9000.0, 9600.0):
        for theta in (-10.0, 0.0, 10.0):
            for pair in ((0, 1), (0, 2), (1, 2)):
                exact = finite_range_pair_phase_rad(
                    theta, radius, np.zeros(3), channel_positions, pair[0], pair[1],
                    16.0e9, metadata
                )
                rows.append({
                    "theta_deg": theta,
                    "slant_range_m": radius,
                    "platform_position_m": [0.0, 0.0, 0.0],
                    "beam_id": 0,
                    "pair_i": pair[0],
                    "pair_j": pair[1],
                    "observed_phase_rad": exact + geometry_bias_m * 1.0e-3 + rng.normal(0.0, 1.0e-4),
                    "coherence": 0.99,
                    "noise_sigma_rad": 1.0e-4,
                    "source_id": method,
                })
    return rows


def test_v2_recovers_signed_bias_and_reports_uncertainty():
    result = estimate_unknown_baseline(make_observations("v2", 2.5, 7), method="v2")
    assert result["estimate_m"] == pytest.approx(0.0025, abs=0.0002)
    assert result["uncertainty_m"] > 0.0
    assert result["status"] in {"APPLY_CORRECTION", "NO_CORRECTION_NEEDED"}


def test_dynamic_deadband_changes_with_range_and_angle_sensitivity():
    near = estimate_deadband(range_m=8800.0, angle_span_deg=10.0, noise_sigma_rad=1.0e-3)
    far = estimate_deadband(range_m=9600.0, angle_span_deg=20.0, noise_sigma_rad=1.0e-3)
    assert near["deadband_m"] > 0.0 and far["deadband_m"] > 0.0
    assert near["sensitivity_m_per_rad"] != pytest.approx(far["sensitivity_m_per_rad"])
~~~

### Implementation

Extend the estimator contract so v1 remains the existing linear path and v2
fits the exact finite-range model over reported ranges 8.8, 9.0, and 9.6 km
and angle spans 10, 15, and 20 degrees. Include noise levels and geometry
errors 0, plus or minus 0.5, plus or minus 1, plus or minus 2.5, and plus or
minus 5 mm, with multiple seeds.

Compute correction status from the fitted uncertainty and local physical
sensitivity:

- NO_CORRECTION_NEEDED when the estimate interval overlaps zero or the
  uncertainty-adjusted physical effect is inside the deadband;
- APPLY_CORRECTION when the signed estimate is identifiable and materially
  exceeds the dynamic deadband;
- FALLBACK_UNIDENTIFIABLE when rank, coherence, sample count, or uncertainty
  makes the sign unsafe.

Record estimate, uncertainty, sensitivity, deadband, recovery metrics, and the
reason for every decision. Do not add an empirical bias table and do not encode
the current 0.16 mm offset as a correction.

### Verification and commit

Run the focused matrix first:

~~~bash
python3 -m pytest -q tests/test_unknown_system_error_matrix.py tests/test_finite_range_geometry_model.py
python3 scripts/run_unknown_system_error_matrix.py --help
python3 scripts/run_unknown_system_error_matrix.py --model v1 --output outputs/geometry_v1_matrix_work
python3 scripts/run_unknown_system_error_matrix.py --model v2 --output outputs/geometry_v2_matrix_work
~~~

Check that every matrix row contains all condition dimensions, all requested
errors and seed IDs, and that v1 and v2 use the same synthetic observations.
Preserve these outputs as ignored run evidence; summarize only compact results
in formal_evidence. Commit the code/tests:

~~~bash
git add scripts/analyze_four_channel_observables.py scripts/run_unknown_system_error_matrix.py tests/test_unknown_system_error_matrix.py tests/test_finite_range_geometry_model.py
git commit -m "feat: add finite-range geometry estimator matrix"
~~~

## Task 4: Add the geometry model-validity gate

### Test first

Add tests/test_unknown_geometry_model_validity.py with cases for:

- a consistent exact-model fit that returns VALID;
- cross-range residual trend or range-block residual trend that returns
  FALLBACK_MODEL_MISMATCH;
- inconsistent pair residuals or non-closing pair residuals that returns
  FALLBACK_MODEL_MISMATCH;
- exact-versus-linear disagreement above the declared uncertainty budget that
  blocks correction even when the optimizer converges;
- a fit with insufficient valid observations that returns
  FALLBACK_UNIDENTIFIABLE.

The test fixture must provide explicit residual arrays and metadata for beam,
range block, pair, and odd/even subsets, so the gate is tested independently
from the optimizer.

### Implementation

Implement scripts/unknown_geometry_model_validity.py with
validate_geometry_model(result, observations, thresholds), returning status,
reasons, and numeric diagnostics. The gate must inspect:

- cross-range residual slope and range-block residual trend;
- pair-to-pair consistency and closure;
- exact-versus-linear disagreement;
- beam, range, and odd/even subset consistency;
- finite inputs, rank, effective sample count, and uncertainty.

Use only thresholds derived from configured phase noise, conditioning, and
physical sensitivity. Persist thresholds and the measured values in every
estimate record. A converged fit is not trustworthy until this gate passes.
When it fails, force action to fallback and retain the production Current
configuration.

### Verification and commit

Run:

~~~bash
python3 -m pytest -q tests/test_unknown_geometry_model_validity.py tests/test_unknown_system_error_matrix.py
python3 -m py_compile scripts/unknown_geometry_model_validity.py scripts/analyze_four_channel_observables.py
git diff --check
~~~

Commit:

~~~bash
git add scripts/unknown_geometry_model_validity.py scripts/analyze_four_channel_observables.py scripts/run_unknown_system_error_matrix.py tests/test_unknown_geometry_model_validity.py
git commit -m "feat: gate geometry model validity"
~~~

## Task 5: Reframe the existing servo v3 pilot as target-assisted

### Test first

Add a regression assertion to the existing servo pilot tests that:

- the output schema and 18 Stage2 cases remain readable;
- the assisted estimate retains the measured baseline fields, including bias
  near minus 0.0108 degrees and RMSE near 0.0138 degrees for the existing
  committed pilot;
- the manifest states target-assisted and does not state blind, operational,
  or target-free calibration;
- causal_data_sources includes target residual/ON-OFF evidence.

### Implementation

Rename user-facing current v3 terminology to
target-assisted servo calibration pilot in
scripts/run_unknown_system_error_servo_pilot.py, its manifest, and the
associated documentation. Keep the existing deterministic estimator and
assisted baseline numerical output unchanged. Replace operational/blind claims
with the explicit limitation that target truth or ON-OFF target evidence is
used and that the pilot is not an operational estimator.

Emit servo_estimates.csv and servo_decisions.csv using the stable Task 1
contracts, including fit, fallback, model-mismatch, and source fields.

### Verification and commit

Run:

~~~bash
python3 -m pytest -q tests/test_unknown_system_error_servo_pilot.py
python3 scripts/run_unknown_system_error_servo_pilot.py --help
python3 -m py_compile scripts/run_unknown_system_error_servo_pilot.py
git diff --check
~~~

Commit:

~~~bash
git add scripts/run_unknown_system_error_servo_pilot.py tests/test_unknown_system_error_servo_pilot.py docs
git commit -m "docs: reframe servo pilot as target assisted"
~~~

## Task 6: Implement the target-free clutter-only servo estimator

### Test first

Add tests/test_clutter_only_servo.py covering:

~~~python
def test_target_free_feature_extraction_uses_only_allowed_inputs():
    features = extract_clutter_features(target_free_packets, reported_context)
    assert set(features.causal_data_sources) == {
        "six_pair_clutter_phase",
        "doppler_ridge_displacement",
        "p38_phase_slope",
        "multi_beam_power_secondary",
        "reported_angle_geometry_platform",
    }
    assert not features.contains_target_truth
    assert not features.contains_on_off_difference


def test_grid_fit_is_deterministic_and_reports_model_mismatch():
    first = estimate_clutter_only_servo(features, grid_config)
    second = estimate_clutter_only_servo(features, grid_config)
    assert first == second
    assert first.status in {"VALID", "FALLBACK_MODEL_MISMATCH", "FALLBACK_UNIDENTIFIABLE"}


def test_inconsistent_ridge_and_phase_blocks_correction():
    result = estimate_clutter_only_servo(inconsistent_features, grid_config)
    assert result.status == "FALLBACK_MODEL_MISMATCH"
    assert result.action == "KEEP_CURRENT"
~~~

### Implementation

Create scripts/estimate_clutter_only_servo.py. The input loader must accept
only target-free C+N four-channel data and reported angle, geometry, and
platform state. Reject target truth, nominal target position, known injected
error, servo truth, and ON minus OFF inputs.

Extract six-pair clutter phase versus theta, Doppler/ridge displacement,
P38/phase slope, and multi-beam power as a secondary feature. Fit the servo
angle with a deterministic one-dimensional grid and robust loss, returning
estimate, uncertainty, phase/ridge residuals, cancellation metric, and explicit
fallback/model-mismatch causes. The fit and gate must be independent of target
detections and must not silently substitute nominal values.

Add configuration for the error sweep 0, plus or minus 0.05, plus or minus
0.1, plus or minus 0.2, and plus or minus 0.5 degrees, multiple seeds,
textures, and ranges. Keep four branches in the summary:
S0 Current, S1 clutter-only, S1K known, and Sassist target-assisted.

### Verification and commit

Run:

~~~bash
python3 -m pytest -q tests/test_clutter_only_servo.py
python3 -m py_compile scripts/estimate_clutter_only_servo.py
python3 scripts/estimate_clutter_only_servo.py --help
git diff --check
~~~

Commit:

~~~bash
git add scripts/estimate_clutter_only_servo.py tests/test_clutter_only_servo.py
git commit -m "feat: estimate servo angle from clutter only"
~~~

## Task 7: Run and audit the clutter-only servo pilot

### Implementation

Add a driver that generates target-free four-channel C+N scenes and calls the
Task 6 estimator using OFF data only. For each error, seed, texture, and range,
run S0, S1, S1K, and Sassist with identical scene seeds and production Current
CFAR settings. ON and target-only data may be generated for evaluation, but
the S1 estimator must never receive them.

Write a manifest, servo_estimates.csv, servo_decisions.csv, and compact
aggregate rows with:

- bias, RMSE, MAE, uncertainty, fallback rate, and model-mismatch rate;
- phase residual, ridge residual, and clutter cancellation;
- target causal-transfer fields, target Pd, and false-hit fraction, clearly
  labeled as evaluation-only;
- exact source restrictions and branch names.

### Verification and commit

Run a minimal matrix before the full one:

~~~bash
python3 scripts/run_clutter_only_servo_pilot.py --help
python3 scripts/run_clutter_only_servo_pilot.py --error-deg 0 --seed 101 --output outputs/clutter_servo_smoke
python3 scripts/run_clutter_only_servo_pilot.py --full-matrix --output outputs/clutter_servo_formal_work
~~~

Inspect the manifest and independently verify that all estimator input
manifests are OFF-only and that target fields appear only in evaluation
records. Do not promote S1 to production based on one seed or a target-only
run. Commit the driver and tests:

~~~bash
git add scripts/run_clutter_only_servo_pilot.py tests
git commit -m "feat: add clutter only servo pilot"
~~~

## Task 8: Add true GMTI moving-target servo end-to-end cases

### Test first

Add a driver-contract test that rejects any run where the estimator input
manifest contains ON or TO packets, target truth, nominal target metadata,
known injected servo error, or servo truth. Add assertions that ON and TO are
evaluation-only and that production Current/CFAR settings are unchanged.

### Implementation

Implement the four required scene classes:

- target-free;
- slow target near the clutter ridge;
- medium-speed target;
- fast target.

Use OFF = C+N for calibration input, ON = S+C+N for target-bearing evaluation,
and TO = S for target-only evaluation. Run multiple seeds per scene. Keep the
estimator restricted to OFF while ON and TO are passed only to the evaluator.
Report S0 Current, S1 clutter-only, S1K known, and Sassist target-assisted in
separate branch columns.

Write target transfer and E2E aggregates with target Pd, false-hit fraction,
clutter cancellation, range/Doppler/angle errors, and causal transfer. Do not
interpret target-Pd differences as amplitude gain without the target-selection,
truth-gate, and position-error audit.

### Verification and commit

Run a one-seed representative case first, then the scene/seed matrix:

~~~bash
python3 scripts/run_servo_gmti_e2e.py --scene target_free --seed 101 --output outputs/servo_gmti_e2e_smoke
python3 scripts/run_servo_gmti_e2e.py --full-matrix --output outputs/servo_gmti_e2e_formal_work
python3 -m pytest -q tests/test_servo_gmti_e2e_contract.py
~~~

For every reported target, trace it to the production detector and, where
tracking is enabled, preserve the detection identity for the later
TrackManager audit. Commit:

~~~bash
git add scripts/run_servo_gmti_e2e.py tests/test_servo_gmti_e2e_contract.py
git commit -m "feat: add moving target servo e2e"
~~~

## Task 9: Close the Pfa accounting loop

### Test first

Add tests/test_pfa_closure.py for:

- pure-noise IID empirical cell Pfa with an exact numerator/denominator;
- stationary clutter, geometry-error clutter, corrected geometry clutter,
  servo-error clutter, and corrected servo clutter;
- independent CUT and multiple-seed aggregation;
- rejection of configured-alpha wording when only empirical false-hit counts
  exist.

### Implementation

Implement or extend scripts/run_pfa_closure.py with H0 through H5:

- H0 pure noise IID;
- H1 stationary clutter;
- H2 geometry-error clutter;
- H3 corrected geometry clutter;
- H4 servo-error clutter;
- H5 corrected servo clutter.

Freeze GO alpha and report empirical cell Pfa for H0 plus structured-clutter
false-hit fraction for H1-H5. Use a large independent CUT set and multiple
seeds. Store numerator, denominator, excluded/invalid CUT count, duplicate
count, detector threshold, and branch configuration. Never claim configured
1e-6 was achieved merely because it is configured, and do not merge invalid
denominators into the aggregate.

### Verification and commit

Run:

~~~bash
python3 -m pytest -q tests/test_pfa_closure.py
python3 scripts/run_pfa_closure.py --help
python3 scripts/run_pfa_closure.py --smoke --output outputs/pfa_closure_smoke
python3 scripts/run_pfa_closure.py --full-matrix --output outputs/pfa_closure_formal_work
~~~

Recompute at least one aggregate from per-seed rows with an independent small
script and compare numerators/denominators. Commit:

~~~bash
git add scripts/run_pfa_closure.py tests/test_pfa_closure.py
git commit -m "feat: close empirical pfa accounting"
~~~

## Task 10: Add the information-matched J2 versus J4 comparison

### Test first

Add tests/test_information_matched_stap.py verifying that:

- J2 consumes exactly F1/F2 generated from the production (1,3)/(2,4)
  fusion;
- J4 consumes native four-channel inputs;
- all covariance, training, diagonal loading, CUT, CFAR, and seed parameters
  are identical;
- the reported spatial contribution is exactly J4 minus J2 on matched cases.

### Implementation

Add an offline-only driver for J2 two-channel JDL/STAP and J4 native
four-channel JDL/STAP. Keep Current as the deployment baseline. Emit
information_matched_pairwise_aggregate.csv with case-level SCNR, improvement,
Pfa, Pd, runtime, invalid counts, and the exact common parameter block.

Do not change the production CUDA path. Promote neither J4 nor a new default
until the matched pair shows a material, stable advantage across representative
seeds and the result passes the target/Pfa audits.

### Verification and commit

Run:

~~~bash
python3 -m pytest -q tests/test_information_matched_stap.py
python3 scripts/run_information_matched_stap.py --help
python3 scripts/run_information_matched_stap.py --smoke --output outputs/information_matched_smoke
~~~

Commit:

~~~bash
git add scripts/run_information_matched_stap.py tests/test_information_matched_stap.py
git commit -m "feat: compare information matched j2 and j4"
~~~

## Task 11: Enforce true versus reported velocity and run the first delta-v study

### Test first

Add configuration and Stage2 integration tests proving:

- true velocity drives trajectory, echo, clutter, and target geometry;
- reported velocity drives INS/header, CTDR/P38, ridge, steering, and
  conversion paths;
- omitting or mixing the two fields fails loudly rather than silently using
  one value for both;
- known-error Current and known-correction branches use the same scene and
  only differ in the intended information condition.

### Implementation

Add explicit velocity_true and velocity_reported fields to the resolved
scenario/config snapshot and propagate them through the producer and evaluator.
Use the first delta-v sweep 0, plus or minus 0.05, plus or minus 0.1, plus or
minus 0.2, and plus or minus 0.5 m/s. Run Current and known correction first,
then a blind deterministic estimator using ridge, P38/CTDR, and slow-time
phase. Preserve the same production detector and evaluation schema.

Record velocity source for each derived field and include residual, fallback,
and model-mismatch status. Do not combine velocity effects with servo or yaw
until this split is auditable.

### Verification and commit

Run:

~~~bash
python3 -m pytest -q tests/test_velocity_source_split.py
cmake --build build -j4
python3 scripts/run_velocity_error_study.py --help
python3 scripts/run_velocity_error_study.py --smoke --output outputs/velocity_split_smoke
~~~

Commit:

~~~bash
git add simulator scripts tests
git commit -m "feat: separate true and reported velocity"
~~~

## Task 12: Add the yaw-first attitude study

### Implementation

After Task 11 has stable outputs, add a yaw-only sweep with the existing
Current, known, and deterministic-estimator branches. Keep pitch and roll at
zero for this stage. Report yaw versus servo versus baseline in separate
columns, with the same target-free and moving-target controls, source
provenance, and model-validity gate.

### Verification and commit

Run:

~~~bash
python3 -m pytest -q tests/test_attitude_source_split.py
python3 scripts/run_yaw_error_study.py --smoke --output outputs/yaw_split_smoke
~~~

Commit:

~~~bash
git add scripts/run_yaw_error_study.py tests/test_attitude_source_split.py
git commit -m "feat: add yaw first attitude study"
~~~

## Task 13: Run the production TrackManager and PIPE gate

### Test first

Extend the existing track audit tests to require that every protocol target
has a same-period detection linked to a production TrackManager row with:

- track state Confirmed;
- matched_this_frame true;
- is_output true where the protocol marks output;
- detection identity and frame/period identity preserved.

Add negative fixtures for Tentative, Coasted-only, prior-frame-only, raw
detection-only, and unmatched predicted points; all must be excluded.

### Implementation

Add a multi-period driver using the production TrackManager/PIPE path for 3 to
5 periods and a representative seed matrix. Retain track_debug outputs and
the protocol payload audit. Compute track Pd, false-track rate, continuity,
angle RMSE, velocity RMSE, and position RMSE. Preserve the Current production
baseline and compare any correction branch only through the same detector,
association, and output rules.

### Verification and commit

Run:

~~~bash
python3 -m pytest -q tests/test_track_manager_protocol_audit.py
python3 scripts/run_p4_truth_eval.py --help
python3 scripts/audit_track_manager_run.py --help
python3 scripts/run_track_manager_e2e.py --smoke --output outputs/track_manager_smoke
~~~

Inspect a representative audit manually and verify every emitted protocol
target has a same-period Confirmed plus matched detection. Commit:

~~~bash
git add scripts/run_track_manager_e2e.py scripts/audit_track_manager_run.py scripts/run_p4_truth_eval.py tests
git commit -m "feat: audit production trackmanager protocol"
~~~

## Task 14: Produce the formal evidence bundle, reports, and final verification

### Implementation

Run the completed geometry, servo, moving-target, Pfa, information-matched,
velocity, yaw, and TrackManager matrices with independent output directories.
Then run the collector from Task 1 to produce only:

- manifest.json;
- summary.csv;
- correction_decisions.csv;
- target_transfer.csv;
- pfa_summary.csv;
- servo_estimates.csv;
- servo_decisions.csv;
- information_matched_pairwise_aggregate.csv.

Update the navigation/report documents with:

- the experiment order and exact commands;
- Current, known-error correction upper bound, estimated-error correction,
  target-assisted, and clutter-only information conditions;
- recovery-space and recovery-ratio definitions;
- empirical Pfa versus configured alpha distinction;
- geometry and servo fallback/model-mismatch rates;
- target-selection and position-error limitations;
- velocity source split and yaw-first scope;
- TrackManager/PIPE evidence paths;
- AI disabled status and the explicit evidence required before any future AI
  stage.

Run the formal link checker or an equivalent repository-local check so every
document link points to a tracked file or a deliberately retained ignored
artifact with a documented reproduction command.

### Final verification

Run fresh commands from the clean implementation worktree:

~~~bash
cmake -S . -B build -DCMAKE_BUILD_TYPE=Release
cmake --build build -j4
ctest --test-dir build --output-on-failure
python3 -m pytest -q
python3 -m compileall -q scripts tests
git diff --check
git status --short --untracked-files=all
~~~

Inspect GPU evidence with nvidia-smi before CUDA experiments and record model,
driver, P-state, frequency, power, temperature, load, warm-up, repetitions,
and timing scope. Recompute the formal aggregates from their compact inputs,
check every manifest source identity, and confirm no raw output is staged.

Commit the final docs and compact evidence in a reviewable commit:

~~~bash
git add docs outputs/formal_evidence .gitignore
git commit -m "docs: publish unknown error formal evidence"
~~~

Push only after staged-path review confirms that no user-owned untracked output,
raw input, secret, or unrelated change is included. Do not force-push or rewrite
history.

## Self-review coverage matrix

Before execution, confirm every attachment requirement maps to a task:

| Requirement | Task |
| --- | --- |
| Compact Git-tracked evidence and no broken links | 1, 14 |
| Finite-range v2 and v1 comparison | 2, 3 |
| Dynamic deadband and no empirical bias table | 3 |
| Residual/closure/model mismatch gate | 4 |
| Target-assisted servo rename | 5 |
| Clutter-only servo estimator and four branches | 6, 7 |
| True moving-target servo E2E | 8 |
| Pfa H0-H5 closure | 9 |
| J2/J4 information-matched attribution | 10 |
| True/reported velocity split | 11 |
| Yaw before pitch/roll | 12 |
| Production TrackManager/PIPE audit | 13 |
| AI remains off pending evidence | Scope, 14 |

The plan is complete when each task has a concrete test, implementation
surface, verification command, and bounded commit. No task may declare a
scientific advantage from file existence or process exit code alone.
