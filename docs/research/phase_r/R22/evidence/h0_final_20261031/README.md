# R22 H0 完整独立 seed 审查证据（2026-10-31）

本目录是 R22 冻结 H0 30-seed、0/+4 ns 配对矩阵的最小可审查汇总。它不替代完整 raw IQ、逐分支日志或本机 compact case ZIP。

## 文件

- `h0_analysis_manifest.json`：分析状态、30 个 seed、60 个 case/archive SHA、Source/Stage2/Production 身份及 bootstrap 规则。
- 分析器：仓库根目录 `scripts/phase_r/analyze_r22_h0_seed_clusters.py`；其统计实现随审查快照提供。原始 60 个 compact ZIP 未上传，无法仅凭 GitHub 重算所有 per-seed 输入。
- `h0_device_execution_note.md`：外部训练并发时的 GPU 状态、余量和运行限制；不是性能基准。
- `h0_per_seed_rates.csv`：1,800 行 per-seed CUT-hit fraction；每行的推断单位为独立 seed。
- `h0_per_condition_summary.csv`：72 个冻结方法/条件×CUT 区域汇总，其中 60 个可评价、12 个零时延不适用。
- `h0_paired_delay_summary.csv`：36 个配对汇总，其中 24 个可评价、12 个零时延不适用。
- `h0_pair_clutter_noise_audit.csv`：30 对真实 `A0_ON` Stage2 面积杂波/噪声摘要逐项比较及 report/archive SHA。
- `h0_pair_clutter_noise_audit_manifest.json`：配对审计统计、范围和 CSV SHA。
- `h0_matrix_audit_summary.json`：60 个唯一 case、63 条 attempt 历史、矩阵状态、命令和运行身份；路径已规范化为仓库相对形式。
- `h0_raw_cleanup_summary.json`：完成 case 与中断 attempt 的 raw 清理量、保留 archive 总大小和归档内残留 raw-array 检查。
- `../h0_checkpoint_20261030/`：29/60 历史检查点及三份中断 attempt 审查 ZIP/index；用来审计恢复和 raw 清理，不作为最终统计结论。

## 结果边界

矩阵 `COMPLETED`，60/60 唯一 case 均有终态 compact archive。H0 分析 `COMPLETE_EVALUABLE`，使用 30 个独立 seed 和 10,000 次 seed-clustered percentile bootstrap。CUT、距离单元、Doppler 单元和处理周期均不作为独立重复。

A0_ON 每 case 有 1,300 个连续面积杂波 packet、6,508,782 个样本；30 个 0/+4 ns seed 配对的杂波、几何、相关性和实测噪声摘要字段全部一致。A0_OFF/A0_TO 是关闭杂波的对照 variant，不用于断言面积杂波是否存在。

本分析是无目标条件下的经验 CUT-hit fraction，不能解释为系统 Pd、系统 Pfa 或 GMTI 目标任务收益，也不解除 R22 Screening 的 `GATE_FAILED`。`PERFORMANCE_VERIFIED=false`；AI 必要性未得到支持。

60 个完成 case 共清理 72,060 个生成路径、472,370,816,936 bytes；另清理 3 个 interrupted attempt 的 21,830,424,640 bytes。compact case ZIP 合计 180,896,331 bytes。完整 60 个 compact case ZIP 保留在本机 `outputs/r22_h0_engineering_post_review_20261029/archives/h0/`，未提交到 GitHub。GitHub 包可核对 per-seed 汇总、配对 Stage2 审计、archive SHA 和执行身份，但无法重放原始 IQ 或逐一检查所有未上传 ZIP 的内部日志；该限制需在独立审查中保留。
