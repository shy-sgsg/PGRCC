# R22-V4 P0 Native CUDA 单 case 证据

本目录保存小体积、Git 可审查的摘要、机器审计 JSON/CSV 和图表。Stage2 原始输入及完整 Production 数据已打包到本机 compact ZIP；ZIP 未加入 GitHub。

## 必读材料

ChatGPT 固定快照审查请求会在报告提交后追加；提交前此链接尚未生成。


1. [预检、seed 与授权](../../37_v4_p0_preflight_and_authorization_20261123.md)
2. [Native CUDA 执行与归档](../../38_v4_p0_native_cuda_execution_20261123.md)
3. [科研结果与方向评估](../../39_v4_p0_preliminary_research_assessment_20261123.md)
4. [确认性冻结准备度](../../40_v4_confirmatory_freeze_readiness_20261123.md)

## 可机器核查的摘要

- build_identity.json：执行源码、二进制 SHA、Native CUDA/Release/USE_COREX=OFF 与研究总纲 blob。
- execution_authorization.json、execution_plan.json、preflight_manifest.json：用户授权范围、唯一 case、命令、设备/磁盘监控和未启动确认矩阵声明。
- seed_registry_final_audit.json：本地 seed 注册扫描范围及限制。
- stage2_input_audit.json：面积杂波、+4 ns 注入身份、背景复用和 Stage2 输入核对。
- production_branch_audit.json：18 个方法/角色分支、runtime XML、分支状态、CFAR geometry 与 TrackManager 审计状态。
- causal_estimator_audit.csv：M1/Huber 每周期输入、估计值、是否 fallback 及 truth-use flags。
- detection_track_audit.json：target-period 命中、参数估计、航迹摘要、CUT 分母及虚警可评价状态。
- target_response_audit.json：CSI_AFTER 目标 ROI 复数行完整性和 target-only 功率比描述统计。
- archive_validation.json、cleanup_ledger.json：ZIP 验证、断点恢复和仅清理本 pilot raw 的记录。
- resource_usage.json、final_pilot_verdict.json：运行耗时、磁盘峰值/结余和阶段标签。

## 本机完整归档（不会随 Git 推送）

相对项目根路径：
outputs/r22_v4_p0_seed1937003_20261123/archives/engineering/engineering__seed_1937003__vr_2d5__snr_20__delay_4ns__targets_20__periods_10__attempt_001.zip

- 大小：13,037,254,578 bytes（约 12.14 GiB）
- SHA-256：0974e1dfd1467cdd2bdced856e8c673d40c8966a53ab05261d0c5db2d99c4b02
- archive manifest SHA-256：4f96e4f1fb1cf7db1ebe2d1d07c79a8537b47fccac1efa3f4fbd7c7a0e4036a0
- 4,461 个成员、CRC 校验通过、422 个 V4 必需成员齐全。

ChatGPT 不能仅凭 GitHub commit 访问此本机 ZIP。独立审查应以固定 commit 中的报告和本目录摘要为可访问证据；涉及 ZIP 内未发布原始成员的判断须标记 NOT_VERIFIED，不能假称已检查归档。

## 图表

- figures/causal_delay_estimates.png：因果时延估计值。
- figures/common_position_rmse.png：共同成功机会的位置 RMSE 描述比较。
- figures/on_detection_coverage.png：单 seed ON target-period 覆盖率。
- figures/target_only_response_ratio.png：TO-only 固定 ROI 功率比。
- figures/production_branch_runtime.png：分支运行耗时。
- figures/disk_free_space.png：pilot 期间空间监控。

图表只帮助阅读，不替代 CSV、manifest 与报告中的限制说明。

## 保留与清理

归档已通过 SHA、manifest、ZIP CRC 和必需成员检查后，清理 ledger 对应 raw case 已清除。保留此单 case compact ZIP 作为高价值完整运行审计，保留上述小型摘要和两份既有 failure/incomplete capture 证据。没有删除历史阶段结果或其他用户数据。本机最终项目占用需低于 20 GiB；报告生成后会再次实测。
