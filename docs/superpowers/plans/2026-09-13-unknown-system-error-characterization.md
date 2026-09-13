# Unknown System Error Characterization Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Turn the existing design-only unknown-system-error phase into a reproducible, bounded four-channel observability pilot without reopening AI Router or training a model.

**Architecture:** Preserve the existing Stage2 packet format and Current production defaults. Generalize only the baseline-geometry impairment so a four-channel packet receives the same horizontal phase-center perturbation on channels 2 and 4, add a Python read-only analyzer for all six channel pairs, and add a pilot driver that creates exact-paired ideal/unknown/known/estimated raw-IQ conditions. The pilot reports phase, coherence, closure-phase and recovery metrics; it does not claim a production CSI/STAP result when the existing production executable has not been run on the same condition.

**Tech Stack:** C++11/CMake Stage2 simulator, Python 3 standard library plus NumPy, unittest, JSON/CSV manifests, existing `.git-real` repository metadata.

**Spec:** `/home/shy/.codex/attachments/c2ccb29d-325e-441c-a586-81fa2ce52296/pasted-text-1.txt`; detailed phase boundary in `docs/AI_CSI_33_真实系统误差参数与可观测性分析.md`.

## Global Constraints

- The research chain remains `真实系统未知误差 → 多通道回波观测 → 未知误差/状态参数估计 → 物理模型修正与通道自校准 → 恢复杂波通道相干性 → CSI / 四通道 STAP → CFAR / Pd / Pfa / 目标保持`.
- `NO_GO_AI_ROUTER_VALUE` closes only the Current/J5/J6 Router branch; do not train AI, restore Router, expand J8, or add image-to-image processing.
- Current is four-channel protocol IQ fused as `(1,3)` and `(2,4)` into F1/F2 before CSI; four-channel STAP remains a separate end-to-end and information-matched baseline.
- New experiments use Ideal/No-error, Current+unknown-error, Known-error correction upper bound, and Estimated-error correction, with `Known − Current`, `Estimated − Current`, and signed recovery ratio.
- Estimated-error correction must not read truth files, target truth, or future-period data.
- Keep unrelated untracked Router outputs out of the commit and preserve them in place.

---

### Task 1: Normalize the research-phase evidence and plan

**Files:**
- Modify: `docs/AI_CSI_33_真实系统误差参数与可观测性分析.md`
- Modify: `docs/AI_CSI_研究主线重构与历史结果重新解释.md`
- Modify: `docs/AI_CSI_研究进展.md`
- Modify: `README.md`
- Modify: `outputs/system_error_inventory/manifest.json`
- Create: `docs/superpowers/plans/2026-09-13-unknown-system-error-characterization.md`

**Interfaces:**
- Consumes: the current tracked source tree and the existing inventory CSVs.
- Produces: an evidence table that distinguishes implemented analyzer/pilot outputs from design-only B0–B3 production comparisons and records the current source identity.

- [x] **Step 1: Record the current status before implementation**

Run:

```bash
git --git-dir=.git-real --work-tree=. status --short --untracked-files=all
git --git-dir=.git-real rev-parse HEAD
```

Expected: only pre-existing untracked experiment outputs are listed; no tracked source changes are introduced by this step.

- [x] **Step 2: Update phase documents after the code/probe evidence exists**

Use the exact status labels `implemented`, `run`, `design_only`, and `not_run` and link each claim to a concrete artifact. Do not change historical numbers.

- [x] **Step 3: Refresh inventory provenance**

Set `source_commit` to the final source commit when the implementation is complete, record the exact pilot command and artifact paths, and keep `preexisting_untracked_outputs_preserved` unchanged.

### Task 2: Make baseline geometry injection four-channel faithful

**Files:**
- Modify: `simulator/target_injection/channel_impairments.cpp`
- Modify: `simulator/target_injection/channel_impairments.h`
- Modify: `simulator/stage2_statistical_sim/simulate_stage2_main.cpp`
- Test: `tests/channel_impairments_selftest.cpp`
- Modify: `CMakeLists.txt`

**Interfaces:**
- Consumes: `ChannelImpairmentConfig`, `RadarConfig::new_protocol_channel_count`, and existing `applyChannelImpairments` call sites.
- Produces: for two-channel input, the existing channel-1/channel-2 behavior; for four-channel input, baseline geometry phase on the right phase centers (channels 2 and 4) while leaving the vertical pair relation (1,3) and (2,4) explicit and auditable.

- [x] **Step 1: Write the failing four-channel impairment test**

Construct a synthetic four-channel float32 packet with one nonzero complex sample, apply only `baseline_error_m`, and assert that channels 2 and 4 rotate by the same expected phase while channels 1 and 3 remain unchanged. Also assert that the 2-channel compatibility path still rotates channel 2 only.

- [x] **Step 2: Run the focused self-test and observe the expected failure**

Run:

```bash
cmake --build build --target channel_impairments_selftest -j4
```

Expected: configuration fails before the target exists, or the new assertions fail because no four-channel self-test/target is implemented.

- [x] **Step 3: Implement the smallest channel-generalized transform**

Load/store all packet channels only when a four-channel geometry is requested. Apply `baseline_error_m` to channels 2 and 4 using the existing `360 * error * sin(theta) / wavelength` model. Keep all unrelated impairment fields on their existing selected-channel behavior and preserve zero-bypass semantics.

- [x] **Step 4: Run the focused self-test and the existing layout self-test**

Run:

```bash
cmake --build build --target channel_impairments_selftest new_protocol_layout_selftest -j4
build/channel_impairments_selftest
build/new_protocol_layout_selftest
```

Expected: both pass with finite, phase-sign-consistent values.

### Task 3: Add six-pair raw-IQ observables and deterministic geometry estimation

**Files:**
- Create: `scripts/analyze_four_channel_observables.py`
- Create: `tests/test_four_channel_observables.py`

**Interfaces:**
- Consumes: Stage2 raw protocol BIN, packet geometry (`pulse_len`, `channel_count`, `iq_data_type`), and optional packet/header metadata.
- Produces: `pair_observables.csv`, `beam_observables.csv`, `observable_summary.json`, and reusable functions `load_packet_channels`, `compute_pair_observables`, `fit_angle_phase_model`, and `estimate_baseline_error`.

- [x] **Step 1: Write failing tests for all six pairs and the angle fit**

Use a small synthetic packet stream with known channel phases and beam angles. Assert pair names are exactly `C13,C24,C12,C14,C23,C34`, coherence is bounded in `[0,1]`, closure phase uses the documented sign, and a linear fit recovers the injected `delta_d` without reading any truth file.

- [x] **Step 2: Run the focused Python test and observe the expected failure**

Run:

```bash
python3 -m unittest -v tests/test_four_channel_observables.py
```

Expected: import or function failure because the analyzer does not exist yet.

- [x] **Step 3: Implement streaming packet decoding and pair statistics**

Decode headers and payloads in bounded chunks; reject truncated packets, non-four-channel layouts, invalid IQ types, non-finite values, and inconsistent packet sizes. For every packet compute complex cross-sums, normalized coherence, mean phase, effective sample count, and quality flags for all six pairs. Aggregate per beam using header `theta_cmd_deg`.

- [x] **Step 4: Implement the deterministic multi-beam estimator**

Fit pair phase against `sin(theta)` with explicit centering and weighted least squares. Estimate `delta_d` from the slope and retain the intercept as nuisance. Require at least two distinct beam angles and expose `fit_status`, `r2`, `rmse_rad`, `n_beams`, and confidence; do not silently return a number when the fit is underdetermined.

- [x] **Step 5: Run the focused tests and malformed-input tests**

Run the unittest module and verify the tests cover one truncated packet and one single-angle underdetermined case. Expected: all pass and errors are explicit.

### Task 4: Add and run the bounded four-condition pilot

**Files:**
- Create: `scripts/run_unknown_system_error_pilot.py`
- Create: `configs/research/unknown_system_error_baseline_pilot.json`
- Create: `tests/test_unknown_system_error_pilot.py`
- Create: `outputs/unknown_system_error_pilot_20260913_v5/` (compact CSV/JSON only; raw BIN retained locally but not committed)

**Interfaces:**
- Consumes: the Release `simulate_stage2_statistical` binary, the pilot config, and an isolated output root.
- Produces: exact-paired ideal/unknown/known/estimated raw-IQ condition metadata, six-pair observable summaries, recovery metrics, and a manifest that states whether production CSI/STAP was run.

- [x] **Step 1: Write failing tests for condition policy and signed recovery metrics**

Assert that the estimated condition rejects any truth-file path, all four condition names are required, `recovery_ratio` is null when the recoverable space is zero, and “lower is better” metrics are sign-normalized before subtraction.

- [x] **Step 2: Run the focused test and observe the expected failure**

Run:

```bash
python3 -m unittest -v tests/test_unknown_system_error_pilot.py
```

Expected: import or function failure because the pilot driver does not exist yet.

- [x] **Step 3: Implement the compact pilot driver**

Generate a seven-beam, short-pulse-count, four-channel raw-LFM case with a fixed seed. Create Ideal/No-error and Current+unknown-error simulator outputs, derive Known-error correction by applying the configured inverse phase, and derive Estimated-error correction only from the unknown-error OFF/background observable fit. Preserve command, source identity, packet layout, input hashes, hardware probe result, and cleanup status in the manifest.

- [x] **Step 4: Run the pilot once in a fresh output directory**

Run:

```bash
python3 scripts/run_unknown_system_error_pilot.py \
  --output-root /tmp/pgrcc_unknown_system_error_pilot_repro
```

Expected: exit 0 with four condition summaries, no truth access by the estimated estimator, finite observable fits, and compact outputs. If the environment cannot provide CUDA, record the probe failure and do not claim a CUDA result; the bounded raw-IQ pilot may still run on the CPU simulator.

- [x] **Step 5: Run the pilot verifier and inspect every condition**

Verify packet counts/layout, phase/coherence direction, finite recovery fields, exact pairing, and no accidental raw-BIN retention. Do not report B0–B3 CSI/STAP/Pd/Pfa as run unless the production executable and the four-channel reference were actually invoked.

### Task 5: Close the documentation and delivery loop

**Files:**
- Modify: `docs/AI_CSI_33_真实系统误差参数与可观测性分析.md`
- Modify: `docs/AI_CSI_研究主线重构与历史结果重新解释.md`
- Modify: `docs/AI_CSI_研究进展.md`
- Modify: `README.md`
- Modify: `outputs/system_error_inventory/manifest.json`

**Interfaces:**
- Consumes: the pilot manifest and observable summaries.
- Produces: synchronized navigation, research progress, inventory evidence, pilot result, B0–B3 formal plan, and explicit limitations.

- [x] **Step 1: Add pilot evidence without rewriting historical facts**

Document which quantities were actually computed, which condition used truth only for evaluation, and which end-to-end metrics remain `design_only`.

- [x] **Step 2: Update the short reproducibility entry point**

Keep the command copyable from a clean checkout and explain that raw pilot files are generated transiently and cleaned after compact summaries are written.

- [x] **Step 3: Run documentation consistency searches**

Search for the four prohibited interpretations and repair only reachable, materially misleading occurrences:

```bash
rg -n -i '不能.*比较四通道|整个.*Physics.?AI.*No.?Go|Oracle.*理论最好|寻找.*理想.*Current' README.md docs AGENTS.md
```

Expected: only corrective explanatory text or historical framing notes remain.

### Task 6: Final verification, commit, and push

**Files:**
- Modify: only reviewed files from Tasks 1–5.

**Interfaces:**
- Consumes: source changes, tests, pilot outputs, and current `.git-real` remote configuration.
- Produces: a normal commit on the configured branch pushed to `origin`, with unrelated untracked outputs excluded.

- [ ] **Step 1: Run applicable verification**

```bash
python3 -m unittest -v tests/test_four_channel_observables.py tests/test_unknown_system_error_pilot.py
cmake -S . -B build -DCMAKE_BUILD_TYPE=Release
cmake --build build -j4
ctest --test-dir build --output-on-failure
git --git-dir=.git-real --work-tree=. diff --check
```

- [ ] **Step 2: Audit the staged file set**

Stage explicit reviewed files only. Confirm no `outputs/router_opportunity_v1/` file, raw BIN, credential, or unrelated user file is staged.

- [ ] **Step 3: Commit and push**

```bash
git --git-dir=.git-real --work-tree=. add AGENTS.md README.md docs/AI_CSI_33_真实系统误差参数与可观测性分析.md docs/AI_CSI_研究主线重构与历史结果重新解释.md docs/AI_CSI_研究进展.md docs/superpowers/plans/2026-09-13-unknown-system-error-characterization.md scripts/analyze_four_channel_observables.py scripts/run_unknown_system_error_pilot.py configs/research/unknown_system_error_baseline_pilot.json simulator/target_injection/channel_impairments.cpp simulator/target_injection/channel_impairments.h simulator/stage2_statistical_sim/simulate_stage2_main.cpp tests/channel_impairments_selftest.cpp tests/test_four_channel_observables.py tests/test_unknown_system_error_pilot.py CMakeLists.txt outputs/system_error_inventory/parameter_inventory.csv outputs/system_error_inventory/propagation_map.csv outputs/system_error_inventory/manifest.json
git --git-dir=.git-real --work-tree=. diff --cached --check
git --git-dir=.git-real --work-tree=. commit -m "research: run unknown system error observability pilot"
git --git-dir=.git-real push origin master
```

- [ ] **Step 4: Verify remote state**

Run:

```bash
git --git-dir=.git-real --work-tree=. status --short --untracked-files=all
git --git-dir=.git-real rev-parse HEAD
git --git-dir=.git-real ls-remote origin refs/heads/master
```

Expected: local and remote commit IDs match; unrelated pre-existing Router outputs remain untracked and untouched.
