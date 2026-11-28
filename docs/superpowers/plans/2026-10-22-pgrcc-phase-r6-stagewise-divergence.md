# PGRCC Phase R6 Stagewise Divergence Investigation Plan

> **历史执行计划快照（2026-10-22）。** 本文的任务范围与复选状态仅描述该计划形成时的阶段任务，不代表当前仍待执行或授权重启；当前研究主题与优先任务以[研究总纲](../../research/RESEARCH_MASTER_PLAN.md)和[研究决策日志](../../research/RESEARCH_DECISION_LOG.md)为准。

> **For agentic workers:** Execute this plan natively in the current worktree; the user supplied the execution scope and acceptance gates in the attached R6 specification.

**Goal:** Determine whether the seed-101 A0/C downstream divergence is reproduced under byte-verified inputs and one immutable Release executable, then locate the earliest numerical or discrete-decision divergence.

**Architecture:** First recover the registered seed-101 Stage2 and corrected-C inputs into a new isolated output root and require exact hashes from R3 records. Reuse existing production CSI-map and CFAR diagnostic taps where possible; run only the A0/A0 control and A0/C OFF/ON/TO roles with one newly identified executable. Analyze maps, CFAR margins/hit indices, detections, truth associations, and TrackManager outputs into the five requested reports.

**Tech Stack:** Existing C++/CUDA Release `GMTI_pipe_core`, `simulate_stage2_statistical`, Python experiment/evaluation scripts, saved XML/JSON/CSV manifests, SHA-256.

**Spec:** `/home/shy/.codex/attachments/90696261-17fe-46bc-b2a1-12b4b46b74e1/pasted-text-1.txt`

## Global Constraints

- Keep seed 101, three periods, the registered target/SNR/geometry/PRF, A0 0 ns, and C +4 ns known correction.
- Do not expand seeds, train AI, execute the full baseline matrix, or change Production Current algorithm behavior.
- A regenerated input is accepted only if its SHA-256 exactly matches the registered historical input; otherwise stop the historical reproduction and do not substitute new data.
- Use the same immutable Release executable for the A0/A0 control and all A0/C roles; each run gets a clean independent output and TrackManager state.
- Use a real CUDA device for Production evidence; CPU/simulation-only results cannot satisfy the Production gates.
- Preserve paired inputs, runtime XML, source/binary identity, logs, maps, threshold/mask data, detection and tracking records through audit completion.

## Review Focus

- Missing original Stage2 binaries: verify each regenerated role/period hash against R3 branch manifests before Production.
- Historical executable/source drift: distinguish historical identity evidence from the newly frozen R6 executable; compare A0/C only within R6's fixed executable.
- Instrumentation perturbation: compare algorithm outputs with diagnostic dumping disabled/enabled while changing no scientific parameter.
- CSI tap semantics: map every saved complex/power field to its actual production stage before computing closure or divergence.
- CFAR discrete sensitivity: use full valid-CUT maps, exact thresholds, signed margins, hit indices, and branch-specific/union counts; do not infer indices from hashes.

---

### Task 1: Historical input and executable identity gate

**Files:** Read the R3 case/branch/run manifests, cleanup ledger, scenario JSON/XML, and Stage2 report under `outputs/phase_r_minimal_cuda_causal_pilot_20261021/` and `docs/research/phase_r/evidence/r3_minimal_cuda_causal_pilot_20261021/`; create regenerated inputs only under the new `outputs/phase_r6_stagewise_divergence_20261022/` root.

- [x] Reconcile A0/C run order, historical source dirty state, pre/post executable hashes, timestamps, runtime XML, and registered Stage2 hashes. R3 binary SHA was absent, so historical binary identity remains unverifiable; R6 A0/C identity is fixed and documented.
- [x] Regenerate only the seed-101 A0/A1 OFF/ON/TO data needed for the experiment into `outputs/phase_r6_stagewise_divergence_20261022/` using the registered Stage2 configuration and binary identity.
- [x] Require every regenerated Stage2 period SHA-256 to equal its R3 manifest value; all 27/27 A0/A1/C period hashes matched before Production.
- [x] Recreate C corrected inputs from the hash-matched A1 inputs with the registered known-correction operator; all corrected-input hashes matched R3 records.

### Task 2: Frozen Production executable and diagnostics

**Files:** Prefer existing taps in `src/csi_metrics_tap.cpp`, `src/processOnePeriod.cpp`, and runtime XML. Modify Production source only if a required stage is not observable with existing taps; any added diagnostic path must be opt-in and pass a diagnostic-off/on output parity check.

- [x] Freeze one Release `GMTI_pipe_core`, record commit, source/binary identity and SHA-256.
- [x] Verify existing CSI tap emits full 130×4096 complex/power maps and `GMTI_CFAR_DUMP_BEAM=50` emits detector-input, threshold, hit, and complex maps for each valid period/branch.
- [x] Compare generated XMLs; A0/C pairs differ only in input/result/track-debug paths, with processing parameters identical.

### Task 3: Minimal CUDA Production control and paired runs

**Files:** New isolated run root `outputs/phase_r6_stagewise_divergence_20261022/`; no legacy output overwrite.

- [x] Run A0/A0 twice on the same hash-verified A0_ON input with identical settings and independent clean TrackManager/output state; full CSI, CFAR threshold/hit, GMTI, normalized detection and TrackManager outputs are repeatable.
- [x] Run paired diagnostic-off/on A0_ON replays. Diagnostics-off has no CSI dump by design; production GMTI, CFAR summary, detection scientific fields and TrackManager outputs match.
- [x] Run only A0 and C OFF/ON/TO with the same frozen executable and registered three-period input pairing.
- [x] Gate Production runs on CUDA availability, runtime XML identity, finite/complete CSI maps, CFAR geometry, target truth coverage and physical input closure. CFAR geometry is valid, but 471,017 nonpositive thresholds make margin/Pfa interpretation partially not evaluable.

### Task 4: Stagewise analysis and requested reports

**Files:**
- Create `docs/research/phase_r/R6_execution_report.md`
- Create `docs/research/phase_r/R6_stagewise_divergence.md`
- Create `docs/research/phase_r/R6_cfar_threshold_sensitivity.md`
- Create `docs/research/phase_r/R6_task_error_attribution.md`
- Create `docs/research/phase_r/R6_next_stage_decision.md`

- [x] Compare Stage2, fused F1/F2, geometry/phase, CSI input/output, detector input, threshold maps and CFAR hit masks with shape, finite count, max absolute error, relative complex RMS, phase/power statistics and provenance.
- [x] Compute exact A0/C shared-only/union hit indices, signed threshold-margin distributions and threshold-near flip concentration for each valid branch/period; nonpositive thresholds are marked not evaluable for dB margins.
- [x] Trace hit changes through final detections, truth matching and TrackManager payloads to task RMSE. Exact per-candidate cluster-ID lineage was not persisted, so that sub-mechanism is explicitly `INCONCLUSIVE`.
- [x] Report historical and R6 results separately; distinguish numeric residual, repeatability, threshold sensitivity and processing-state mismatch; preserve `INCONCLUSIVE` where attribution remains unresolved.
- [x] Preserve frozen runtime XML/run manifests/logs locally, program/input hashes, compact map/margin/indices evidence, known failures and cleanup ledger; exact wrapper argv was not serialized and the runtime replay form is documented. Stop after the five reports and decision are verified.
