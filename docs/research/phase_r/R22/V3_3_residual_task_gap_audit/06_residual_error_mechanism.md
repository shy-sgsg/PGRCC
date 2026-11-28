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

全十周期位置误差现可通过 `target_associations.csv` 中保存的 evaluator `position_error_m` 评价。四方法共同机会上，M1 相对 PK Known 的位置 RMSE 高 `18.387 ± 4.503 m`；Huber 高 `24.940 ± 4.507 m`；Fixed D3 高 `32.447 ± 2.191 m`。同一组 12 个 seed 中，Huber、M1 均比 Fixed D3 位置误差低，M1 又比 Huber 低，但仍未闭合到 PK。该复算与归档 own-success position metric 有 576/576 的 case×method parity。

在唯一可由 compact ZIP 重建完整 Detection→truth 参数 lineage 的 period 8、四方法共同成功机会中，M1 相对 PK 的位置 RMSE 高约 21.35 m、方位 RMSE 高约 0.01412°；斜距和折叠 Doppler差异较小。Huber 的位置残差也高于 PK。完整十周期方位、斜距和折叠 Doppler共同成功仍不可评价。

period 8 子集含 1,982/2,880 个机会，参数 Detection 产品只覆盖 period 8。位置全周期共同集含 19,682/28,800 个机会。两类共同成功比较的机会定义和覆盖范围不同，不可混合。归档其他十周期 own-success 参数 RMSE使用各方法不同的成功样本集合，只可作保存摘要，不能替代共同机会比较。故“检测恢复接近 Known”不能概括成“全部任务参数已恢复”。

## 物理解释边界

12 个 seed 的时延残差与任务参数残差并存，足以指出后续确认实验应保存估计输入、估计值、校正后物理量和共同任务机会；但不足以将角度/位置残差唯一归因于时延估计偏差、增益拟合、CSI、检测选择或轨迹输出。

当前 compact ZIP 没有完整 target-only 复数响应、除位置 evaluator association 外的全部周期 Detection lineage 或逐周期 TrackManager debug。不得用 CSI ROI summary 代替目标专属复数幅相，也不得把 0.787703 之类的复数闭合误差解释为目标能量损失。

机器可读数据：[`delay_estimation_by_seed.csv`](evidence/delay_estimation_by_seed.csv)、[`delay_estimation_summary.csv`](evidence/delay_estimation_summary.csv) 和 [`parameter_fourway_common_success_by_seed.csv`](evidence/parameter_fourway_common_success_by_seed.csv)。
