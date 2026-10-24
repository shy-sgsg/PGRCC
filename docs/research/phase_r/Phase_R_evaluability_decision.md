# Phase R 可评估性裁决（R5）

日期：2026-10-22。此裁决只重解释 R3/R4 已保存证据；没有新 CUDA、seed、AI 或生产算法改动。

## 总体裁决

Phase R 仍是 `INCONCLUSIVE`（面向整体 GMTI 主瓶颈/论文主线）。R3 提供一个受控 `+4 ns` 误差方向性证据；R4 是零失配、单 seed 方法筛查。二者均不能建立 AI 必要性，也不能据此把主线转到低速保护或复杂杂波模型。

| 议题 | 可保留结论 | 不可声称 |
|---|---|---|
| 未知误差造成的任务损失 | R3 的 +4 ns Current B 相对 A，在 seed 101/202/303 的角度、TrackManager 地速、位置 RMSE 均更差；H1 仍 3/3 period 命中 | 通用机载误差损害、统计显著 Pd/Pfa 下降、radial-velocity RMSE |
| Known correction 恢复 | C 与 A 的 F1/F2 输入在冻结 `1e−3` 相对复 RMS 内闭合；C 相对 B 的三项 RMSE 降幅约 89.5% / 86.0% / 89.6%（三 seed 中位数） | 全部状态、速度、杂波与真实 airborne 系统均可恢复；统计显著性 |
| 传统盲估计 D | seed 101 估得 `3.409965 ns`（真值 +4 ns），任务 RMSE 比 C 高约 `4.11× / 2.57× / 4.04×` | 跨 seed 传统估计性能上限或 AI 优越性 |
| A0/C 首个分歧级 | 同一 fusion-cache 路径、F1/F2 与 CSI 区域标量近闭合；CFAR hits/detection counts 不同 | 精确首个处理 stage；逐格 CSI `.npy` 缺失使其不可识别 |
| R4 低速 P5 | 0.5/1 m/s 局部活动带背景 CA ROI 无交集；配对 H0 target-ROI SCNR 行仍可用 | 将 P5 `incomplete` 等同于全部低速 target metric 失败或 CSI 无效 |
| R4 H0 CFAR | 分支级 target-free false-CUT fraction 为 2.9937% / 3.2415% | empirical Pfa=`1e−6`、unique union、检测/cluster/track 虚警率 |

## 状态标签

- `IMPLEMENTED`：生产路径的 R3 修复、R4 runner 子集/前置面积杂波 gate、逐期 P5 evaluator 均已存在。
- `EXECUTED`：R3 三 seed A/B/C（seed101 另有 D）真实 CUDA Production；R4 seed101 八 case、120 Production branch 执行。
- `EVALUABLE`：R3 配对输入闭合、当前已报告 RMSE、每期分支 CFAR hit fraction；R4 面积杂波、配置/几何、branch-specific H0 false-CUT fraction、有效的配对 target SCNR 子集。
- `NOT_EVALUABLE`：R3 精确首个差异级与逐 cell union；R4 低速背景 CA ROI、跨 branch unique-hit union、cluster/detection/track-level calibrated false alarm、单 seed 之外的 R4 方法排名。
- `PERFORMANCE_VERIFIED`：仅限 R3 明示的三 seed、单目标、单速度/SNR 的任务 RMSE 方向性；R4 整体 `false`，没有统计显著性或泛化验证。

## 决策问题

1. **未知通道误差是否确实降低 GMTI 性能？** 在 R3 这个 `+4 ns`、单目标 6.7 m/s、30 dB 的受控场景中，三 seed 的 angle/ground-speed/position RMSE 都显示退化；目标命中数仍为 3/3。范围有限，但方向比目前其他机制的直接证据强。
2. **已知物理校正恢复多少？** 三 seed 中位数相对 B 的上述 RMSE 下降 89.5% / 86.0% / 89.6%；A↔C 输入闭合通过。误差单位按 RMSE 比较，不解读为总体 Pd/Pfa 提升。
3. **传统盲估计落后多少？** seed101 D3 相对 C 的 angle/speed/position RMSE 倍数为 4.11/2.57/4.04；仅一个 seed，不能外推。
4. **是否继续未知物理状态估计？** 可以继续窄化的未知时延可观测性/确定性估计验证；优先确认 A0/C 首个差异级，再判断盲估计器残差是否任务相关。AI 入口条件未满足，不训练。
5. **是否转低速保护或复杂杂波？** 暂不转主线。R4 低速 background ROI 的问题是支持域不重叠；R4 H0 的高 branch hit fraction 提高了杂波/阈值审计优先级，却还不能证明根因或比较算法。

## 下一步门

只保留一个新实验方案，见[Next minimal experiment plan](Next_minimal_experiment_plan.md)。在其输入 hash gate 或 Production/物理闭合 gate 失败时停止；不得扩 seed、调低门槛或改做 AI。R4 residual closure 对本次零时延条件为 `NOT_APPLICABLE`，原通用 evaluator 的 `NOT_EVALUABLE` 仍保留作审计记录。
