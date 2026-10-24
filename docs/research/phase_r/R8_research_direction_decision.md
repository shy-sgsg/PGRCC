# R8 阶段研究方向裁决

日期：2026-10-23

裁决依据：seed101/202/303 的 A0/B/C 配对任务结果、seed202/303 本机 CUDA Production 与 CFAR 审计、Known Correction 物理闭合。三个 seed 支持受控场景内的重复性判断；不构成总体统计显著性结论。

## Q1–Q7 裁决

### Q1：修复 GO-CFAR 后，+4 ns 失配是否在不同 seed 上持续造成正确目标检出损失？

**是，在本次固定仿真配置和冻结关联门内三个 seed 都观察到损失。** A0/B/C 正确 detection 机会分别为 `8/9、0/9、8/9`；每个 seed 的 B 都是 `0/3`，A0/C 分别为 `3/3、2/3、3/3`。seed202 的 A0 自身有一个漏检，说明场景/目标候选也会影响绝对检出率。此结论只适用于本次通道2 `+4 ns` 条件，不外推为任意 airborne 时延分布的普遍效果。

### Q2：已知物理逆校正是否持续恢复检测与参数估计？

**检测计数恢复到 A0 水平。** C 比 B 多 8 个正确 detection，补回本次全部 `8` 个 A0−B 计数差；A0 与 C 各为 `8/9`。正确 confirmed-track 关联也由 B 的 `0/9` 回到 A0/C 各 `5/9`（seed101/202/303 为 `2/3、1/3、2/3`）。正确关联目标上的角度、位置和地速误差在 A0/C 闭合；B 没有正确 detection，参数 RMSE 为 `NOT_EVALUABLE`。三 seed 的 F1/F2 最大相对复 RMS 约 `9.56e-5`，低于冻结 `1e-3` 闭合门。

### Q3：主要可观测损失发生在哪一层？

证据显示损失不是“目标在 CSI/CFAR 前完全消失”：B 在 seed202/303 的目标 5×5 邻域仍分别有 `41/44` 个 CFAR hit，但三个 period 的最近候选 Doppler 偏差均在冻结 ±2-bin 门外。目标关联失败出现在候选形成/选择至 detection 的阶段；TrackManager 因没有门内目标 detection 而没有正确目标航迹关联。CSI 层也有变化：B 的 H0 cancellation 比 A0 低约 `0.44–0.49 dB`，输入融合 F2 功率降低约 `0.22–0.25 dB`。因此 CSI/CFAR 输入变化与门外候选同时存在；当前证据不能将唯一根因拆分到某个内部算子。

### Q4：损失是稳定物理后果，还是高度依赖场景与关联？

**损失方向在三个独立 seed 上一致，损失大小和候选位置仍依赖场景。** B 的正确 detection 均为 `0/3`，但门外 Doppler 偏移 seed202 为 `[-6,+24,+25]`、seed303 为 `[-6,+23,+29]` bins；A0 自身在 seed202 也有一个 period 未过冻结门。故可称为本实验设置下的重复因果效应，不能称作与场景无关的固定偏移或通用概率结论。

### Q5：是否已确定双通道 GMTI 的最大可恢复性能瓶颈？

**没有。** R8 只测一种时延误差、一个目标/速度配置和一种连续面积杂波设置；未比较其他失配、低速 sweep 或统计校准 Pfa。结果证明未知时延是当前受控场景中的可恢复性能贡献因素，不足以认定它是系统最大瓶颈。

### Q6：是否具备进入传统盲时延估计器 D3 多 seed 验证的条件？

**具备进入 D3 方案与代码审查的科学依据，尚无可执行的合格 D3 估计器。** B/C 的重复损失/恢复满足继续研究 target-free 确定性时延估计的前置动机；本阶段 D 条件为 `NOT_AVAILABLE`，未运行，因此 D 与 Known Correction 的差距未知。下一阶段需先定义允许观测、真值隔离、失败判据并审查估计器，再安排盲验证。

### Q7：是否存在充分证据证明 AI 必要？

**`AI_NOT_JUSTIFIED`。** 当前没有盲估计误差、确定性估计残差或跨场景失败证据。Known Correction 是注入真值的 evaluator-only 上限，不能证明 AI 必要，也不能替代盲估计结果。

## 三条候选主线的证据与优先级

| 方向 | 当前证据等级 | 研究价值与局限 | 优先级 |
|---|---|---|---:|
| C：未知系统物理失配 | 中等、受控因果重复 | 三个 seed 的 +4 ns 任务损失方向一致，Known Correction 恢复计数且输入闭合；尚缺盲估计器、其他误差参数和真实 airborne 分布。 | **1**：先做 target-free 确定性时延估计的可观测性与代码审查，再设计 D3 多 seed 验证。 |
| B：复杂杂波与检测统计 | 初步线索 | H0 仍有候选，B 的 H0 unique union hits 增加；没有统计 Pfa 校准、杂波模型 sweep 或检测阈值敏感性因果试验。 | **2**：作为后续竞争瓶颈审查，不据本轮宣称 Pfa 变化。 |
| A：低速目标保护 | R8 未检验 | 单一 6.7 m/s 配置不是低速 sweep，也没有低速分层目标保护对照。 | **3**：本证据不足以把主线转到低速目标保护。 |

## 阶段结论与停止

- `IMPLEMENTED=true`、`EXECUTED=true`、`EVALUABLE=true`、`PERFORMANCE_VERIFIED=false`。
- R8 支持继续研究未知物理时延的确定性盲估计；D3 尚未实现/执行，性能差距 `NOT_AVAILABLE`。
- 当前没有 AI 必要性证据；不训练 AI、不开发 Router、不扩展六参数联合估计。
- **本阶段到此停止。** 仅建议下一阶段先完成 blind estimator 的观测边界与代码审查；不自动运行 D3 或启动 R9。

详见：[检出与任务级明细](R8_detection_causal_results.md)、[差异级分析](R8_mechanism_analysis.md)、[CUDA 执行记录](R8_native_cuda_execution.md)、[机器 manifest](evidence/r8_unknown_delay_multiseed_20261023/evaluation/r8_experiment_manifest.json)。
