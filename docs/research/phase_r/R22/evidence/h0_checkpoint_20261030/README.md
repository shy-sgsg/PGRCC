# R22 H0 检查点证据

本目录保存 2026-10-30 H0 矩阵的 **29/60 case 检查点**，不是最终矩阵结论。

- `h0_analysis_manifest.json`：冻结设计摘要、执行身份、已校验归档索引和完整性状态。
- `h0_per_seed_rates.csv`：已归档 seed 的每 seed CUT-hit fraction。
- `h0_per_condition_summary.csv`：当前可用 seed 的条件汇总；不完整条件没有完整置信区间。
- `h0_paired_delay_summary.csv`：当前配对 seed 的描述性 +4−0 差值；未达到 30 seed 时不得作为最终推断。
- `h0_interrupted_attempt_archive_index.json`：seed1935015/+4 ns 两次中断 attempt 的状态、SHA、清理量和 archive 位置。
- `interrupted_attempt_archives/`：两次中断现场的紧凑 ZIP。attempt002 的 archive 包含分支清理记录；attempt001 的 cleanup JSON 被 archive policy 排除，其 SHA/大小及 aggregate 删除量在 archive index 和仓库清理台账中记录。

检查点中 14 个完整 0/+4 ns 配对的 Stage2 杂波/噪声摘要逐项一致。剩余 case 未完成，manifest 状态为 `INCOMPLETE_NOT_EVALUABLE_AS_FULL_MATRIX`；不得从该快照声称完整 H0 性能结论。

每个完成 case 的完整紧凑 ZIP 存放在本机 `outputs/r22_h0_engineering_post_review_20261029/archives/`，未全部提交到 Git。GitHub 检查点文件提供已核验的 seed 汇总、身份和 SHA 索引；不能替代对未随仓库提供的逐 case archive 内容进行核验。
