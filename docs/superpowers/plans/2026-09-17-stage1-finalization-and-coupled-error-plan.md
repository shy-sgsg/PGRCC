# Stage-1 Channel-Delay Finalization and Stage-2A Coupled-Error Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use `superpowers:executing-plans` for the implementation, inline in the current session. Use `superpowers:subagent-driven-development` only when the user explicitly requests delegation; this task does not require subagents. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 在生产等效 `4ch protocol IQ → F1/F2 → two-channel CSI → production GO-CFAR → clustering/positioning → TrackManager/PIPE` 链路上，完成 channel-delay Paper-1 的 Stage-1.1 最终收口、clean Formal-v2 和三种 Stage-2A coupled-error deterministic baseline；保留可复现证据，停止在 Stage-2B NN 实现之前。

**Architecture:** 先以不可变 Formal-v1 sidecar audit 建立历史边界；再用纯 Python inverse-closure 和传统 estimator baseline 解释 A2/A0；在真实 CUDA GO-CFAR kernel 与真实 `TrackManager::updateRawDetections` 增加只读诊断 tap；用 physical scene block 作为 hierarchical statistics 单位；完成 materiality/literature/hardware protocol 后提交 clean frozen commit，再运行独立 Formal-v2。Stage-2A 只有 A–L gate 全部通过才启动，使用三种组合、M0/M1/M2/MK、sensor-prior decomposition、局部 observability/SVD 和 recovery-gap 分析。

**Tech Stack:** 现有 C++/CUDA production pipeline、CMake Release/CTest、Python 3/NumPy/pytest、CSV/JSON/Markdown、真实 CUDA 设备和现有 simulator/TrackManager runner。不新增第三方依赖，不启用 `ai_training`、Router 或 native four-channel STAP/JDL。

**Spec:** [2026-09-17-stage1-finalization-and-coupled-error-design.md](../specs/2026-09-17-stage1-finalization-and-coupled-error-design.md)

## Global Constraints

- 只在当前隔离工作树 `/home/shy/AIR/SAR+AI/project/PGRCC/.worktrees/unknown-system-error-next-stage` 工作；先保护既有 untracked `outputs/`，不批量 `git add outputs/`。
- `outputs/formal_evidence/stage1_delay/` 和 `outputs/formal_delay_stage1_20260916/` 是 Formal-v1 历史资产，禁止覆盖、重写或删除；v2 使用独立目录。
- 生产算法行为不能因诊断 tap 改变：不调 CFAR 参数、Track gate、confirmation、assignment、cluster、PIPE filtering。
- 科学 estimator 只接收 `F1=(C1+C3)/2`、`F2=(C2+C4)/2`；raw 4ch 只用于 packet/layout/fusion audit。A3 必须保持 `truth_used_in_estimator=false`，估计失败不得静默退化为 A1。
- 每个新增 Python/C++/CUDA 行为按 TDD：先写能证明缺失行为的 failing test，运行并记录失败，再做最小实现，运行定向测试后提交。
- 每项完成以独立普通 commit 结束；commit 前执行适用的 focused test、`git diff --check` 和 staged diff review。无关格式化、重命名、依赖、抽象和 speculative hardening 不进入提交。
- 每个正式运行 manifest 记录命令、配置/template/source hash、commit、dirty 状态、输入身份/hash、GPU 状态、输出路径、退出码和 NOT_EVALUABLE reason。退出码 0 或文件存在不单独证明算法结论。
- Stage-1.1 A–L 通过前不启动 Stage-2A；Stage-2A 期间不写 NN。任何 AI core gate 失败写 `NO_GO_PHYSICS_AI`。
- 子代理不启动；所有工作由当前主会话 inline 完成，避免额外额度消耗。

---

## File map and source boundaries

### Existing files to inspect or modify

- `src/gpu/gpu_kernels.cu`: `cfar_detect_kernel` 和 `GMTIProcessor::dpca_cfar2_fast_cuda`；增加 valid-CUT diagnostic data only。
- `include/GMTIProcessor.hpp`: 若 CUDA wrapper 需要结构化 tap output，在现有函数声明末尾增加兼容的 optional diagnostic pointer；不改变默认调用语义。
- `src/processOnePeriod.cpp`: dynamic/full/union/split branch 传递 branch identity、写 CFAR geometry records；保留当前 hit/cluster/target path。
- `src/TrackManager.cpp`: 扩展 `association_candidates.csv`/新增 versioned audit rows；复用 `computeAssocScoreEN`、assignment、events、payload 和 `TrackManager::updateRawDetections`。
- `scripts/estimate_channel_delay.py`: 复用并必要时扩展 `rewrite_float32_protocol_delay`、`correct_fractional_delay_frequency_domain`，不改变现有 positive-delay convention。
- `scripts/run_track_manager_e2e.py`: 复用 `_protocol_layout_from_scenario`、F1/F2 loading、truth/provenance；不创建平行 runner。
- `scripts/run_delay_stage1_formal.py`: 复用 A0/A1 scene generation、A2/A3 input preparation、manifest/status；增加 v2 provenance/CFAR source integration only after tests。
- `scripts/analyze_delay_stage1_formal.py`: 保持现有 v1 compact schema；必要时增加 v2 tap fields without rewriting v1 output。
- `scripts/audit_delay_track_id_switch.py`: 保持 evidence-first join，扩展 production audit columns/classification。
- `configs/research/channel_delay_stage1_formal.json`: 只在测试证明 v2 必须改变默认 formal matrix 时修改；v1 manifest/output 不回写。v2 命令显式传全九个 delay。
- `tests/test_channel_delay_correction.py`, `tests/test_delay_stage1_contract.py`, `tests/test_delay_track_id_switch.py`, `tests/test_pfa_audit.py`, `tests/test_track_manager_protocol_audit.py`: 扩展已有契约，不另建重复的历史 contract。
- `CMakeLists.txt`: 仅为真实 C++ geometry selftest 注册必要 target/test。

### New files

- `docs/AI_CSI_37_ChannelDelay_Finalization_Audit.md`
- `docs/AI_CSI_38_ChannelDelay_Final_Formal_Conclusion.md`
- `docs/literature/ChannelDelay_Calibration_Novelty_Audit.md`
- `docs/experiments/ChannelDelay_Hardware_Validation_Protocol.md`
- `scripts/audit_fractional_delay_inverse_closure.py`
- `tests/test_fractional_delay_inverse_closure.py`
- `scripts/analyze_delay_stage1_hierarchical.py`
- `tests/test_delay_stage1_hierarchical_stats.py`
- `configs/research/channel_delay_stage1_materiality.json`
- one or two stronger baseline helpers/tests in `scripts/delay_stage1_core.py` and `tests/test_delay_stage1_core.py`
- `include/cfar_geometry.hpp`, `tests/cfar_geometry_selftest.cpp`, and Python tap schema tests; the pure geometry helper is the smallest testable boundary for the kernel’s exact denominator rules.
- `configs/research/coupled_error_stage2a.json`
- `scripts/run_coupled_error_stage2a.py`
- `tests/test_coupled_error_stage2a.py`
- `tests/test_physics_ai_gate.py`
- `configs/research/physics_ai_gate.json`
- `docs/AI_CSI_40_Coupled_Error_Stage2A_Report.md`
- `docs/AI_CSI_39_PhysicsAI_CoupledCalibration_Design.md` only when Stage-2A evidence supports a design-only artifact; never add NN implementation in this plan

### Retained output boundaries

- Formal-v2 raw root: `outputs/formal_delay_stage1_v2_<YYYYMMDD>/`.
- Formal-v2 compact evidence: `outputs/formal_evidence/stage1_delay_v2/`.
- Closure output: `outputs/fractional_delay_inverse_closure_<date>/` with JSON/CSV summary and only representative plots/arrays if required by a failure.
- Stage-2A compact output: `outputs/coupled_error_stage2a_<date>/` with manifest, matrices, summaries, gate result and high-value diagnostics; clean up redundant raw arrays only after manifests are verified.

## Phase 0 — baseline, disk/GPU, and historical freeze

### Task 0: Establish a bounded baseline before modifications

**Purpose:** satisfy repository recovery and cleanup safety requirements without changing user data.

- [ ] Run `git status --short --untracked-files=all` in the isolated worktree and save a compact status count; separately record `git status --short --untracked-files=no` for tracked cleanliness.
- [ ] Inspect `du -sh . outputs`, target output roots, and `/tmp` project temp directories; do not delete anything until each target is identified as a disposable derivative or a retained evidence asset.
- [ ] Run `nvidia-smi` and save GPU model/driver/power/P-state/temperature/load; if GPU is unavailable, record the exact failure and continue with CPU-side work without claiming CUDA evidence.
- [ ] Read the immutable v1 manifest and compute its current SHA-256; compare it with the recorded `30e0cc4b...` value before any new output is created.
- [ ] Run focused baseline tests `python3 -m pytest -q tests/test_channel_delay_correction.py tests/test_delay_stage1_contract.py tests/test_delay_track_id_switch.py tests/test_pfa_audit.py` and record actual counts; do not rerun a long formal experiment at this point.
- [ ] Review only current relevant diffs; no commit is created for an unchanged baseline.

**Review gate:** if the v1 manifest hash changed, the worktree contains overlapping tracked changes, or a proposed cleanup target cannot be proven disposable, stop that cleanup and preserve/report it before continuing.

## Stage-1.1 — Channel-delay Paper-1 finalization

### Task 1: Freeze Formal-v1 and write the finalization audit

**Files:** `docs/AI_CSI_37_ChannelDelay_Finalization_Audit.md`, `tests/test_stage1_finalization_audit.py`.

- [ ] Write `tests/test_stage1_finalization_audit.py` first. It must fail when the audit document is absent, require the v1 manifest path, assert that the document labels v1 as development formal rather than final frozen-paper formal, and assert that the recorded source hash/commit span and `ai_training/router/STAP` flags appear.
- [ ] Run `python3 -m pytest -q tests/test_stage1_finalization_audit.py` and observe the expected missing-document failure.
- [ ] Implement the audit document with a table of retained v1 evidence, unusable/future evidence, dirty provenance, commit span, source manifest hash, output row counts, known missing valid-CUT denominator and ID-switch attribution limits. State explicitly that the v1 files are not overwritten.
- [ ] Re-run the focused test and `git diff --check`; independently recompute the v1 manifest SHA-256 and confirm the test did not modify it.
- [ ] Commit only the audit and its test: `git commit -m "docs: audit immutable channel delay formal v1"`.

**Review gate:** v1 manifest bytes and v1 compact CSVs are unchanged; the audit cannot call v1 final frozen-paper evidence.

### Task 2: Diagnose and close fractional-delay inverse behavior before changing formal conclusions

**Files:** `scripts/audit_fractional_delay_inverse_closure.py`, `tests/test_fractional_delay_inverse_closure.py`; only if a failing closure test proves a bug, the minimal affected code in `scripts/estimate_channel_delay.py` and its existing tests.

**Public pure interfaces to implement:**

```python
inject_fractional_delay(x: np.ndarray, delay_ns: float, fs_hz: float, *, mode: str, zero_pad: int = 0) -> np.ndarray
correct_fractional_delay_exact(x: np.ndarray, delay_ns: float, fs_hz: float, *, mode: str, zero_pad: int = 0) -> np.ndarray
closure_metrics(reference: np.ndarray, recovered: np.ndarray, *, boundary_mask: np.ndarray | None = None) -> dict[str, float | int | str | None]
run_closure_matrix(*, delays_ns: Sequence[float], seed: int, output_dir: Path, production_input: Path | None = None) -> dict[str, object]
```

- [ ] Write failing tests for C0 deterministic tone/impulse, positive/negative integer and fractional delay, FFT sign, amplitude/energy, circular-vs-zero-padded boundary masks, and packet/pulse boundary labels.
- [ ] Add tests for C1 known LFM, C2 multi-pulse target-free clutter, C3 four-channel protocol with F1/F2 fusion, and partial-channel correction. Assert fields: complex NMSE, amplitude error, phase RMS, group-delay residual, edge-sample loss, energy ratio and coherence.
- [ ] Run `python3 -m pytest -q tests/test_fractional_delay_inverse_closure.py`; confirm import/API failures before implementation.
- [ ] Implement deterministic waveform generators and the exact forward/inverse transformations using the repository’s positive-delay convention. Preserve packet boundaries; never flatten packets when the production model is per-packet.
- [ ] Implement C0–C3 output rows with `interior`, `pulse_boundary`, `packet_boundary`, `finite`, `fallback_reason`, transform mode, zero-padding, correction location, channel indices and fusion formula. Make all tolerance values explicit in the test/config, not result-dependent.
- [ ] Implement C4 as a read-only adapter for an existing production CSI/raw diagnostic fixture. If no retained input contains the required production stage, emit `NOT_EVALUABLE` with the missing path/schema reason; do not fabricate C4 or call a synthetic row production evidence.
- [ ] Run the focused tests and the CLI on a small synthetic matrix: `python3 scripts/audit_fractional_delay_inverse_closure.py --output-dir outputs/fractional_delay_inverse_closure_<date> --delays-ns 0,1,-1,2,-2,4,-4,8,-8`.
- [ ] Review whether any interior failure is a sign/layout/normalization bug. If yes, add a narrowly scoped failing regression test, make one minimal fix, rerun the full closure matrix, and commit before any formal rerun. If only edge residuals remain, record the finite-window/non-reversible boundary explanation and do not tune detector/tracker.
- [ ] Commit closure code/tests/output manifest: `git commit -m "test: verify fractional delay inverse closure"`.

**Review gate:** A2/A0 cannot be described as a material algorithm gap until C0–C4 are either closed within predeclared tolerance or explicitly classified as boundary/non-evaluable with evidence.

### Task 3: Export the real production GO-CFAR valid-CUT denominator

**Files:** `include/cfar_geometry.hpp` (if confirmed as the smallest pure boundary), `include/GMTIProcessor.hpp`, `src/gpu/gpu_kernels.cu`, `src/processOnePeriod.cpp`, `tests/cfar_geometry_selftest.cpp`, `tests/test_cfar_geometry_diagnostic.py`, `tests/test_pfa_audit.py`.

- [ ] Write failing Python tests for the required row schema, split/dynamic branch separation, zero-denominator `NOT_EVALUABLE`, and the four distinct OFF waterfall layers. Run them before adding the tap.
- [ ] Write failing C++ geometry selftest cases for rectangular non-circular edges, circular Doppler rows, guard/background radius, excluded rows, in-band/out-of-band cut masks, and union de-duplication. Register only this focused selftest in `CMakeLists.txt`.
- [ ] Define `CfarGeometryDiagnostics` with `schema_version`, period/beam/branch identity, `total_cells`, `excluded_cells`, `valid_cut_count`, `threshold_test_count`, `hit_cut_count`, stable hit hash, `configured_pfa`, `cfar_type`, `alpha`, training geometry and guard geometry. Define valid CUT as the exact production-eligible CUT after edge/circular, exclusion and cut-band rules; define threshold-tested cells separately.
- [ ] Add an optional diagnostic output to `dpca_cfar2_fast_cuda` at the declaration in `include/GMTIProcessor.hpp` and implementation in `src/gpu/gpu_kernels.cu`, preserving all existing default calls. Compute counts from the same `H/W/g/b/R`, circularity, exclusions and band mode used by `cfar_detect_kernel`; use the existing hit map/count rather than a reconstructed detector.
- [ ] In `src/processOnePeriod.cpp`, pass unique branch labels for dynamic/full/union/split in-band/split out-of-band, write one record per period/beam/branch under the existing runtime diagnostic directory, and preserve existing `[CFAR][SUMMARY]` behavior. Do not make diagnostics a new detector input.
- [ ] Add Python parsing/aggregation that calculates `hit_cut_count / valid_cut_count` only when the denominator is positive; otherwise writes `NOT_EVALUABLE`. Keep false cluster count, protocol detection row count and false track count in separate fields and sources.
- [ ] Build the focused C++ target and run `ctest --test-dir build -R 'cfar_geometry_selftest' --output-on-failure`; run `python3 -m pytest -q tests/test_cfar_geometry_diagnostic.py tests/test_pfa_audit.py`.
- [ ] Run one representative production diagnostic case (CPU only if CUDA is unavailable, clearly labeled) and confirm the tap is present and detector decisions/counts otherwise match the pre-tap control. Record `nvidia-smi` before any CUDA run.
- [ ] Commit: `git commit -m "feat: export production cfar valid cut diagnostics"`.

**Review gate:** no formal OFF cell Pfa claim can proceed unless the actual production branch exports a positive denominator or explicitly reports `NOT_EVALUABLE`.

### Task 4: Add read-only production TrackManager association instrumentation

**Files:** `src/TrackManager.cpp`, `include/TrackManager.hpp` only if a public diagnostic type is required, `tests/test_track_manager_association_audit.py`, `tests/test_track_manager_protocol_audit.py`, and existing C++ TrackManager selftests.

- [ ] Write failing Python schema tests requiring frame/result, track state before/after, measurement/detection index, candidate count/IDs, existing innovation fields, distance/gate, rank/cost/dummy cost/reject reason, lifecycle event and explicit unavailable markers.
- [ ] Identify the current association loop at `TrackManager::updateRawDetections`, the existing `AssocScore`, assignment vector and `appendTrackAssociationLog`/`appendTrackEvent` calls. Reuse these values; do not recompute association in Python.
- [ ] Add a versioned read-only audit CSV or extend the existing association CSV without removing old columns. For each candidate write candidate rank, candidate IDs/count where the current loop has them, `euclidean_dist_m`, Mahalanobis distance, gate thresholds, speed/heading innovations already computed, assignment/dummy result and reject reason. Use empty plus `unavailable_in_production_state` for angle/velocity fields not stored by production.
- [ ] Record lifecycle transitions from the existing event/state path: new, confirmed, coasted, lost/delete, reacquired when the production state actually exposes it. Do not invent merge/split; emit unavailable when no production field exists.
- [ ] Run `python3 -m pytest -q tests/test_track_manager_association_audit.py tests/test_track_manager_protocol_audit.py` and the relevant CTest/TrackManager selftest. Use a small existing TrackManager debug fixture to compare output count and protocol payloads before/after.
- [ ] Commit: `git commit -m "feat: audit production track associations"`.

**Review gate:** production output rows and state transitions are byte/decision-equivalent except for additive diagnostic files; no new gate or lifecycle rule appears in the diff.

### Task 5: Make ID-switch attribution substantive or explicitly unclassifiable

**Files:** `scripts/audit_delay_track_id_switch.py`, `tests/test_delay_track_id_switch.py`, any narrowly required schema tests.

- [ ] Add failing fixtures containing enriched candidate competition, gate-boundary, lifecycle transition, position/velocity jump and missing-production-field cases. Assert that missing fields classify as `unclassifiable_production_fields` or `other` with a reason and missing-field list, not an unexplained all-`other` result.
- [ ] Extend `_CLASSIFICATIONS`, `AUDIT_COLUMNS`, `_association_rows` and `classify_switch` to consume the versioned production audit rows while preserving evidence-first joins and established truth matching. Candidate count/rank must come from production rows; truth may only label the switch, never create candidates.
- [ ] Add classification-count and missing-field-count summaries to `write_audit`/caller output. Keep merge/split explicitly unavailable when not exported.
- [ ] Run focused tests and re-analyze the retained v1 compact audit read-only. Do not rewrite v1 output; save any comparison as a new audit artifact.
- [ ] Commit: `git commit -m "fix: attribute delay stage1 track switches"`.

**Review gate:** the result either has evidence-backed mechanisms or states exactly which production fields prevent attribution; a 100% `other` count caused only by missing parser fields is not accepted.

### Task 6: Add hierarchical scene-block statistics

**Files:** `scripts/analyze_delay_stage1_hierarchical.py`, `tests/test_delay_stage1_hierarchical_stats.py`.

**Pure interfaces:**

```python
make_physical_block_id(row: Mapping[str, object]) -> str
paired_delay_effects(rows: Sequence[Mapping[str, object]], *, comparisons: Sequence[str], delays_ns: Sequence[float]) -> list[dict[str, object]]
block_bootstrap_effect(rows: Sequence[Mapping[str, object]], *, block_key: str, treatment_key: str, value_key: str, comparison: str, trials: int, seed: int) -> dict[str, object]
binary_cluster_paired(rows: Sequence[Mapping[str, object]], *, block_key: str, treatment_key: str, current_key: str, comparison_key: str) -> dict[str, object]
write_hierarchical_evidence(input_paths: Sequence[Path], output_dir: Path, *, seed: int, trials: int) -> dict[str, object]
```

- [ ] Write failing tests proving two delay rows from one physical scene share one block, missing/duplicate block-delay pairs are reported, binary tests resample blocks, zero recoverable space becomes `NOT_EVALUABLE`, and continuous effects include direction/CI/effective block count.
- [ ] Run the focused test and observe missing module/API failure.
- [ ] Implement readers for current `A0_A1_A2_A3_summary.csv`, track/ID/OFF rows and v2 tap rows. Build block IDs from seed, target velocity, SNR, working point and texture/geometry identity; never use delay as a physical block key.
- [ ] Emit per-delay paired effects for `±1/±2/±4/±8 ns` for A1→A2 and A1→A3, block bootstrap 95% CI, delay×SNR/velocity/working-point trends, binary cluster-aware results, missingness and provenance. Tag the old 135-case McNemar as `Formal-v1 exploratory`.
- [ ] Run on synthetic rows first, then read-only on retained v1 compact evidence into a new output directory. Compare row counts to the known 15-block registry and record whether v1 has seven or nine E2E delays in each layer.
- [ ] Commit: `git commit -m "feat: add hierarchical channel delay statistics"`.

**Review gate:** no row treats repeated delays within the same physical scene as independent scene blocks; all final effects expose the block count and layer coverage.

### Task 7: Freeze exploratory materiality before Formal-v2

**Files:** `configs/research/channel_delay_stage1_materiality.json`, `tests/test_channel_delay_materiality.py`.

- [ ] Write failing schema tests requiring every requested metric: delay RMSE, target Pd, track Pd, position RMSE, angle RMSE, velocity RMSE, false cluster/detection/track, ID switch; each needs unit, direction, threshold, scope, source classification and status.
- [ ] Run the test before creating the config.
- [ ] Add thresholds only when supported by a reachable source. Use `system_requirement`, `engineering_judgment`, `historical_production_variability`, `literature-derived` or `not_available`; unsupported thresholds carry `exploratory_pending=true` and cannot be used as a final pass/fail claim.
- [ ] Include separate recoverable-space/recovery-ratio interpretation and explicit `NOT_EVALUABLE` rules. Set AI/Router/STAP flags false in the config snapshot.
- [ ] Run JSON validation and focused tests; commit before any Formal-v2 run: `git commit -m "config: preregister channel delay materiality"`.

**Review gate:** materiality file hash is in the future v2 manifest and no threshold is silently chosen after seeing v2 outcomes.

### Task 8: Add one or two stronger traditional baselines

**Files:** `scripts/delay_stage1_core.py`, `tests/test_delay_stage1_core.py`, `scripts/run_delay_stage1_formal.py`/`analyze_delay_stage1_formal.py` only where required to carry rows.

- [ ] Add failing tests for at most two selected methods: generalized phase-slope ML with nuisance intercept/variance weighting and oversampled/sinc cross-correlation with fractional peak refinement. Test positive/negative fractional delays, LFM bandwidth, low SNR fallback and truth-blind input.
- [ ] Run focused tests and observe missing method failures.
- [ ] Implement methods using exactly the same F1/F2 inputs, target-free OFF calibration, sample/pulse support and delay sweep as D1/D2/D3. Truth can appear only in post-hoc bias/RMSE calculation.
- [ ] Extend `delay_method_suite`/MC summaries with method name, estimate, bias, RMSE, 95% CI, fallback reason/rate and runtime. Preserve the existing methods and do not weaken their masks/tolerances.
- [ ] Run `python3 -m pytest -q tests/test_delay_stage1_core.py tests/test_delay_stage1_contract.py` and a small parameter-level MC; retain only summary rows/manifest.
- [ ] Commit: `git commit -m "feat: add traditional channel delay baselines"`.

**Review gate:** all methods see identical F1/F2/OFF/samples/truth-blind/sweep conditions and the comparison table does not promote estimator-only results to Pd/Pfa claims.

### Task 9: Complete the literature novelty audit

**Files:** `docs/literature/ChannelDelay_Calibration_Novelty_Audit.md`.

- [ ] Use web search only for this explicitly requested literature task. Search classic calibration, multichannel SAR, airborne GMTI/STAP and papers from approximately the last 5–10 years; technical claims use primary papers, standards or official platform documentation.
- [ ] Build the required table columns: paper, platform, error type, calibration target/source, target-free status, method, fractional-delay handling, real-data status, downstream Pd/Pfa/tracking evidence, and difference from this work.
- [ ] Record DOI/direct URL and search date for each source; paraphrase rather than reproduce long copyrighted passages.
- [ ] Add a conclusion that Paper-1 must not claim first/novel before the audit; phrase contributions as potential contributions tied to current evidence.
- [ ] Run a Markdown/link/table inspection and `git diff --check`; commit: `git commit -m "docs: audit channel delay calibration novelty"`.

**Review gate:** every novelty statement in the Paper outline is traceable to this table or is marked potential/unverified.

### Task 10: Write the hardware validation protocol without inventing results

**Files:** `docs/experiments/ChannelDelay_Hardware_Validation_Protocol.md`.

- [ ] Describe a testable topology: four RF/IF paths, common clock/trigger, programmable delay emulator, phase/drift source, capture point before fusion, instrument calibration and timestamping.
- [ ] Define truth uncertainty, repeat count, temperature/power/environment controls, positive/negative fractional delays, packet/pulse boundary cases and metrics matching C0–C4 plus downstream CSI/Pd/Pfa/tracking.
- [ ] Define pass/fail and data retention/manifest requirements. State explicitly that current evidence is simulation + production software only and contains no hardware result.
- [ ] Run Markdown review and `git diff --check`; commit: `git commit -m "docs: define channel delay hardware validation"`.

### Task 11: Complete Stage-1.1 reports and acceptance preparation

**Files:** `docs/AI_CSI_36_单一系统误差确定性自校准阶段报告.md`, `docs/papers/Paper1_ChannelDelay_SelfCalibration_Outline.md`, `docs/AI_CSI_38_ChannelDelay_Final_Formal_Conclusion.md`, relevant navigation in `README.md` and `docs/AI_CSI_研究进展.md`.

- [ ] Add the new audit, closure, CFAR denominator, TrackManager attribution, hierarchical stats, materiality, baseline, novelty and hardware protocol to the reading/navigation order.
- [ ] Update the current-stage report and Paper outline only with evidence already produced; preserve historical dates/commit identities and distinguish Formal-v1 exploratory from future v2.
- [ ] Write the final-conclusion template with explicit slots for A2/A0 residual cause, closure status, materiality status, A3 recovery, false-alarm waterfall, ID-switch attribution, limitations and Stage-2A GO/NO-GO. Do not fill an unrun result with prose claiming success.
- [ ] Add a contract test that checks `ai_training=false`, `router_enabled=false`, `native_four_channel_stap=false` are present in all new configs/docs/manifests where required.
- [ ] Run focused documentation/config tests and `git diff --check`; commit: `git commit -m "docs: prepare channel delay finalization reports"`.

**Review gate before Formal-v2:** all Stage-1.1 source/config/runner/analyzer changes are committed; there are no tracked diffs; all focused tests/builds pass; v1 hashes remain unchanged; GPU/device and disk checks are recorded.

### Task 12: Run clean, independent Formal-v2 and freeze evidence

**Files:** `scripts/run_delay_stage1_formal.py`, `scripts/analyze_delay_stage1_formal.py`, provenance tests, and only new output roots.

- [ ] Add failing provenance tests requiring a v2 manifest to record source commit before/after, `dirty=false`, config/template/build hashes, command, resume/attempt, GPU snapshot, input hashes, AI flags, tap paths and separate output roots.
- [ ] Implement the minimal runner/analyzer provenance fields and v2 output routing. Keep Formal-v1 path immutable. The v2 command must explicitly pass `--delay-errors-ns 0,1,-1,2,-2,4,-4,8,-8` and the registered 15 scene blocks; manifest must show actual counts rather than infer them from expected config.
- [ ] Run focused provenance tests, full applicable Python tests, Release build, CTest and `git diff --check`. Commit all source/config/docs before the formal run; record the frozen commit hash.
- [ ] Verify `git status --short --untracked-files=no` is empty, run `nvidia-smi`, check output/disk budget, then run the first representative CUDA case to expose environment/input issues. If it fails, preserve the failure manifest/log, stop, diagnose, fix/commit, and restart from a new v2 root; never resume with a changed source identity.
- [ ] Run the full v2 command from the frozen commit with an independent root. During the run do not modify source/config/template/runner/analyzer. Retain command log, `nvidia-smi`, timing, case manifests, compact tap/track audit and cleanup manifests; delete only verified raw derivatives after compact evidence is written.
- [ ] Run the hierarchical analyzer, OFF waterfall parser, ID-switch auditor and final compact collector into `outputs/formal_evidence/stage1_delay_v2/`. Verify the source commit before/after inside the run manifest is identical and dirty=false; verify v1 files are unchanged by SHA-256.
- [ ] Commit only the compact high-value v2 evidence and final run metadata; do not stage raw binary/scenes or unrelated historical outputs: `git commit -m "evidence: freeze channel delay formal v2"`.

**Review gate J:** source/config/template/runner/analyzer commit used by v2 is immutable and reproducible; v2 output is separate; a failed/interrupted run is not relabeled completed.

### Task 13: Stage-1.1 acceptance A–L and push the completed stage

- [ ] Build an acceptance matrix with rows A–L and columns requirement, exact evidence path, command/exit code, result (`passed`, `NOT_EVALUABLE`, or failed), and limitation.
- [ ] Confirm A2/A0 is explained by closure evidence, valid CUT denominator is real or explicitly NOT_EVALUABLE, OFF waterfall layers remain distinct, ID switch attribution is substantive/unclassifiable with reasons, hierarchy uses scene blocks, materiality was pre-frozen, baseline is fair, novelty/hardware docs exist, v2 is clean/frozen, full checks pass, and AI/Router/STAP are off.
- [ ] Fill `docs/AI_CSI_38_ChannelDelay_Final_Formal_Conclusion.md` with only current v2 numbers and explicit v1/v2 provenance; update `docs/AI_CSI_36...` and Paper outline with the final conclusion and limitations.
- [ ] Run final Stage-1.1 tests/build/CTest/diff checks required by AGENTS.md; review staged diff for untracked data/secrets.
- [ ] Commit the acceptance/conclusion changes: `git commit -m "docs: conclude channel delay stage1"`.
- [ ] Push this completed Stage-1.1 stage to the configured remote branch with `git push origin HEAD` only after confirming the staged commits contain no unreviewed output or user changes.

**Hard gate:** if any A–L row is failed or unverified, do not start Stage-2A; report the exact missing evidence and keep the goal active.

## Stage-2A — Coupled deterministic calibration after Stage-1.1 A–L

### Task 14: Register exactly three coupled combinations and ground-truth matrix

**Files:** `configs/research/coupled_error_stage2a.json`, `tests/test_coupled_error_stage2a.py`.

- [ ] Write failing config tests requiring only C1 delay+inter-pulse drift, C2 delay+servo angle with declared cosine near `-0.92`, and C3 platform velocity+inter-pulse drift; reject unknown combinations and more than two coupled errors per case.
- [ ] Add zero/zero, A-only, B-only, same-sign and opposite-sign values with engineering-plausible ranges, multiple seeds/SNR/working points, explicit source fields and separate truth/report/estimate/residual schema. Keep `ai_training=false`, `router_enabled=false`, `native_four_channel_stap=false`.
- [ ] Validate that each scenario uses the existing 4ch protocol/F1/F2 production input contract and records delay/servo/velocity/yaw source (`radar_observable`, `sensor_prior`, `simulator_truth_upper_bound`, or `unavailable`).
- [ ] Run JSON/test checks and commit: `git commit -m "config: define stage2a coupled error matrix"`.

### Task 15: Implement the Stage-2A runner with M0/M1/M2/MK

**Files:** `scripts/run_coupled_error_stage2a.py`, `tests/test_coupled_error_stage2a.py`, existing production runner helpers only when reused through an explicit adapter.

**Interfaces:**

```python
build_coupled_cases(config: Mapping[str, object]) -> list[dict[str, object]]
run_coupled_case(case: Mapping[str, object], output_root: Path) -> dict[str, object]
run_sequential_baseline(observations: Mapping[str, object], order: tuple[str, str]) -> dict[str, object]
run_joint_deterministic_baseline(observations: Mapping[str, object], config: Mapping[str, object]) -> dict[str, object]
apply_known_error_upper_bound(observations: Mapping[str, object], truth: Mapping[str, object]) -> dict[str, object]
```

- [ ] Write failing tests for case enumeration, truth/report separation, no truth in M1/M2, both M1 orders, M2 finite output/fallback, and MK-only upper-bound labeling.
- [ ] Implement a small adapter that reuses existing scenario generation and production execution; do not duplicate CFAR/TrackManager or create an offline simplified tracker.
- [ ] Implement M0 Current, M1 A→B and B→A traditional sequential estimates, M2 weighted nonlinear LS or MAP without truth prior, and MK known-error correction upper bound. Record estimator residual/confidence/source per parameter.
- [ ] Add smoke fixtures and run focused tests; commit: `git commit -m "feat: run stage2a deterministic coupled baselines"`.

### Task 16: Add sensor-prior decomposition

**Files:** `scripts/run_coupled_error_stage2a.py`, `tests/test_coupled_error_stage2a.py`, output schema docs.

- [ ] Write failing tests for radar-only, sensor-prior-only and sensor-prior+radar residual paths, with the exact relation `x_corrected=x_reported+delta_x_radar` and source labels.
- [ ] Implement sensor prior ingestion as a read-only input with uncertainty/covariance; no truth fallback is allowed for M1/M2. Keep MK truth strictly evaluation-only.
- [ ] Emit parameter-level and system-level source decomposition rows; explicitly mark missing prior or missing radar observability as `NOT_EVALUABLE`.
- [ ] Run focused tests and commit: `git commit -m "feat: decompose stage2a sensor priors"`.

### Task 17: Compute coupled observability and recovery gaps

**Files:** `scripts/run_coupled_error_stage2a.py` or a pure analysis module, `tests/test_coupled_error_observability.py`, `tests/test_coupled_error_recovery_gap.py`.

- [ ] Write failing tests for finite-difference/analytic observation Jacobian, SVD singular values/rank, condition number, parameter-column cosine, Hessian/posterior covariance conditioning, and explicit refusal to make a global-identifiability claim from one operating point.
- [ ] Implement evaluation over multiple working point/SNR/amplitude combinations for C1–C3. Store the design point and local validity region with every rank/condition/cosine result.
- [ ] Write failing tests for `MK-M0`, `M1-M0`, `M2-M0`, `MK-M2` and direction-normalized recovery ratio across parameter error, CSI residual, target transfer, Pd, valid-cut false hit, track Pd, false tracks, ID switches, position/angle/velocity RMSE. Zero denominator or unavailable production denominator must be `NOT_EVALUABLE`.
- [ ] Implement compact CSV/JSON outputs and run synthetic fixtures before any production case. Then run the configured Stage-2A matrix only after Stage-1.1 gate evidence is current.
- [ ] Commit: `git commit -m "feat: analyze coupled observability and recovery"`.

### Task 18: Freeze Physics-AI gate and Stage-2A final report

**Files:** `configs/research/physics_ai_gate.json`, `tests/test_physics_ai_gate.py`, `docs/AI_CSI_40_Coupled_Error_Stage2A_Report.md`, and conditional `docs/AI_CSI_39_PhysicsAI_CoupledCalibration_Design.md`.

- [ ] Write failing gate tests requiring six independent fields: material recoverable space, deterministic repeatable gap, held-out seeds/working points, visible information in F1/F2+sensor prior, known-bug exclusion, and system-metric impact. Any core failure must evaluate to `NO_GO_PHYSICS_AI`.
- [ ] Implement the config with predeclared decision rules and explicit evidence paths. Do not set a gate by post-hoc narrative.
- [ ] Run Stage-2A final analysis, fill the second report with the three combinations, M0/M1/M2/MK, sensor prior, observability limitations, recovery gaps, system metrics and GO/NO-GO. Mark no-hardware/unavailable metrics honestly.
- [ ] If and only if the six gates support a design, add `docs/AI_CSI_39_PhysicsAI_CoupledCalibration_Design.md` covering input/output/residual/confidence, held-out split, fallback and audit; do not add NN code or training data.
- [ ] Run full applicable pytest, Release build, CTest, Python syntax checks and `git diff --check`; review staged diff and output retention.
- [ ] Commit: `git commit -m "docs: conclude stage2a physics ai gate"` and push `git push origin HEAD` after confirming the final branch is complete and safe.

## Verification and handoff checklist

- [ ] `python3 -m pytest -q` passes with only the repository’s known skips and no new unexplained failures.
- [ ] Changed Python files pass `python3 -m py_compile`; changed shell files pass `bash -n`.
- [ ] `cmake --build build -j4` passes in the existing native Release build directory; no alternate CUDA/toolchain build directory is mixed in.
- [ ] `ctest --test-dir build --output-on-failure` passes, including new CFAR geometry/selftest coverage.
- [ ] `git diff --check` passes and staged diffs contain only requested code/docs/config/compact evidence.
- [ ] v1 manifest/compact evidence hashes match the pre-task records; v2 and Stage-2A outputs are separate and have manifests.
- [ ] Every final scientific claim is traceable to a current output row, source/config hash, command, environment and interpretation rule; unrun, unavailable and NOT_EVALUABLE items remain visible.
- [ ] Final response begins with what was completed, lists actual commands/results and evidence paths, states limitations, and provides the shortest reproducible command. Only then mark the active goal complete.
