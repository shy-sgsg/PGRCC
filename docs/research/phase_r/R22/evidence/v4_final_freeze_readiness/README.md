# R22-V4 最终冻结准备度证据

本目录保存 CPU 统计模拟、P0 compact 归档验证、四类 case schema 夹具、240-case dry-run、授权/续跑/资源门测试及最终决策摘要。**本目录不是新的 GMTI 实验结果。** 本阶段没有运行 Stage2、Production、Native CUDA 确认矩阵或AI训练。

## 阅读顺序

1. `final_freeze_readiness.json`：当前可供用户决策的状态及未闭合门。
2. `statistical_hypothesis_comparison.json`、`position_power_corrected.csv`、`joint_position_coverage_power.csv`、`bootstrap_coverage_audit.json`：定位方案A/B、seed级联合模拟和区间校准；宽参数网格中明确区分 t-reference 规划与 studentized bootstrap-t 代表性校准。
3. `missing_opportunity_policy_candidates.json`、`h0_statistical_contract.json`：机会缺失规则与独立H0统计对象。
4. `compact_archive_validation.json`、`compact_archive_parity.csv`、`compact_archive_size_report.json`：新生成的本机compact ZIP完整性、11表独立复算parity和实际体积。
5. `compact_schema_by_case_type.json`、`seed_registry_local_audit.json`：四种 synthetic CPU schema 和当前项目本地身份扫描；它们不等于外部seed注册审查或四schema真实CUDA尺寸测量。
6. `confirmatory_execution_plan.json`、`confirmatory_case_registry.json`、`confirmatory_authorization_template.json`、`confirmatory_dry_run.json`：240-case/120批次静态计划；模板保持默认拒绝。
7. `confirmatory_authorization_test.json`、`confirmatory_resume_test.json`、`confirmatory_resource_gate_test.json`、`cpu_test_summary.json`：CPU自动化验证结果。
8. `resource_projection_updated.json`、`cleanup_ledger.json`：存储模型和已删除的两份重复compact账目。

## 关键结果

- 定位正式候选：seed为独立单位，10,000次 studentized bootstrap-t；位置与覆盖共同成功。Scheme A 检验改善大于零、另设点估计MDE；Scheme B 检验真实改善超过MDE。二者科研主张不同。
- 历史 V3.3 12-seed 方差只作为规划输入；V4 causal-prefix方差未实测。bootstrap-t真实规则用合成seed Monte Carlo验证代表性边界，27,000行宽网格使用 t-reference planning，不声称是精确 bootstrap 功效。
- 本机compact ZIP位于 `outputs/r22_v4_p0_seed1937003_20261123/archives_compact_candidate/p0_target_present_plus4ns_compact_schema_v2.zip`，大小17,603,158 bytes，SHA-256 `71b50b6110e04403b2152f8e292bb3e7df9ae4201fd2790d9d97595e7b12ae5a`。不提交Git。
- 422个必需成员、8个 evaluator成员，430成员SHA/CRC通过；11张核心重算表与历史完整P0复算逐字节一致。重分析只打开compact，没有读取13GB完整ZIP。
- 四种 target-present/H0 × 0/+4 ns schema通过CPU synthetic fixtures；目前只有target-present +4 ns有真实compact体积。
- 执行器dry-run为240 cases、120 batches，`NOT_AUTHORIZED`；授权测试/恢复测试在CPU模拟环境通过。Stage2、CUDA、Production矩阵与AI均未启动。
- 本地 seed身份审计检查125份直接identity manifests及418个ZIP中421份小型identity manifests，没有发现1933001–1933030或1936001–1936030已执行碰撞；远端和外部归档未访问，不能称全局registry审计完成。
- 项目根目录实测约18.38 GB，低于20 GB最终保留限额；未来完整矩阵保留代理为2.46–5.28 GB，叠加当前目录会超过20 GB，必须把审查后的清理写入执行方案。

原始P0 SHA-256 `0974e1dfd1467cdd2bdced856e8c673d40c8966a53ab05261d0c5db2d99c4b02` 仍在本机保留。未经用户批准不得删除或迁移。机器资源预测不是全矩阵保证；实际执行还要求四schema测量、实时磁盘门、用户阈值决定、外部seed注册审计和单独授权。
