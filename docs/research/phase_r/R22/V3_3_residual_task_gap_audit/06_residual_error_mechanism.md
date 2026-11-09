# 06｜确定性时延估计的残差

## seed 级估计误差

对 12 个 seed 各自的 12 个 `+4 ns` case 求均值后，估计值和有符号残差如下。残差定义为 `估计时延 − 注入真值`；负值表示低估。结果只作 screening 描述。

| 方法 | 平均施加校正（ns） | 平均有符号残差（ns） | seed 间 SD（ns） |
|---|---:|---:|---:|
| Fixed D3 | 3.4100 | −0.5900 | 0.0000 |
| Huber D3 ON | 3.5445 | −0.4555 | 0.0690 |
| M1 Physics Matched | 3.6658 | −0.3342 | 0.0681 |

固定 D3 的估计来自历史静态校正；Huber 与 M1 使用 target-present raw C1/C2 观测，M1 还拟合常量复增益。三者均有非零估计残差，但本筛选没有形成其残差对检测率有可辨任务损失的证据：核心估计方法的命中率均接近 PK Known。

## 任务参数残差

在唯一可由 compact ZIP 重建 Detection lineage 的 period 8、四方法共同成功机会中，M1 相对 PK 的位置 RMSE 高约 21.40 m、方位 RMSE 高约 0.01416°；斜距和折叠 Doppler差异较小。Huber 的位置残差也高于 PK。固定 D3、Huber、M1 均未在这些参数指标上闭合到 Known。

该共同成功子集含 1,982/2,880 个机会，参数检测产品只覆盖 period 8。归档已有的十周期 own-success 参数 RMSE 使用各方法不同的成功样本集合，只可作保存摘要，不能替代共同机会比较。故“检测恢复接近 Known”不能概括成“全部任务参数已恢复”。

## 物理解释边界

12 个 seed 的时延残差与任务参数残差并存，足以指出后续确认实验应保存估计输入、估计值、校正后物理量和共同任务机会；但不足以将角度/位置残差唯一归因于时延估计偏差、增益拟合、CSI、检测选择或轨迹输出。

当前 compact ZIP 没有完整 target-only 复数响应、全部周期的 Detection lineage 或逐周期 TrackManager debug。不得用 CSI ROI summary 代替目标专属复数幅相，也不得把 0.787703 之类的复数闭合误差解释为目标能量损失。

机器可读数据：[`delay_estimation_by_seed.csv`](evidence/delay_estimation_by_seed.csv)、[`delay_estimation_summary.csv`](evidence/delay_estimation_summary.csv) 和 [`parameter_fourway_common_success_by_seed.csv`](evidence/parameter_fourway_common_success_by_seed.csv)。
