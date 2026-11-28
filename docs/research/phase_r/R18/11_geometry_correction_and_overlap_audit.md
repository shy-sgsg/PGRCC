# R18 几何更正与目标—杂波交叠预注册

状态：`PRE_REGISTERED`。本附录在修正后的 CUDA pilot 和正式矩阵运行前形成。旧配置、旧快照及其结果均保留为历史证据，不回写旧结果。

## 发现的几何错误

原冻结矩阵记录了 `range_m=85000` 和 `expected_bin=2048`，但这两个字段控制不同对象：`range_m` 配置连续面积杂波窗口中心；`expected_bin` 通过 `beam_bin_azimuth_offset` 初始化目标。Stage2 的目标初始化源码使用：

```text
R = c/2 × (sample_delay + (range_crop_start + expected_bin) / fs)
```

运行时参数为 `sample_delay=488 us`、`range_crop_start=3864`、`fs=60 MHz`、`c=299792458 m/s`。因此 `expected_bin=2048` 对应参考斜距约 `87919.139 m`，不是 85 km。历史 seed606/seed707/seed808 真值分别显示目标跨三个 period 约处于 `87.917–88.453 km`，而有效面积杂波只注入 `84.5–85.5 km`。这 12 个原矩阵场景在距离维度均无目标—面积杂波交叠；原有 9/9 目标命中不能作为目标处于面积杂波中的任务表现证据。

## 修正配置

新增配置 [`phase_r18_native_cuda_geometry_corrected.json`](../../../../configs/research/phase_r18_native_cuda_geometry_corrected.json)，不修改原冻结配置。保留 seed、目标速度、SNR、时延、波形、功率、杂波窗、方法和 Production 处理链，仅将 R18 pilot/formal 的目标初始化改为 `expected_bin=880`，并使用 `working_point=r18_geometry_corrected` 标识新运行。该 bin 对应参考斜距约 `85001.155 m`。pilot 先验证实际逐脉冲真值；通过后才运行 3 seed × 2 speed × 2 SNR 的 12 场景矩阵。

由旧真值中观察到的跨 period 距离变化，修正后预计 0.5 m/s 三个 period 的目标都落入 84.5–85.5 km 面积杂波窗；6.7 m/s 的末 period 可能略超出窗上界。正式报告按实际真值逐 period 判定机会；没有范围交叠的 period 保留在矩阵中，标记为无目标—面积杂波共同机会，不删样本、不移动目标或杂波窗，也不据此计算目标处于杂波时的性能。

## 目标 ROI 与经验杂波 Doppler 支持

每个 corrected case、period 使用同一 production branch 产生的目标真值、运行时 `fa_axis`/`range_axis` 与目标自由 `C0_not_applicable_OFF` 输入：

1. 目标中心由 `truth_pulse.csv` 中该 period 的真值斜距和 `af_total_truth_hz` 映射到该 period 的实际 range/Doppler 轴。目标 ROI 固定为真值中心周围 ±2 range bins、±2 Doppler bins；这与现有 CSI tap manifest 的冻结 ROI 半宽一致，不按结果调整。
2. 物理杂波支持从 OFF/H0 的 CSI 前 `before_power` 图估计，面积窗固定为 84.5–85.5 km。逐 Doppler bin 计算面积窗平均功率，并减去两个固定窗 `83.5–84.3 km`、`85.7–86.5 km` 的合并平均噪声功率；缺少任一噪声 sideband 轴覆盖或净面积能量非正时，该 period 标记 `NOT_EVALUABLE`。
3. 经验 clutter Doppler support 定义为净面积能量从高到低累计达到总正净能量 95% 所需的最小 Doppler-bin 集合。报告每 period 的完整功率谱、support 边界、净能量和目标 ROI 命中 support 的 Doppler bins，不只报告二值交叠。
4. 一个 period 的共同物理机会须同时满足：目标 5×5 ROI 的 range 轴覆盖 84.5–85.5 km，且目标 ROI 的 Doppler bins 与 H0 经验 clutter support 相交。按 period 报告 range、Doppler、joint 三种交叠状态；只有 joint opportunity period 进入目标处于面积杂波中的目标保持汇总。

本场景 PRF 为 1300 Hz、每 period 130 pulses，名义 Doppler 栅格间距为 10 Hz。几何真值提示 0.5 m/s 与静止地杂波 Doppler 相近、6.7 m/s 相对远离；这只是预期机制，不替代上述 H0 实测 support。所有 target response、检测和参数误差结论均按实际 joint opportunity 报告；不得把无交叠 period 当成低速目标抗杂波证据。

## 执行门与历史结果

- corrected pilot 必须在每个 period 确认面积散射 packet/sample、有效功率、目标真值距离单元和 Doppler、H0 输入身份、Production 输出及 range/Doppler/joint 交叠审计；任一关键输入或轴缺失时停止扩展正式矩阵。
- pilot 通过后按原 12 个 seed/speed/SNR 组合依次执行。原 `expected_bin=2048` 结果只用于说明设计错误及审计执行历史，不纳入几何修正矩阵的性能汇总。
- 所有范围、support、目标 ROI、共同机会和评估分母按本附录先冻结；不根据 corrected pilot 的方法胜负修改门槛。
