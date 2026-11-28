# R11 紧凑审查证据包

该包对应 seed303 / period 1 / beam 50 的六点通道 2 delay scan。它保存完整实验 manifest、六组实际 runtime XML 和 config dump、完整 production log、CFAR geometry/numerical diagnostics、CSI tap summaries 与 target-cell complex response、检测 CSV、cluster lineage、TrackManager 诊断、汇总表、图和首次失败。仓库绝对路径以 `$REPO_ROOT` 表示。
本机固定的任务数据目录在配置副本中记为 `$MISSION_DATA`，Core/工具根目录记为 `$PIPE_ROOT`；绝对宿主路径已从审查副本中移除。

runner 源码：[run_r11_local_delay_scan.py](../../../../../scripts/phase_r/run_r11_local_delay_scan.py)。

## 本机复现与输入边界

本包不包含 49,204,480-byte A0/A1 协议 IQ、六份 corrected IQ、编译后的 R7 binary 或全图 CSI `.npy`。manifest 记录原始/校正输入的路径、大小和 SHA-256；全图 CSI 临时 tap 的大小与 SHA-256 在 cleanup ledger 中。原始 IQ 和 Native CUDA binary 需在本机输出目录存在，且 binary SHA 必须为 `de3026ed4f1263be4143dcbcd10ccbb5e53b4be357e21906e1bf19ea0ec29d00`。

从包含 R8 seed303 数据、`build_r7/GMTI_pipe_core` 和本次 `outputs/phase_r11_local_delay_scan_20261024/` 的原工作树重新计算汇总/图：

```bash
python3 scripts/phase_r/run_r11_local_delay_scan.py --mode analyze
```

新建输出目录的完整执行顺序为 `--mode prepare` 后 `--mode run --attempt 2`，再 `--mode analyze`。runner 使用固定输出路径 `outputs/phase_r11_local_delay_scan_20261024/`；请在隔离的新工作树/新实验副本执行，避免覆盖本次已审计产物。每个正式 Native CUDA 命令、runtime config、退出码和分支门控均记录在 `experiment_manifest.json` 与对应 `conditions/<condition>/branch_manifest.json`。

重建源 binary 需要 R7 基线源码和本机 CUDA 构建环境；R11 使用 `USE_COREX=OFF`。本次六个分支是实际 Native CUDA Production 证据，CPU 仿真没有替代它们。

## 目录

- `experiment_manifest.json`：完整机器 manifest，包含失败和成功尝试、输入/二进制身份、门控、GPU 快照及每分支路径/哈希。
- `scan_metrics.csv`、`production_candidate_detections.csv`：六点指标和全部 91 条 Production detection。
- `target_cell_csi_responses.csv`、`csi_map_cleanup_ledger.csv`：固定 cell 复数 tap 与被清理全图 tap 的大小/hash 审计。
+ `conditions/`：六个正式分支的实际 XML、runtime dump、日志、输入改写审计、CFAR、CSI summary、detection、cluster lineage、timing 和 TrackManager CSV。
- `native_text_artifacts.zip`：六个正式分支和首次失败的完整 production/configure logs、runtime text dumps、GPU 快照；成员路径保留目录结构，文本记录按证据包约定替换宿主机绝对路径。
- `failure_attempt_01/`：初次错误 selector 导致读取越界的 XML、config JSON、manifest 和输入审计；失败日志位于上述文本归档，不作为算法数据。
- `package_files.csv`：证据包内文件相对路径、字节数和 SHA-256 清单。
- `charts/`：输入 closure、CSI/phase、候选位置误差及 Jacobian closure。

`PERFORMANCE_VERIFIED` 仅限报告中的固定-cell 机制敏感度，不代表跨 seed 统计、Pd/Pfa 或任务级单调改善。
