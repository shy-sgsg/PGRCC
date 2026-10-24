# R13 CFAR 与速度评价审计

日期：2026-10-25。运行输入身份及 map-backed 机器审计在 [R13 紧凑证据包](evidence/r13_baseline_cuda_20261024/README.md) 的 `analysis/` 中。

## CFAR 结果链

R7 的 `_cfar_counts()` 已修复为按 `(period_id, beam_id)` 汇总唯一 union map，保留逐期行和跨期 valid-CUT 总数；R13 evaluator 直接检查生产最终 union `.f32` map，不从两个 branch 计数推断 union。每张图必须同时与尺寸/边缘有效 CUT、两条 component geometry 记录、日志 `[CFAR][SUMMARY]` 命中数闭合。

本次两场景、四控制/方法、三角色、三 period 共 72 张图，审计全部通过。每图尺寸为 130×4096，有效 CUT=527,280；每三期每分支分母=1,581,840。A0 与 C0 在 18 张对应 map 上 SHA 完全一致。

OFF 的描述统计（两个速度场景复用同一 seed101 C+N 背景）：

| 方法 | final-union hits / valid CUT | observed fraction | OFF detections | Confirmed current-frame track-period associations |
|---|---:|---:|---:|---:|
| C0 Current | 83,985 / 1,581,840 | 5.3093% | 406 | 171 |
| C2 SCC | 76,033 / 1,581,840 | 4.8066% | 393 | 163 |
| C3 Robust DDC | 64,365 / 1,581,840 | 4.0690% | 405 | 169 |

这些是单次 H0 观测命中比例和错误输出计数，不是经验 Pfa。配置的 CFAR Pfa 为 `1e-6`，不能用来代替实际 Pfa。R13 无 truth-filtered false-CUT estimator，也没有估计虚警概率。

## 地杂波、F1/F2 与方法可比性

场景平台高 6,000 m、地面 0 m，地杂波斜距窗 84.5–85.5 km，对应地距约 84.287–85.289 km，物理可达。A0_ON 是该配对背景的 Stage2 writer：报告记录 continuous area packets=390、samples=1,952,655、实测 mean power=0.0223353（配置 0.02）、lag-1 rho=0.980007、CTDR lag-2 rho=0.960408、power CV=2.04919、平均噪声功率约 0.001。A0_OFF 复用 A0_background，故它自己的 Stage2 report 写“本次注入 0”；OFF 每期输入 SHA 与 A0_background 对应期完全相同，证明它读取的就是含面积杂波的 C+N 背景。ON 与对应写出输入在零时延条件下逐期闭合；完整关系见 `analysis/paired_input_audit.csv`。

Runtime XML 确认 4-channel protocol IQ，通道 (1,3)、(2,4) 融合为 F1/F2 后进入 Production CSI。源自 CUDA `csi_metric_tap` 的两通道功率 maps 给出生产对齐/FFT/DBS/P38 后、CSI adapter 前的 F1/F2 active-support 输入功率；`analysis/f1f2_input_audit.csv` 对 18 个 scene/role/period 组比较 A0/C0/C2/C3，F1 和 F2 map SHA 每组均只有一个值。C2/C3 都在同一生产 F1/F2 上工作，适用于同信息类别内比较。

C2/C3 的 Mode-A 在目标自由 OFF 上拟合 Gamma，再通过 `reference_gamma_artifact_fixed_apply` 固定应用于 ON/TO；36 个 adapter 行状态均为 OK，truth-used=false，无 Current fallback。C0 不读取 paired OFF 标定参考。因此 C2/C3 互相比是相同信息条件；C0 对比是现实生产 Current 与增加 OFF 参考可用性的能力对比，不作纯算法因果归因。A0 和 C0 的最终 map、CSI tap SHA 与 target ROI 值逐项完全相同。

## Doppler 与速度

Production Doppler 轴每图 130 rows、10 Hz/bin，起始轴频率约 −1228.8201 Hz。任务匹配采用 2 range-bin、2 circular Doppler-bin 固定门；速度误差只在匹配机会计算。折叠 Doppler RMSE可评价；radial velocity ambiguity 本轮仍 unresolved，因此 radial RMSE=`NOT_EVALUABLE`。TrackManager ground-speed RMSE只基于当前确认且当前周期命中的两个 target track-period 机会（而不是 3 个 truth period）；该指标不能替代径向速度。

## 审计限制

- 运行结束后不把完整 `.f32`、`.npy`、Stage2 IQ 纳入 Git；逐文件 SHA、命中计数、target ROI samples、CSI maps SHA 和可重建的命令在证据包中。
- R13 的地图审计是软件/数值闭合证据；不单独证明虚警分布或统计性能。
- 本次运行通过所有现有质量门，未调低 CFAR 门限或目标匹配门。
