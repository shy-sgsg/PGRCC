# R6 紧凑审查证据

推荐先读 `../../R6_execution_report.md`，再按顺序查看 stagewise、CFAR、任务误差和阶段决策报告。

- `manifest.json`：基线、输入复核、程序身份、实验条件、证据计数和限制。
- `input_sha256_matches.csv`：R3 注册的 27 个 A0/A1/C 三 period 输入 SHA 与 R6 重建输入逐文件复核结果。
- `r6_task_metrics.csv`：官方 TrackManager evaluator 对六个 A0/C 条件的机器可读指标。
- `stagewise/`：54 条 A0/C 全 map 指标、36 条 A0/A0 全 map control 指标及 27 条逐 period cluster/selected/detection 计数；每行 map 保留身份 SHA。
- `cfar/`：36 branch-period margin 汇总、27 条 A0/C branch/union flip 汇总、18 条 union 校验及精确 hit-index NPZ。
- `cleanup_ledger.md`：大小检查、保留/删除记录。

所有压缩证据均由 `scripts/phase_r/analyze_r6_stagewise_divergence.py` 和 `scripts/phase_r/analyze_r6_cfar_branch_maps.py` 从本地 R6 输出生成。原始 Stage2 `.bin`、Production logs/runtime XML、完整 CSI/CFAR maps、检测和航迹记录仍在被 `.gitignore` 忽略的 `outputs/phase_r6_stagewise_divergence_20261022/`，未上传；缺少这些原始输入时，无法仅凭此 compact package 重跑 CUDA Production。`manifest.json` 和每个 map 的 SHA 用于说明当前本地证据身份，不替代原始数据本身。
