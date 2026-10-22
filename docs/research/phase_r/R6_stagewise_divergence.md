# R6：A0/C 分阶段数值差异

## 输入物理闭合

用 `scripts.run_delay_stage1_formal._load_fused_from_periods` 对齐重载每组 3 periods fused F1/F2，比较 A0 原始输入与由 A1(+4 ns)生成的 Known-C 输入。数组均为 390×11820、4,609,800 samples；冻结闭合容差为 `1e-3`。

| 角色 | F1 相对复 RMS / max | F2 相对复 RMS | F2 max 绝对误差 | F2 相位 RMS |
|---|---:|---:|---:|---:|
| ON | 0 / 0 | 8.9314e-5 | 0.1200714 | 0.0103550 rad |
| OFF | 0 / 0 | 9.3708e-5 | 0.1200714 | 0.0103550 rad |
| TO | 0 / 0 | 7.0951e-8 | 7.1526e-7 | 5.6366e-8 rad |

三种角色均通过冻结闭合条件；F1 完全相同，差异集中在由通道 2 时延校正影响的 F2。

## 全图比较

Production CSI tap 按当前代码定义分别保存 `channel1/2`、`ctdr_channel1/2`、真实 CSI 输出 `csi_after` 以及实际 detector input `after`。全图为 130×4096，所有 map 有限，逐 period 与逐 tap 比较。定义：`relative_complex_rms = RMS(C−A0)/RMS(A0)`；相位差为 `angle(C·conj(A0))`，任一端为零的 cell 不参加相位统计；power relative RMS 按功率差的 RMS 归一化。

下表给出三 period 中最大相对复 RMS：

| 角色 | `channel1` / `ctdr_channel1` | `channel2` | `ctdr_channel2` | `csi_after` | detector input `after` |
|---|---:|---:|---:|---:|---:|
| ON | 0 | 1.3970e-4 | 1.6887e-4 | 1.7568e-5 | 7.9781e-4 |
| OFF | 0 | 2.7383e-3 | 2.0933e-3 | 7.9257e-5 | 2.1926e-3 |
| TO | 0 | 1.6026e-7 | 2.8810e-7 | 1.5469e-6 | 1.1430e-5 |

微小差异首次在 `channel2` 出现；经过 CTDR/CSI 后，`csi_after` 的相对复差小于输入端，最终 detector-input map 的最大差异仍低于 2.2e-3。Production 使用 CSI band 与原 channel2 的混合 detector input，因此 detector-input tap 不等于全图 CSI 输出。

R6 A0_ON rep1、rep2 相对 A0_ON_complexdiag 的 36 个全图 tap 比较行全部为 0 误差（6 taps×3 periods×2 repeats）。诊断开启/关闭的无插桩 parity 由 GMTI、CFAR hit/threshold、检测字段和航迹输出验证；诊断关闭时没有对应 CSI dump，未伪造缺失 map。

## 首个离散变化

在 CFAR 之前，CSI 全图差异仍为小量；完整门限图经 32-bit integral-image CFAR 计算后，A0/C 的 valid-CUT hit mask 已有成千至数万项变化。六个 A0/C 条件共 36 个 branch-period maps，每图 527,280 个 valid CUT；所有分支的 OR hit mask 均与各自 Production union hit map 精确一致。精确门限敏感性见 [R6 CFAR 审计](R6_cfar_threshold_sensitivity.md)。

对目标候选的具体追踪表明：ON 的 `row=119,col=2201` 在 A0 和 C 的 dynamic CFAR 都命中，但只进入 A0 的最终 detection 列表；周边 hit topology 也不同，A0 在该邻域额外命中 `col=2202`。现存产物没有逐 cluster ID 的持久 lineage，因此可定位到 **CFAR 之后的 cluster/detection selection**，但不能声称已经证明是哪一条 cluster 子规则删除了 C 的候选。具体目标任务影响见 [R6 任务误差归因](R6_task_error_attribution.md)。

## 证据

- [逐 map CSV 与 A0/A0 控制 CSV](evidence/r6_stagewise_divergence_20261022/stagewise/)
- 本地完整 `.npy` CSI maps：`outputs/phase_r6_stagewise_divergence_20261022/production/{A0_ON_complexdiag,C_ON,...}/result/csi_metrics/`
- 固定程序：`build/GMTI_pipe_core`，SHA-256 `f65a8aa446754cc79b6417f9275b3a01e73eb85098c6a4f9eaea0cb999c38962`
