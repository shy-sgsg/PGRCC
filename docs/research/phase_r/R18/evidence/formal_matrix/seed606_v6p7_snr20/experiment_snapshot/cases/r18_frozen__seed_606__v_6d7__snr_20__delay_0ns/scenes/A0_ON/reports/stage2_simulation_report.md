# 阶段二统计杂波纯仿真报告

## 新系统参数

- fc: 16 GHz
- Br: 50 MHz
- fs: 60 MHz
- Tr: 130 us
- PRF: 1300 Hz
- DDC_len: 11820
- DDC_acquired_len: 11820
- FFT_len: 12288
- pc_crop_start: 3864
- pc_crop_len: 4096
- beam_count: 61
- generated_beam_subset: 50..50
- pulse_num: 130
- scan_min_deg: -60
- scan_step_deg: 2
- beam_width_deg: 2.28

- scan_mode: electronic
- servo_angle_error_enabled: false
- servo_true_minus_reported_deg: 0

- platform_height_m: 6000
- platform_speed_mps: 60

- platform_velocity_true_mps: 60
- platform_velocity_reported_mps: 60
- new_protocol_velocity_source: position_delta

## 场景配置

- scene_mode: full
- random_seed: 606
- area_model: continuous_texture
- area_scatterers: 0
- area_temporal_correlation_rho: 0.98
- area_rho_lag1_measured: 0.979977053458
- area_rho_at_CTDR_lag: 0.960345053969
- area_ctdr_lag_pulses: 2
- area_correlation_time_ms: 38.0756280404
- area_mean_power_measured: 0.0227469995924
- area_power_cv_measured: 1.97777211621
- strong_scatterers: 0
- line_scatterers: 0
- single_or_other_scatterers: 0
- continuous_area_packets: 390
- continuous_area_samples: 1952655
- continuous_area_precompressed_packets: 0
- continuous_area_raw_lfm_packets: 390
- output_signal_domain: raw_lfm
- thermal_noise_enabled: true
- thermal_noise_power: 0.001
- target_enabled: true

## 生成统计

- packets_written: 390
- scatterer_echoes: 0
- scatterer_samples: 0
- target_pulses_injected: 390
- target_samples_injected: 3042000
- max_abs_component: 28.4645652771
- mean_noise_power_per_complex_sample: 0.000999776808015
- has_nan: false
- has_inf: false
- generation_time_ms: 13993.682772

## 当前局限

- 第一版平台模型为 ideal_straight，真实 POS/姿态尚未接入。
- LFM rect 延迟采用 `0 <= dt < Tr`，仍需用算法脉压结果标定峰值固定偏移。
- 检测评价和航迹评价文件已预留，当前尚未自动解析 GMTI 检测输出。
