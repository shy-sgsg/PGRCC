# R7 本地实验产物清理记录（2026-10-22）

## 基线

- 检查路径：`/home/shy/AIR/SAR+AI/project/PGRCC`
- 清理前实占约 12 GB，已低于用户要求的 20 GB；主要占用在活跃工作树的 `outputs/`（约 11 GB）。
- 清理后复测：仓库目录约 9.7 GB；活跃工作树约 9.3 GB，其中 `outputs/` 约 8.8 GB。
- 清理范围仅限当前分支活跃工作树中的 R7 非权威重复结果；源码、配置、输入、R6 唯一诊断现场和权威 R7 结果均不删除。

## 已删除

| 路径 | 清理前大小 | 理由 |
|---|---:|---|
| `outputs/phase_r7_cfar_correctness_20261022/post_fix_matrix_3period/` | 142 MB | 仅含未完成的重复性控制尝试；由 `post_fix_matrix_3period_final/` 中成功完成的控制替代。 |
| `outputs/phase_r7_cfar_correctness_20261022/post_fix_matrix_3period_forced_ids/` | 约 1.7 GB | 与权威矩阵条件重复；旧 manifest 中诊断关闭控制标记为 `FAILED_OR_INCOMPLETE`。 |

对应最终矩阵 `outputs/phase_r7_cfar_correctness_20261022/post_fix_matrix_3period_final/` 对全部正式条件及重复性/诊断关闭控制均记录 `EXECUTED`。本台账保留了两次旧尝试的状态、大小和移除理由；其派生结果不再保留。

## 保留项

- 权威 R7 三周期 Production 结果、运行配置及其 manifest。
- `docs/research/phase_r/evidence/r7_cfar_correctness_20261022/` 中的 compact manifest、runtime XML、日志、数值分析、CFAR 几何、CSI tap、目标关联及关键回归 fixture。
- R6 阶段差异原始材料与 `outputs/stage2/` 现场；它们被现有 R6 审计记录认定为当前仍需保留的输入/唯一诊断现场，本次未删除。

本记录不把“达到磁盘容量目标”解释为算法结论，也不替代实验 manifest。
