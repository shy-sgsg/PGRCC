# R12 阶段方向裁决

日期：2026-10-24。依据 R12 seed404 Native CUDA pilot；结论边界为单一 `+4 ns` C2 时延场景、一个 seed、三个目标机会。

## 阶段结论

1. **未知通道误差会降低本场景的 GMTI 检测表现。** B 的 3 个冻结目标机会均未在关联门内检测；A0 为 2/3。B 的 H0 false detection 行为 33、false confirmed tracks 为 6，均高于 A0 的 27 和 3。单 seed 不能外推总体 Pd/Pfa。
2. **Known physical correction 恢复了输入与任务表现，但只回到 A0 水平。** F2 相对 A0 的 RMS 误差在 ON/OFF/TO 分别为 `8.73e-5`、`9.19e-5`、`7.14e-8`；A2 的检测为 2/3，和 A0 一致，未超过 A0。
3. **传统盲估计尚未达到 Known correction。** 同输入 D3 的估计误差为 0.4119 ns，M1 为 0.2406 ns，R9 固定校正为 0.5900 ns。M1 比同输入 D3 的绝对误差少 0.1713 ns，但其 F2 输入相对 A0 仍差 2.684%；固定 D3 差 6.372%。三者都未闭合到 Known 的 `1e-3` 门限。M1 和固定 D3 在本 seed 都恢复 3/3 检测，但参数 RMSE 不一致：M1 角度/位置优于固定 D3，地速更差。
4. **继续研究未知物理状态估计有依据，AI 尚无必要性证据。** 下一步应在冻结 M1、不改模型门的前提下做少量独立 seed 复验，并验证几何误差和相对频响模型失配。若这种确定性估计稳定地落后于 Known upper bound，再判断盲估计瓶颈。当前不训练 AI、不开发 Router、不做六参数联合估计。
5. **当前证据不支持转向低速目标保护或复杂杂波建模。** 本次不是低速或复杂杂波敏感性设计；H0 有效面积杂波虽已确认存在，但本实验不能比较这些方向的任务收益。

## 状态与限制

- `IMPLEMENTED`：M1 物理匹配估计器与校正输入准备实现。
- `EXECUTED`：合成验证及 seed404 的 15 条 Native CUDA Production 分支均完成；每条件含 ON/OFF/TO。
- `EVALUABLE`：面积杂波、CSI、输入闭合、正确检测机会、H0 描述计数、目标保持和可匹配参数误差可评估。unique union CFAR hit-cell、统计 Pfa、径向速度 RMSE 不可评估。
- `PERFORMANCE_VERIFIED`：否。一个独立 seed 和每条件三个目标机会不支持总体性能或显著性结论。

机器证据、逐分支文件与复现说明见[紧凑证据包](evidence/r12_physics_matched_delay_20261024/)；完整本机运行目录为 `outputs/phase_r_r12_physics_matched_delay_20261024/`。阶段到此停止，后续 seed 复验需由用户另行启动。
