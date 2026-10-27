# R22 延续实验：工程门失败记录（2026-10-28）

## 结论

R22 扩展工程矩阵在第一个 0 ns 工程条件处停止：`seed=1930101`、`2.5 m/s`、`20 dB`、20 个目标、10 个周期。Stage2 已生成场景并完成面积杂波与配对审计；盲时延估计随后给出 `+0.188762554 ns`，其零扩展线性插值逆算子因首样本不满足模型前提而按设计拒绝。**Production 未启动，不能报告检测、CFAR、CSI、航迹或任务性能结果。** 矩阵状态为 `GATE_FAILED`，筛选、确认、密集目标、桥接和独立 H0 阶段均未启动。

研究总纲保持不变。本次未修改算法、阈值、质量门或冻结实验矩阵；未训练 AI。

## 执行身份与状态

实际启动命令：

```bash
python3 scripts/phase_r/r22_experiment_matrix.py \
  --phase engineering \
  --output-root outputs/r22_extension_20261028/engineering \
  --stage2-binary build/simulate_stage2_statistical \
  --production-binary build/GMTI_pipe_core \
  --max-cases 1
```

该矩阵根记录了失败状态，runner 按设计拒绝在同一根目录恢复。代码修复/审查后的重跑应使用新的证据根，并保留当前失败快照。

| 项目 | 实际状态 |
|---|---|
| 分支 / 实验源码 commit | `codex/unknown-system-error-next-stage` / `5c66bb0de3704a3e93f750847cf5b55059da9a9e` |
| 研究总纲 blob | `e637e3769bde7814298ff012d0f42301884e2985`，保持不变 |
| Stage2 | 已执行（场景仿真，不是 CUDA Production）；Native CUDA 设备仅在预检中可见 RTX 3050 Laptop GPU，P8，41°C，6.58 W，9% 利用率；Stage2 binary SHA-256 `ca8aa9eb8b7d97e5743a27e67c312e66a4efecbf2c76880db581192afd08ce8e` |
| Production | 未执行；准备的本地 Native CUDA binary SHA-256 `3c4c6bef08291e7d02cefa1b23387d442dfb00cf0fbed0604e9a0fbecaa86760` |
| `IMPLEMENTED` | 是，R22 场景生成器、runner、矩阵和 evaluator 已在先前提交实现 |
| `EXECUTED` | 否（指完整 Production case）；Stage2 仿真已执行，Production 否 |
| `EVALUABLE` | 否（指 Production 任务结果）；Stage2 杂波/配对与盲校正输入前提可单独审计 |
| `PERFORMANCE_VERIFIED` | 否 |

运行身份、完整 Stage2 配置、阶段报告、日志、输入 SHA 和清理清单见[紧凑证据包](evidence/extension_engineering/seed1930101/)。

## 杂波与输入配对

A0_ON 的 Stage2 报告记录连续面积杂波 `1300 packets / 6,508,782 samples`，平均功率 `0.0225128995081`，功率 CV `1.9925009136`，`rho(lag 1)=0.979999807132`，CTDR lag 2 的相关系数 `0.960397551126`；热噪声实测功率为 `0.00100014187201`。这证明本 case 的面积杂波真实生成。

0 ns 配置下 A1_OFF 被 runner 按等价配置复用为 A0_OFF；A0_OFF 使用 A0_ON 写出的 `A0_background`。对全部 10 个周期逐文件核验 SHA-256：A0_OFF 与 A0_background 完全一致，A0_ON 与 A1_ON 完全一致。盲估计器实际输入 `target_free_OFF_C_plus_N.bin` 有 1300 个 packet，大小 `492,044,800` bytes，SHA-256 `110fac37aecb730fd3e355e0381112afd0797102c29277e126fcde4ac2738462`。因此 A0_OFF 报告中的直接杂波注入计数为 0 是背景复用口径，不代表估计器没有收到 C+N。

该 OFF 输入是配对诊断流，不是独立 H0 seed，不能用于经验 Pfa 推断。

## 盲估计与前置失败

注入时延为 0 ns。对 10 周期 target-free OFF=C+N 的 F1/F2 输入调用既有 Stage1 方法套件，得到：

| 方法 | 时延估计 (ns) | 拟合 `R²` |
|---|---:|---:|
| D1 ordinary LS | +0.160424583 | 0.007499 |
| D2 weighted LS | +0.172614299 | 0.008749 |
| D3 Huber weighted LS（当前选择） | +0.188762554 | 0.012462 |
| D4 generalized phase slope ML | +0.063558808 | — |
| Cross-correlation | −0.008068055 | — |
| GCC-PHAT | −0.010050501 | — |
| Oversampled cross-correlation | −0.009856640 | — |

当前 `_select_delay_estimate` 优先选择有有限值的 D3/D2/D1；该选择没有使用本次拟合质量阈值。D3 对应 `60 MHz` 下 `0.011325753` 个采样点。被校正的通道 2 首样本为 `0.006142768 − 0.007733644j`，幅度 `0.009876379`；packet RMS `5.206051970`，零扩展逆算子允许的首样本阈值为 `5.20605e−7`。前提不成立，`rewrite_float32_protocol_delay(... simulator_linear_fractional_delay_inverse=True)` 报错：

```text
ValueError: packet 0 first sample is not zero-extended-delay compatible
```

该拒绝符合当前物理算子的 fail-closed 契约；不能放宽首样本门槛、把估计改成 0、读取真值或改用只对该条件有利的校正算子。现有证据只能确定估计与校正算子前提不兼容；它尚不能区分弱可辨识/噪声和杂波诱发的相位斜率、未建模物理残差或 estimator 选择策略的问题。

## 影响与下一门

本次只运行了 12 个计划工程 case 中的第 1 个。Production 处理阶段、逐 beam 日志、CSI tap、CFAR geometry、ON/OFF/TO 任务评价、H0 独立 false-alarm 统计和速度/航迹指标均没有产物。故不能回答未知时延是否降低 GMTI 性能、Known 校正可恢复多少、传统盲估计与 Known 差距多大，也没有 AI 必要性的证据。

下一步建议先由独立审查判断该事件应如何处理：

1. 将 0 ns 条件的 P1/P2 记为方法级 `NOT_EVALUABLE`，并明确矩阵工程门仍失败；或
2. 先修正/标定盲估计器的零误差判别或物理校正契约，再用新证据目录重跑同 seed 的 0/+4 ns 配对工程条件。

任何修订都需保留当前失败证据，不得在看到结果后新增置信度/时延阈值，也不得用 Known、目标真值或配对参考进入盲估计。审查意见和用户决策前，不启动扩展矩阵或训练 AI。

## 空间清理

本 case 的 52 个可再生 `.bin` 文件（Stage2 IQ、合并后的 estimator 输入和 0 字节的失败校正输出）共 `2,952,268,800` bytes。清理前逐个保存 SHA-256、大小和路径；保留场景配置、运行 XML、Stage2 报告/日志、运行及矩阵 manifest、估计数值和失败原因后删除。逐文件身份和删除理由记录于 [`raw_cleanup.json`](evidence/extension_engineering/seed1930101/raw_cleanup.json)；全项目清理台账见 [`cleanup_ledger.md`](evidence/cleanup_ledger.md)。
