# R9 紧凑审查证据包

本包保存 R9 的机器可读摘要、哈希、实际 Production 配置/日志/CSI tap/CFAR geometry 与任务评价。证据包中的 CSV/log 副本统一为 LF；log 行尾仅去除表格对齐用空格，Production 原始 log 未改动且其 SHA 在 branch manifest 记录。完整 CSI/CFAR arrays、原始 R8 输入 IQ 和 TrackManager run tree仍留在本机忽略目录 `outputs/phase_r_r9_deterministic_delay_20261023/`，没有复制进 Git。R9 复用 R8 seed202/303 输入；Stage2 未重跑。

## 阅读顺序

1. `manifest.json`：阶段状态、基线、Native CUDA/GPU、二进制/runner 身份和限制。
2. `records/estimator/`：预生产估计、物理 preflight、D1/D2/D3、公式诊断。
3. `records/production_branches.csv` 与 `records/production_period_inputs.csv`：12 个分支、每一期 raw/corrected IQ SHA、命令、有效 runtime XML、log、CSI tap 和 CFAR geometry 的本地路径。
4. `records/production/seed*/R9_*/{ON,OFF,TO}/`：所列 branch manifest、实际 runtime XML、配置快照、逐 beam Production log、CSI tap summary、CFAR geometry、detection CSV 和 cluster→detection lineage。
5. `records/cfar_numeric_audit_summary.csv` 与 `records/cfar_map_audits/`：8 组真实 CUDA map dump replay 的三期数值审计 JSON、manifest 和 diagnostic runtime XML；不含重复 raw map。
6. `records/evaluation/`：task metrics、target lineage、R8 B/ON target neighborhood、F1/F2 物理闭合和总体结果。
7. `records/failure_cases.json`：首次 evaluator 中止、B 条件候选丢失、残余漏检和历史输入可用性。

## 关键结果

- 12/12 R9 Native CUDA Production branches 和 8/8 独立 CUDA CFAR 数值审计通过。所有 ON/OFF/TO 角色使用一致的 normalized processing XML。
- R3 seed101 D3 固定标定 `3.409965 ns` 在 seed202/303 将正确 detection 从 B 的 `0/6` 恢复到 `5/6`，与 A0/Known 的观察计数相同；F2 物理闭合未达到 Known。
- ON batch D3 分别估计 `3.321230 ns`、`2.987994 ns`；不读取 estimator truth，但也不构成实时在线部署证明。
- 无统计显著性 Pd/Pfa 主张；radial velocity 和校准置信区间不可评估。无 AI 训练。

## 原始运行和复现

完整本地 manifest 位于 `outputs/phase_r_r9_deterministic_delay_20261023/manifest.json`。本轮命令阶段为 `preflight → production → evaluate → summarize`。重跑需在提交基线 `22cefb8e947a8a3565a88445fe2c1d3616408c47` 的隔离 worktree 运行，因为 R9 runner 要求该冻结 HEAD；将 R9 runner 从当前提交复制到隔离 worktree，并链接现存 R8 output root 与 `build_r7`，再执行：

```bash
python3 scripts/phase_r/run_r9_deterministic_delay_estimator.py --phase preflight --output-root outputs/phase_r_r9_replay
python3 scripts/phase_r/run_r9_deterministic_delay_estimator.py --phase production --output-root outputs/phase_r_r9_replay
python3 scripts/phase_r/run_r9_deterministic_delay_estimator.py --phase evaluate --output-root outputs/phase_r_r9_replay
python3 scripts/phase_r/run_r9_deterministic_delay_estimator.py --phase summarize --output-root outputs/phase_r_r9_replay
```

GPU 依赖的 Production 自动使用本机 Native CUDA 和 `USE_COREX=OFF`。原始 seed101 标定 IQ 已清理，仅保留历史输入 SHA 和冻结估计值；完整 R8 seed202/303 raw IQ 与本地 CUDA 输出需预先存在。


## 清理台账

R9 完成全部运行和评价后，按 `cleanup_ledger.json` 校验并删除了 36 个可重建的 corrected protocol IQ 副本，共 1,771,361,280 bytes（约 1.65 GiB）。原始 R8 IQ、branch manifests 中的 corrected SHA、CUDA Production 结果、日志、CSI taps、CFAR geometry 与 8 组 CFAR map 审计均保留。
