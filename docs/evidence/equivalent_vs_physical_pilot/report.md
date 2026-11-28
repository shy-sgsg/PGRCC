# Production Hierarchical Calibration Pilot

- Status: `completed_with_gaps`
- Decision: `NEED_MORE_SINGLE_ERROR_PRODUCTION_EVIDENCE`
- Production results require the recorded CUDA run and all downstream audit files.
- Cell Pfa 只使用 `hit_cut_count / valid_cut_count`，且分母必须为正；configured Pfa 不是实测 Pfa。
- 目标保护只从 `OFF=C+N`、`ON=S+C+N`、`TO=S` 的 ON−OFF 与 TO 因果量报告，禁止用 ON power alone。
- 下游层级严格分开：CFAR → cluster → protocol detection → TrackManager track。
- Track evidence 使用 association/state/payload；现有 debug 无法解释 ID-switch 时标记 `NOT_IDENTIFIABLE_FROM_CURRENT_DEBUG`。
- ID-switch audit: `NOT_EVALUABLE`; limitation: `track_provenance_missing_or_empty`。
- A0/PK/PKR residual closure 在 estimator 归因之前检查。

## 本次 formal 运行摘要

- 实际执行 12 个 clean-source CUDA case：`delay_error_ns ∈ {-4,+4}`、seed `101/202/303`、目标速度 `6.7/12.0 m/s`、SNR `30 dB`，每 case 5 periods；`0/±2 ns` path check 另行完成但不混入本 formal 矩阵。
- 12 个 runner 进程均返回 0，但 case 状态均为 `completed_with_gaps`；`algorithmic_results_claimed=false`，最终决策保持 `NEED_MORE_SINGLE_ERROR_PRODUCTION_EVIDENCE`。
- C1/C2/C3 的 Gamma reference rows 在方法族校验修复后可验收；formal 汇总共有 180 条 `evaluable` Gamma rows。C4/P2/PKR 共 72 条 reference rows 为 `NOT_EVALUABLE`，对应 production 日志中的 `scan beam quality gate failed: valid=0/1 required=1 min_ratio=0.95`，不能用于 Robust DDC-RB/physical+robust 归因。
- 每个 case 的 causal triplet gate 为 `passed`，但 A0/PK/PKR residual closure 因 PKR CSI tap 缺失保持 `NOT_EVALUABLE`；valid-cut denominator、四层 false-alarm waterfall 和 ID-switch provenance 也未达到可宣称算法结果的门槛。
- 本目录只保留六个 compact 文件；完整 production/BIN/IQ/NPY/NPZ/PNG 与 branch 审计材料在 Git 外的独立审计目录中。

## Delay-stage questions

- 1_robust_ddc_production_f1_f2: `NOT_EVALUABLE`
- 2_current_vs_ordinary_subtraction: `evaluable`
- 3_ddc_fast_time_delay_clutter_recovery: `NOT_EVALUABLE`
- 4_ddc_range_angle_position_velocity_track: `NOT_EVALUABLE`
- 5_blind_physical_delay_recovery: `NOT_EVALUABLE`
- 6_physical_plus_robust_complementarity: `NOT_EVALUABLE`
- 7_known_delay_to_a0: `NOT_EVALUABLE`
- 8_target_preservation: `NOT_EVALUABLE`
- 9_false_alarm_waterfall: `NOT_EVALUABLE`
- 10_delay_class: `NOT_EVALUABLE_until_single_error_closure`
- 11_channel_delay_research_value: `NOT_EVALUABLE_without_formal_delay_matrix`
- 12_servo_velocity_gate: `CLOSED`
- 13_coupled_physical_state_gate: `CLOSED`
- 14_ai_gate: `CLOSED`

原始 simulator/production 输出保留在 compact 目录之外。
