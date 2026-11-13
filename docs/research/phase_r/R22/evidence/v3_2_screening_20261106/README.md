# R22 V3.2 Screening 精简审查证据

该目录面向 GitHub/ChatGPT 独立审查，包含完整筛选的 seed-level 汇总、方法覆盖、不可评价单元、参数与 TrackManager 摘要、CSI ROI 摘要及每个最新 case 的 archive SHA 索引。文件 SHA、设计和运行身份见 [`review_manifest.json`](review_manifest.json)。

## 阅读顺序

1. [`summary.md`](summary.md)：机器分析器的总体状态和解释边界。
2. [`case_audit.csv`](case_audit.csv) 与 [`archive_index.csv`](archive_index.csv)：288 个最新 case 的状态、面积杂波 gate、归档大小/SHA 和 raw 清理标记。
3. [`method_coverage.csv`](method_coverage.csv) 与 [`method_gaps.csv`](method_gaps.csv)：分母、覆盖率及 49 个方法级缺口。
4. [`seed_condition_detection.csv`](seed_condition_detection.csv)、[`seed_level_detection.csv`](seed_level_detection.csv)、[`seed_detection_summary.csv`](seed_detection_summary.csv)：目标机会、seed 聚合和覆盖率。
5. [`paired_seed_recovery.csv`](paired_seed_recovery.csv) 与 [`paired_recovery_summary.csv`](paired_recovery_summary.csv)：Current/Known/Estimated 的配对恢复。
6. [`parameter_metrics_by_seed.csv`](parameter_metrics_by_seed.csv) 与 [`parameter_summary.csv`](parameter_summary.csv)：own-success 参数 RMSE 和缺失机会。
7. [`csi_roi_summary_by_seed.csv`](csi_roi_summary_by_seed.csv)：宽 ROI 的 CSI tap 描述统计；不包含 target-only complex ROI。
8. [`track_metrics_by_case.csv`](track_metrics_by_case.csv)：每个可评价 ON 方法单元的 TrackManager 统计；不等价于系统 Pfa。
9. [`off_false_cut_by_seed.csv`](off_false_cut_by_seed.csv)：paired OFF 的 seed 汇总，仅描述性 false-CUT 比例。

## 可复核边界

本精简包约 3 MB。每项 CSV 都从本地 V3.2 `analysis/` 输出直接导出，并在 manifest 中记录字节数和 SHA-256。完整 288 个压缩 case archive（约 1.5 GiB）、每个 Production 分支的运行时 XML、日志和 CSI/CFAR 细节保存在本机 `outputs/r22_v3_2_screening_20261106/archives/`，没有上传到 GitHub。仅凭本目录不能声称已经独立读取那些本机文件。

OFF false-CUT 数值不是独立 H0 系统 Pfa。CSI active-support ROI 混合目标、杂波与噪声；强功率 ROI 按输入功率选取，tap power ratio 不是最终 SCNR。TrackManager 计数也不是独立虚警样本。

完整执行报告：[R22 V3.2 Native CUDA Screening 执行与结果](../../27_v3_2_screening_execution_report_20261109.md)。
