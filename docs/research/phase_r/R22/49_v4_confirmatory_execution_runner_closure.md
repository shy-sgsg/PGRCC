# R22-V4 240-case确认性执行器闭合

日期：2026-11-24

结论：CPU dry-run 与 fail-closed 门控验证通过；正式设计未冻结、未授权执行。

## 1. 静态矩阵展开

本阶段在当前设计草案上重新执行正式执行器 `dry_run()`，生成机器计划、case registry、默认拒绝授权模板与初始 checkpoint：

- target-present：180 cases（30 seed × 3速度 × 0/+4 ns）；
- 独立 H0：60 cases（30 seed × 0/+4 ns）；
- 总计：240 cases、120个同 seed/速度的时延配对批次；
- 注册方法/角色科学槽位：1,470，其中 target-present 1,260、H0 210；
- 计划 Production 调用：2,520，包括1,260个内部辅助调用；
- 计划 Stage2 调用：1,200–1,440，取决于0 ns场景身份是否可复用。

机器展开的实际计数以 `confirmatory_execution_plan.json` 和 `confirmatory_case_registry.json` 为准。它们是计划，不是运行覆盖或实验结果。registry SHA-256：`4847beb276ba2c0621f9de8f17fc37e72edace6632974726d9c2c0e0383a8a2d`。

## 2. 独立授权与默认拒绝

正式矩阵授权与既有 P0 单 case 授权分离。验证器绑定冻结设计/registry、case与batch白名单、method config、scenario template、runtime源码身份、Native CUDA backend、`use_corex=false`、GPU UUID、Stage2/Production二进制 SHA、完整归档 profile、输出根、MDE/非劣/H0阈值、资源证据、最小可用空间、授权有效期与撤销状态。授权必须有显式用户批准并通过 canonical SHA 校验。试图用 pilot 授权扩展到确认矩阵会被拒绝。

当前实际 dry-run 状态为 `NOT_AUTHORIZED`；模板为 `NOT_AUTHORIZED`，checkpoint 中240个 case 均为 `PLANNED`。registry 的阈值仍是 null，设计仍 `DRAFT_NOT_FROZEN`。本次没有向授权模板填值、没有修改设计阈值，也没有启动执行。

## 3. case事务、失败类型与续跑

正式执行器按固定 paired batch 调度，并为每个 case 写身份 SHA、attempt、状态与 archive/evaluation 记录。技术错误（崩溃、损坏、输入身份不符）只允许对相同已注册 seed/config 按冻结规则恢复，最多3次；算法任务差（估计失败、漏检、定位误差或fallback）保留原结果，不重新抽 seed。断点续跑必须验证 registry、授权 SHA、case 身份、归档 profile，以及之前完成归档的 ZIP/manifest；只有归档验证完成后才清理 case raw。归档缺失、manifest/CRC/SHA异常或身份变化时 fail closed。

资源门要求四种 case schema 各自的实测最大 compact 大小、实测原子 case 临时峰值、预留空间、实时 free bytes 和获批 GPU UUID；最低空间计算为所选归档尺寸总和加25%后备、单 case 临时峰值和授权 reserve 的总和。当前只有 target-present +4 ns 的实测 compact ZIP，没有 H0/0 ns完整输出资源测量，因此正式资源授权状态仍是 `BLOCKED`。

## 4. dry-run与测试范围

实际 CLI 命令：

```bash
python3 scripts/phase_r/r22_v4_confirmatory_executor.py \
  --dry-run \
  --output-root docs/research/phase_r/R22/evidence/v4_final_freeze_readiness
```

返回 `status=NOT_AUTHORIZED`、240 cases、120 batches、`execution_started=false`、`stage2_executed=false`、`production_executed=false`、`cuda_executed=false`、`ai_training=false`。授权拒绝、参数篡改、Pilot授权拒绝、resume、资源门和CPU模拟批处理测试均属于软件门控验证；模拟执行使用 monkeypatch/fake runner 与 archive，不代表真实 CUDA。

R12/R21/R22 定向回归共21个测试文件，254 passed、0 failed、0 skipped，pytest 3.804秒。测试摘要与授权/resume/resource-gate分项记录见 `evidence/v4_final_freeze_readiness/cpu_test_summary.json`、`confirmatory_authorization_test.json`、`confirmatory_resume_test.json`、`confirmatory_resource_gate_test.json`。

## 5. 下次真正运行前的必要动作

1. 用户/工程需求方先决定 Scheme A 或 B、`MDE_position_m`、检测覆盖非劣 margin、航迹保护和 H0 接受边界。
2. 完成候选 seed 的外部/远端 registry 审计。当前仅本地项目执行身份检查没有发现候选碰撞，外部归档不可访问；见 `seed_registry_local_audit.json`。
3. 若用户单独授权，测出四种 case schema 的完整输出体量与原子 case 峰值；这仍是尺寸 pilot，不是确认矩阵授权。
4. 设计冻结后，以实际获批输出根重新生成 registry、授权与 checkpoint；当前 dry-run registry 的 output_root 是证据目录，不可直接复用为执行目录。
5. 每个 case 启动前重新检查 GPU、磁盘和 authorization；本阶段未运行 GPU、Stage2 或 Production。
