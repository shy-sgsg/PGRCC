# 单一通道时延确定性自校准阶段实施计划

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans. Steps use checkbox (\`- [ ]\`) syntax for tracking.

**Goal:** 在生产等效 4ch protocol IQ → F1/F2 → CSI → GO-CFAR → TrackManager/PIPE 链路上完成 channel delay error 的单误差 A0/A1/A2/A3 闭环、传统估计器对照、参数级 Monte Carlo、目标开关配对控制、ID-switch 审计和可复现 compact evidence。

**Architecture:** 保留现有 run_track_manager_e2e.py 作为历史三分支回归入口，新增无文件副作用的 delay_stage1_core.py 供单元测试、参数级 Monte Carlo 和正式汇总复用；新增 run_delay_stage1_formal.py 统一编排 simulator、四条件、OFF/ON/TO 和生产 TrackManager；新增独立审计器和分析器产出小型 CSV/JSON 证据。仿真已有的 paired background/reuse/signal-only 能力用于保证同一 C+N/S/C+N 配对，生产门限、确认规则和关联规则保持不变。

**Tech Stack:** Python 3、NumPy、现有 Stage2 CUDA simulator、现有 GMTI_pipe_core、pytest、CMake Release、CTest、CSV/JSON/Markdown；不增加第三方依赖，不启用 AI、Router 或 native four-channel STAP/JDL。

**Spec:** docs/superpowers/specs/2026-09-16-channel-delay-stage1-design.md

## Global Constraints

- 科学输入固定为 F1=(C1+C3)/2、F2=(C2+C4)/2；raw four-channel 只用于 packet/layout/fusion audit。
- A0 为零误差 Ideal，A1 为未知误差 Current，A2 为 known-error correction upper bound，A3 为 target-free blind estimated correction。
- A3 的 truth_used_in_estimator=false 且 correction_applied=true；估计失败不得静默退化为 A1。
- delay sweep 固定覆盖 0, ±1, ±2, ±4, ±8 ns，并在 manifest 中区分 engineering-realistic 与 stress-test 范围。
- OFF/ON/TO 必须来自同一 scene identity，且 ON-OFF-TO additive audit 失败时相关结论写 NOT_EVALUABLE。
- theoretical GO-CFAR Pfa=1e-6 只能作为配置理论值；结构化杂波实测字段名为 empirical_structured_clutter_false_hit_fraction。
- TrackManager/PIPE 结果必须复用生产 track_debug、同周期 detection 和 payload provenance；不得重写简化跟踪器或调 gate。
- 所有正式 manifest 写入 ai_training=false、router_enabled=false、源码身份、dirty 状态、命令、输入 hash、设备和输出路径。
- 旧的 outputs/ 原始产物不覆盖、不删除、不提交；本轮只提交 outputs/formal_evidence/stage1_delay/ 的 compact 证据。
- 每个实现任务先写可失败测试，定向测试通过后再进入下一个依赖任务；最终运行完整 pytest、Python 检查、Release build、CTest 和 git diff --check。

---

## 文件边界

- Create: configs/research/channel_delay_stage1_formal.json — delay sweep、工作点、Monte Carlo、paired control、理论 Pfa 和统计配置的唯一声明。
- Create: scripts/delay_stage1_core.py — 估计器、基线、理论量、MC 和 paired statistics 的纯函数。
- Create: scripts/run_delay_stage1_formal.py — pilot/formal 的场景生成、A0–A3 分支、OFF/ON/TO、生产运行和 manifest 编排。
- Create: scripts/audit_delay_track_id_switch.py — 只读生产 debug/detection/payload 的逐目标逐周期 ID-switch 证据审计。
- Create: scripts/analyze_delay_stage1_formal.py — 从 runner manifest 和审计 CSV 生成九个 compact evidence 文件。
- Create: tests/test_delay_stage1_core.py — delay convention、baseline、理论和统计函数测试。
- Create: tests/test_delay_stage1_contract.py — A0–A3、OFF/ON/TO、证据 schema 和 runner 配置契约测试。
- Create: tests/test_delay_track_id_switch.py — 六类新增机制和 other 可追溯分类测试。
- Modify: tests/test_channel_delay_correction.py — 共享的 F1/F2 correction 输入和 fractional-delay 回归契约。
- Modify: tests/test_track_manager_e2e_contract.py — 保证历史三分支入口与新 Stage-1 schema 互不冲突。
- Create: docs/AI_CSI_36_单一系统误差确定性自校准阶段报告.md — 面向老师/决策者的当前证据、结论和限制。
- Create: docs/papers/Paper1_ChannelDelay_SelfCalibration_Outline.md — Paper 1 结构、公式、实验表和未完成证据清单。
- Modify: README.md, docs/AI_CSI_研究进展.md — 导航到阶段报告、论文提纲和 compact evidence。
- Modify: AGENTS.md only if the new formal command or evidence contract is a durable repository rule not already covered; do not copy this run's sample counts into permanent instructions.

### Task 1: Add the formal experiment configuration and test its contract

**Files:**
- Create: configs/research/channel_delay_stage1_formal.json
- Test: tests/test_delay_stage1_contract.py

**Interfaces:**
- Produces JSON keys consumed by later tasks: schema_version, scientific_input, delay_sweep_ns, delay_ranges_ns, working_points, pilot, formal, monte_carlo, paired_control, cfar, statistics, ai_training, router_enabled.
- A working point has exact keys name, beam_id, expected_bin, range_m, texture_sigma, clutter_rho, target_velocity_mps, target_snr_db.

- [ ] **Step 1: Write the failing configuration tests.**

~~~python
def test_stage1_config_declares_delay_sweep_and_disabled_ai():
    config = load_stage1_config()
    assert config["delay_sweep_ns"] == [0, 1, -1, 2, -2, 4, -4, 8, -8]
    assert config["ai_training"] is False
    assert config["router_enabled"] is False
    assert config["scientific_input"]["fusion_pairs"] == [[1, 3], [2, 4]]


def test_stage1_config_has_paired_control_and_registered_working_points():
    config = load_stage1_config()
    assert config["paired_control"] == {"formula": "ON-OFF-TO", "dtype": "float32"}
    names = {point["name"] for point in config["working_points"]}
    assert {"base", "low_snr", "high_velocity", "alternate_texture_geometry"} <= names
    assert set(config["formal"]["seeds"]) == {101, 202, 303}
    assert set(config["formal"]["target_velocities_mps"]) == {6.7, 12.0}
    assert min(config["formal"]["target_snr_db"]) < 30.0
~~~

- [ ] **Step 2: Run the tests and verify the missing file fails.**

Run: python3 -m pytest -q tests/test_delay_stage1_contract.py

Expected: FAIL because configs/research/channel_delay_stage1_formal.json does not exist.

- [ ] **Step 3: Add the exact configuration.**

Use the 12 legacy base combinations (seed in {101,202,303}, velocity in {6.7,12.0}, SNR in {30,35}), one low-SNR point at 20 dB, one high-velocity point at 12 m/s, and one alternate beam/range/texture point. Put all nine delay values in delay_sweep_ns; formal CUDA selection uses zero, ±1, ±4, and ±8 ns across the registered cases. Set monte_carlo.trials to 100, theoretical cfar.pfa to 1e-6, and bootstrap repetitions to 2000.

- [ ] **Step 4: Run the configuration tests and JSON syntax check.**

Run: python3 -m pytest -q tests/test_delay_stage1_contract.py && python3 -m json.tool configs/research/channel_delay_stage1_formal.json >/dev/null

Expected: PASS and exit code 0.

- [ ] **Step 5: Commit the configuration contract.**

~~~bash
git add configs/research/channel_delay_stage1_formal.json tests/test_delay_stage1_contract.py
git commit -m "test: define channel delay stage1 contract"
~~~

### Task 2: Implement the pure delay estimator and theory/statistics core

**Files:**
- Create: scripts/delay_stage1_core.py
- Test: tests/test_delay_stage1_core.py

**Interfaces:**
- estimate_delay_d1_d2_d3(f1_time: np.ndarray, f2_time: np.ndarray, fs_hz: float) -> dict[str, dict[str, object]] returns D1_ordinary_LS, D2_weighted_LS, D3_Huber_weighted_LS; each row contains status, delta_tau_ns, finite, residual_phase_rms_rad, runtime_sec, support_count, and fallback_reason.
- estimate_delay_cross_correlation(f1_time, f2_time, fs_hz) -> dict[str, object] and estimate_delay_gcc_phat(f1_time, f2_time, fs_hz) -> dict[str, object] return the same row schema and use the positive delay convention from C12=X1*conj(X2).
- delay_method_suite(f1_time, f2_time, fs_hz) -> list[dict[str, object]] returns D1/D2/D3 plus both traditional baselines without reading truth.
- weighted_slope_variance(frequency_hz, phase_rad, weights) -> dict[str, float] returns weighted mean, slope_rad_per_hz, slope_variance, delay_variance_sec2, delay_std_ns, ci95_low_ns, ci95_high_ns, and effective_sample_count.
- residual_phase_from_delay_error(frequency_hz, error_ns) -> np.ndarray; approximate_two_channel_cancellation_loss(frequency_hz, error_ns) -> dict[str, float] reports phase and the explicitly approximate sin²(εφ/2) loss.
- paired_mcnemar_exact(current_hits: Sequence[bool], comparison_hits: Sequence[bool]) -> dict[str, object]; paired_bootstrap_ci(values_a, values_b, block_ids, trials, seed) -> dict[str, object]; recovery_ratio(ideal_or_upper, current, blind, direction) -> dict[str, object] writes NOT_EVALUABLE for a zero denominator.
- run_parameter_monte_carlo(delay_errors_ns, snr_db_values, trials, seed, fs_hz, bandwidth_hz, pulse_samples) -> list[dict[str, object]] generates wideband complex LFM pairs and returns method-level bias/RMSE/STD/95% CI/outlier/fallback/runtime rows.

- [ ] **Step 1: Write failing mathematical and API tests.**

~~~python
def test_cross_spectrum_sign_convention_recovers_positive_and_negative_fractional_delay():
    f1, f2, fs = make_fractionally_delayed_lfm(delay_ns=2.25, snr_db=60.0)
    rows = delay_method_suite(f1, f2, fs)
    assert rows_by_method(rows)["D1_ordinary_LS"]["delta_tau_ns"] == pytest.approx(2.25, abs=0.03)

    f1, f2, fs = make_fractionally_delayed_lfm(delay_ns=-2.25, snr_db=60.0)
    rows = delay_method_suite(f1, f2, fs)
    assert rows_by_method(rows)["D2_weighted_LS"]["delta_tau_ns"] == pytest.approx(-2.25, abs=0.03)


def test_baseline_fallback_is_structured_for_insufficient_support():
    row = estimate_delay_gcc_phat(np.ones(4, complex), np.ones(4, complex), 60e6)
    assert row["status"] == "fallback"
    assert row["fallback_reason"] == "insufficient_calibration_support"
    assert row["delta_tau_ns"] is None


def test_zero_recovery_denominator_is_not_evaluable():
    result = recovery_ratio(ideal_or_upper=0.8, current=0.8, blind=0.8, direction="higher_is_better")
    assert result["status"] == "NOT_EVALUABLE"
    assert result["recovery_ratio"] is None


def test_residual_phase_and_cancellation_loss_are_monotonic_in_delay_error():
    frequency = np.linspace(-25e6, 25e6, 101)
    small = approximate_two_channel_cancellation_loss(frequency, 1.0)
    large = approximate_two_channel_cancellation_loss(frequency, 8.0)
    assert large["mean_residual_phase_abs_rad"] > small["mean_residual_phase_abs_rad"]
    assert large["mean_approximate_loss"] > small["mean_approximate_loss"]
~~~

- [ ] **Step 2: Run the focused tests and observe the missing-module failure.**

Run: python3 -m pytest -q tests/test_delay_stage1_core.py

Expected: FAIL with an import or missing-function error.

- [ ] **Step 3: Implement the minimum pure core.**

Import the existing estimate_from_raw_time_arrays for D1/D2/D3 semantics, but normalize its result into the new row schema. For the two baselines, estimate lag using signed np.correlate/GCC-PHAT peak and quadratic interpolation; reject fewer than eight finite complex samples with insufficient_calibration_support. For spectral methods use X1 * conj(X2), unwrap only finite supported bins, and never accept a truth delay or truth-derived frequency mask. Use weighted Sxx_w = Σ w(f-fbar_w)² for the variance, report a normal 95% interval, and use sin²(εφ/2) only as a theory approximation.

- [ ] **Step 4: Add and test the Monte Carlo and paired-statistics functions.**

Generate an analytic complex LFM with known fractional delay in the frequency domain, add independent complex Gaussian noise at the requested SNR, run the full method suite, and compute errors only after the estimator returns. Resample scene blocks, not individual periods, in paired_bootstrap_ci; compute two-sided exact McNemar p-values from discordant pairs using a binomial tail. Preserve finite runtime and fallback counts for every method.

- [ ] **Step 5: Run the core tests and compile check.**

Run: python3 -m pytest -q tests/test_delay_stage1_core.py && python3 -m py_compile scripts/delay_stage1_core.py

Expected: PASS and exit code 0.

- [ ] **Step 6: Commit the pure core.**

~~~bash
git add scripts/delay_stage1_core.py tests/test_delay_stage1_core.py
git commit -m "feat: add channel delay stage1 estimator core"
~~~

### Task 3: Add paired-scene construction and A0/A1/A2/A3 correction contract

**Files:**
- Create: scripts/run_delay_stage1_formal.py
- Modify: tests/test_delay_stage1_contract.py, tests/test_channel_delay_correction.py

**Interfaces:**
- build_stage1_branch_contract() -> dict[str, object] declares A0_Ideal, A1_Current_unknown_error, A2_Known_error_correction_upper_bound, A3_Blind_target_free_estimated_correction and truth-read/correction requirements.
- audit_additive_triplet(on: np.ndarray, off: np.ndarray, target_only: np.ndarray, tolerance: float) -> dict[str, object] returns status, max_abs_error, rms_error, sample_count, tolerance, and reason.
- build_scene_variants(config, scene_identity, delay_error_ns, case_root) -> dict[str, Path] creates paired A0/A1 ON, OFF and TO scenario JSON files using paired_background_output_dir, background_input_dir, signal_only, and the same seed/scene settings.
- prepare_condition_inputs(case_root: Path, condition: str, period_paths: Sequence[Path], calibration_input: Path, delay_truth_ns: float, delay_estimate_ns: float | None, layout: Mapping[str, object]) -> dict[str, object] returns raw paths, corrected paths, estimate/source/residual, truth_used_in_estimator, correction_applied, and audit paths; A3 uses only A1 OFF calibration, A2 uses truth only for correction.

- [ ] **Step 1: Write failing A0–A3 and additive-control tests.**

~~~python
def test_stage1_branch_contract_has_four_conditions_and_truth_blind_a3():
    contract = build_stage1_branch_contract()
    assert set(contract["conditions"]) == {
        "A0_Ideal", "A1_Current_unknown_error",
        "A2_Known_error_correction_upper_bound",
        "A3_Blind_target_free_estimated_correction",
    }
    assert contract["requirements"]["A3_Blind_target_free_estimated_correction"]["truth_used_in_estimator"] is False


def test_additive_triplet_passes_for_float32_linear_pair():
    off = np.array([1 + 2j, 2 - 1j], dtype=np.complex64)
    target = np.array([3 - 1j, -2 + 4j], dtype=np.complex64)
    result = audit_additive_triplet(off + target, off, target, tolerance=1e-5)
    assert result["status"] == "passed"


def test_additive_triplet_failure_is_not_evaluable():
    result = audit_additive_triplet(np.ones(2, complex), np.zeros(2, complex), np.zeros(2, complex), tolerance=1e-6)
    assert result["status"] == "NOT_EVALUABLE"
    assert result["reason"] == "on_minus_off_minus_target_exceeds_tolerance"
~~~

- [ ] **Step 2: Run the contract tests and confirm missing runner APIs fail.**

Run: python3 -m pytest -q tests/test_delay_stage1_contract.py tests/test_channel_delay_correction.py

Expected: the new contract tests fail while existing delay-correction tests remain the baseline reference.

- [ ] **Step 3: Implement scenario variants using existing simulator pairing.**

Extend the new runner's scenario builder from existing run_track_manager_e2e._build_scenario behavior. Set protocol channel count to 4, channel geometry explicitly, and only modify delay impairment, target enablement, output directory, and registered working-point fields. For A1, request a paired C+N output in the same simulator pass; for OFF/TO reuse that exact background with background_input_dir; for TO set signal_only=true. For A0 repeat with delay zero and the same scene identity. Hash each JSON/data file and record simulator return codes.

- [ ] **Step 4: Implement correction preparation with explicit provenance.**

Fuse every calibration packet with fuse_protocol_channels_to_f1_f2 before calling the delay core. A1 keeps original bytes. A2 calls rewrite_float32_protocol_delay with the injected truth only after the estimator stage. A3 calls it with the finite target-free estimate from A1 OFF. If A3 has no finite estimate, write a failed correction record and do not run it as a Current substitute. Apply the same physical rewrite to ON, OFF and TO before production evaluation.

- [ ] **Step 5: Run focused contract tests.**

Run: python3 -m pytest -q tests/test_delay_stage1_contract.py tests/test_channel_delay_correction.py tests/test_track_manager_e2e_contract.py

Expected: PASS, with existing three-branch regression behavior unchanged.

- [ ] **Step 6: Commit the paired condition contract.**

~~~bash
git add scripts/run_delay_stage1_formal.py tests/test_delay_stage1_contract.py tests/test_channel_delay_correction.py
git commit -m "feat: add paired channel delay conditions"
~~~

### Task 4: Add target-off waterfall and production branch execution

**Files:**
- Modify: scripts/run_delay_stage1_formal.py
- Modify: tests/test_delay_stage1_contract.py

**Interfaces:**
- evaluate_target_off_waterfall(off_result_dir: Path, off_debug_dir: Path | None, cfar_summary: Mapping[str, object], theoretical_pfa: float) -> dict[str, object] returns distinct cell, cluster, protocol-detection and false-track rows, each with value, status, denominator, and definition.
- run_production_branch(case_root: Path, branch_name: str, input_path: Path, source_xml: Path, truth_by_period: Mapping[int, Mapping[str, str]], period_count: int, input_mode: str, layout: Mapping[str, object]) -> dict[str, object] wraps existing XML configuration, GMTI_pipe_core, audit_track_manager_run.audit_debug_dir, detection snapshots, payload audit, and CFAR log parser without changing production gates.
- run_stage1_case(config: Mapping[str, object], scene_identity: Mapping[str, object], output_root: Path, input_mode: str) -> dict[str, object] returns one case manifest containing A0/A1/A2/A3, OFF/ON/TO additive audit, estimator suite, production metrics, and paths/hashes.

- [ ] **Step 1: Write failing OFF waterfall tests.**

~~~python
def test_target_off_waterfall_does_not_alias_detection_records_to_cfar_pfa(tmp_path):
    result = evaluate_target_off_waterfall(
        tmp_path, None, {"status": "passed", "hit_cells": 12, "clusters": 3, "selected": 2}, 1e-6
    )
    assert result["theoretical_go_cfar_cell_pfa"] == 1e-6
    assert result["cell_false_hit_fraction"]["status"] == "NOT_EVALUABLE"
    assert result["protocol_false_detections"]["status"] == "NOT_EVALUABLE"
    assert "empirical_structured_clutter_false_hit_fraction" in result


def test_target_off_with_explicit_denominators_reports_four_separate_layers(tmp_path):
    write_off_fixture(tmp_path, valid_cuts=100, cell_hits=4, cluster_count=2, protocol_rows=3, track_ids=[7])
    result = evaluate_target_off_waterfall(tmp_path, tmp_path / "debug", {"status": "passed", "hit_cells": 4, "clusters": 2, "selected": 2}, 1e-6)
    assert result["cell_false_hit_fraction"]["value"] == pytest.approx(0.04)
    assert result["false_clusters"]["value"] == 2
    assert result["protocol_false_detections"]["value"] == 3
    assert result["false_tracks"]["value"] == 1
~~~

- [ ] **Step 2: Run the focused tests and observe the missing evaluator.**

Run: python3 -m pytest -q tests/test_delay_stage1_contract.py -k target_off

Expected: FAIL because the evaluator is not implemented.

- [ ] **Step 3: Implement exact waterfall semantics.**

Parse valid CUT denominators only when production logs or an explicit audit fixture exposes them; otherwise return NOT_EVALUABLE. Count clusters only from an explicit cluster identity/count, protocol false detections only from OFF detection_results rows, and false tracks only from production TrackManager IDs. Never use target-on period proxies, detection-record false rates, payload rates, or selected-minus-one heuristics as these layers. Always include theoretical 1e-6 separately and use the exact structured-clutter field name.

- [ ] **Step 4: Implement production execution for each condition.**

Reuse _configure_xml, _run_logged, _find_debug_dir, _find_pipe_run, _parse_cfar_summaries, audit_debug_dir, and existing TrackManager metric evaluator through imports or small adapters. Use identical XML gate overrides for all conditions, store track_debug, detection CSV, payload CSV, CFAR logs, return code and elapsed time under the case root, and attach track_audit_total_violations to each branch. Run OFF separately for A0/A1/A2/A3 using corresponding raw/corrected input.

- [ ] **Step 5: Run unit and one local production smoke.**

Run: python3 -m pytest -q tests/test_delay_stage1_contract.py tests/test_track_manager_e2e_contract.py; then run the pilot command from Task 9 with --input-mode local and inspect that all four conditions and four OFF layers are present in its manifest.

- [ ] **Step 6: Commit production branch execution.**

~~~bash
git add scripts/run_delay_stage1_formal.py tests/test_delay_stage1_contract.py
git commit -m "feat: close target-off and production stage1 branches"
~~~

### Task 5: Add the pilot/formal CLI and parameter Monte Carlo manifest

**Files:**
- Modify: scripts/run_delay_stage1_formal.py
- Modify: scripts/delay_stage1_core.py
- Test: tests/test_delay_stage1_contract.py

**Interfaces:**
- CLI accepts --mode pilot|formal, --delay-errors-ns, --seeds, --target-velocities-mps, --snr-db, --working-point, --mc-trials, --output-root, --input-mode, --period-count, and --skip-cuda.
- CLI writes manifest.json with mode, exact resolved values, commands, git, gpu_status_before, gpu_status_after, disk_status_before, disk_status_after, ai_training, router_enabled, mc_summary, cases, and status.
- Formal selected E2E uses all nine delays in Level 1 and a registered subset in Level 2; pilot is a bounded one-seed/one-working-point/three-delay run.

- [ ] **Step 1: Write failing CLI/schema tests.**

~~~python
def test_cli_rejects_nonempty_output_root(tmp_path):
    output_root = tmp_path / "nonempty"
    output_root.mkdir()
    (output_root / "existing.txt").write_text("keep", encoding="utf-8")
    result = subprocess.run([sys.executable, "scripts/run_delay_stage1_formal.py", "--mode", "pilot", "--output-root", str(output_root)], capture_output=True, text=True)
    assert result.returncode != 0
    assert "refuse to overwrite non-empty output root" in result.stderr


def test_cli_parser_exposes_required_stage1_arguments():
    parser = build_arg_parser()
    assert {"--mode", "--delay-errors-ns", "--seeds", "--target-velocities-mps", "--snr-db", "--working-point", "--mc-trials", "--output-root"} <= set(parser._option_string_actions)
~~~

- [ ] **Step 2: Run the CLI tests and observe the missing parser.**

Run: python3 -m pytest -q tests/test_delay_stage1_contract.py -k cli

Expected: FAIL because run_delay_stage1_formal.py does not yet expose the parser contract.

- [ ] **Step 3: Implement parser, pilot selection and formal selection.**

Parse comma-separated/repeated lists, reject non-finite delays, non-positive velocities, empty lists and non-empty output roots. Resolve named working points from JSON. Record every override, config hash, template hash, source commit and dirty state. Do not allow CLI truth values into A3 estimator calls. Keep raw case roots independent from compact evidence root.

- [ ] **Step 4: Integrate Level-1 Monte Carlo before CUDA cases.**

Run at least 100 trials per configured delay/SNR point, include random_seed_namespace, sampling rate, bandwidth, pulse sample count, method, bias, RMSE, STD, 95% CI, outlier rate, fallback rate and mean runtime. Write raw MC table under independent run root and later copy only aggregated rows to compact evidence.

- [ ] **Step 5: Add the A0/A1/A2/A3 Level-2 orchestration.**

For each selected case, generate A0 and A1 simulator scenes, build OFF/ON/TO, estimate from A1 OFF, prepare A2/A3 corrections, run production branches, and assert branch metadata before evaluating metrics. Store exact NOT_EVALUABLE reasons for missing CUT/cluster/detection identity/transport evidence. Finish manifest even when a branch has a scientific gap, but set status=completed_with_gaps rather than calling result complete.

- [ ] **Step 6: Run parser tests and compile the runner.**

Run: python3 -m pytest -q tests/test_delay_stage1_contract.py -k cli && python3 -m py_compile scripts/run_delay_stage1_formal.py scripts/delay_stage1_core.py

Expected: PASS and exit code 0.

- [ ] **Step 7: Commit runner and MC integration.**

~~~bash
git add scripts/run_delay_stage1_formal.py scripts/delay_stage1_core.py tests/test_delay_stage1_contract.py
git commit -m "feat: add channel delay stage1 pilot and formal runner"
~~~

### Task 6: Implement the production ID-switch mechanism audit

**Files:**
- Create: scripts/audit_delay_track_id_switch.py
- Test: tests/test_delay_track_id_switch.py
- Modify: scripts/run_delay_stage1_formal.py

**Interfaces:**
- classify_switch(previous: Mapping[str, object], current: Mapping[str, object], context: Mapping[str, object]) -> dict[str, object] returns one of miss_to_reacquisition, multiple_candidate_competition, false_track_takeover, gate_boundary_crossing, position_velocity_jump, track_confirmation_or_reset, other plus reason and evidence fields.
- audit_track_id_switches(track_debug_dir: Path, detection_dir: Path, payload_path: Path, truth_path: Path | None) -> list[dict[str, object]] reads production artifacts and emits one row per actual adjacent-period target switch, with source paths/rows.
- write_audit(path: Path, rows: Sequence[Mapping[str, object]]) -> None writes stable CSV columns including target/detection/track/candidate/innovation/residual/distance/gate/confirmation/lost/reacquired/merge/split fields.

- [ ] **Step 1: Write failing classification tests with synthetic debug rows.**

~~~python
def test_classify_switch_covers_six_requested_mechanisms():
    for label, previous, current, context in synthetic_switch_fixtures():
        result = classify_switch(previous, current, context)
        assert result["classification"] == label


def test_unavailable_evidence_is_other_and_keeps_reason():
    result = classify_switch({"track_id": 1}, {"track_id": 2}, {})
    assert result["classification"] == "other"
    assert result["reason"] == "insufficient_debug_fields_for_mechanism_classification"
~~~

- [ ] **Step 2: Run the focused tests and confirm missing audit APIs fail.**

Run: python3 -m pytest -q tests/test_delay_track_id_switch.py

Expected: FAIL because the new audit module does not exist.

- [ ] **Step 3: Implement evidence-first classification.**

Read only fields present in production debug/detection/payload files. Use other when candidate IDs/counts, innovation/residual, gate threshold, confirmation state, or target association evidence is missing; include missing field names and source rows. Detect the six named mechanisms only from explicit conditions: prior miss followed by a reacquired detection, multiple candidates, a false-track identity taking a target slot, distance crossing a recorded gate, a position/velocity jump above recorded residual evidence, and confirmation/reset transitions. Do not tune thresholds or derive a new tracker.

- [ ] **Step 4: Integrate per-case audit output.**

Run the audit after every selected A0/A1/A2/A3 ON branch with track_debug, detection CSV, payload CSV and truth path. Add id_switch_audit_path, row count, classification counts and source file identity to the branch manifest. Keep rows grouped by scene/seed/case so later bootstrap treats a complete scene as one block.

- [ ] **Step 5: Run tests and compile the audit script.**

Run: python3 -m pytest -q tests/test_delay_track_id_switch.py tests/test_track_manager_e2e_contract.py && python3 -m py_compile scripts/audit_delay_track_id_switch.py

Expected: PASS and exit code 0.

- [ ] **Step 6: Commit the switch audit.**

~~~bash
git add scripts/audit_delay_track_id_switch.py scripts/run_delay_stage1_formal.py tests/test_delay_track_id_switch.py
git commit -m "feat: audit channel delay track id switches"
~~~

### Task 7: Implement compact analysis, paired statistics and evidence schema

**Files:**
- Create: scripts/analyze_delay_stage1_formal.py
- Modify: tests/test_delay_stage1_contract.py

**Interfaces:**
- build_compact_evidence(run_manifest: Path, output_dir: Path) -> dict[str, Path] writes exactly manifest.json, delay_estimation_summary.csv, delay_baseline_comparison.csv, A0_A1_A2_A3_summary.csv, target_off_false_alarm_summary.csv, target_on_detection_summary.csv, track_summary.csv, id_switch_audit.csv, statistics_summary.csv.
- compute_condition_summary(rows, condition_names) -> list[dict[str, object]] writes metric direction, status and NOT_EVALUABLE denominator reasons.
- compute_paired_statistics(scene_rows) -> list[dict[str, object]] uses exact paired binary tests and scene-block bootstrap for continuous RMSE/cancellation/transfer metrics, returning effect size, lower/upper CI, p-value, block count and materiality label.

- [ ] **Step 1: Write failing evidence-schema tests.**

~~~python
def test_compact_evidence_has_exact_required_files(tmp_path):
    outputs = build_compact_evidence(write_fixture_manifest(tmp_path), tmp_path / "evidence")
    assert set(outputs) == {
        "manifest.json", "delay_estimation_summary.csv", "delay_baseline_comparison.csv",
        "A0_A1_A2_A3_summary.csv", "target_off_false_alarm_summary.csv",
        "target_on_detection_summary.csv", "track_summary.csv", "id_switch_audit.csv",
        "statistics_summary.csv",
    }


def test_summary_preserves_not_evaluable_zero_denominator():
    rows = compute_condition_summary([{"condition": "A1", "metric": "cfar_pfa", "value": None, "status": "NOT_EVALUABLE"}], ["A0", "A1", "A2", "A3"])
    assert rows[0]["status"] == "NOT_EVALUABLE"
    assert rows[0]["value"] is None
~~~

- [ ] **Step 2: Run the schema tests and confirm analyzer is absent.**

Run: python3 -m pytest -q tests/test_delay_stage1_contract.py -k evidence

Expected: FAIL because the analyzer module does not exist.

- [ ] **Step 3: Implement compact aggregation.**

Read only runner manifest, estimator rows, target-off waterfall, target-on detection/track rows and ID-switch audit. Aggregate by complete scene/seed/case blocks. Keep A1, A2, A3 comparisons against A0, compute recoverable_space=A2-A1, blind_recovery=A3-A1, and recovery_ratio=blind_recovery/recoverable_space; for low-is-better metrics transform to a positive recovery and store metric_direction and transformed_metric_definition.

- [ ] **Step 4: Implement paired tests and materiality labels.**

Use McNemar exact for Current miss → Blind hit and analogous binary transitions. Use scene-block bootstrap for continuous differences, report a 95% CI and effect size, and label materiality only from explicit config effect thresholds; if the CI crosses zero or the denominator is unavailable, report indeterminate or NOT_EVALUABLE rather than a forced win.

- [ ] **Step 5: Write the exact compact output directory.**

Create outputs/formal_evidence/stage1_delay/ only after the source run has finished. Copy no raw BIN/NPY/F32/PNG. The compact manifest includes source run path/hash, config/template hash, input hashes, output file hashes, source commit/dirty state, GPU before/after, disk before/after, command, delay range labels, AI/Router flags, and all unresolved NOT_EVALUABLE levels.

- [ ] **Step 6: Run analyzer tests and compile.**

Run: python3 -m pytest -q tests/test_delay_stage1_contract.py -k evidence && python3 -m py_compile scripts/analyze_delay_stage1_formal.py

Expected: PASS and exit code 0.

- [ ] **Step 7: Commit analysis and evidence schema.**

~~~bash
git add scripts/analyze_delay_stage1_formal.py tests/test_delay_stage1_contract.py
git commit -m "feat: add compact delay stage1 evidence analysis"
~~~

### Task 8: Add the report and Paper 1 outline

**Files:**
- Create: docs/AI_CSI_36_单一系统误差确定性自校准阶段报告.md
- Create: docs/papers/Paper1_ChannelDelay_SelfCalibration_Outline.md
- Modify: README.md, docs/AI_CSI_研究进展.md

**Interfaces:**
- The report cites actual compact evidence paths and uses A0/A1/A2/A3, NOT_EVALUABLE, empirical_structured_clutter_false_hit_fraction, and Known-error correction upper bound terminology consistently.
- The paper outline contains exact delay convention, WLS/Huber objective and weights, variance expression, residual-phase/cancellation approximation, estimator/baseline table, MC table, E2E table, paired statistics, limitations, potential contributions and a gate for the future coupled-error study.

- [ ] **Step 1: Write document smoke tests.**

~~~python
def test_stage1_docs_name_the_four_conditions_and_unresolved_paper_gaps():
    report = Path("docs/AI_CSI_36_单一系统误差确定性自校准阶段报告.md").read_text(encoding="utf-8")
    outline = Path("docs/papers/Paper1_ChannelDelay_SelfCalibration_Outline.md").read_text(encoding="utf-8")
    for text in (report, outline):
        assert "A0" in text and "A1" in text and "A2" in text and "A3" in text
        assert "NOT_EVALUABLE" in text
    assert "Paper 1" in report or "Paper1" in report
~~~

- [ ] **Step 2: Run the document test and observe missing files.**

Run: python3 -m pytest -q tests/test_delay_stage1_contract.py -k docs

Expected: FAIL because the two documents do not exist.

- [ ] **Step 3: Write the exact theory and experiment outline.**

Use x2(t)=x1(t-δτ), X2=X1 exp(-j2πfδτ), C12=X1 conj(X2), φ12=φ0+2πfδτ, εφ=2πfετ, and Var(δτ̂)≈σφ²/((2π)²Sxx_w) only as a WLS slope variance. Explain D1 ordinary LS, D2 magnitude/power weighted LS, D3 Huber reweighting, and why these are baselines/robustification rather than claimed algorithmic novelty. Explain target-free calibration, fractional-delay physical correction, four-layer OFF waterfall, and six ID-switch mechanisms.

- [ ] **Step 4: Fill the report from generated evidence after the first real run.**

Do not invent numeric results. Include actual manifest/source commit, selected working points, delay/SNR coverage, estimator bias/RMSE/CI/fallback/runtime, A0–A3 recovery, additive audit, OFF waterfall, target-on Pd/transfer/Track metrics, paired effect sizes/CI, ID-switch counts, and exact unresolved levels. State AI/Router/native four-channel STAP/JDL remain off.

- [ ] **Step 5: Run doc and link tests.**

Run: python3 -m pytest -q tests/test_delay_stage1_contract.py -k docs && rg -n "AI_CSI_36|Paper1_ChannelDelay|formal_evidence/stage1_delay" README.md docs/AI_CSI_研究进展.md

Expected: PASS and both navigation files link to the report, outline and evidence directory.

- [ ] **Step 6: Commit documentation and navigation.**

~~~bash
git add docs/AI_CSI_36_单一系统误差确定性自校准阶段报告.md docs/papers/Paper1_ChannelDelay_SelfCalibration_Outline.md README.md docs/AI_CSI_研究进展.md
git commit -m "docs: add channel delay self calibration report"
~~~

### Task 9: Run pilot, formal CUDA evidence and final verification

**Files:**
- Modify: docs/AI_CSI_36_单一系统误差确定性自校准阶段报告.md
- Create/Modify: outputs/formal_evidence/stage1_delay/ compact files only

**Interfaces:**
- Pilot command:

~~~bash
python3 scripts/run_delay_stage1_formal.py \
  --mode pilot \
  --input-mode local \
  --output-root outputs/track_delay_stage1_pilot_20260916 \
  --delay-errors-ns 0,4,-4 \
  --seeds 101 \
  --target-velocities-mps 6.7 \
  --snr-db 30 \
  --working-point base \
  --mc-trials 100
~~~

- Formal command:

~~~bash
python3 scripts/run_delay_stage1_formal.py \
  --mode formal \
  --input-mode local \
  --output-root outputs/track_delay_stage1_formal_20260916 \
  --delay-errors-ns 0,1,-1,2,-2,4,-4,8,-8 \
  --seeds 101,202,303 \
  --target-velocities-mps 6.7,12.0 \
  --snr-db 20,30,35 \
  --working-point registered \
  --mc-trials 100
~~~

- Final verification commands:

~~~bash
python3 -m pytest -q
python3 -m py_compile scripts/delay_stage1_core.py scripts/run_delay_stage1_formal.py scripts/audit_delay_track_id_switch.py scripts/analyze_delay_stage1_formal.py
cmake --build build -j4
ctest --test-dir build --output-on-failure
git diff --check
~~~

- [ ] **Step 1: Inspect disk, tracked state and GPU before long runs.**

Run: df -h .; du -sh outputs; nvidia-smi; git status --short --untracked-files=no.

Expected: record values in the run manifest; preserve all existing output roots.

- [ ] **Step 2: Run the pilot and inspect the real failure surface.**

Run the pilot command above. Check simulator return codes, 4ch packet audit, F1/F2 fusion, A0–A3 metadata, A1 OFF calibration provenance, additive audit, production XML, TrackManager audit, payload reverse lookup, OFF waterfall statuses, and ID-switch input columns. Fix only evidence-backed failures and rerun the focused failing command.

- [ ] **Step 3: Run the formal local-input CUDA study.**

Run the formal command above with the real CUDA simulator/production core. Keep nvidia-smi before/after and source/config/input/output hashes in the manifest. If the device is unavailable in the sandbox, request the repository-authorized limited escalation and retry; temporary GPU failure does not erase CPU/analysis deliverables.

- [ ] **Step 4: Build compact evidence without staging raw data.**

Run: python3 scripts/analyze_delay_stage1_formal.py --run-manifest outputs/track_delay_stage1_formal_20260916/manifest.json --output-root outputs/formal_evidence/stage1_delay

Expected: exactly the nine required CSV/JSON files exist, no raw binary is copied, all zero denominators remain NOT_EVALUABLE, and the compact manifest points back to the formal run.

- [ ] **Step 5: Update report with actual result and limitation tables.**

Use only compact evidence rows. Explicitly answer whether the delay-only loop is closed, which OFF levels are not evaluable due to missing valid CUT/cluster/detection identity/SHM transport, what Paper 1 still lacks, and whether the repository is ready for a multi-error coupled study. Readiness is positive only if delay-only E2E closure, baseline comparison and paired statistics are evidenced; otherwise name the exact missing gate.

- [ ] **Step 6: Run the complete quality gate.**

Run the final verification commands above, plus git status --short --untracked-files=no and a staged-path review. Expected: all tests/builds pass, diff check is clean, AI/Router flags remain false, and only requested source/tests/docs/config/compact evidence are staged.

- [ ] **Step 7: Commit and push the completed stage.**

~~~bash
git add scripts/delay_stage1_core.py scripts/run_delay_stage1_formal.py scripts/audit_delay_track_id_switch.py scripts/analyze_delay_stage1_formal.py configs/research/channel_delay_stage1_formal.json tests/test_delay_stage1_core.py tests/test_delay_stage1_contract.py tests/test_delay_track_id_switch.py tests/test_channel_delay_correction.py tests/test_track_manager_e2e_contract.py docs/AI_CSI_36_单一系统误差确定性自校准阶段报告.md docs/papers/Paper1_ChannelDelay_SelfCalibration_Outline.md README.md docs/AI_CSI_研究进展.md outputs/formal_evidence/stage1_delay/manifest.json outputs/formal_evidence/stage1_delay/delay_estimation_summary.csv outputs/formal_evidence/stage1_delay/delay_baseline_comparison.csv outputs/formal_evidence/stage1_delay/A0_A1_A2_A3_summary.csv outputs/formal_evidence/stage1_delay/target_off_false_alarm_summary.csv outputs/formal_evidence/stage1_delay/target_on_detection_summary.csv outputs/formal_evidence/stage1_delay/track_summary.csv outputs/formal_evidence/stage1_delay/id_switch_audit.csv outputs/formal_evidence/stage1_delay/statistics_summary.csv
git diff --cached --check
git commit -m "feat: complete channel delay stage1 self calibration"
git push origin codex/unknown-system-error-next-stage
~~~

Do not use git add -A, force push, history rewriting, or stage the independent raw output roots.
