# R22 V1A seed1930101 精简审查证据

该包对应 `R22_EXTENSION_20261028_V1A` 的首个 0 ns CUDA Production case。总实验矩阵为 `GATE_FAILED`，本包不代表完整矩阵已执行。

## 阅读顺序

1. [`case_summary.json`](case_summary.json)：设计、源码/二进制身份、Stage2 面积杂波、Production/评价状态和 robust gamma 支持不足行。
2. [`branch_status.csv`](branch_status.csv)：41 个方法/角色记录的执行状态、Production return code、reference/CFAR 门状态。
3. [`matrix_manifest.json`](matrix_manifest.json) 与 [`r22_run_manifest.json`](r22_run_manifest.json)：矩阵和 case 执行身份。
4. [`stage2/A0_ON_report.md`](stage2/A0_ON_report.md)、[`stage2/A0_ON.log`](stage2/A0_ON.log)、两个实际 runtime XML 与 `scenarios/`：连续面积杂波注入及运行配置。
5. `production/` 下的 Production XML、runtime config dump 文本副本、逐 beam/cycle 日志、CSI tap、CFAR geometry、run manifest。
6. [`references/reference_gamma_mode_a_off.csv`](references/reference_gamma_mode_a_off.csv) 与 [`references/reference_gamma_mode_b_on.csv`](references/reference_gamma_mode_b_on.csv)：完整 12,090 行参考 tap。`file_index.json` 记录每个归档文件的来源、大小和 SHA-256。
7. [`raw_cleanup.json`](raw_cleanup.json)：本 case 可再生 binary 的清理逐文件台账。

## 审计要点

- Mode-A/OFF 与 PKR/Mode-A/OFF reference 内容相同；Mode-B/ON 与 PKR/Mode-B/ON reference 内容相同。重复 payload 仅归档一次，SHA-256 保留在 case summary 和 file index。
- C4/PKR 参考使用 133-bin 分组、`min_support=8`。Mode-A/OFF 有 15 行、Mode-B/ON 有 11 行 `NOT_EVALUABLE`，原因均为 `insufficient_robust_support`；支持数低于 8。生产进程返回码 0 不会覆盖这一质量门失败。
- `C3_modeb_ON` 是一个通过分支的代表性审计样本；它不代表所有 C4/PKR 角色通过。
- `.bin` IQ 与 CSI `.npy` 中间文件未归档；其身份、删除路径和理由由原始 case manifest 与 cleanup ledger 保存。本包保留复现命令所需的配置与可判读的精简证据，不声称无需重跑即可重建原始 IQ。
- `runtime_config_dump.normalized.txt` 只移除了每行结尾的空格/制表符，以保留有效字段且通过仓库空白检查。原始 runtime dump 保留在本机 `outputs/`；`file_index.json` 同时记录原始 source SHA 和归档副本 SHA。Production XML 是原样归档。
- 本包共 95 个文件，约 4.7 MB。`file_index.json` 的清单覆盖归档的运行产物；`case_summary.json`、`branch_status.csv`、matrix/run/cleanup 摘要另由各自文件承载。
