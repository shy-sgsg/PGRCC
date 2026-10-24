# PGRCC Phase R Research and Verification Plan

> **历史执行计划快照（2026-10-18）。** 本文的任务范围与复选状态仅描述该计划形成时的阶段任务，不代表当前仍待执行或授权重启；当前研究主题与优先任务以[研究总纲](../../research/RESEARCH_MASTER_PLAN.md)和[研究决策日志](../../research/RESEARCH_DECISION_LOG.md)为准。

> **For agentic workers:** Execute the phases in order. Preserve each phase's evidence and mark the checklist only after its acceptance evidence exists.

**Goal:** Identify and verify the dominant GMTI performance bottlenecks that are both present and recoverable in PGRCC, then recommend a falsifiable research direction without presuming AI is needed.

**Architecture:** Audit the production F1/F2 pipeline and historical evidence first; combine that with primary-source literature and a PGRCC-specific physical model; audit evaluation validity before registering a minimal experiment design; run only the smallest experiments that can distinguish remaining hypotheses; finish with an evidence-ranked A/B/C/(D) decision and a reproducible next-stage plan.

**Tech Stack:** C++/CUDA production code, XML configuration, Python simulation/evaluation scripts, Markdown/CSV research records, primary academic sources.

**Spec:** `/home/shy/.codex/attachments/9d684da8-b980-41c3-95ea-5f294d429c46/pasted-text-1.txt`.

## Global Constraints

- Keep Production Current semantics: 4-channel protocol IQ → `(1,3)/(2,4)` fusion to F1/F2 → two-channel CSI → CFAR → clustering/positioning → TrackManager/PIPE.
- Do not treat four physical receivers as four independent production CSI degrees of freedom; native four-channel STAP/JDL remains frozen as Phase-II.
- Keep `ai_training=false`, `router_enabled=false`, and `native_4ch_stap=false` until the stated evidence gates are met.
- Distinguish ideal/no-error, Current+unknown-error, Known-error correction upper bound, and blind estimated correction; never use evaluator truth in a blind estimator.
- Empirical Pfa requires actual valid CUTs and an explicit denominator; configured Pfa is not a measured result.
- Do not start new experiments until the current evaluation path is audited and `05_experiment_design.md` is written; do not expand matrices without a decision-relevant reason.
- Use primary sources for technical literature; label inaccessible full text `FULLTEXT_UNAVAILABLE` and distinguish simulation from measured radar evidence.
- Preserve `.scratch/`, packages, and historical evidence except where the user explicitly authorizes cleanup. On 2026-10-21, the user authorized selective cleanup of the active worktree's `outputs/`: remove verified redundant/reproducible case trees, failed sandbox/pilot outputs, and retain compact manifests, configs, compressed audit records, and high-value Phase R evidence. Do not touch `.scratch/` or the main checkout's separate `outputs/`.
- The user's later GitHub-publication request and automatic phase-push rule supersede the original local-only restriction. Stage and push only reviewed phase files to the configured branch; leave unrelated untracked artifacts out.

## Review Focus

1. Target speed may not equal radial velocity. Verify coordinate/frame, LOS projection, Doppler ambiguity, and estimator semantics from simulator, protocol, and production code before interpreting 6.7/12 m/s.
2. A configured `Pfa=1e-6` is not empirical Pfa. Trace `valid_cut_count` and `hit_cut_count` to production CUT selection and verify nonzero denominators.
3. A row-level `evaluable` status is not end-to-end method evidence. Separate estimator, residual closure, causal target transfer, CFAR/cluster/protocol, track, and ID-switch provenance.
4. F1/F2 methods and raw four-channel/offline STAP references have different information conditions. Report end-to-end capability separately from information-matched algorithm effects.
5. Results across commits, manifests, seeds, and pilot types are not interchangeable. Attribute every numerical claim to its source, input scope, and status.

## Phase Tasks

### Task 1 — R0: Current-system architecture audit

**Files:** Create `docs/research/phase_r/01_system_architecture_audit.md`.

- [x] Inspect current protocol/configuration, fusion geometry, processing order, CSI/CFAR, localization/velocity, tracking/PIPE, CPU/CUDA paths, and method information boundaries.
- [x] Build a source-cited block diagram and method/evidence matrix; mark implemented, production-executed, evaluable, and performance-verified separately.
- [x] Verify every cited symbol/file/line against current HEAD and trace target-speed semantics from simulation truth to reporting.
- [x] Record unverified assumptions and avoid new code changes unless a concrete correctness defect is found.

**Acceptance:** The audit can be independently checked against current source and configuration; every pipeline ordering and geometry claim has a source pointer; historical outputs are explicitly dated/scoped.

### Task 2 — R1: Primary-source literature review

**Files:** Create `docs/research/phase_r/02_literature_review.md` and `docs/research/phase_r/literature_matrix.csv`.

- [x] Cover the six requested areas: low-speed/MDV; heterogeneous clutter and finite samples; channel/target preservation; system mismatch; target parameter estimation; physical/model-driven learning.
- [x] Read full texts where accessible; record model, data, baselines, results, limitations, measured-data status, PGRCC fit, DOI/official URL, and full-text access status.
- [x] Record publication/source type and a paper-specific reproduction recommendation for all 13 literature-matrix entries.
- [x] Make the required per-paper performance bottleneck, algorithm/method, and gain-applicability conditions explicit; verify the full 17-item record coverage and use `FULLTEXT_UNAVAILABLE` for each inaccessible full text.
- [x] Separate research prevalence, theoretical generality, PGRCC-observed loss, and algorithmically recoverable loss.
- [x] Validate CSV schema, links, and that cited statements are supported by the indicated primary sources.

**Acceptance:** No paper is included based on title/abstract alone as if fully reviewed; inaccessible papers are labeled; recent-work cutoff is 2026-10-18.

### Task 3 — R2: PGRCC-specific physical limits

**Files:** Create `docs/research/phase_r/03_physical_limits.md`; add only necessary reproducible calculation code/figures under `scripts/phase_r/` and `docs/research/phase_r/figures/`.

- [x] Derive the F1/F2 target/clutter response and degrees-of-freedom boundary for the current processing domain.
- [x] Calculate low-speed Doppler/ambiguity/beam and geometry quantities from the actual configurations and code; distinguish radial velocity from scalar ground speed.
- [x] Separate physical decorrelation, channel mismatch, finite-sample covariance error, target cancellation, and CFAR/association effects.
- [x] State when CRLB/FIM is unsupported by current nuisance and noise assumptions; do not invent a theoretical bound.

**Acceptance:** Formula units and sign conventions are explicit, numerical inputs are traceable, and any figure is generated from documented data or a labeled model.

### Task 4 — R3: Evaluation-pipeline audit and minimal repair

**Files:** Create `docs/research/phase_r/04_evaluation_pipeline_audit.md`; modify source only if current code inspection proves a defect affecting validity.

- [x] Recheck latest C4/P2/PKR quality-gate failures, A0/PK/PKR closure, valid CUT denominator, hit definition, Mode-B counterfactual comparability, target truth leakage, and downstream tracking evidence against current code and surviving run artifacts.
- [x] Distinguish an input/design limitation from an implementation bug; do not lower gates to force evaluability.
- [x] If fixing code, add a failing regression test first, preserve Production Current, and test adjacent CPU/CUDA paths as applicable.
- [x] Report `IMPLEMENTED`, `EXECUTED`, `EVALUABLE`, and `PERFORMANCE_VERIFIED` separately.

**Acceptance:** The audit identifies the exact current blocker(s), source location(s), and the smallest decision-relevant remedy; no algorithmic conclusion is based on a failed or zero denominator.

### Task 5 — R4: Pre-registered minimal experiment design

**Files:** Create `docs/research/phase_r/05_experiment_design.md` before any new experiment run.

- [ ] State hypotheses, paired inputs, one-factor changes, methods, metrics, statistical units, confidence intervals, pass/falsification/stopping rules, and expected discriminating value for E1–E5.
- [ ] Reuse valid existing evidence where its source identity and condition match; identify only the smallest missing experiment set.
- [ ] Check GPU, disk, data, and source/build identity; pilot first and preserve failure cases.

**Acceptance:** Every proposed run can change an A/B/C/D decision; no experiment begins before its input, analysis, and stopping rule are frozen in the design document.

### Task 6 — R4/R5: Execute minimal discriminating experiments

**Files:** Create `docs/research/phase_r/06_bottleneck_experiment_results.md`; store compact manifest/results in a dedicated Phase R evidence directory.

- [ ] Run only the registered minimum needed after the evaluation path is usable; use real CUDA for claims requiring production CUDA.
- [ ] Preserve paired seeds, complete failures, denominators, confidence intervals, device state, source/config identity, commands, and outputs.
- [ ] Reuse prior experiments only when their actual source/inputs/metrics meet the current design; never relabel historical results as new runs.

**Acceptance:** Result status is explicit, all failed/unevaluable branches remain visible, and the evidence supports the stated scope only.

### Task 7 — R5/R6: Direction decision and next-stage plan

**Files:** Create `docs/research/phase_r/07_research_direction_decision.md` and `docs/research/phase_r/08_next_stage_plan.md`.

- [ ] Compare A/B/C and evidence-supported D on observed bottleneck, recoverable space, baseline strength, information limit, AI necessity, novelty, and validation feasibility.
- [ ] Answer Q1–Q12 with evidence/interpretation/unknowns separated; state counter-evidence and falsification conditions.
- [ ] Recommend the earliest useful next study and define inputs, outputs, tests, and stop/go gates; keep AI off unless deterministic gaps are demonstrated.

**Acceptance:** One direction is selected only if all task gates are satisfied; otherwise the conclusion is explicitly `INCONCLUSIVE` with the exact evidence needed to decide.

### Task 8 — Navigation and final evidence review

**Files:** Create `docs/research/phase_r/README.md`; update repository documentation navigation only where a current entrypoint exists and the addition is in scope.

- [x] Close the R2 visual gap found in final review with a reproducible analytic CSI response figure; the test and rendered PNG confirm the exact formula and readable labels. This is not an empirical Pd/SCNR result.
- [x] Correct wrapped-angle finite differences and replay saved Phase-I observations with per-seed, FFT-feature and state-scale comparisons; publish compact replay inputs/results without rewriting historical evidence.
- [ ] Review all required deliverables against the attachment's Q1–Q12 and section-by-section requirements.
- [ ] Recompute consequential aggregates from source tables; verify citation/DOI targets, figure labels/units, no false Pfa claims, and cross-report consistency.
- [ ] Run applicable test/build/diff checks and list GPU or data work not executed.
- [ ] Preserve the existing external review package and retained historical evidence; use the user's current publication authorization for reviewed phase changes only.

**Acceptance:** The Phase R package has a usable reading order, reproducible evidence pointers, and an honest final status for every requirement.
