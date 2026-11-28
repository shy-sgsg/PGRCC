# R22 screening support 失败：补充只读审计

- 审查快照：`95a8854b01b9af26e6e955d0871e130db81e2f6e`；主要实验源码：`5a60a514cf53cab9f23b17ab29992fd4485876fa`。当前 `src/production_calibration_adapter.cpp` 与该源码提交一致。
- 生产实现 `src/production_calibration_adapter.cpp:221–278` 对每个有效距离单元的 F2·conj(F1) 归一化为单位相位，计算相位中心，再以相位偏差门筛选。`support_count` 是筛选后保留的单元数；少于 8 返回 `insufficient_robust_support`。互谱幅度不直接作为相位中心的权重。
- 同 seed1932001、2.5 m/s、+4 ns、period 5、beam 50、az 52、距离组 1065–1197：SNR20 gamma CSV 为 support=25、phase coherence=0.202899567021942、status=OK；SNR30 为 support=7、phase coherence=0.11720003232345、status=NOT_EVALUABLE。二者来自相应 case 的 `production/C4_modeb_ON/result/production_calibration_reference_gamma.csv`。
- SNR30 的目标 T010 在该组内、同组 target-free OFF support=55；该配对现象与目标污染相符，但目标泄漏到多少个单元、中心相位如何变化及其因果贡献均未确认。较低 coherence 也不能单独区分目标污染与背景相位波动。
- 失败目录保留的 `paired_range_phase_raw_period050_beam050.f32` 与 CSI 对应文件是 `src/processOnePeriod.cpp:2674–2689` 或等价生产路径保存的距离相位校正向量，不应当作为该组筛选前 F1/F2 逐单元复数输入。gamma CSV 仅保存组级汇总，不能从中重建筛选前相位残差。
- 已记录 raw `.bin/.npy/.npz` 经 runner 清理；上述紧凑包不包含完整 raw IQ。若独立审查认为需要归因，应先提出可复现、同背景的目标 ON/OFF 逐单元复数诊断或受控反事实方案，明确数据生成与算子身份，再根据冻结规则决定重跑；不应以修改阈值或删除困难组代替验证。
- 本附记为源码和已保存数据的只读检查，不是新的 CUDA 实验或性能结果。原筛选矩阵维持 `GATE_FAILED`，性能 `NOT_VERIFIED`。
