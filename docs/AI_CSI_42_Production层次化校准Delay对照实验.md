# AI-CSI-42：Production 层次化校准 Delay 对照实验

## 当前状态

本报告对应唯一允许的 production delay stage。代码和 analyzer 已具备 targeted pilot 入口、A0/PK/PKR residual closure、实测 cell-Pfa 分母、OFF/ON/TO 因果指标以及 CFAR→cluster→protocol→track 分层。在没有 clean formal CUDA 产物前，结论固定为：

```text
NEED_MORE_SINGLE_ERROR_PRODUCTION_EVIDENCE
```

不得把 CPU、编译、`--skip-cuda` contract 或历史 mechanism pilot 当成算法结果。

## 方法与对照

| ID | 含义 | estimator 信息 | apply/用途 |
|---|---|---|---|
| C0 | Production Current | 无研究 estimator | unchanged default |
| C1 | ordinary subtraction | OFF 或 ON，按 Mode-A/B | residual equivalent |
| C2 | SCC | OFF 或 ON，按 Mode-A/B | residual equivalent |
| C3 | Robust DDC | OFF 或 ON，按 Mode-A/B | residual equivalent |
| C4 | Robust DDC-RB | OFF 或 ON，按 Mode-A/B | residual equivalent |
| P1 | blind physical delay + Current | target-free OFF delay estimate | physical only |
| P2 | blind physical delay + Robust DDC-RB | target-free OFF，再 residual | hierarchical |
| PK | known physical upper bound + Current | truth evaluator only | upper bound |
| PKR | known physical upper bound + Robust DDC-RB | truth evaluator only | upper bound |

配对输入固定为 `OFF=C+N`、`ON=S+C+N`、`TO=S`，共享 seed、背景、目标、delay 和 production identity。Mode-A 只能从 OFF 估计，Mode-B 只能从 truth-blind ON 估计；TO 不得触发 estimator。

## 验收顺序

1. 先检查 `A0`（ideal/no-error）、`PK`、`PKR` 的 residual closure。若缺失、不可比或 `PK != A0` 未解释，不能把差异归因给 Gamma estimator；需区分 fractional-delay 边界、finite window、pair fusion、pulse compression、P38 和 DDC residual。
2. 再比较 channel/clutter metrics，并保留 residual power、Gamma support、phase coherence 和 status。
3. 目标保护使用 causal `ON-OFF` 与 `TO`，不能仅看 ON power。
4. false-alarm waterfall 依次报告 CFAR cell、cluster、protocol detection、TrackManager track；cell-Pfa 只使用 `hit_cut_count/valid_cut_count`。
5. 航迹级只接受现有 TrackManager/PIPE debug；ID-switch 可解释时分类，不能解释时明确写 `NOT_IDENTIFIABLE_FROM_CURRENT_DEBUG`。

## 14 个 delay-stage 问题

| 问题 | 当前判定 |
|---|---|
| 1. Robust DDC 是否接入 production F1/F2？ | 接入点已审计；算法结论待 clean CUDA evidence |
| 2. Current 与 ordinary subtraction 有何区别？ | 已由 tap contract 分离；结果待 pilot |
| 3. DDC 恢复多少 fast-time delay clutter？ | 待 residual/clutter metrics |
| 4. 是否恢复 range/angle/position/velocity/track？ | 待 causal downstream waterfall |
| 5. blind physical delay 恢复多少？ | 待 P1/P2 target-free estimate 结果 |
| 6. physical + Robust DDC 是否互补？ | 待 P2 与 C4/PKR 配对比较 |
| 7. known delay 能否回到 A0？ | 必须先过 A0/PK/PKR closure；当前未完成 |
| 8. 哪种 target preservation 最安全？ | 只按 ON−OFF/TO 判定；当前未完成 |
| 9. 哪种 false-alarm waterfall 最好？ | 只按四层分开判定；当前未完成 |
| 10. delay 属 Class-E 还是 Class-P？ | 单误差 evidence 未闭合，暂不分类 |
| 11. Paper-1 channel-delay 是否仍有独立价值？ | 需完成 production 对照后再判 |
| 12. 是否进入 servo/velocity pilot？ | 否，gate CLOSED |
| 13. 是否进入 coupled physical-state study？ | 否，gate CLOSED |
| 14. AI gate 是否关闭？ | 是：AI/router/native 4ch STAP 全关闭 |

## 运行与证据

先运行 `0,+/-2 ns` 的 path check，再在设备窗口允许时运行代表性的 `+/-4 ns`，至少 3 seeds、2 velocities。运行前确认 tracked worktree clean；原始 simulator、BIN/IQ、NPY/NPZ、PNG 和完整 production branch 输出只放独立 `.scratch/task5...` 目录。tracked compact evidence 只允许六个文件：

```text
manifest.json
method_contract.csv
gamma_recovery.csv
clutter_metrics.csv
decision_matrix.csv
report.md
```

`manifest.json` 必须记录 commit/config/runner/analyzer hash、GPU probe、命令、输入 identity、selection、AI gate 和 raw output 外置位置。任何缺失 denominator、causal triplet、reference group 或 track provenance 都是 `NOT_EVALUABLE`，不是 Current fallback。

## 允许的最终 decision

只能输出以下之一：

- `GO_EQUIVALENT_CALIBRATION_ONLY`
- `GO_PHYSICAL_CALIBRATION_ONLY`
- `GO_HIERARCHICAL_CALIBRATION`
- `NEED_MORE_SINGLE_ERROR_PRODUCTION_EVIDENCE`

在当前没有 clean formal CUDA 算法结果的状态下，本报告只能输出最后一个标签。
