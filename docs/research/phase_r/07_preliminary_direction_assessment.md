# R3 初步研究方向评估

依据 [05 冻结实验设计](05_experiment_design.md) 和 [06 修复后 CUDA Production pilot](06_minimal_cuda_causal_pilot.md)。判断只适用于通道 2 `+4 ns`、单一 6.7 m/s 目标场景和当前 Production 链。它不是 Phase R 全研究问题最终结论。

## 研究问题回答

1. **未知通道误差是否确实降低 GMTI 性能？**

   在本次可比的有效面积杂波仿真中，是。+4 ns 当前处理 B 相对 0 ns A，在 seed 101/202/303 的角度 RMSE、跟踪地速 RMSE、位置 RMSE 全部恶化；H0 CSI cancellation 也低约 0.44–0.46 dB。每个 seed 目标 hit 都是 3/3，故本次未显示 target detection hit 数下降。SCNR 自适应代理量反向较高，说明不是所有观察量都一致，不能据此宣称整体链路普遍退化。

2. **已知物理校正能够恢复多少？**

   C 与 A 的融合 F1/F2 在 ON/OFF/TO 全部通过 `1e-3` 相对复 RMS 闭合。按三 seed 中位数，C 相对 B 将角度、跟踪地速、位置 RMSE 分别降低约 89.5%、86.0%、89.6%；最终误差为 `0.01318°`、`1.279 m/s`、`20.566 m`，均低于 B，也低于对应三 seed 中位数 A。杂波 cancellation 从 B 的 `5.132 dB` 回到 C 的 `5.590 dB`，约恢复至 A 水平。

3. **传统盲估计距离已知校正参考还有多少差距？**

   seed101 的传统 D3_Huber_weighted_LS 从 A1_OFF 估得 `3.409965 ns`，对已知 `4 ns` 有 `−0.590035 ns` 偏差。该 seed 的 D 相对 C 角度/跟踪地速/位置误差约为 `4.11× / 2.57× / 4.04×`。D 没有独立 seed 复验，这是初步差距，不是传统盲估计性能上限。

4. **是否有必要继续研究未知物理状态估计？**

   可以继续一个窄化、确定性的时延估计方向：本次误差造成的任务指标损失跨 seed 重复，已知逆模型能恢复，盲确定性估计则存在可量化残差。这满足“误差退化—已知校正恢复—盲估计残差”的前置证据。下一步应首先在独立 seed 验证 D，并明确估计支持与失败条件；不扩展到六参数联合估计，不训练 AI。AI 必要性仍未建立。

5. **是否应调整为低速目标保护或复杂杂波建模主线？**

   暂不能把其中任何一个定为唯一主线。本试验目标 6.7 m/s，H0 分支出现大量结构杂波 hit/false clusters，且径向速度输出处于 ambiguity unresolved；这使低速目标保护、虚警统计和复杂杂波建模值得作为竞争瓶颈进一步审查。但当前 H0 命中比例不是经校准的 Pfa，且单场景缺少不同目标速度/杂波分布对照；这些信号不足以推翻已观察到的通道时延因果损失。

## 阶段门控结论

- A/B/C Production、有效地面面积注入、CFAR valid CUT、CSI 输出和 A↔C 物理闭合均通过，因此本阶段允许且已经完成 202/303 的 A/B/C 重复。
- C 相对 B 对三项任务 RMSE 的方向在三 seed 一致；未知单参数误差估计方向暂不暂停。
- D 仅一个 seed 且明显落后 C；下一步若获准，应先复验并诊断传统盲估计误差来源，而不是训练 AI 或据此声称 AI 优越。
- 由于 Pd/Pfa 未完成统计校准，径向速度不可评估，且低速/杂波模式只有一个 operating point，仍需对“通道误差是否为核心任务瓶颈”保持限定判断。

## 建议的下一步（本轮不自动启动）

先请外部审阅者核对本报告和紧凑证据。若认可，再单独批准一个**只重复 D、沿用 A/B/C 已完成 seed 的 A1_OFF truth-free 输入**的小任务：优先在 seed 202、303 估计并记录 D3 输入身份、支持点、残差和恢复指标；不重跑 Stage2/Production A/B/C，不改阈值、不训练 AI。并行或后续应由用户决定是否补做 CFAR H0 false-cell 到 Pfa 的校准、径向速度解模糊审计，以及低速目标/杂波场景对照。只有盲估计限制在独立 seed 可复现且其余决定性误差来源已审查后，才重新讨论 AI 是否提供确定性估计之外的增益。

## 状态标签

`IMPLEMENTED`：相关代码/配置存在；`EXECUTED`：真实 CUDA Production 已运行；`EVALUABLE`：只对明确列出的指标成立；`PERFORMANCE_VERIFIED`：三 seed 的跟踪/定位 RMSE 方向重复。这里的 `PERFORMANCE_VERIFIED` 不表示显著性、Pfa/Pd、径向速度或真实 airborne 系统验证。

## Phase R4：E1/E4/E5 seed-101 对方向的更新

本节只解释 [06 的 R4 实际 pilot](06_minimal_cuda_causal_pilot.md)；R4 固定为 0 ns 通道误差，因此不重复验证或推翻上文 R3 的 +4 ns 结论。

| 问题 | R4 证据允许的结论 |
|---|---|
| 未知通道误差是否降低 GMTI？ | R4 没有通道误差，不回答该因果问题。R3 三 seed 的已知结果仍是唯一直接证据。 |
| Known physical correction 能恢复多少？ | R4 没有 Known-correction 条件；不更新 R3 的恢复比例。 |
| 传统盲估计距离已知校正还有多少差距？ | R4 没有盲估计条件；不更新 seed 101 D 与 Known correction 的既有差值。 |
| 是否需要 AI？ | 没有新证据支持 AI。R4 是零失配确定性方法筛查，且低速 P5 和汇总门未通过；不得据此启动 AI 或声称 AI 必要。 |
| 是否转向低速目标保护/复杂杂波建模？ | 两者仍是待判别的候选瓶颈，但 R4 不足以选定主线。低速 P5 背景 ROI 为空，不能当作低速抑制性能失败；命中 3/3 也受单 seed/三期和高 unmatched detection-record 比例限制。 |

### R4 gate 判定

- **已通过**：8 个 case 的物理可达连续面积杂波注入；120 个 CUDA Production 分支；ideal/none triplet 数值闭合；有真实目标 truth 和逐分支 CFAR valid CUT 记录。
- **未通过/不可评估**：0.5/1 m/s 的 P5 背景 ROI 为 0/30，2 m/s 为 20/30；聚合 CFAR false-hit/Pfa 因 hit-cell 只有 hashes、缺少唯一 cell union/truth join 而不可评估；residual-closure evaluator 要求本次冻结子集未运行的 PK/PKR。
- 因这些 gate 未通过，seed 202/303 没有运行，`PERFORMANCE_VERIFIED` 为否。所有结论限于 seed 101 的三期描述性结果；未下调 ROI 或检测质量门槛。

### 建议的下一步（尚未执行）

先修正审查契约而不是扩实验：明确 residual-closure gate 与 C0–C3 冻结子集是否兼容；保留可验证的 CFAR hit-cell 身份/真值 join；重新检查低速 P5 背景 ROI 对活动杂波 Doppler 支持的覆盖。若仍需比较 F1/F2 原始输入功率，须在新 pilot 中保留最小功率摘要，因为本轮可重建 `.bin` 已按授权清理、manifest 仅保留 size/hash。随后只重做受影响的 seed-101 evaluation gate；通过后再按预注册决定是否增加 seed 202/303。期间不训练 AI、不扩展六参数估计。
