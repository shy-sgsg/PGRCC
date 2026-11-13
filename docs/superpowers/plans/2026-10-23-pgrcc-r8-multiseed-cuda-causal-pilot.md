# PGRCC R8 Multi Seed CUDA Causal Pilot Implementation Plan

> **历史执行计划快照（2026-10-23）。** 本文的任务范围与复选状态仅描述该计划形成时的阶段任务，不代表当前仍待执行或授权重启；当前研究主题与优先任务以[研究总纲](../../research/RESEARCH_MASTER_PLAN.md)和[研究决策日志](../../research/RESEARCH_DECISION_LOG.md)为准。

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to execute this plan task by task. Steps use checkbox syntax for tracking.

**Goal:** Complete the R8 paired A0/B/C causal validation for seed202 and seed303 on the local Native CUDA production path, then assess it alongside the already executed seed101 R7 evidence.

**Architecture:** Reuse the frozen Stage1 scenario generation, physical known-correction operator, Production runner, and R7 task evaluator. Add a narrow R8 entry point that selects the verified R7 binary and records its identity, then aggregate existing seed101 evidence and the two fresh runs into machine-readable outputs and the five requested reports.

**Tech Stack:** Ubuntu, `/usr/bin/nvcc`, Native CUDA Release binary, existing Stage2 simulator, Python experiment/evaluation scripts, Markdown, JSON, CSV, and PNG.

**Spec:** `/home/shy/.codex/attachments/6071a8fe-b287-414d-b5f2-ca3918a12409/pasted-text-1.txt`

## Global Constraints

- Run Native CUDA on the local NVIDIA GPU with `USE_COREX=OFF`; CoreX is not a build or acceptance requirement.
- Reuse seed101 from the R7 repaired Production evidence; run only seed202 and seed303 for the new A0/B/C × ON/OFF/TO matrix, three periods per case.
- Require nonzero Stage2 continuous-area packets and samples, repaired GO-CFAR correctness, paired inputs, and A0/C physical closure at relative complex RMS `<= 1e-3`.
- Known correction may use injected +4 ns only in the evaluator-only correction; do not add blind D3, AI, Router, other delay levels, or new scene variants.
- Treat periods within each seed as correlated; report three independent seeds descriptively and make no statistical-significance or population Pd/Pfa claim.

## Review Focus

- A stale `build/GMTI_pipe_core` can run the pre-R7 CFAR; the runner must select and verify `build_r7/GMTI_pipe_core` before the first Production smoke.
- Reconstructed seed202/303 inputs must match historical per-period SHA-256 values or be labeled as fresh paired scenes, never silently pooled as historical replay.
- The Stage2 gate must use measured nonzero continuous-area packets/samples and measured clutter power, not the `area_enabled` flag.
- Every production run must retain valid CUT geometry, finite thresholds, and the repaired GO-CFAR identity before its detection result is evaluated.
- Detection and TrackManager conclusions require exact cluster lineage, same-period/same-beam one-to-one truth association, and truth-derived radial velocity evaluability.

---

### Task 1: Freeze R8 experiment design and entry point

**Files:**
- Create: `docs/research/phase_r/R8_experiment_design.md`
- Create: `scripts/phase_r/run_r8_multiseed_causal_pilot.py`
- Test: `tests/test_phase_r8_causal_pilot.py`

**Interfaces:**
- Consumes: `scripts/phase_r/run_r3_minimal_cuda_causal_pilot.py::run_abc_extension`, `scripts/run_delay_stage1_formal.py`, and `scripts/run_track_manager_e2e.py`.
- Produces: one seed-scoped runner invocation with required `--output-root` and `--seed {202,303}`, fixed `build_r7/GMTI_pipe_core`, and a fail-closed nonempty-directory check.

- [x] Write tests proving invalid seed, wrong binary SHA, and nonempty output root fail before simulation; a mocked A/B/C extension records both Stage1 and Production binary paths as the selected R7 binary.
- [x] Run the focused runner tests and verify the seed-scoped Native CUDA wrapper rejects invalid inputs and records the selected R7 binary identity.
- [x] Implement the seed-scoped Native CUDA wrapper; require binary SHA `de3026ed4f1263be4143dcbcd10ccbb5e53b4be357e21906e1bf19ea0ec29d00`, set both production module paths, and save the effective command and source identity.
- [x] Re-run the focused test and `python3 -m py_compile scripts/phase_r/run_r8_multiseed_causal_pilot.py`.
- [x] Write the frozen A0/B/C roles, historical SHA policy, physical and numerical gates, evaluation definitions, and stop conditions in `R8_experiment_design.md`.

### Task 2: Execute the paired CUDA cases

**Files:**
- Create: `outputs/phase_r8_unknown_delay_multiseed_20261023/seed202/`
- Create: `outputs/phase_r8_unknown_delay_multiseed_20261023/seed303/`
- Create: `docs/research/phase_r/evidence/r8_unknown_delay_multiseed_20261023/`

**Interfaces:**
- Consumes: Task 1 runner; R3 historical seed manifests; R7 seed101 Production manifest and task metrics.
- Produces: two real CUDA case manifests, per-period input hashes, runtime XML/logs, truth, beam stages, CSI taps, CFAR geometry, detections, cluster lineage, tracks, and gate results.

- [x] Verify `nvidia-smi`, the Stage2 simulator SHA, Native CUDA cache (`USE_COREX=OFF`), and the fixed Production SHA; record current device and disk state.
- [x] Execute seed202 first. Stage2 clutter, pairing, physical closure, CUDA Production, and GO-CFAR gates passed.
- [x] Compare all reconstructed seed202 input SHA-256 values against its historical manifest; all 27/27 period inputs matched.
- [x] After seed202 passed, execute seed303 and apply the same gates and per-file SHA comparison; all 27/27 period inputs matched. One audit replay required elevated local GPU access and passed on retry.
- [x] Confirm each new matrix contains exactly A0/B/C × ON/OFF/TO with three periods, one repaired Production binary, and complete runtime evidence.

### Task 3: Evaluate task-level effects and mechanisms

**Files:**
- Create: `scripts/phase_r/evaluate_r8_multiseed_causal_pilot.py`
- Create: `docs/research/phase_r/evidence/r8_unknown_delay_multiseed_20261023/`
- Create: `outputs/phase_r8_unknown_delay_multiseed_20261023/evaluation/`

**Interfaces:**
- Consumes: seed202/303 production manifests; R7 seed101 fixed-gate task metrics; `scripts/phase_r/evaluate_r7_task_metrics.py`; `scripts/phase_r/evaluate_r3_minimal_cuda_causal_pilot.py`.
- Produces: per-period A0/B/C metrics and lineage, cross-seed JSON/CSV, figure(s), and a gate/status summary.

- [x] Apply the frozen R7 truth association and cluster lineage evaluator to each ON/OFF/TO run; missing required fields remain `NOT_EVALUABLE`.
- [x] Verify A0/C F1/F2 closure, ON/OFF pairing, measured clutter, new-seed F1/F2 input comparability, GO-CFAR valid CUTs, target-neighborhood hits, and target-only transfer. Seed101 raw IQ removal is recorded as `NOT_EVALUABLE` for recomputed F1/F2 input power.
- [x] Report CSI residual power, target amplitude/phase, paired output SCNR, H0 descriptive false-candidate counts, detection and confirmed-track opportunities, and only truth-supported parameter errors.
- [x] Combine three seeds descriptively, preserving per-period new-seed neighborhood data and noting the independent sample size is three.
- [x] Regenerate deterministic evaluation artifacts from the preserved manifests and verify status gates and local evidence links.

### Task 4: Write the five R8 reports and close the phase

**Files:**
- Create: `docs/research/phase_r/R8_native_cuda_execution.md`
- Create: `docs/research/phase_r/R8_detection_causal_results.md`
- Create: `docs/research/phase_r/R8_mechanism_analysis.md`
- Create: `docs/research/phase_r/R8_research_direction_decision.md`
- Modify: `docs/research/phase_r/README.md`

**Interfaces:**
- Consumes: Tasks 1–3 reports, manifests, logs, figures, and cleanup ledger.
- Produces: a navigable R8 record answering Q1–Q7 and a single evidence-based next direction; no R9 launch.

- [x] Record exact Native CUDA commands, source/binary/input/XML identities, device state, gates, failures, and execution labels.
- [x] Report all available seed/period CFAR→cluster→detection→track evidence, H0 descriptive counts, target parameters, and unavailable radial-velocity cases.
- [x] Map observed losses to CSI, CFAR, cluster/detection, or TrackManager evidence and assess low-speed protection, clutter/statistical robustness, and physical delay mismatch.
- [x] State blind D3 as `NOT_AVAILABLE` and keep AI unjustified by current evidence.
- [x] Update Phase R navigation, finish final script/test/document checks, review exact staged paths, then commit and push only R8 deliverables to the current branch (`eaf599f`, remote HEAD verified).
