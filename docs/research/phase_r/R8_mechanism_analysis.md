# R8 差异级与目标丢失阶段分析

日期：2026-10-23。此分析只归因当前 seed202/303 的实测记录，不将相关性升级为一般机制结论。逐期数据见 [目标邻域 CFAR 表](evidence/r8_unknown_delay_multiseed_20261023/evaluation/target_neighborhood_cfar.csv)。

## 目标邻域与冻结关联门

CFAR 邻域为 truth-centered Doppler circular ±2、range ±2 bins。它用于判断目标邻域有没有过门 CUT，不直接算作目标 detection。正确 detection 仍须满足 R7 的同 period、beam、range ±2、Doppler ±2 一对一关联。

| Seed | 条件 | 三期局部 CFAR hit 数 | 最近 detection 的 signed Doppler 偏差 (bins) | 门内正确检出 |
|---:|---|---:|---|---:|
| 202 | A0 | 26 | -1, -4, 0 | 2/3 |
| 202 | B +4 ns | 41 | -6, +24, +25 | 0/3 |
| 202 | C Known | 26 | -1, -4, 0 | 2/3 |
| 303 | A0 | 31 | 0, 0, +1 | 3/3 |
| 303 | B +4 ns | 44 | -6, +23, +29 | 0/3 |
| 303 | C Known | 31 | 0, 0, +1 | 3/3 |

B 的每个 target period 都有邻域 hit，但最近 cluster/detection 位于冻结 Doppler 门外；目标关联阶段分类均为 `nearest_cluster_or_detection_outside_frozen_target_gate`。B 的最近 detection range 偏差分别为 seed202 `[0,-1,-1]`、seed303 `[0,-1,-1]` bins。局部 hit 不证明目标检出，门外候选也不能被重新解释为成功检出。

## CSI、输入融合与物理闭合

未知 +4 ns 条件 B 的 H0 CSI cancellation 相对 A0 下降：seed202 `5.818→5.329 dB`，seed303 `5.436→4.994 dB`；Known Correction C 回到 A0（差约 0.001 dB 以内）。H0 unique union hits 也在 B 增加：seed202 `432→472`，seed303 `396→428`。这支持“B 下残余/过门候选增加”的描述性观察，但不能作为 Pfa 或其总体变化的估计。

新增 seed 的四通道融合输入功率显示 B 的 F1 与 A0 相同；F2 功率降低，ON 分别为 seed202 `-0.221 dB`、seed303 `-0.230 dB`，OFF 为 `-0.238/-0.247 dB`，TO 为 `-0.059/-0.059 dB`。C 的 F1/F2 功率与 A0 闭合。逐角色、逐条件数值在 [CSV](evidence/r8_unknown_delay_multiseed_20261023/evaluation/r8_seed_condition_metrics.csv) 与 [实验 manifest](evidence/r8_unknown_delay_multiseed_20261023/evaluation/r8_experiment_manifest.json)。seed101 的原始 Stage2 IQ 已清理，F1/F2 功率为 `NOT_EVALUABLE`，故不扩写到三 seed 输入功率趋势。

Known Correction 使用注入的时延真值，只由 evaluator/输入校正阶段读取，不用于目标真值评价。其相对 A0 的 F1/F2 复 RMS 最大值：seed202 `9.13e-5`、seed303 `9.56e-5`，均低于预注册 `1e-3`；seed101 R7 为 `9.37e-5`。因此 A0/C 的物理闭合门通过。

## 为什么不能归因于“目标被 CSI 对消”

- B 的输出 SCNR proxy 并未下降：seed202 `70.65→72.85 dB`，seed303 `74.46→75.05 dB`。该 proxy 是目标 ROI 与 H0 ROI 的功率比，受分母及输入融合影响，不等于 Pd。
- paired complex transfer amplitude 在 B 相对 A0 的变化跨 seed 不同：seed101 下降约 `1.98 dB`，seed202/303 分别上升约 `3.42/3.35 dB`。该 ROI 传递量不是固定物理增益，不能证明或排除所有形式的目标泄漏；但现有数值不支持一致的幅度自对消解释。
- 可直接观察到的稳定差别是 B 在目标附近保留 CFAR hits，却把最近候选推到冻结 Doppler 关联门外；A0/C 的候选偏差回到门附近，检出计数同步恢复。

## 归因边界

本实验支持差异至少延伸到 CSI 输入融合与 CFAR/候选定位，并在目标关联门处表现为任务损失。它尚不能精确区分 Doppler 偏移由通道误差、Stage1 物理校正、CFAR 候选筛选还是 cluster/检测选择中哪一环主导。要区分这些机制，应使用已有逐期 F1/F2、CSI、CFAR margin、cluster lineage 和 detection 记录做针对性审计，不应通过扩大阈值或改变门限来制造门内匹配。
