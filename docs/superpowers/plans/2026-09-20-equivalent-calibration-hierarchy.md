# Equivalent Calibration Hierarchy Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 在生产等效 F1/F2 Phase-I 边界内实现并验证传统等效复校准 baseline，完成合成 sanity 与小规模 mechanism pilot/evidence，同时把研究主线改写为 physical coarse correction 加 robust residual calibration。

**Architecture:** 文档层冻结 Class-E/Class-P/Class-D 和 Phase-II/AI 边界；算法层提供纯 NumPy 的 truth-blind calibration core；实验层通过固定 seed 的 synthetic mechanism runner 生成 Mode-A/Mode-B 和 T1–T5 compact evidence，并为现有生产 pilot 保留可审计的 downstream `NOT_EVALUABLE`/历史参考边界。

**Tech Stack:** Python 3、NumPy、pytest、JSON/CSV、现有 CMake/CUDA 工程与 Git provenance helpers。

**Spec:** `docs/superpowers/specs/2026-09-20-equivalent-calibration-hierarchy-design.md`

## Global Constraints

- Phase-I scientific input is `4ch protocol IQ → (1,3)/(2,4) F1/F2`; raw six-pair is diagnostic only.
- `Class-E` accepts local multiplicative `Gamma`; `Class-P` preserves physical registration/state correction; `Class-D` preserves irreducible decorrelation.
- `clutter-derived Gamma` is a traditional baseline, not a novelty claim.
- Estimators never read truth system error, target truth, or known injected error.
- Mode-A estimates from `OFF=C+N`; Mode-B robust methods may estimate from `ON` without target truth; report separately.
- Insufficient support and zero denominator are explicit `NOT_EVALUABLE`; no silent Current fallback.
- Keep `ai_training=false`, `router_enabled=false`, and native four-channel STAP/JDL frozen.
- Do not run the full Cartesian CUDA matrix before the synthetic sanity and targeted pilot are green.
- Preserve existing channel-delay, true/report, observability, TrackManager, Pfa, servo/velocity/yaw artifacts and their limitations.

## Review Focus

- F1/F2 conjugation/sign convention: the estimator must use `sum(F2*conj(F1))/sum(|F1|²)` and residual `F2-Gamma*F1`; test exact recovery.
- Local status propagation: a zero denominator or low support must be observable and must not become a zero/Current result; test `NOT_EVALUABLE` and apply rejection.
- Robust target exclusion: online robust estimation must not access target truth and must reject a deliberately phase-inconsistent strong target; test ON-derived contamination.
- Range-band/Doppler broadcasting: DDC and DDC-RB must apply the returned Gamma with correct axes; test shape and band boundaries.
- Evidence separation: Mode-A/Mode-B and measured/not-evaluable/historical-reference rows must remain distinguishable; test manifest contracts and no raw-data copying.

---

### Task 1: Freeze the new research boundary in durable docs

**Files:**
- Modify: `AGENTS.md:90-145` with the Phase-I hierarchy and Class-E/Class-P/Class-D rules.
- Modify: `README.md:1-190` to lead with the hierarchical calibration title, three layers, preserved channel-delay interpretation, and frozen Phase-II/AI boundaries.
- Create: `docs/AI_CSI_39_等效复校准与物理系统校准边界.md` with the error classification table, Blasone baseline attribution, Paper-1 reframing, and decision-gate template.

**Interfaces:**
- Consumes: existing `AI_CSI_34`–`AI_CSI_38` framing and the spec.
- Produces: durable repository rules and a numbered Phase-I reading/report entry; no algorithm behavior changes.

- [ ] **Step 1: Write the documentation contract test**

Add `tests/test_equivalent_calibration_docs.py` that reads the three files and asserts the exact hierarchy tokens (`Class-E`, `Class-P`, `Class-D`, `clutter-derived`, `ai_training=false`, `router_enabled=false`, and the Phase-II native four-channel freeze) plus the new numbered document path in README.

- [ ] **Step 2: Run the documentation test to verify it fails**

Run: `pytest -q tests/test_equivalent_calibration_docs.py`

Expected: FAIL because the new durable rules and document do not yet exist.

- [ ] **Step 3: Update the durable docs and write the classification table**

Keep existing historical evidence and limitations. Add the hierarchy, three classes, the channel-delay Class-P framing, the five traditional calibration baselines, the equal-information modes, and the no-AI/Phase-II gates without claiming pilot results.

- [ ] **Step 4: Run the focused documentation test**

Run: `pytest -q tests/test_equivalent_calibration_docs.py`

Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add AGENTS.md README.md docs/AI_CSI_39_等效复校准与物理系统校准边界.md tests/test_equivalent_calibration_docs.py
git commit -m "docs: define hierarchical equivalent calibration boundary"
```

### Task 2: Implement the truth-blind complex calibration core

**Files:**
- Create: `scripts/two_channel_complex_calibration.py` with `CalibrationEstimate`, `estimate_scc`, `estimate_ddc`, `estimate_ddc_rb`, `estimate_robust_ddc`, `estimate_robust_ddc_rb`, and `apply_complex_calibration`.
- Create: `tests/test_two_channel_complex_calibration.py` with unit tests for constant, Doppler, range-Doppler, sign, shape, support, and zero-denominator behavior.

**Interfaces:**
- Consumes: equal-shape complex `F1/F2[range,doppler]` and equal-shape boolean `clutter_support`.
- Produces: an estimate object with `.gamma`, `.status`, `.support_count`, `.method`, `.metadata`; `apply_complex_calibration` returns `F2-Gamma*F1` and rejects invalid estimates unless explicitly handled by the caller.

- [ ] **Step 1: Write the failing estimator tests**

Tests must construct deterministic arrays and assert exact/near-exact Gamma recovery, DDC superiority on Doppler variation, DDC-RB superiority on range+Doppler variation, circular phase robust exclusion, explicit `NOT_EVALUABLE`, and a conjugation-sign regression.

- [ ] **Step 2: Run the estimator tests to verify they fail**

Run: `pytest -q tests/test_two_channel_complex_calibration.py`

Expected: FAIL with the module/functions unavailable.

- [ ] **Step 3: Implement the minimal shared estimator and status path**

Use the fixed conjugation formula. Validate finite shape/support inputs, return per-local support/status, use caller-supplied `min_support`, `range_band_size`, and `phase_threshold_rad`, and never inspect any truth/target field.

- [ ] **Step 4: Run the focused estimator tests**

Run: `pytest -q tests/test_two_channel_complex_calibration.py`

Expected: PASS.

- [ ] **Step 5: Run Python syntax and adjacent estimator regressions**

Run: `python3 -m py_compile scripts/two_channel_complex_calibration.py && pytest -q tests/test_delay_stage1_core.py tests/test_joint_physics_calibration.py`

Expected: PASS with no change to existing delay/physics contracts.

- [ ] **Step 6: Commit**

```bash
git add scripts/two_channel_complex_calibration.py tests/test_two_channel_complex_calibration.py
git commit -m "feat: add two-channel complex calibration baselines"
```

### Task 3: Add the synthetic Γ recovery sanity runner

**Files:**
- Create: `scripts/run_two_channel_complex_calibration_sanity.py` generating T1–T5, running Mode-A and Mode-B, writing compact CSV/JSON evidence, and returning nonzero on failed pre-registered checks.
- Create: `tests/test_two_channel_complex_calibration_sanity.py` covering the runner’s case contract, target-truth exclusion, coherence-floor calculation, and required outputs.
- Modify: `configs/research/equivalent_vs_physical_calibration_pilot.json` to declare fixed seed, dimensions, support, thresholds, methods, cases, modes, and evidence root.

**Interfaces:**
- Consumes: Task 2 estimator API and JSON config.
- Produces: `sanity_manifest.json`, `gamma_recovery.csv`, `clutter_metrics.csv`, `target_transfer.csv`, and a `sanity_status` that is `passed` only when T1–T5 assertions pass.

- [ ] **Step 1: Write the failing runner-contract tests**

Assert all T1–T5 are present, no estimator input path contains truth/target truth, output schemas include status/mode, theoretical decorrelation floor is recorded, and the target-contamination case shows robust improvement.

- [ ] **Step 2: Run the runner tests to verify they fail**

Run: `pytest -q tests/test_two_channel_complex_calibration_sanity.py`

Expected: FAIL because the runner/config/output contract is absent.

- [ ] **Step 3: Implement deterministic generation and checks**

Generate only F1/F2/support arrays in memory, keep truth in evaluator-only variables, execute target-free and ON-derived modes separately, write compact evidence, and mark unavailable downstream fields `NOT_EVALUABLE` rather than inventing proxy Pd/Track values.

- [ ] **Step 4: Run the sanity tests and runner**

Run: `pytest -q tests/test_two_channel_complex_calibration_sanity.py && python3 scripts/run_two_channel_complex_calibration_sanity.py --config configs/research/equivalent_vs_physical_calibration_pilot.json --output-root outputs/formal_evidence/equivalent_vs_physical_pilot`

Expected: PASS and a manifest with `sanity_status=passed`, explicit method rows, and no raw binary arrays.

- [ ] **Step 5: Commit the sanity implementation and compact evidence contract**

```bash
git add scripts/run_two_channel_complex_calibration_sanity.py tests/test_two_channel_complex_calibration_sanity.py configs/research/equivalent_vs_physical_calibration_pilot.json
git commit -m "test: add synthetic complex calibration sanity"
```

### Task 4: Add the targeted equivalent-vs-physical pilot/evidence analyzer

**Files:**
- Create: `scripts/run_equivalent_vs_physical_calibration.py` with the small mechanism cases and Mode-A/Mode-B separation.
- Create: `scripts/analyze_equivalent_vs_physical_calibration.py` producing the required evidence CSVs and one decision label.
- Create: `tests/test_equivalent_vs_physical_calibration_pilot.py` for case coverage, no truth leakage, output schema, and decision-label admissibility.
- Modify: `configs/research/equivalent_vs_physical_calibration_pilot.json` only as needed to expose the targeted pilot cases and frozen parameters.

**Interfaces:**
- Consumes: Task 2 calibration core, existing current/physical evidence paths as declared historical references, and the pilot config.
- Produces: `manifest.json`, `method_contract.csv`, `gamma_recovery.csv`, `clutter_metrics.csv`, `target_transfer.csv`, `detection_metrics.csv`, `track_metrics.csv`, `decision_matrix.csv`, and `report.md`.

- [ ] **Step 1: Write failing pilot contract tests**

Cover pure equivalent mismatch, delay, servo, velocity, and decorrelation; assert both calibration modes; assert delay is represented as fast-time/range registration rather than silently passed to a Doppler-only estimator; assert unmeasured downstream fields are `NOT_EVALUABLE`.

- [ ] **Step 2: Run the pilot tests to verify they fail**

Run: `pytest -q tests/test_equivalent_vs_physical_calibration_pilot.py`

Expected: FAIL because the runner/analyzer and required evidence are absent.

- [ ] **Step 3: Implement the targeted pilot runner**

Use small deterministic arrays and mechanism metadata. For fast-time delay, include a frequency-phase/range-registration observable distinct from Doppler Gamma. For servo/velocity, preserve physical-state bias fields and do not claim clutter suppression equals state recovery. For decorrelation, compute empirical coherence and the single-coefficient residual floor. Do not add a CUDA Cartesian sweep.

- [ ] **Step 4: Implement analyzer and decision gate**

Aggregate only measured rows, preserve `NOT_EVALUABLE` and `historical_reference` statuses, compare robust residual calibration against physical-state fields, and choose one of the four allowed non-AI labels without hard-coding the expected outcome.

- [ ] **Step 5: Run focused pilot tests and the pilot**

Run: `pytest -q tests/test_equivalent_vs_physical_calibration_pilot.py && python3 scripts/run_equivalent_vs_physical_calibration.py --config configs/research/equivalent_vs_physical_calibration_pilot.json --output-root outputs/formal_evidence/equivalent_vs_physical_pilot`

Expected: PASS, complete compact evidence contract, explicit limitations, and one decision label.

- [ ] **Step 6: Commit**

```bash
git add scripts/run_equivalent_vs_physical_calibration.py scripts/analyze_equivalent_vs_physical_calibration.py tests/test_equivalent_vs_physical_calibration_pilot.py configs/research/equivalent_vs_physical_calibration_pilot.json
git commit -m "feat: add equivalent versus physical calibration pilot"
```

### Task 5: Write the current-stage report and run verification gates

**Files:**
- Create: `docs/AI_CSI_40_等效复校准与物理校准定向Pilot报告.md` with actual sanity/pilot results, evidence paths, historical references, limitations, and the decision gate.
- Modify: `README.md` only if the final evidence/reading navigation needs a one-line entry.
- Create: `tests/test_equivalent_calibration_report.py` to pin the report/evidence contract.

**Interfaces:**
- Consumes: Task 3/4 compact outputs, current branch/source identity, and existing AI_CSI_36–38 limitations.
- Produces: a source-backed report that answers the requested eleven questions without converting `NOT_EVALUABLE` to pass or opening AI.

- [ ] **Step 1: Write the report contract test**

Assert the report names every required evidence file, states the actual sanity/pilot status, includes the five mechanism cases, records DDC substitution findings separately from physical-state findings, and keeps `ai_training=false`/`router_enabled=false`.

- [ ] **Step 2: Run the report test to verify it fails**

Run: `pytest -q tests/test_equivalent_calibration_report.py`

Expected: FAIL because the report is absent.

- [ ] **Step 3: Author the report from compact evidence**

Do not invent full CUDA/Track/Pfa results. Link the existing delay/servo/velocity documents as historical references with their `NOT_EVALUABLE` boundaries, and state the next formal experiment only where the new pilot evidence warrants it.

- [ ] **Step 4: Run final verification**

Run: `pytest -q tests/test_equivalent_calibration_docs.py tests/test_two_channel_complex_calibration.py tests/test_two_channel_complex_calibration_sanity.py tests/test_equivalent_vs_physical_calibration_pilot.py tests/test_equivalent_calibration_report.py`; `python3 -m py_compile scripts/two_channel_complex_calibration.py scripts/run_two_channel_complex_calibration_sanity.py scripts/run_equivalent_vs_physical_calibration.py scripts/analyze_equivalent_vs_physical_calibration.py`; `cmake --build build -j4`; `ctest --test-dir build --output-on-failure`; `git diff --check`.

Expected: all focused Python tests, syntax checks, CMake build, CTest, and diff check pass; any pre-existing unrelated failures are recorded by name.

- [ ] **Step 5: Commit the report and final navigation**

```bash
git add docs/AI_CSI_40_等效复校准与物理校准定向Pilot报告.md README.md tests/test_equivalent_calibration_report.py
git commit -m "docs: report equivalent calibration pilot boundary"
```

### Delivery and push gate

After Task 5, inspect the staged file list and ensure no untracked `outputs/`, raw BIN/NPY, or unrelated user artifact is included. Because the repository instructions explicitly enable stage-complete auto-push, push only the reviewed commits to `origin/codex/unknown-system-error-next-stage` after all verification gates pass; do not force-push or rewrite the existing 18 local commits.
