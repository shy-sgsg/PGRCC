# PGRCC Phase R7 GO-CFAR Correctness Implementation Plan

> **历史执行计划快照（2026-10-22）。** 本文的任务范围与复选状态仅描述该计划形成时的阶段任务，不代表当前仍待执行或授权重启；当前研究主题与优先任务以[研究总纲](../../research/RESEARCH_MASTER_PLAN.md)和[研究决策日志](../../research/RESEARCH_DECISION_LOG.md)为准。

> **For agentic workers:** Implement this user-approved R7 scope inline, task by task, with test-first verification.

**Goal:** Repair CUDA GO-CFAR accumulation without changing its geometry, alpha, or strict hit rule, then re-evaluate seed-101 detection/association and physical-correction results on comparable pre/post Production runs.

**Architecture:** Keep independent CPU float64 direct-window and integral-image references separate from CUDA. Share the production CUDA double-precision accumulation/window helpers with a focused CUDA selftest, then validate on saved R6 maps and full Production replays. Freeze one-to-one truth association before examining repaired-condition gains; report H0, target-only, and target-present metrics separately.

**Tech Stack:** C++11, native CUDA, CMake/CTest, Python 3 + NumPy, existing seed-101 Stage2 data and Production runner.

**Spec:** `/home/shy/.codex/attachments/64117f5f-95df-4d9b-a81e-dbaa4b3877bc/pasted-text-1.txt`

## Global Constraints

- Preserve Production input power as float32, GO geometry, runtime alpha, valid-CUT mask, and strict `CUT > threshold` decisions.
- Do not clamp thresholds, add epsilons, retune Pfa, or change unrelated Production parameters.
- Use real CUDA for kernel and Production evidence; distinguish implementation, execution, evaluability, and performance verification.
- Use only seed 101 and the A0/B/C OFF/ON/TO matrix plus A0/A0 repeatability; do not launch seeds 202/303, AI, D3, or new algorithm baselines.
- Reuse hash-verified R6 inputs and outputs read-only; write new evidence only to a new R7 output root.
- H0/OFF must not report target Pd or target-parameter RMSE. Do not remove post-hoc outliers.

## Review Focus

- A zero-valued true background must remain zero; no positive floor or threshold clamp may be introduced.
- A strong scatterer outside a local training window must not corrupt its nonnegative window sum through prefix subtraction.
- Circular Doppler wrapping, non-circular row edges, range edges, exclusion rows, and CUT-band filtering must match Production exactly.
- CPU direct and CPU integral references must agree before either is used to judge CUDA output, including the saved R6 Production map.
- In `csi_detection_band_mode=union`, the exported power map is a per-cell max of dynamic/full branch maps; the threshold at each CUT follows the higher-power branch, but its training neighborhood in the exported map can mix branches. Use branch-specific input power maps for threshold/reference comparisons.
- The exact runtime alpha must be read from Production geometry evidence; metadata alpha and actual CUDA alpha must be reconciled before threshold comparison.
- Truth association must be one-to-one, same-period, same-beam, bounded in range/Doppler, and pass the Production cluster/detection path; fix the rule before post-fix A/B/C metrics are inspected.

---

### Task 1: Independent CPU float64 GO-CFAR references

**Files:**
- Create: `scripts/phase_r/go_cfar_reference.py`
- Create: `tests/test_phase_r7_cfar_reference.py`

**Interfaces:**
- Produce immutable `CfarGeometry(height, width, guard_cells, background_cells, doppler_circular, exclude_row_start, exclude_row_end, cut_band_start, cut_band_end, cut_band_mode)`, `ThresholdDetail`, and `ThresholdMaps`.
- Implement `thresholds_direct_f64(power_f32, geometry, alpha, detail_cuts=()) -> ThresholdMaps`, the same-signature `thresholds_integral_f64(...)`, and single-CUT `threshold_detail_direct_f64(power_f32, geometry, alpha, row, col)` / `threshold_detail_integral_f64(...)`. `ThresholdMaps` contains float64 threshold/noise arrays, geometry-valid and valid-CUT masks, and per-requested-CUT directional sums/means/window bounds.
- Require direct/integral agreement at `rtol=1e-8, atol=1e-12` for positive references, and exact zero for all-zero directional windows. When the integral cancellation bound exceeds half an output float32 ULP, recompute that rectangle directly in float64 and expose the fallback count; do not relax tolerance or clamp outputs.

- [x] First run a regression against the saved R6 Production map; verify CPU references disagree with the old CUDA threshold at `(111,3337)` for the expected numeric reason.
- [x] Test direct and integral references against one another on uniform, high-dynamic-range, weak-background/strong-target, sparse/zero-background, wrapped-Doppler, edge, exclusion, cut-band, and nonuniform/correlated maps.
- [x] Implement the host reference and geometry semantics; keep the integral image as a fast path but directly recompute only ill-conditioned rectangles, then run focused Python tests and existing CFAR geometry tests.
- [x] Commit Task 1 source/tests after focused tests passed (`f50ce8a`); do not push before the whole R7 stage is reviewed.

### Task 2: CUDA double-precision GO-CFAR and regression harness

**Files:**
- Create: `include/go_cfar_device.cuh`
- Create: `tests/go_cfar_cuda_numerical_selftest.cu`
- Modify: `src/gpu/gpu_kernels.cu`
- Modify: `CMakeLists.txt`

**Interfaces:**
- Shared CUDA helpers expose `row_prefix_kernel(const float*, double*, int, int)`, `col_prefix_to_integral_kernel(const double*, double*, int, int)`, `rect_sum_device(const double*, int, int, int, int, int)`, `doppler_rect_sum_device(...)`, and `go_noise_level_device(...)`. Production and the CUDA selftest call the same helpers.
- Threshold output remains float32 for the existing diagnostics ABI; compare the float32 emission against the float64 references with a predeclared maximum error of 2 float32 ULPs. If a rectangle's cancellation error bound exceeds half an output ULP, recompute that direction directly in float64 and record the fallback; exact-zero reference windows must remain exactly zero.

- [x] Run focused pre-fix CUDA replays on the hash-verified seed-101 A0_ON period-0 input with `csi_detection_band_mode=dynamic` and `full`; compare the dynamic branch at CUT `(111,3337)` with its paired CPU references. Keep the R6 union map as a merged-output fixture, not as a branch reference.
- [x] Test all requested synthetic classes and every valid CUT; use actual float32 power input and the alpha recorded by Production geometry diagnostics.
- [x] Implement double accumulation while preserving geometry, alpha input, and decision semantics; keep output/memory accounting correct.
- [x] In CUDA, use the same cancellation-bound fallback policy as the CPU reference; fallback recomputes the directional window from the float32 power map rather than clipping a sum or threshold.
- [x] Run the CUDA selftest on the real device and verify reference agreement, zero-background behavior, hit decisions, and old-R6 CUT correction.
- [x] Commit Task 2 source/tests only after native Release build and CUDA tests pass; committed as `04958a9` before the current R7 closeout.

### Task 3: Freeze and test task association/H0 evaluation

**Files:**
- Create: `scripts/phase_r/evaluate_r7_task_metrics.py`
- Create: `tests/test_phase_r7_task_metrics.py`
- Create: `tests/test_phase_r7_cluster_lineage.py`
- Modify: `src/processOnePeriod.cpp`
- Reuse read-only: existing Production detection, cluster summaries, TrackManager debug, and truth files.

**Interfaces:**
- The evaluator consumes per-period truth, Production detection rows, CFAR/cluster counts, and TrackManager records; it emits separate ON, OFF, and TO metrics with row-level association evidence.
- Freeze the truth gate as same beam and period, range and circular Doppler each within 2 bins, and a Production cluster/detection candidate; solve one-to-one by minimum normalized bin residual, with no fallback to the former 1 km / 150 m gate.

- [x] Add opt-in per-period lineage rows for post-filter cluster peak, selection, and raw detection; join final detection by exact row/column. Diagnostics-off control emits no lineage.
- [x] Test one-to-one competing detections, H0 detections near truth, rejected clusters, unresolved radial velocity, Production Doppler-axis mapping, and target-only complex transfer.
- [x] Verify lineage completeness for repaired Production outputs and keep legacy pre-fix cluster counts `NOT_EVALUABLE`.
- [x] Verify separate ON/H0/TO metrics and per-opportunity position/angle/speed/radial error evidence; freeze-gate evaluator ran on pre-fix before repaired comparisons.
- [x] Focused evaluator and lineage tests pass; R7 changes will be reviewed and committed as one auditable stage.

### Task 4: Same-input CUDA Production revalidation and R7 reports

**Files:**
- Modify: `scripts/phase_r/run_r7_minimal_production_matrix.py`
- Modify: `scripts/phase_r/run_r7_prefix_b_replays.py`
- Create: `scripts/phase_r/evaluate_r7_task_metrics.py`
- Create: `scripts/phase_r/evaluate_r7_production_matrix.py`
- Create: `docs/research/phase_r/R7_CUDA_CFAR_numerical_root_cause.md`
- Create: `docs/research/phase_r/R7_CUDA_CFAR_fix_and_tests.md`
- Create: `docs/research/phase_r/R7_H0_detection_association_audit.md`
- Create: `docs/research/phase_r/R7_minimal_production_revalidation.md`
- Create: `docs/research/phase_r/R7_research_direction_decision.md`
- Create compact run manifest/configuration and analysis products under `outputs/phase_r7_cfar_correctness_20261022/`.

- [x] Preserve the pre-fix executable by SHA and verify reused A0/C seed-101 inputs and pre-fix outputs against R6 manifests; run missing pre-fix B roles with that executable.
- [x] Run the repaired immutable Release executable on A0/B/C × OFF/ON/TO, plus an A0/A0 repeatability control, with actual XML, logs, CSI taps, CFAR maps/geometry, stage summaries, and runtime/build/GPU identity.
- [x] Compare pre/post using identical Stage2 inputs; report nonpositive thresholds split by negative/zero and ON/OFF/TO, reference error and hit flips, H0 detections/tracks, TO response, and the available ON candidate metrics.
- [x] Quantify double-precision memory cost and document that device load invalidates a clean performance benchmark.
- [x] Produce the five R7 reports and keep historical R6 source records unchanged; R7 separately reanalyzes and qualifies historical CFAR counts.
- [x] Run full applicable CTest/Python tests, Release build, `git diff --check`, and artifact/manifest consistency checks.
- [x] Obtain the user's approval to implement the explicitly requested minimum cluster-lineage diagnostics; validate diagnostics-on/off behavior.
- [x] Execute the final three-period CUDA Production matrix, strict one-to-one task evaluation, A0 repeat, and diagnostics-off control; preserve the single-period numerical-map coverage limitation explicitly.
- [x] Complete the five R7 reports and compact task-metrics evidence; answer Q1–Q10 and stop without starting new seeds/D3/AI.
- [x] Complete final build/tests: native Release build passed, 45 focused Python tests passed, regular CTest passed 24 with the device-invisible CUDA test skipped, and restricted-device CUDA numerical selftest passed 1/1; `git diff --check` and compact-evidence comparisons passed.
- [ ] Finish the code/diff review, stage only audited R7 code/tests/docs/evidence, then commit and push to `origin/codex/unknown-system-error-next-stage`.

## Current status (2026-10-22)

- Task 1: complete and committed.
- Task 2: complete, real CUDA-tested, and committed in `04958a9`.
- Task 3: complete. Frozen one-to-one evaluator, per-period task errors, and diagnostic-gated cluster lineage are implemented; repaired output lineage is complete. Historical pre-fix cluster lineage and complete three-period union hit-cell count remain unavailable and are reported as such.
- Task 4: complete. Nine three-period pre/post pairs plus repeat/diagnostics-off controls executed on CUDA; five reports and compact evidence include final task metrics. Numerical all-CUT comparisons remain scoped to the saved one-period maps and R6 branch-period maps; three-period sidecars retain per-period counts/hash but not separate raw map files.
- Closeout: final test/build/diff review, explicit staging, commit and push remain. No seed202/303, D3, AI, Router, or new baseline has been run.

## Completion Gates

- CPU direct and CPU integral references agree before CUDA comparison.
- The R6 saved Production map reproduces the pre-fix CUDA error; repaired CUDA thresholds meet the declared float32 ULP tolerance across valid CUTs and the required fixtures.
- Actual R7 Production runs use a native CUDA build and exact paired seed-101 inputs; no CPU result is labeled Production evidence.
- Every requested conclusion is marked `IMPLEMENTED`, `EXECUTED`, `EVALUABLE`, or `PERFORMANCE_VERIFIED` as applicable.
- R7 reports stop at evidence-based recommendations; no AI training or seed 202/303 launch occurs.
