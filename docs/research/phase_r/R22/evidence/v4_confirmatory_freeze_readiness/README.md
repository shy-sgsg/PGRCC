# R22-V4 P0 冻结准备度证据

本目录包含 2026-11-23 对 P0 完整归档的离线独立重分析，以及基于 12-seed V3.3 历史机会表的统计敏感度和 V4 源码计划资源展开。

## 复现入口与身份

- 入口脚本：[`analyze_r22_v4_p0_freeze_readiness.py`](../../../../../../scripts/phase_r/analyze_r22_v4_p0_freeze_readiness.py)
- P0 ZIP 本地路径：`outputs/r22_v4_p0_seed1937003_20261123/archives/engineering/engineering__seed_1937003__vr_2d5__snr_20__delay_4ns__targets_20__periods_10__attempt_001.zip`
- P0 SHA-256：`0974e1dfd1467cdd2bdced856e8c673d40c8966a53ab05261d0c5db2d99c4b02`
- P0 执行源码 commit：`715639c8e9aad7359eb1a5058f9238c0d51f99a1`
- 分析基线：`f84a3607a5b10c4f0a79dacb9d389f60e87abd06`
- 分析不启动 Stage2、Production、Native CUDA、确认矩阵或 AI，不把 13 GB ZIP 解压到磁盘。

## 产物

- 身份和复核：`archive_validation.json`、`source_identity.json`、`analysis_summary.json`。
- P0 指标：`p0_method_summary.csv`、`p0_recomputed_parity.csv`、`p0_pairwise_common_position_rmse.csv`、`p0_coverage_recovery.csv`、`p0_per_period_metrics.csv`、`p0_per_target_metrics.csv`。
- Detection/TrackManager/CFAR：`p0_track_summary.csv`、`p0_track_payload_lineage_audit.csv`、`p0_target_track_lineage.csv`、`p0_cfar_geometry_summary.csv`。
- 目标复响应：`p0_target_response_summary.csv`，按 90,000 行固定 ROI cell 结果汇总。
- 历史精度敏感度：`historical_v33_seed_level_sensitivity.csv`、`historical_v33_sensitivity_summary.csv`、`power_precision_sensitivity.json`、`position_mde_precision_by_n.csv`。
- 设计与空间：`case_plan_counts.json`、`resource_model.json`、`p0_resource_categories.csv`。
- 图：`figure_p0_coverage_and_position.svg`、`figure_p0_target_response.svg`、`figure_v33_precision_sensitivity.svg`。

完整 P0 ZIP 为本地唯一完整 run archive，没有纳入 GitHub。当前 commit 的派生表和报告可供审查；审查者若不能读取本机 ZIP，应将原始输入重放和完整运行包成员验证标为 `NOT_VERIFIED`。该 ZIP 需保留到独立审查完成，再按清理记录处理。
