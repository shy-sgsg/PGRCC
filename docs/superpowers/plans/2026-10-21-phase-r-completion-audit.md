# PGRCC Phase R Completion Audit and Research Synthesis Plan

> **历史执行计划快照（2026-10-21）。** 本文的任务范围与复选状态仅描述该计划形成时的阶段任务，不代表当前仍待执行或授权重启；当前研究主题与优先任务以[研究总纲](../../research/RESEARCH_MASTER_PLAN.md)和[研究决策日志](../../research/RESEARCH_DECISION_LOG.md)为准。

> **For agentic workers:** Use the inline execution approach authorized by the user; complete the tasks in order and keep this checklist current.

**Goal:** Turn the existing Phase R evidence into a source-checked, complete research decision package that answers Q1–Q12 without overstating unmeasured performance or AI need.

**Architecture:** Preserve the existing dated R0–R4 records and audit their claims against the current worktree, production code, local experiment evidence, and primary literature. Add the explicitly requested consolidated E1–E5 results, A/B/C direction decision, and gated next-stage plan, then make the Phase R README the authoritative reading map.

**Tech Stack:** C++/CUDA source inspection, Python/CSV/JSON evidence checks, primary-source web research, Markdown, and reproducible static research figures where existing evidence supports them.

**Spec:** `/home/shy/.codex/attachments/9d684da8-b980-41c3-95ea-5f294d429c46/pasted-text-1.txt`; repository `AGENTS.md`.

## Global Constraints

- Distinguish theory, external literature, historical simulation, current-source audit, and actual CUDA Production evidence.
- Keep `ai_training=false`, `router_enabled=false`, and `native_4ch_stap=false`; do not train AI or unfreeze Phase-II work.
- Do not lower CFAR, scan-beam, ROI, target-support, or other quality gates to manufacture evaluability.
- Reuse current R3/R4 evidence; do not rerun broad Baseline matrices or repeat completed CUDA cases without a specific decision-critical gap.
- Label `IMPLEMENTED`, `EXECUTED`, `EVALUABLE`, and `PERFORMANCE_VERIFIED` separately; missing evidence remains `NOT_EVALUABLE`/`INCONCLUSIVE`.
- Preserve unrelated/untracked experiment evidence; stage or commit only reviewed Phase R deliverables; do not push absent explicit authorization.

## Review Focus

- **Source drift:** Any source, configuration, or interface changed after the cited audit baseline must be checked against current code; attach the exact current path/line or report the gap.
- **Input/processing comparability:** Paired cases must match seed/background/target/processing operator; do not infer F1/F2 power comparability after the raw IQ arrays were removed.
- **Truth, grain, and denominators:** Ground-speed input is not radial truth; periods/CUTs are not independent seeds; observed branch hit fraction is not empirical Pfa.
- **Literature strength:** Use primary sources; separate full-text from abstract-only access and simulated/benchmark/semi-measured/flight evidence.
- **Claim support:** No method ranking, statistical improvement, AI necessity, or universal GMTI claim without evidence at the corresponding scope.

### Task 1: Establish current baseline and evidence map

**Files:** Read `AGENTS.md`, `README.md`, `docs/research/phase_r/01–07`, `docs/evidence/equivalent_vs_physical_pilot/`, the named AI_CSI research reports, and current Git history/status. No output mutation.

- [x] Map each explicit requirement/Q1–Q12 to existing authoritative artifacts and mark complete, partial, contradicted, or missing.
- [x] Check current production-source diff from the R0/R2 audit baseline and preserve all unrelated untracked evidence.
- [x] Verify the prior R3/R4 commit and current run roots; do not treat prior status text as source evidence.

### Task 2: Revalidate system architecture, physical model, and R3 evaluator

**Files:** Update only where needed: `01_system_architecture_audit.md`, `03_physical_limits.md`, `04_evaluation_pipeline_audit.md`.

- [x] Cross-check channel geometry, four-channel-to-F1/F2 fusion, CSI input/output, processing order, speed/LOS semantics, and TrackManager/PIPE contracts in current source/config.
- [x] Independently recompute key projection, Doppler alias, baseline-delay, and CSI target-response values from the documented inputs; keep model-derived and measured quantities separate.
- [x] Verify fixes/tests and current R4 manifests for the CFAR pointer, CUT, A0/PK physical closure, Mode-B contract, scan quality gate, truth leakage, and downstream provenance; retain `NOT_EVALUABLE` where run evidence is absent.

### Task 3: Refresh the primary-source literature review

**Files:** Update `02_literature_review.md` and `literature_matrix.csv`.

- [x] Verify core classical sources and add/replace only decision-relevant 2023–2026 primary papers through the current date across low-speed GMTI, heterogeneous/finite-sample clutter, target preservation, calibration, parameter estimation, and model-driven learning.
- [x] Record exact citation/DOI, system and information dimensions, algorithm/baseline, data type, validated result, limitations, PGRCC fit, and full-text access status for every retained row.
- [x] Cite only sources actually read and keep paper performance separate from PGRCC evidence.

### Task 4: Consolidate E1–E5 and current experimental results

**Files:** Create `06_bottleneck_experiment_results.md`; link to frozen design and compact R3/R4 evidence. Modify experiment code only if an actual current defect blocks a required result.

- [x] Recompute core counts/metrics from manifests and summaries, separating R3 E2, R4 E1/E4/E5, historical E3, and unevaluated metrics.
- [x] State units, definitions, denominators, seed count, uncertainty, failures, and input-retention limits; include an evidence-backed compact table/figure where useful.
- [x] Use existing current CUDA outputs; do not start seed expansion or broaden the matrix unless every predeclared gate supports it.

### Task 5: Decide the research direction and next stage

**Files:** Create `07_research_direction_decision.md` and `08_next_stage_plan.md`.

- [x] Compare A (low-speed/target protection), B (nonuniform clutter/finite samples), C (unknown physical system mismatch), and an evidence-derived D only if warranted, using explicit criteria and counter-evidence.
- [x] Answer Q1–Q12 with current evidence, explicit uncertainty, an AI entry decision, and falsifiable stop/go conditions.
- [x] Specify the earliest useful next experiment/method, exact inputs/outputs/metrics/tests/gates, and which missing resource or evidence blocks each claim.

### Task 6: Navigation and final audit

**Files:** Update `docs/research/phase_r/README.md`, figures/scripts only when needed.

- [x] Link all required 01–08 deliverables and identify historical evidence vs current conclusions.
- [x] Validate source links, local paths, formulas, table counts, chart labels, and visual rendering.
- [x] Run targeted tests/compilation for any changed code, `git diff --check`, and inspect the exact commit scope; leave the goal active if a material requirement remains incomplete or externally blocked.

### Task 7: Correct R4 H0/CFAR and zero-delay closure interpretation

**Files:** Update only the R4 descriptions in `06_minimal_cuda_causal_pilot.md`, `06_bottleneck_experiment_results.md`, `07_preliminary_direction_assessment.md`, `07_research_direction_decision.md`, and `08_next_stage_plan.md`.

- [x] Trace the H0 input from the A0_ON Stage2 area-generation report through the pre-target paired-background writer into target-disabled A0_OFF; record that the shared-background scenario snapshot retains a target-enabled source flag and the raw IQ is no longer present.
- [x] Report per-branch, target-free H0 CUT exceedance fractions as descriptive false-CUT fractions; distinguish them from detector-level false-alarm counts, a pooled branch union, and statistically validated empirical Pfa. Note the identical per-branch hit-hash signature across the eight summary rows.
- [x] Scope residual-closure applicability: it is `NOT_APPLICABLE` to the zero-delay R4 E1/E4/E5 screen, not a pass; retain the R3 nonzero-delay A0/PK closure requirement and the raw evaluator status without changing evaluator code.
- [x] Preserve the conclusion `INCONCLUSIVE`; do not add seeds, rerun CUDA, lower gates, or change evaluation code in this documentation-only correction.

**Acceptance:** Independently recompute the two branch fractions from the compact CSV; verify case-manifest/report/source links; ensure the revised documents consistently state the H0 scope, correlation/independence limit, and zero-delay closure status. `git diff --check` must pass.
