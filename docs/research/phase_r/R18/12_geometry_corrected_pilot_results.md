# R18 几何修正 CUDA Pilot 结果

## 状态与范围

- `IMPLEMENTED`：几何修正配置、pilot runner 和分析脚本已存在。
- `EXECUTED`：seed 101、目标速度 0.5 m/s、SNR 30 dB、时延误差 0 ns 的 Native CUDA Stage2 与 Production pilot 已运行。
- `EVALUABLE`：面积杂波注入、输入配对、Production/CSI gate、目标与 clutter support 的共同机会、配对目标传递和描述性检测指标可评估。
- `NOT_EVALUABLE`：经验 Pfa、径向速度误差和逐像素 CSI map 最大差异。
- `PERFORMANCE_VERIFIED`: `false`。本 pilot 只用于几何与链路正确性及可行性判断，不支持统计显著性或算法收益结论。

## 运行身份

- 几何修正配置：[`phase_r18_native_cuda_geometry_corrected.json`](../../../../configs/research/phase_r18_native_cuda_geometry_corrected.json)，SHA-256 `14e2f6799b34c19fe9a6bc870b68a23417b3923412ceb5c094ebd90025fc6613`。
- 实验源码 commit：`839dde524d751c27fbd2cfb7debeea807009f9e6`。
- Stage2 可执行文件 SHA-256：`5dd0ac4e2536b8bcf2508cc53db8a30780eba75f40c025e13ffaa930f8ba7b9b`。
- Production 可执行文件 SHA-256：`3328d7c61bc36b6d3b906692e355c46c328150c0399fa457e6f871bcb79abaf0`。
- 分析运行使用脚本 SHA-256：`1fee2e5b501b428c88851d097d63ac7f70732432926b2bd9f61521347533fd8c`。
- 配置、逐 case/branch manifest、实际 XML、日志、CSV、图表、`.f32` 诊断和保留小型输出见 [`pilot evidence`](evidence/geometry_corrected_pilot_seed101_v0p5_snr30/)。

## 门控结果

Stage2 报告确认连续面积杂波实际注入 `390` 个 packets、`1,952,655` 个 samples；面积平均功率为 `0.0223353`，lag-1 相关系数为 `0.980007`，CTDR lag-2 相关系数为 `0.960408`，平均噪声功率约为 `0.001`。实际地面散射距离窗为 `84.287–85.289 km`，与配置的 `84.5–85.5 km` 有交叠。

目标真值 median range 在三个 period 分别为 `85.001`、`85.229`、`85.459 km`。目标真值中心 ±2-bin Doppler ROI 与 H0 的 95% 正净面积能量 support 分别交叠 5、5、5 个 bins；三个 period 均满足 joint opportunity。H0 C0 OFF support 大小为 24、25、25 bins，面积/噪声功率比约为 9,377、8,668、9,315。

C0–C3 的 OFF/ON/TO 生产输入 SHA 配对一致，CSI/CFAR 输入定义一致，36 个 branch inventory 项全部通过，未发现 truth-use 标记。完整机器可读 gate 结果见 [`analysis JSON`](evidence/geometry_corrected_pilot_seed101_v0p5_snr30/analysis/r18_geometry_corrected_pilot_analysis.json)。

## 结果

四种方法为 C0 Production Current、C1 ordinary complex subtraction、C2 SCC、C3 Robust DDC。H0 支持区指标是在 C0 OFF pre-map 冻结的同一 Doppler support × 84.5–85.5 km range mask 上，逐方法读取各自 OFF CSI 输出图计算。

| 方法 | H0 支持区 CSI 平均功率 | 相对 C0 | 配对目标幅度比 | 目标相位误差 (rad) | 正确检测 period | 最终检测数 | False associations* | Ground-speed RMSE (m/s) |
|---|---:|---:|---:|---:|---:|---:|---:|---:|
| C0 | 560,721.767 | 0.000 dB | 0.999999 | -0.004196 | 3/3 | 33 | 30 | 5.544 |
| C1 | 560,721.767 | 0.000 dB | 0.999999 | -0.004196 | 3/3 | 34 | 31 | 7.839 |
| C2 | 560,721.767 | 0.000 dB | 0.999999 | -0.004196 | 3/3 | 33 | 30 | 5.544 |
| C3 | 560,721.767 | 0.000 dB | 0.999999 | -0.004196 | 3/3 | 33 | 30 | 5.544 |

四种方法的 paired target amplitude ratio 均为 `0.99999854`，phase error 为 `-0.00419551 rad`，normalized residual RMS 为 `0.00409545`；target-only CSI retention 为 `+0.0160 dB`，output SCNR proxy 为 `18.0563 dB`。这些相同的标量均值不证明各方法的 CSI map 每个像素都相同；本轮保留的派生结果未计算或保存逐像素最大差值。

*False associations 不是 Pfa。经验 Pfa 缺少所需的 truth-filtered valid H0 CUT 分母，标为 `NOT_EVALUABLE`。径向速度误差同样不可评估；表中 ground-speed RMSE 只是当前任务 evaluator 的单 seed 描述值。所有指标均没有多 seed 置信区间。

分析 CSV 原有 `definition` 字段曾误写成“same output CSI map across methods”；分析代码实际逐方法读取各自的 OFF CSI 图，并在公共 C0 OFF support 上计算。现已更正该描述，数值没有重算或更改。执行时脚本身份保留在 analysis manifest 中，当前脚本仅修正此字段文本。

## 判断

本 pilot 证明修正后的低速目标几何与连续地杂波在三个 period 中确实重叠，且本次 CUDA Production/CSI 链路和 paired inputs 满足预设 gate。它没有施加非零未知通道误差，因此不能回答未知误差的任务损失、已知校正恢复量或盲估计与已知校正的差距；方法间也未显示可用此单 seed 结果确认的任务性能收益。

下一步应按既有几何修正预注册运行 3 seed × 2 speed × 2 SNR 的 12 场景矩阵，逐场景封存审计并清理可重建数组。当前结果不构成启动 AI 训练或 Router 的依据。
