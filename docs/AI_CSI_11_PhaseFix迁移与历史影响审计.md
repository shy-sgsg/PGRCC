# Phase Fix 迁移与历史影响审计

日期：2026-09-10。本文记录生产 GMTI 工程中已验证的双通道相位修复、低相干 H0 gate 和 CUDA workspace 修复在 PGRCC 的迁移结果，以及旧实验是否需要重跑。

## 结论

PGRCC 已在当前源码上完成并使用 RTX 3050 CUDA 验证：

- reusable background 按 source packet header 的 physical `theta_cmd_deg` 建立 beam-group 映射，不再按 source stream offset 顺序读取；异常布局会失败退出。
- reuse 后保留 source 的 UTC、姿态、位置、速度和物理 theta，只重写输出连续 `prt_counter`；映射审计写入 `background_reuse_mapping.csv`，并记录 `source_group`（0-based）、source/output packet/counter、`theta_error_deg` 和 `mapping_status`。
- 双通道定义保持 `c12 = sum(F1 * conj(F2))`，补偿保持 `F2 *= exp(+j*phi)`，没有翻转 complex sign。
- CPU/CUDA legacy CSI 都使用 row-level coherence，默认 `rho < 0.5` 时保留 channel 1 并跳过 min-amplitude equalization 与 CSI subtraction。
- CPU 直接 selftest 与 CUDA 生产 A/B 使用同一阈值和旁路语义：低相干行 CPU 旁路误差为 `0`，显式 legacy path 相对误差为 `1`，相干行 gate 开关差异为 `0`。
- cropped cuFFT 初始化会保留最大 single-buffer byte 数，三周期 packed input 已在同一生产进程中通过。

## 当前 CUDA 证据

| 回归 | 实际结果 | 证据 |
|---|---|---|
| 单 beam source-equivalent | requested beam 15 / -1° 对应 source packet 1792 / -1°；after-vs-source-requested coherence `0.999999568`，phase `-58.6364°` | [`phase_input_audit.json`](../outputs/phase_a_reuse_source_equivalent/phase_audit/phase_input_audit.json) |
| beam 14/15/16 × 3 periods | 9 个 physical mapping rows，三个输出文件各 384 packets | [`background_reuse_mapping.csv`](../outputs/phase_a_reuse_14_16_3period/reports/background_reuse_mapping.csv) |
| 生产 CUDA beam 14/15/16 | cancellation 分别 `19.237249/18.697901/13.233184 dB`；rho 分别 `0.9941032/0.99325982/0.97625727` | `outputs/phase_a_reuse_14_16_3period/algorithm_result/period_0000/csi_metrics/*/csi_metric_tap_summary.csv` |
| pure-noise H0 A/B | clean `GMTI_core` 重放：gate=false split hits `21`；gate=true `75/75` 行旁路、hits `0` | [`outputs/h0_gate_ab_formal_clean`](../outputs/h0_gate_ab_formal_clean) |
| coherent A/B | gate 开关不改变该 coherent case 的主 CSI 统计 | [`outputs/coherent_gate_ab`](../outputs/coherent_gate_ab) |
| CPU gate direct selftest | `csi_gate_cpu_selftest` 通过；低相干旁路误差 `0`，相干 gate 差异 `0` | `ctest --test-dir build -R '^csi_gate_cpu_selftest$' --output-on-failure` |
| persistent workspace | 三周期 packed input 同一进程完成，无 workspace overflow | [`outputs/phase_a_workspace_3period`](../outputs/phase_a_workspace_3period) |

## 历史影响分类

逐实验分类保存在 [`impact_matrix.csv`](../outputs/background_reuse_impact/impact_matrix.csv)。

- 直接使用“单 beam target + full-scan background reuse”的两个实验属于 D 类，受 beam-reuse bug 影响；旧输出保留为 `superseded`，corrected single-beam 和 beam14/15/16×3-period rerun 已完成，证据路径记录在 `impact_matrix.csv`，不能再把旧输出当作物理结论。
- V2.1 screen/formal、velocity、ROC、transition、mixed stress，以及 Oracle V1/V1.1 和 Support Routing 没有 full-scan source reuse，因此不受 beam mapping bug 影响；但它们的 Current 没有新的 H0 gate 语义，标记为 conditional，不能直接冒充新的 Phase-Corrected Current。
- single-beam minpoints 参考不受 beam reuse 影响，也不把源工程数字直接写成 PGRCC 新结果。

## 校准政策

PGRCC 已在 gate=true、phase fix=true 的当前 compact-statistical 配置下重新运行两枚 seed、5 个配置 SNR、每点 3 replicas，共 30 个 paired records。offset 的 mean/median/std 为 `29.9570/33.6916/12.4939 dB`；两枚 seed 的 spread 和同一 seed 内的 spread 都很大，因此没有冻结 scalar gain。

完整原始记录与判定见 [`calibration_phase_corrected_gate.json`](../outputs/ai_csi_phase_corrected_baseline/calibration_phase_corrected_gate.json)。历史 calibration 保留但不混用；源工程的 `21.3086164 dB` 不是 PGRCC 结果。

## 复现入口

```bash
ctest --test-dir build -R '^csi_gate_cpu_selftest$' --output-on-failure

python3 scripts/audit_stage2_phase_consistency.py \
  --manifest outputs/phase_a_reuse_source_equivalent/reports/background_reuse_mapping.csv
```

生产回归使用各输出目录中保存的 XML；重新运行前先检查 `nvidia-smi`、`free -h` 和 `df -h .`。Phase-A 历史输出仍保留其原始运行身份；本次 model-mismatch formal CUDA 证据另行保存了 clean-source provenance，见 [`AI_CSI_15_ModelMismatch_Challenge报告.md`](AI_CSI_15_ModelMismatch_Challenge报告.md)。
