# R12 Physics-Matched Delay Estimation Implementation Plan

> **历史执行计划快照（2026-10-24）。** 本文的任务范围与复选状态仅描述该计划形成时的阶段任务，不代表当前仍待执行或授权重启；当前研究主题与优先任务以[研究总纲](../../research/RESEARCH_MASTER_PLAN.md)和[研究决策日志](../../research/RESEARCH_DECISION_LOG.md)为准。

> **For agentic workers:** Execute this plan in order; stop at each failed evidence gate.

**Goal:** Determine whether a deterministic delay estimate matched to the Stage2 physical channel model can recover the R7/R8 production task performance under unknown channel delay.

**Architecture:** First derive observability from Stage2 injection, raw protocol IQ, production fusion, and correction operators. Compare raw physical C1/C2 after reported-geometry phase removal using the exact Stage2 linear-interpolator response, with one unknown complex scalar nuisance. Add one estimator only under the declared flat-response assumption; validate it synthetically before freezing it, then run one local Native CUDA production comparison on an independent seed. Keep truth confined to evaluator-only Known correction and scoring.

**Tech Stack:** Existing C++/CUDA Native production binary with `USE_COREX=OFF`, Python/NumPy estimators and evaluators, JSON/CSV manifests, Markdown evidence.

**Spec:** User's R12 request in the active conversation; baseline `3e23235180f59fbb09b890af000544ae7a7c9fd0`.

## Global Constraints

- Do not train AI, implement Router, expand six-parameter joint estimation, use CoreX, or launch R13.
- Do not treat seed101/202/303 used for method development as independent validation; use frozen seed404 if all were involved.
- Do not pass injected delay, A0, Known, truth, paired OFF/TO, or detection success to the blind estimator.
- Do not run Native CUDA production until observability and formula-level synthetic checks pass.
- Do not claim population Pd/Pfa from a single seed; unresolved metrics remain `NOT_EVALUABLE`.

## Review Focus

- Arbitrary unknown frequency response can be indistinguishable from delay: reject the estimate unless declared physical nuisance assumptions make it identifiable.
- The Stage2 fractional-delay interpolator is not an ideal pure delay: model its exact response and boundary behavior.
- Production F1/F2 fusion may not expose raw physical channel 2: derive the estimator input from the actual available production data path.
- Unknown constant phase and complex gain must not bias delay under the declared model.
- Insufficient/noisy support and geometry phase-model error must produce explicit rejection or degraded status, not a fabricated estimate.

### Task 1: Establish the physical observation model

**Files:** Read `simulator/target_injection/channel_impairments.cpp`, `simulator/target_injection/lfm_echo_generator.cpp`, `src/gpu/gpu_kernels.cu`, `scripts/delay_stage1_core.py`, `scripts/estimate_channel_delay.py`, production correction sources, and R8/R9 manifests; create `docs/research/phase_r/R12_observability_analysis.md`.

- [x] Trace the physical channel-2 delay injection, C2/C4 phase-center compensation, raw protocol IQ layout, F1/F2 fusion, and production correction path.
- [x] Derive delay/frequency-response identifiability and state assumptions/calibration data needed to separate them.
- [x] Record source-backed equations, frequencies, geometry, data availability, and the decision gate for M1.

### Task 2: Implement and synthetically validate M1 if identifiable

**Files:** Create `scripts/phase_r/physics_matched_delay.py` and `tests/test_r12_physics_matched_delay.py`; reuse `tests/test_channel_delay_correction.py` and `tests/test_fractional_delay_inverse_closure.py`.

- [x] Freeze estimator inputs, the constant-complex-gain nuisance assumption, exact interpolator response, objective, delay bounds, support rule, and explicit non-identifiability rejection in `docs/research/phase_r/R12_physics_matched_estimator.md`.
- [x] Compare D1/D2/D3 and M1 on zero, positive/negative delay, constant phase, complex gain, fractional interpolation, noise/insufficient support, geometry-model error, and non-identifiable response cases.
- [x] Freeze code and parameters before production validation; if no identifiable blind M1 is supported, document the evidence and do not invent one.

### Task 3: Run the minimal blind Native CUDA production comparison

**Files:** Create `scripts/phase_r/run_r12_physics_matched_delay.py` and `outputs/phase_r_r12_physics_matched_delay_20261024/` only if Tasks 1–2 pass.

- [x] Reuse eligible exact-input historical production results; otherwise create only required R8-configuration paired inputs on frozen independent seed404.
- [x] Compare A0, +4 ns uncorrected, evaluator-only Known correction, frozen R9 D3, and blind M1 using matched ON/OFF/TO inputs and Native CUDA (`USE_COREX=OFF`).
- [x] Preserve runtime XML, per-beam stages, CSI taps, CFAR geometry, logs, source/build/input identities, and task-evaluator outputs; fail closed on incomplete or incomparable branches.
- [x] Report GPU state and actual run status. Keep one-seed detection/false-alarm population claims `NOT_EVALUABLE`.

### Task 4: Close evidence and decide the next direction

**Files:** Create `docs/research/phase_r/R12_blind_validation.md`, `R12_production_task_comparison.md`, and `R12_final_research_direction_decision.md`; update the Phase R document index if present.

- [x] Report `IMPLEMENTED`, `EXECUTED`, `EVALUABLE`, and `PERFORMANCE_VERIFIED` separately with a complete manifest and key-value metrics.
- [x] Answer the R12 direction questions and issue a bounded next-step recommendation strictly from the evidence.
- [x] Run scoped verification, inspect the exact stage diff, and keep only reviewed R12 files plus the compact evidence package staged for the authorized push.
