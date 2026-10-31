# R22-06：阶段科研价值与方向评估

> **最新执行裁决（2026-10-28 11:34:54 UTC）：** V2 screening 已在方法级可评估性 gate 停止。高信噪比目标条件下，C4 Mode-B ON reference 有一组 support=7 < 8；配对 OFF 为55，SNR20同组为25，SNR30目标 T010 位于该组。现有证据支持“ON侧盲参考在该样本局部支持不足”的机制解释，但仅一个 seed/一个失败 case，不能推断通用 SNR 阈值或长期主线。confirmatory、dense、bridge、H0 未启动。完整证据和待审查选项见 [05](05_resource_and_execution_plan.md) 与 [ChatGPT checkpoint 提示词](CHATGPT_REVIEW_PROMPT_SCREENING_CHECKPOINT_20261028.md)。

> **后续执行更新（2026-10-28）：** 本文下方裁决对应早期工程 pilot / V1A gate 阶段，不是当前 screening 总结。V2 seed1932001 的 0 ns 与 +4 ns 单 case 已通过冻结工程/evaluator 门，+4 ns 的 `PERFORMANCE_VERIFIED=false`；完整 V2 screening 已恢复执行。因此旧的“先停止、不执行 screening”只保留为当时的历史裁决。当前仍没有足够独立 seed 证据判断未知误差退化、Known recovery、盲估计差距、AI 必要性或方向切换。当前矩阵进度见 [05 资源与执行记录](05_resource_and_execution_plan.md)，独立审查入口见 [V2 screening ChatGPT 提示词](CHATGPT_REVIEW_PROMPT_SCREENING_CHECKPOINT_20261028.md)。

## 阶段裁决

**`INCONCLUSIVE / STOP_AT_ENGINEERING_GATE`。** 多目标 Native CUDA Production 输入路径、面积杂波配对和单周期任务评价已形成真实证据；PKR support gate 阻止完整 residual closure。R22 full screening 与确认实验没有执行。

## 对核心问题的阶段回答

1. **未知通道误差是否降低 GMTI 任务表现？** 在两个 +4 ns 工程 seed 的单周期、单 beam 20-target 观察中，C0 命中为 4/20 与 1/20，A0 分别为 17/20 与 16/20。方向一致，提示本设置下误差可能造成明显任务退化，但样本仅两个 seed，且目标周期/beam 很少，`PERFORMANCE_VERIFIED=false`。
2. **已知物理校正恢复多少？** seed900203 PK 为 16/20，和 A0 的 16/20 命中相同；参数 RMSE 也接近 A0。seed900202 未运行 PK，完整 Known correction 恢复率不能估计。必须补齐冻结的 CSI residual closure 后才可把“命中数相同”解释为物理闭合。
3. **盲估计距 Known 有多远？** seed900203 R9/Huber/M1 的命中分别 17/20、16/20、16/20；M1 的 angle/position RMSE 仍高于 A0/PK。一个 seed 的条件命中和参数记录不足以判定一般差距，且 PKR 不可评估。
4. **是否需要继续未知物理状态估计？** 作为 GMTI 候选问题仍有初步任务价值线索，但当前优先事项是补上 PKR 支持/残差评价链的工程闭合，而不是训练 AI 或扩展 estimator 方法矩阵。
5. **是否转向低速保护或复杂杂波？** 本次单一 2.5 m/s / 20 dB 配置没有对低速、杂波统计或密度做因果对照，不支持改换研究主线。
6. **AI 是否必要？** 未建立。确定性 Huber/M1 已能在本工程 seed 产生估计并得到描述性任务输出；真实恢复差距、跨 seed 规律和确定性方法上限尚未闭合。

## 唯一下一步建议

先请 ChatGPT 按 [独立审查提示词](CHATGPT_REVIEW_PROMPT.md) 审查本阶段；若用户决定继续，只授权一项离线 PKR 配置/代码契约审计，解释 `range_band_bins=6`、`min_support=8` 与实际 support_count 之间的关系，并提交不降低门槛的最小修复方案。该审计结束前不新增 CUDA seed、不执行 screening/confirmatory，不启动 AI。

研究总纲保持不变。本裁决只记录阶段状态和下一项候选工作，不自动改写长期研究目标。
