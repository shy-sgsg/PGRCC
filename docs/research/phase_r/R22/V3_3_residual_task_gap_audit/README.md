# R22-V3.3：确定性校正后的任务残差离线审计

**状态：** `OFFLINE_AUDIT_COMPLETE / PERFORMANCE_NOT_VERIFIED`
**范围：** 只读复核 V3.2 的 144 个 `+4 ns` compact case ZIP；未启动 Stage2、Production、CUDA 或新实验。

## 阅读顺序

1. [最终研究报告](FINAL_RESEARCH_REPORT.md)：核心结论、限制与阶段裁决。
2. [执行与质量审计](00_execution_and_quality_audit.md)：固定身份、复现命令、磁盘与状态标签。
3. [数据身份与可评价性](01_data_identity_and_evaluability.md)：样本分母、输入/归档身份、可读产物边界。
4. [方法信息条件](02_method_information_conditions.md)：Known、固定校正、target-present 估计与 paired-OFF 方法的公平边界。
5. [检测与机会重叠](03_detection_and_opportunity_overlap.md)：seed 级命中率、恢复量和机会重叠。
6. [共同成功参数误差](04_common_success_parameter_analysis.md)：period 8 多参数复算，以及从 evaluator association 重建的全十周期位置共同成功。
7. [目标保持与航迹输出](05_target_retention_and_track_analysis.md)：TrackManager 汇总及非 Pfa 的 false-confirmed 指标。
8. [残余误差机制](06_residual_error_mechanism.md)：物理时延估计残差及不能归因的部分。
9. [科研价值与方向判断](07_scientific_value_and_direction.md)。
10. [下一阶段唯一建议](09_next_stage_decision.md)：仅为建议，不授权新 CUDA 运行。

## 紧凑证据

机器可读分析和图表位于 [`evidence/`](evidence/)，主入口为：

- [`manifest.json`](evidence/manifest.json)：研究输入身份、脚本身份、状态、限制和分析产物 SHA-256。
- [`summary.json`](evidence/summary.json)：机器可读审计结论。
- [`case_validation.csv`](evidence/case_validation.csv) 与 [`archive_identity.csv`](evidence/archive_identity.csv)：144 个 case 的输入门、面积杂波、生产分支与归档 SHA 复核。
- [`case_method_screening.csv`](evidence/case_method_screening.csv)、[`paired_recovery_by_seed.csv`](evidence/paired_recovery_by_seed.csv) 和 [`paired_recovery_screening_summary.csv`](evidence/paired_recovery_screening_summary.csv)：方法级命中和 A0/C0/PK/estimated 配对恢复。
- [`parameter_opportunity_audit.csv.gz`](evidence/parameter_opportunity_audit.csv.gz)、[`parameter_evaluator_parity.csv`](evidence/parameter_evaluator_parity.csv)：115,200 条逐机会审计及 576 个全周期位置 evaluator parity 检查。
- [`position_all_periods_common_success_summary.csv`](evidence/position_all_periods_common_success_summary.csv)、[`position_all_periods_pairwise_common_by_seed.csv`](evidence/position_all_periods_pairwise_common_by_seed.csv)：全十周期位置 own-success、pairwise 与四方法共同成功比较。
- [`parameter_fourway_common_success_by_seed.csv`](evidence/parameter_fourway_common_success_by_seed.csv) 与 [`parameter_common_success_summary.csv`](evidence/parameter_common_success_summary.csv)：period 8 可读取 Detection 产品的其余参数复算和共同成功集。
- [`track_screening_summary.csv`](evidence/track_screening_summary.csv)、[`delay_estimation_summary.csv`](evidence/delay_estimation_summary.csv)：航迹与物理时延残差。
- [`figures/`](evidence/figures/)：七张报告图，包括全十周期位置配对差异与残余任务差距汇总。

## 空间与证据保留

分析器在 ZIP 内直接读取所需成员；没有复制、解压、删除或改写任何 V3.2 compact archive。144 个使用中的归档 SHA 与 V3.2 索引全部一致；额外历史归档保持未触碰。分析输出约 6.1 MB。项目工作树检查为约 4.8 GiB，低于 20 GB 最终上限，文件系统约有 11 GiB 可用；本轮仅清理自身 Matplotlib 临时缓存。

**完整 V3.2 ZIP 仍保存在本机 `outputs/r22_v3_2_screening_20261106/archives/screening/`，没有上传 GitHub。** GitHub 审查快照只能审查本目录的紧凑重算证据；不能声称 ChatGPT 已读取本机 ZIP、全部逐 beam 日志或原始 IQ。

**ChatGPT 固定快照审查请求：** [`CHATGPT_REVIEW_REQUEST.md`](CHATGPT_REVIEW_REQUEST.md)。该提示词在证据包提交完成后绑定固定 commit。
