# Production Hierarchical Calibration Pilot

- Status: `completed`
- Decision: `NEED_MORE_SINGLE_ERROR_PRODUCTION_EVIDENCE`
- Production results require the recorded CUDA run and all downstream audit files.
- Empirical cell Pfa 仅用 truth-filtered `false_cut_hit_count / valid_h0_cut_count`；缺任一项即 `NOT_EVALUABLE`。`tested_cut_hit_fraction` 不是 Pfa，configured Pfa 也不是实测 Pfa。
- 目标保护只从 `OFF=C+N`、`ON=S+C+N`、`TO=S` 的 ON−OFF 与 TO 因果量报告，禁止用 ON power alone。
- 下游层级严格分开：CFAR → cluster → protocol detection → TrackManager track。
- Track evidence 使用 association/state/payload；现有 debug 无法解释 ID-switch 时标记 `NOT_IDENTIFIABLE_FROM_CURRENT_DEBUG`。
- ID-switch audit: `NOT_EVALUABLE`; limitation: `track_provenance_missing_or_empty`。
- A0/PK/PKR residual closure 在 estimator 归因之前检查。

## Delay-stage questions

- 1_robust_ddc_production_f1_f2: `NOT_EVALUABLE`
- 2_current_vs_ordinary_subtraction: `evaluable`
- 3_ddc_fast_time_delay_clutter_recovery: `NOT_EVALUABLE`
- 4_ddc_range_angle_position_velocity_track: `evaluable`
- 5_blind_physical_delay_recovery: `NOT_EVALUABLE`
- 6_physical_plus_robust_complementarity: `evaluable`
- 7_known_delay_to_a0: `NOT_EVALUABLE`
- 8_target_preservation: `causal_ON_minus_OFF_and_TO_required`
- 9_false_alarm_waterfall: `NOT_EVALUABLE`
- 10_delay_class: `NOT_EVALUABLE_until_single_error_closure`
- 11_channel_delay_research_value: `NOT_EVALUABLE_without_formal_delay_matrix`
- 12_servo_velocity_gate: `CLOSED`
- 13_coupled_physical_state_gate: `CLOSED`
- 14_ai_gate: `CLOSED`

原始 simulator/production 输出保留在 compact 目录之外。
