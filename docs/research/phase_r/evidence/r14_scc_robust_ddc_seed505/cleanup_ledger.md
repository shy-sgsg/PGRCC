# R14 seed505 输出清理账本

## 清理对象

- 唯一目标：`outputs/phase_r14_scc_robust_ddc_seed505_20261025/`。
- 该路径在本轮首次运行前不存在，且本轮结束前只含本次 seed505 实验与其 evaluator 派生物；清理未触碰任何其他 `outputs/`、`.scratch/` 或已有用户文件。
- 清理前：1,598 files，6,007,532,085 bytes（约 5.60 GiB）。计数保存在 [`provenance.json`](provenance.json)。

## 保留的高价值证据

在删除 raw run root 前，已复制并核对：runner/case/compact manifests、冻结配置、12 个 branch manifests 与 runtime XML/configure logs/Production logs、逐期 detection CSV、TrackManager states/frames/payloads、CFAR geometry/meta、CSI tap summary/ROI manifests、C2/C3 adapter 与 reference gamma、Stage2 configs/reports/logs/truth CSV、全部 evaluator CSV、目标候选 map cell audit、GPU/build/source provenance 和重绘脚本/图表。

这些 machine-generated evidence 文本保留原始 CRLF 与日志表格尾部空格；`.gitattributes` 仅对本 R14 evidence 子树声明相应 whitespace 规则，确保 `git diff --check` 不把原始日志格式误报为源码空白问题。没有修改日志字段或实验数据值。

证据包内 `target_candidate_cell_audit.csv` 的 9 个 ON target candidates 均在原 Production final-union CFAR map 对应 cell 上为正 hit，map SHA 与 evaluator audit 一致。CFAR map 的完整命中数、valid CUT、geometry/log 数值和 SHA 均保存在 `analysis/cfar_union_audit.csv`；CSI target ROI 原始数值和 period-level support/response/hash 在 `analysis/csi_target_roi_samples.csv`、`analysis/csi_period_metrics.csv` 与 `analysis/csi_results.csv`。

## 删除内容及理由

删除该单次实验目录中的完整生成 IQ `.bin`、重复的 `input_merged.bin`、完整 CSI `.npy` maps、CFAR `.f32` maps、临时中间数据、重复绘图/运行派生文件。Stage2 生成数据由仓库源码、保存的 R14 config、seed505 和场景 manifest 可重建；原始文件的大小/hash、配对关系与测量结果已写入 machine manifests 和 compact CSV。完整大 arrays 不作为 Git 长期产物保留，以遵循保留必要审计与高价值结果、清理可复现大文件的指示。

删除动作只针对上方唯一 R14 output root，已执行并核验：该目录现在不存在；共删除 1,598 files、6,007,532,085 bytes（5.595 GiB）。清理后的文件系统状态为 464G 总量、415G 已用、26G 可用；这是一项当前磁盘快照，不声称只由此次删除造成同等数值的空闲空间变化。证据包为 30,458,914 bytes，保留运行时来源路径，因此 raw root 的 absolute paths 在清理后不再可访问；重新复现需按 README 命令生成新的 output root。
