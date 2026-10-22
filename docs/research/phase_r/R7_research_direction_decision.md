# R7：研究方向阶段裁决

日期：2026-10-22
结论等级：seed101 单场景、3 个 period 的 CUDA Production 可行性与正确性验证；不是独立 seed 统计裁决。

## 结论摘要

R7 发现并修复了 CUDA GO-CFAR float32 积分前缀求和的消去误差；历史 R6 分支地图、早期单周期比较及最终 27 张三周期 union 地图均支持该数值根因。修复后三周期 Production 矩阵完成了 A0/B/C × OFF/ON/TO 任务评价并加入 cluster→detection lineage。结果显示此 seed 的 +4 ns Current 条件三期都没有通过冻结目标检测门限，A0 与 Known Correction 均三期通过；但三期来自同一 seed，且 A0/C 仅 2/3 期形成目标关联 confirmed track，因此不能称为统计上可重复的 GMTI 性能改善。

### 对 R7 指定问题的回答

| 问题 | 结论 | 证据与限定 |
|---|---|---|
| Q1：负门限是否由单精度积分图数值消去导致？ | 是。 | 大量 prefix sum 做包含—排除时，小窗口和成为大数相减残差。保存的 R6 真实功率图复现负门限反例；根因与数学定义、几何和 alpha 无关。见[数值根因](R7_CUDA_CFAR_numerical_root_cause.md)。 |
| Q2：修复后是否与独立 float64 参考一致？ | 最终三周期地图逐 CUT 闭合。 | 27 个 case-period 的 14,236,560 个 union CUT 上，post threshold 等于参考转 float32，unique union hit flips 为 0；54 张 branch map 的 28,473,120 个有效 CUT 同样逐点闭合。逐 period map 身份、输入 hash、result-ID 映射和 geometry hit counts 均交叉核验。 |
| Q3：错误门限造成多少 CUT 命中决策差异？ | 最终三周期 pre-fix union map 有 695,232 extra、1,431 missed；post-fix 为 0/0。 | 历史 R6 36 张 branch-period map 有 610,185 extra、3,074 missed；早期 R7 单周期九张图有 312,779 extra、215 missed。不同 map 集合不可相加；这些是 CUT 决策翻转，不是独立虚警事件或 Pd/Pfa。 |
| Q4：OFF 仍有多少命中、虚警候选和虚假航迹？ | pre→post unique union hits：A0 `31,193→404`、B `35,296→442`、C `30,935→403`；post branch-hit-cell 和为 `679/674/676`，clusters/detections 为 `24/27/24`，false confirmed tracks 均为 `4`。 | unique union CUT counts 已由 period-separated hit maps 评估；branch sum 会重复计入重叠格。H0 detections/tracks 是描述性候选，hit 数不是统计 Pfa。 |
| Q5：R3 的 A0/C 任务 RMSE 大幅差异是否仍存在？ | 不存在于本轮冻结的一对一评价中。 | 三周期 post-fix A0/C：position RMSE `40.338603/40.338642 m`，angle `0.02632572/0.02632575°`，ground-speed `0.382643/0.382627 m/s`；径向速度均未解析。 |
| Q6：差异是否受单条边界关联 payload 主导？ | A0/C 差异已近零，不能再归因于边界关联。 | period 0 的 68.7 m 位置误差占 A0/C 各自总平方误差约 96.79%，但该匹配为 range Δ0、Doppler Δ1，不在门限边缘；period 2 是 Doppler Δ2 边界匹配，位置误差仅约 3.33 m。所有记录均保留，未剔除样本。 |
| Q7：+4 ns 是否仍造成可重复任务损失？ | 本 seed 的 3 个 period 都观察到损失：B 为 0/3 门内 detection，A0 为 3/3。 | pre/post CFAR 修复后结果相同；这说明差异不是 CFAR 修复制造的。但 period 共享一个 seed/场景，不能替代独立 seed 复验或显著性检验。 |
| Q8：Known Correction 是否恢复损失？ | 在这三个机会中，C 由 B 的 0/3 恢复至 3/3 门内 detection，并与 A0 参数误差几乎闭合。 | A0/C 均 2/3 目标 detection 关联到 confirmed matched-this-frame track；B 为 0/3。只能称本场景可行性信号，不能宣称总体恢复率。 |
| Q9：是否具备继续研究传统盲时延估计 D3 的条件？ | 当前不具备。 | 缺少独立 seed 的任务损失/恢复复验；本轮无 D3 估计器条件，无法测其与 Known Correction 上限的差距。先不启动 D3。 |
| Q10：是否已找到可作为论文主线的核心性能瓶颈？ | 尚未。 | GO-CFAR 问题属于已修复的实现数值 bug；+4 ns 任务损失值得继续检验，但统计单位仍单一。修复后仍有 H0 候选与目标航迹闭环问题，但此结果不足以据此切换到低速保护或复杂杂波主线。 |

## 目标与 H0 细节

ON 三期严格一对一关联：A0/C 各有 3 个正确候选、0 miss；B 有 3 miss。post-fix A0/C 各有 175 条 final detection，其中 172 条未与三次真值机会关联；各有 29 条 confirmed track，其中 2 条正确关联到目标。B 有 161 条 final detection、42 条 confirmed track，但均未与目标关联。`radial_velocity` 三期全未解析，不能以其它速度字段替代。

H0/OFF 每个修复后 case 有 24–27 条 final detection、4 条 false confirmed track。分支 hit 数、cluster、检测与航迹的逐期计数见[H0 与关联审计](R7_H0_detection_association_audit.md)。TO 的 truth-centered CSI complex transfer 幅度比/相位误差为 A0 `0.062682 / 0.364889 rad`、B `0.075411 / 0.028992 rad`、C `0.062682 / 0.364889 rad`；这是 pre-CSI→CSI 输出的 5×5 ROI transfer，不是独立目标检测性能。CFAR 修复前后这些值相同。

## 证据状态

- `IMPLEMENTED`：GO-CFAR 稳定双精度累计与误差界直接重算；三周期 runner、冻结的一对一 truth evaluator、per-opportunity 参数误差和 diagnostics-gated cluster→detection lineage。
- `EXECUTED`：RTX 3050 Laptop 真实 CUDA numerical selftest；九条件 pre/post 三周期 Production 回放；同二进制 A0_ON repeat；diagnostics-off 控制；以及 A0_ON period-0 修复前/后各 1 次 warm-up、3 次计时的端到端性能 pilot。最终核心矩阵每个 case 的 3 个 period 顺序完整，以上运行 exit code 均为 0。
- `EVALUABLE`：保存的 pre/post branch 与 union 数值地图对 float64 reference 的三周期逐 CUT 复核、unique union hit 和 hit flips；三周期目标候选、逐期参数误差、cluster/detection lineage、H0 描述计数、TO complex transfer；以及单周期 pilot 的描述性时间、输出候选数与主机 RSS。pilot 的候选数不同且设备前后状态变化，不能用来验证 CFAR 性能增益。CoreX 仅完成 CMake/source 静态检查，实际编译和运行 `NOT_EXECUTED`。统计 Pd/Pfa、跨 seed 总体性能和 D3 差距仍不可评估。
- `PERFORMANCE_VERIFIED`：否。补做的 period-0 A0_ON 修复前/后 3+3 次 pilot 得到外部 wall median 1.41/1.33 s、内部算法计时 median 1095.104/1030.419 ms；但过滤 detection 数 140/19 不同，GPU 状态运行前后也由 P8/8% 变化到 P0/5%，所以不能把表观耗时差归因于修复或宣称提速。没有 kernel/传输隔离、VRAM high-water 或独立 seed 的统计性能验证。逐次数据和日志见[性能 A/B pilot](evidence/r7_cfar_correctness_20261022/performance_ab_20261023/)。

完整三周期产物位于本机 `outputs/phase_r7_cfar_correctness_20261022/`。轻量审查证据和逐期任务指标位于 [`evidence/r7_cfar_correctness_20261022/`](evidence/r7_cfar_correctness_20261022/)；配对、配置和二进制身份见其中 manifest。最终 Production binary SHA-256：`de3026ed4f1263be4143dcbcd10ccbb5e53b4be357e21906e1bf19ea0ec29d00`。

## 阶段门控与停止

R7 的数值修复、真实 CUDA 正确性测试、三周期任务评价和阶段裁决已完成；但 CoreX 编译/运行兼容性仍为 `NOT_EXECUTED`，因此完整阶段关闭门尚未通过。当前机器缺少 CoreX SDK/compiler；需在可访问的 CoreX 环境补做后才能关闭该项。本轮不自动追加 seed202/303、不运行 D3、不训练 AI、不开发 Router，也不扩展六参数估计。后续科学方向需在用户审阅上述证据后另行决定。当前证据只支持：先前 A0/C RMSE 大差异不再出现；该单 seed 下 +4 ns 目标检测损失与 Known Correction 恢复值得独立 seed 验证；还不足以宣告 AI 必要性或新的论文瓶颈。
