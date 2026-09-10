# 阶段二统计杂波纯仿真报告

## 新系统参数

- fc: 16.4721 GHz
- Br: 80 MHz
- fs: 60 MHz
- Tr: 150 us
- PRF: 1300 Hz
- DDC_len: 13056
- DDC_acquired_len: 13032
- FFT_len: 16384
- pc_crop_start: 0
- pc_crop_len: 4032
- beam_count: 30
- generated_beam_subset: 14..16
- pulse_num: 128
- scan_min_deg: -29
- scan_step_deg: 2
- beam_width_deg: 1.81871

- platform_height_m: 6000
- platform_speed_mps: 60

## 场景配置

- scene_mode: full
- random_seed: 20260831
- area_model: rayleigh_lognormal_texture
- area_scatterers: 0
- area_temporal_correlation_rho: 1
- strong_scatterers: 0
- line_scatterers: 0
- single_or_other_scatterers: 0
- continuous_area_packets: 0
- continuous_area_samples: 0
- continuous_area_precompressed_packets: 0
- continuous_area_raw_lfm_packets: 0
- output_signal_domain: raw_lfm
- thermal_noise_enabled: false
- thermal_noise_power: 0.01
- target_enabled: true

## 生成统计

- packets_written: 1152
- scatterer_echoes: 0
- scatterer_samples: 0
- target_pulses_injected: 384
- target_samples_injected: 2303686
- max_abs_component: 25459
- mean_noise_power_per_complex_sample: 0
- has_nan: false
- has_inf: false
- generation_time_ms: 3792.61

## 当前局限

- 第一版平台模型为 ideal_straight，真实 POS/姿态尚未接入。
- LFM rect 延迟采用 `0 <= dt < Tr`，仍需用算法脉压结果标定峰值固定偏移。
- 检测评价和航迹评价文件已预留，当前尚未自动解析 GMTI 检测输出。
