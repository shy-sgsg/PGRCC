# R22-V4 物理效应与传统基线差距

日期：2026-11-23
证据来源：[41 号 P0 独立重分析](41_v4_p0_independent_evidence_reanalysis_20261123.md)、P0 本地 Native CUDA 归档及 V3.3 历史机会表。

## 1. 本阶段回答的科学问题

在固定的 seed1937003、20 dB、2.5 m/s、20 目标、10 周期和 +4 ns 场景中，未知时延是否与 GMTI 任务退化一致；物理 Known 校正能恢复多少；周期因果盲估计相对强传统固定/稳健估计器表现如何？

## 2. P0 的配对任务证据

同一目标真值机会集合上，Current +4 ns 为 26/200 正确关联命中（13%）；A0/0 ns 和 Known correction 均为 140/200（70%）。这支持“该已知注入时延在该场景降低任务输出，Known correction 恢复了 57 个百分点的命中覆盖”，不是对所有速度、SNR、杂波或平台误差的普遍结论。

A0 与 Known 在 140 个共同成功机会上的定位 RMSE 几乎完全相同（差约 0.000063 m），提供本场景物理校正闭合证据。Current 与 Known 的 23 个共同命中机会显示 135.493 m 对 13.797 m，但这是只在两者都成功的强选择子集，不能解释为全部 Current 机会的恢复误差。

## 3. 盲估计与传统基线

单 seed P0 的主任务观察：

- Fixed D3：142/200 命中（71%）；M1 causal prefix：140/200（70%）。共同成功的 135 个机会中，M1 定位 RMSE 比 Fixed D3 低 12.500 m。
- Huber D3 causal prefix：142/200；M1 在 136 个共同成功机会上的 RMSE 比 Huber 低 7.245 m。
- M1 只读取 causal prefix 是 V4 实现目标；此一 seed 的数值改善还不能证明跨 seed 可重复，也不能排除方法表现与命中集合相关。
- M1 与 Known 有 134 个共同成功机会，RMSE 分别为 43.872 m、26.684 m；即使 M1 恢复了命中覆盖，仍存在 Known 上限的共同机会定位差距。

V3.3 历史审计中，12 个独立 seed 的 full-10-period batch M1−Fixed 位置差均值为 `−11.146 m`、seed SD `4.797 m`（12/12 同向）；M1−Huber 为 `−5.603 m`、SD `0.985 m`（12/12 同向）。这些是历史 batch estimator 筛选证据，不是本次 causal-prefix V4 确认性证据，也不代替独立重跑。

## 4. 目标保持和 false alarm 的边界

固定真值 ROI 的 TO-only CSI_AFTER 结果显示 M1 power-ratio median 为 1.000，P10/P90 为 0.983/1.186；它排查了明显的中位数目标响应损失，但不能代替 Detection、Pd 或跨场景目标保持评价。

P0 的 94,910,400 次汇总 CUT 测试不是独立样本数。该 case 没有独立 H0 truth join，配置 Pfa 不能写成经验 Pfa；target-present 上的未匹配 Detection 或 false-confirmed 轨迹也不能替代 H0 虚警率。

## 5. 对研究方向的判断

证据支持继续研究**真实未知物理误差造成的任务退化及其确定性/盲校正收益**，因为 P0 有配对的退化与 Known 可恢复空间，且 causal-prefix 方法在单 seed 的共同成功定位机会优于 Fixed D3。证据尚不足以宣称 M1 是可靠创新，或它优于所有信息匹配传统方法。

当前没有证据把主线改为低速目标保护或复杂非平稳杂波：P0 仅有 2.5 m/s 单速度和一种固定面积杂波条件，没有相应的独立受控对照。当前也没有 AI 必要性证据：Known gap 尚未完成确认性量化，M1/Huber/固定 D3 的确定性替代尚未在 V4 预注册 seed 上验证。

因此本报告裁决为：未知误差物理估计方向 `CONTINUE_TO_CONFIRMATORY_REVIEW`；AI `NO_GO_AT_PRESENT_EVIDENCE`；低速目标与复杂杂波仍为 `NOT_VERIFIED`。不启动新实验。
