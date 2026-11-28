# R22-V4 确认性统计设计审查

日期：2026-11-23
统计方案：固定草案复核；阈值未指定，不构成冻结或执行授权。

## 1. 推断单位和主估计量

V4 草案计划 30 个独立 seed。主比较为 `M1_CAUSAL_PREFIX − FIXED_D3`，条件 +4 ns、20 dB；每个 seed 先在 0.5、2.5、6.7 m/s 三个速度层计算共同正确关联机会的定位 RMSE 差，再等权平均成一个 seed-level 差值。负值有利于 M1。

主区间为 10,000 次 seed-cluster bootstrap 95% CI，PCG64 seed `22029`；抽样单位是 seed，并保留 seed 内全部速度/目标/周期。若报告速度层检验，须对三次检验使用 Holm 校正。目标、周期、CUT、Detection、branch 均不是独立 seed。

共同成功集合的可评估性必须预先定义。不能把漏检填成零误差，不能在结果出来后移除困难 seed/速度，也不能仅对方法各自成功的不同集合做主排名。若某 seed 的预注册速度层没有共同成功机会，该 seed-level 统计应按预先冻结的规则标为缺失/不可评估并揭示覆盖，不得静默缩小分母。

## 2. 历史方差和精度敏感度

我从 V3.3 机会审计重算 +4 ns、20 dB、三个计划速度的 12-seed slice：

| 比较 | 12-seed 平均位置差 | seed SD | 覆盖差均值 ± SD |
|---|---:|---:|---:|
| M1 batch − Fixed D3 | −11.146 m | 4.797 m | +0.083 ± 0.495 pp |
| M1 batch − Huber D3 | −5.603 m | 0.985 m | 0.000 ± 0.333 pp |
| M1 batch − Known | +21.358 m | 4.455 m | −0.056 ± 0.372 pp |

基于 M1−Fixed 历史 seed SD 4.797 m 的 paired noncentral-t 敏感度：n=30 时 80% power 的 MDE 约 2.539 m；若真实改善 3 m，敏感度估算 power 约 0.912。coverage SD 0.495 pp 下，真实差为 0 时，一侧 α=0.025、80% power 的非劣 margin 敏感度约 0.262 pp。30-seed 95% t 半宽约 1.791 m / 0.185 pp。

这些数字只来自 12 个旧 V3.3 full-case batch seeds，而 V4 改为 causal-prefix estimator。方差可迁移性未证明；它们是精度/功效敏感度，不是可靠 power 保证，更不是工程阈值或 MDE/非劣界值。若工程要求的 MDE 明显大于或小于此敏感度，须先重做盲化/历史方差与精度论证，不得用确认数据调样本数。

## 3. 检测与航迹保护门

位置误差只在共同正确关联成功集合上定义，故必须另设自己的检测覆盖非劣门。需在解盲前由用户/工程需求方确定 `MDE_position_m` 和 M1 相对 Fixed D3 的 `margin_detection_pp`。建议冻结候选表述为：位置改善只有在 seed-level 95% CI 的上界低于 `−MDE_position_m` 且覆盖差 95% CI 下界高于 `−margin_detection_pp` 时才支持实际任务收益。该判据是待审批候选，不替用户填写阈值。

若要求 TrackManager 保护，还要预先给出 target-confirmation/match coverage 与 false-confirmed track 的界值；不能使用 P0 的某个单 seed 观察值倒推门槛。

## 4. 独立 H0 虚警设计

计划有 30 个新 H0 seed，每 seed 10 个有效 beam-50 周期。Primary endpoint 是每个 seed 内至少一个符合冻结 postfilter Detection 的周期比例，再在 seed 层汇总；另报告 false unique CUT/valid CUT、false cluster、false-confirmed track。单 beam/十周期不应称为全 61-beam 系统 Pfa。每个 seed 的周期和 CUT 不能当作独立重复。

若 30 个独立 H0 seed 恰好零 seed 发生 false event，单侧 95% Clopper–Pearson 上界仍约为 9.50%（针对 seed-any-event 概率），说明 30 个 seed 对很低的系统虚警率主张能力有限。应预设可接受 H0 event/track 边界及需报告的区间；P0 paired OFF 不是 H0，不能合并使用。H0 候选 seed 还须完成完整 registry 碰撞审计。

## 5. 统计裁决

设计的推断单位和配对结构可以接受为**冻结候选**；但 MDE、检测覆盖非劣界值、航迹保护界值、H0 可接受边界、共同成功缺失处理尚未闭合，且历史方差到 causal-prefix 的迁移未知。因此当前保持 `DRAFT_NOT_FROZEN`，不声明 V4 功效已验证，不进行矩阵执行。
