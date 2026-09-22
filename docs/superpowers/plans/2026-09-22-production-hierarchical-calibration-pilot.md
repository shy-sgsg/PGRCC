# Production Hierarchical Calibration Pilot — Implementation Plan

> **For the implementer:** REQUIRED SUB-SKILL: Use `superpowers:executing-plans` (or `superpowers:subagent-driven-development` when dispatching the tasks) to execute this plan task by task. Every production-code change follows TDD: add or update a focused test, run it red, implement the smallest change, run it green, then run the relevant regression suite.

## Global Constraints

- Work only in `/home/shy/AIR/SAR+AI/project/PGRCC/.worktrees/unknown-system-error-next-stage` on `codex/unknown-system-error-next-stage`.
- Preserve all unrelated tracked and untracked user output. Never run `git clean`, `git reset --hard`, `git checkout --`, or a broad recursive delete.
- The production Current path is unchanged when the research calibration flag is absent or false. No default XML, CSI kernel, TrackManager, PIPE, CFAR, channel fusion, or native four-channel STAP behavior changes.
- The research adapter is allowed only after final production F1/F2 coarse alignment, FFT/DBS, and range-phase correction, before the existing CSI output reaches CFAR. It must not overwrite F1/F2 input buffers.
- Gamma convention is fixed: `Gamma=sum(F2*conj(F1))/sum(|F1|^2)` and residual `F2-Gamma*F1`.
- Support is derived from production support/P38/CSI validity and finite/power/coherence checks. No target truth, target position, injected error label, or known error state may enter an estimator. Known physical branches are evaluator-only and labeled as such.
- `ai_training=false`, `router_enabled=false`, `native_4ch_stap=false` in every new config, manifest, and report. Do not add AI, router, or native 4ch STAP work.
- TrackManager/PIPE acceptance reuses existing production debug outputs. Do not implement a replacement tracker or alter association semantics. CFAR cell Pfa is `hit_cut_count/valid_cut_count` only when the denominator is positive; cluster/protocol/track counts stay separate.
- Formal evidence is compact and tracked only under `docs/evidence/equivalent_vs_physical_pilot/`: `manifest.json`, `method_contract.csv`, `gamma_recovery.csv`, `clutter_metrics.csv`, `decision_matrix.csv`, `report.md`. Raw BIN/IQ/NPY/NPZ/F32/PNG and full production output remain in an independent ignored output root.
- Before the formal pilot, tracked `git status --porcelain` must be empty. The manifest records a frozen commit, `dirty=false`, config/runner/analyzer hashes, seed, and exact command. The runner must fail closed rather than overwrite a non-empty root.
- Delay is the only production error in this stage. Run the targeted pilot before any larger sweep. Servo, velocity, decorrelation, and coupled-error work remains a later stage.
- Final verification uses native CUDA Release build and CTest where available, plus Python tests, syntax/JSON checks, `git diff --check`, and a recorded GPU probe. If GPU access is unavailable, preserve the exact pending command and mark the GPU experiment as pending; do not claim algorithmic success.

## Review Focus

- Semantic separation of mechanism-only `M0/M1` from production `C0`.
- Research flag/default compatibility and XML/runtime snapshot completeness.
- Adapter placement and buffer ownership: final F1/F2 read-only, CSI output only, fail-closed support status.
- DDC sign/conjugation, robust phase-outlier rule, range-band final-short-band boundary, and low-coherence/zero-support status.
- Mode-A OFF-only estimator input and Mode-B truth-blind ON input.
- Same scene/seed/target/CFAR/clustering/TrackManager/PIPE contract across method branches.
- Causal target accounting (`ON-OFF`, `TO`) and the CFAR → cluster → protocol → track waterfall.
- No false promotion of sensor priors to radar estimates; no historical decision leakage into the delay decision.
- Evidence provenance and dirty-worktree guard; no raw artifacts staged.

## Task 1: Correct historical semantic/status contracts and add Python regressions

**Files:** `scripts/run_equivalent_vs_physical_calibration.py`, `scripts/analyze_equivalent_vs_physical_calibration.py`, `configs/research/equivalent_vs_physical_calibration_pilot.json`, `tests/test_equivalent_vs_physical_calibration_pilot.py`, `tests/test_equivalent_calibration_report.py`, and a focused new/updated status test under `tests/`.

1. Add/update tests first. Assert that the mechanism-only method contract names `M0` as `uncalibrated_subtraction_proxy`, `M1` as `ordinary_complex_subtraction`, and never uses either name as production Current. Assert servo/velocity physical rows are `SENSOR_PRIOR_ONLY`, with the complete status vocabulary available and invalid/low-coherence metadata is `NOT_EVALUABLE`.
2. Run the focused tests and capture the expected red failures.
3. Change the method contract, report text, status fields, and allowed decision labels so the mechanism report does not emit `GO_COUPLED_PHYSICAL_STATE_STUDY`; when production evidence is absent it emits `NEED_MORE_SINGLE_ERROR_PRODUCTION_EVIDENCE`. Keep historical references visibly historical.
4. Add the low-coherence metadata regression without changing estimator math or thresholds.
5. Run the focused tests green, then the existing equivalent-calibration test files. Record the test commands in the task report.

**Acceptance:** The existing mechanism pilot remains deterministic and truth-blind, but its names/statuses cannot be confused with production Current or radar-estimated servo/velocity. No production C++ behavior changes in this task.

## Task 2: Implement a tested host-side production calibration adapter

**Files:** new `include/production_calibration_adapter.hpp`, new `src/production_calibration_adapter.cpp`, new `tests/production_calibration_adapter_selftest.cpp`, `CMakeLists.txt` (source/selftest registration), and only the minimum include changes needed.

1. Add a failing C++ selftest before implementation. Cover: ordinary `F2-F1`; Gamma=1 residual; positive phase/sign convention; SCC, robust DDC, and robust DDC-RB; final short range band; unsupported/zero-support `NOT_EVALUABLE`; robust low-coherence/outlier exclusion; read-only F1/F2 buffers; and diagnostic metadata containing `truth_used_in_estimator=false`.
2. Run the selftest target (or compile the test translation unit if the target is not yet registered) and capture red output.
3. Implement a small host-side adapter API that takes final production F1/F2, the production `az/range` support bounds, `min_support`, `range_band_bins`, and robust phase threshold. It returns output CSI plus explicit method/status/support/excluded/Gamma summary. Use no target/truth inputs. Use `F1` unchanged outside the declared production support, and return `NOT_EVALUABLE` instead of falling through to Current for an unavailable requested method.
4. Add the source to the production build and the selftest target. Keep C++11 compatibility and avoid new dependencies.
5. Run the selftest green, then the adapter-focused CTest/CTest-equivalent target. Record the red/green sequence.

**Acceptance:** The adapter is deterministic, finite-checked, boundary-safe, and independently testable. It does not know about XML, target truth, or TrackManager and does not mutate its F1/F2 inputs.

## Task 3: Add explicit research configuration, runtime provenance, and the CUDA production tap

**Files:** `include/config_structs.hpp`, `src/loadXML.cpp`, `src/runtime_diagnostics.cpp`, `include/runtime_diagnostics.hpp` if required, `src/gpu/gpu_kernels.cu`, `include/GMTIProcessor.hpp` only if required, `CMakeLists.txt`, and focused C++/Python tests.

1. Add failing configuration/runtime tests first. Assert absent/false research configuration preserves defaults, legal modes parse, invalid values fail, runtime snapshots contain all research fields, and the CUDA hook is not taken when disabled.
2. Run the focused tests red.
3. Add research-only fields with safe defaults, for example an enable flag, method (`production_current`, `ordinary_subtraction`, `scc`, `robust_ddc`, `robust_ddc_rb`), support minimum, range-band size, and robust phase threshold. Parse, validate, and report them in the existing runtime snapshot/text diagnostics. Do not change `csi_cancellation_mode` defaults.
4. In the shared `clutter_cancel_38_paper_1_cuda` entry point, after final F1/F2 are available and after the existing P38/range phase preparation, conditionally download the read-only F1/F2 arrays only for the research branch, call the host adapter, copy only the adapter result into the existing CSI output device buffer, and preserve the existing downstream CFAR path. Emit one compact per-run/beam adapter diagnostic CSV row with method, status, support, excluded count, Gamma summary, source and truth-blind metadata. Fail closed on `NOT_EVALUABLE` rather than silently using Current.
5. Add a small production tap/audit comment and test coverage for both non-fusion and fusion `processOnePeriod` call paths through the shared entry point. Do not duplicate a second calibration implementation in either path.
6. Run focused tests green and build the affected CUDA targets in the existing Release build directory; do not mix toolchains or install dependencies.

**Acceptance:** With research disabled, the exact existing CUDA branch is used. With research enabled, the adapter is placed after final F1/F2 formation and before CFAR, output ownership is explicit, and the runtime artifact can prove which method/status was used.

## Task 4: Build the delay production pilot runner and paired branch contract

**Files:** new `configs/research/production_hierarchical_calibration_delay_pilot.json`, new `scripts/run_production_hierarchical_calibration_pilot.py`, new `scripts/analyze_production_hierarchical_calibration_pilot.py` (or a clearly named shared analyzer module), `tests/test_production_hierarchical_calibration_pilot.py`, and any minimal test fixture/config files.

1. Write failing Python contract tests first. Cover method IDs `C0/C1/C2/C3/C4/P1/P2/PK/PKR`, shared input identity, Mode-A OFF-only and Mode-B truth-blind input declarations, required delay sweep/seed/velocity/SNR selection, `NOT_EVALUABLE` support handling, compact evidence file allow-list, dirty-source refusal, and four allowed decision labels only.
2. Run the focused tests red.
3. Implement the runner using the existing Stage2 scenario/packet generator and production `GMTI_pipe_core`/TrackManager path. Reuse existing packet fusion and fractional-delay rewrite utilities; do not create a simplified tracker or synthetic replacement for production. Generate paired `OFF=C+N`, `ON=S+C+N`, and `TO=S` inputs with shared seed/background/target/production settings.
4. Configure each branch through explicit XML overrides: C0 leaves the research flag off; C1–C4 select only their research method; P1/PK apply raw channel-2 fractional delay correction before a Current branch; P2/PKR apply the same input correction before robust DDC-RB. P1 estimates only from target-free OFF phase-vs-fast-frequency data; PK/PKR declare known error as evaluator-only.
5. Run the same GO-CFAR, clustering, localization, TrackManager, and PIPE settings for all branches. Keep full per-branch output outside Git and collect compact summaries from the existing CSI/adapter, CFAR geometry, detection, TrackManager association/state/payload, and target-truth evaluator files.
6. Emit the frozen manifest/contract and compact evidence rows. Reject target truth in estimator input paths, missing valid-CUT denominators, missing causal triplets, or dirty source identity as `NOT_EVALUABLE`/failed evidence, not as a successful Current fallback.
7. Add `--skip-cuda`/contract mode for fast tests, and explicit pilot/formal selection so a first targeted run is not the full Cartesian matrix. Record `nvidia-smi`, disk, build, input/config hashes, command, return codes, and output paths.
8. Run focused tests green and run the runner in contract/skip mode. Do not claim production algorithm results from skip mode.

**Acceptance:** The runner can produce auditable branch manifests with no truth leakage, identical downstream configuration, and explicit layer/status separation. It is ready for a targeted CUDA run without changing the production default.

## Task 5: Analyze the pilot, write the production tap audit and delay report

**Files:** new `docs/AI_CSI_41_Production复校准接入点与信息边界.md`, new `docs/AI_CSI_42_Production层次化校准Delay对照实验.md`, analyzer/report code from Task 4, and tracked compact evidence files generated only from a clean formal run.

1. Add failing analyzer tests for A0/PK/PKR residual closure, empirical cell-Pfa denominator handling, OFF/ON/TO causal target metrics, separate cluster/protocol/track layers, TrackManager ID-switch classification or `NOT_IDENTIFIABLE_FROM_CURRENT_DEBUG`, and the four allowed decision labels.
2. Run the analyzer tests red.
3. Document the production audit: F1/F2 dimensions and axes, phase/conjugation convention, P38 and range-correction location, Current minimum-magnitude behavior, Gamma meaning, and the fact that DDC is residual equivalent calibration rather than coarse-alignment replacement.
4. Implement the analyzer and report to answer the attachment’s 14 delay-stage questions. Preserve A0, perform PK/PKR closure before assigning an estimator class, and return `NEED_MORE_SINGLE_ERROR_PRODUCTION_EVIDENCE` for missing/insufficient evidence. Never use ON power alone as target protection.
5. Run only the targeted CUDA pilot first (`0,+/-2` ns for path check, then representative `+/-4` ns with at least three seeds and two velocities when the device window permits). Check output correctness before timing claims. If GPU is unavailable, save the exact pending command and report the experiment as pending while completing all non-GPU validation.
6. On a clean tracked worktree and frozen commit, generate the compact six-file evidence directory; inspect that no raw artifacts or forbidden files are present. Keep experiment outputs independent and do not modify runner/config during the formal run.

**Acceptance:** The report contains a defensible single-error delay decision or explicitly returns `NEED_MORE_SINGLE_ERROR_PRODUCTION_EVIDENCE`; it never promotes the historical mechanism result or an unmeasured downstream metric.

## Task 6: Whole-branch verification, focused review, commit, and push

**Files:** no new scope; only repairs required by review or verification findings.

1. Run the full relevant Python suite, CMake Release build, CTest with failure output, changed-script `py_compile`, JSON validation, and `git diff --check`.
2. Run the production adapter/selftests and the strongest available end-to-end representative chain. Capture actual commands, return codes, logs, and evidence paths. Distinguish “checked but not run”, “run with output”, and “GPU pending/blocked”.
3. Inspect the diff for user-scope compliance, accidental raw output staging, default behavior changes, target-truth leakage, and stale decision labels. Use the SDD final whole-branch reviewer and fix findings through implementer fix rounds only.
4. Stage only reviewed tracked code/docs/config/tests/evidence. Do not stage unrelated `outputs/` files or existing user changes. Create normal commits with meaningful messages and push to `origin/codex/unknown-system-error-next-stage` through isolated non-interactive SSH. Never force-push or rewrite history.
5. Verify the remote branch SHA and final clean tracked status. Report the exact verification commands and any limitations before marking the goal complete.

**Acceptance:** The branch is build/test/audit complete to the extent allowed by available CUDA access, the remote contains the reviewed commits, and every unverified or pending requirement is explicitly disclosed.

