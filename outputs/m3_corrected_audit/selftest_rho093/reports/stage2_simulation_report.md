# 阶段二统计杂波纯仿真报告

## 新系统参数

- fc: 16 GHz
- Br: 50 MHz
- fs: 60 MHz
- Tr: 130 us
- PRF: 1300 Hz
- DDC_len: 512
- DDC_acquired_len: 512
- FFT_len: 512
- pc_crop_start: 0
- pc_crop_len: 512
- beam_count: 1
- generated_beam_subset: 1..1
- pulse_num: 8
- scan_min_deg: 0
- scan_step_deg: 2
- beam_width_deg: 2.28

- platform_height_m: 6000
- platform_speed_mps: 60

## 场景配置

- scene_mode: full
- random_seed: 20260910
- area_model: continuous_texture
- area_scatterers: 0
- area_temporal_correlation_rho: 0.93
- area_rho_lag1_measured: 0.866497114232
- area_rho_at_CTDR_lag: 0.750137166841
- area_ctdr_lag_pulses: 2
- area_correlation_time_ms: 10.5997440452
- area_mean_power_measured: 0.104258013115
- area_power_cv_measured: 1.42911229773
- strong_scatterers: 0
- line_scatterers: 0
- single_or_other_scatterers: 0
- continuous_area_packets: 8
- continuous_area_samples: 13576
- continuous_area_precompressed_packets: 0
- continuous_area_raw_lfm_packets: 8
- output_signal_domain: raw_lfm
- thermal_noise_enabled: false
- thermal_noise_power: 0
- target_enabled: false

## 生成统计

- packets_written: 8
- scatterer_echoes: 0
- scatterer_samples: 0
- target_pulses_injected: 0
- target_samples_injected: 0
- max_abs_component: 26.8737010956
- mean_noise_power_per_complex_sample: 0
- has_nan: false
- has_inf: false
- generation_time_ms: 30.522506

## 当前局限

- 第一版平台模型为 ideal_straight，真实 POS/姿态尚未接入。
- LFM rect 延迟采用 `0 <= dt < Tr`，仍需用算法脉压结果标定峰值固定偏移。
- 检测评价和航迹评价文件已预留，当前尚未自动解析 GMTI 检测输出。
