# R22-V4 Phase H：size pilot、资源闭环与确认矩阵冻结准备

日期：2026-11-28
研究总纲：`docs/research/RESEARCH_MASTER_PLAN.md`，blob `e637e3769bde7814298ff012d0f42301884e2985`（正文未修改）
设计：`config/r22_v4_causal_output_complete_confirmatory_draft_20261113.json`，当前状态 `FROZEN`
正式确认矩阵：`NOT_AUTHORIZED`，本报告未启动该矩阵。

## 1. 本阶段回答的问题

本阶段服务于研究总纲中的“建立可信物理与任务评价基准、识别传统方法能力边界，再决定是否需要方法创新”。已批准的研究方向筛选方案比较真实 `+4 ns` 条件下的因果前缀校正与固定 D3，并保留已知物理校正上限、Current、Huber、因果 M1、批处理诊断和独立 H0 作为审查边界。size pilot 的作用是确定这套确认性设计是否能在真实 Native CUDA 输出链中逐 case 执行、验证、归档、清理和续跑；它们不是算法性能验证。

用户已批准的筛选参数保持不变：Scheme A；位置 MDE 3 m；每个速度层共同成功支持至少10；检测覆盖差的非劣界为1个百分点；航迹覆盖只作完整描述性报告；target-present 与独立 H0 各30个 seed；位置区间采用 seed-cluster studentized bootstrap-t；检测/航迹覆盖差采用 paired t；H0 两个独立 seed 事件率的单侧95% exact 上界均须严格小于10%。所有统计缺失与零方差规则继续 fail-closed。

## 2. 冻结的矩阵与 seed 身份

冻结矩阵为240个 case、120个配对 batch：target-present 180个（30 seed × 3速度 × 2时延），H0 60个（30 seed × 2时延）。Target-present 使用 seed `1933001–1933030`；独立 H0 使用 `1936001–1936030`。registry ID 为 `R22_V4_PHASE_H_SEED_REGISTRY_20261126`，规范化内容哈希为 `ec73bb776882a25cde4250d0092fec24157b7d38aaea6961729a909f1826abfd`。

已扫描当前可访问的 PGRCC/R22 实验身份与已保存注册表，未发现这些候选 seed 与已记录 case identity 冲突。该结果只覆盖本地可读范围；外部、离线或未提供的 registry 不可验证，因此不声称完成全局唯一性证明，也不据此替换 seed。此限制已冻结为审查边界，结果观察后不得重抽 seed。

复审修复后的 CPU dry-run 产物记录 registry、120个 batch、240个 case、授权模板、运行计划和完整 runtime source identity。冻结设计SHA-256为`5f3cfa00f0409ea0129fea24b799bab8b6648cad2790d3accfe0c9399fb68455`，registry SHA-256为`1546ed2c90836c6cf163547080352f1bd16b93c18c1e28f0ff6011f7347cd57c`，runtime source identity SHA-256为`8cb61445ab92f83ff89c05755f6385bcb135c5bc51dec69851a59aaafac2bb66`，seed registry SHA-256为`ec73bb776882a25cde4250d0092fec24157b7d38aaea6961729a909f1826abfd`。dry-run 状态为 `NOT_AUTHORIZED`，`execution_started=false`；输出独立的`confirmatory_dry_run_checkpoint.json`，不创建会阻止首次正式执行的`confirmatory_checkpoint.json`。没有 Stage2、Production、CUDA 或 AI 运行。产物见[复审后 CPU dry-run](evidence/v4_phase_h/preflight_20261128_executor_reviewed/confirmatory_dry_run.json)。

## 3. 三类已授权 Native CUDA size pilot

三种缺失 schema 的 pilot 均已执行。完整矩阵尚未执行。

| Schema / pilot | 终态 | 观测完整 case 峰值 | Full shards /源数据 | Compact 保留物 | 单 case 耗时 |
|---|---|---:|---:|---:|---:|
| Target-present 0 ns，seed1937004 | `PASSED`；单 case 可评价 | 4,685,795,328 B | 3,412,294,746 B / 5,660,167,704 B，8 shards | 3,710,726 B | 538.872 s |
| H0 0 ns，seed1937005，attempt003 | `H0_EVALUABLE_WITH_GAPS`；资源测量完成 | 3,687,739,392 B（attempt003） | 2,925,687,241 B / 5,159,540,309 B，8 shards | 328,855 B | 131.976 s |
| H0 +4 ns，seed1937005 | `H0_EVALUABLE_WITH_GAPS`；资源测量完成 | 14,705,225,728 B | 10,242,105,938 B / 23,079,822,542 B，28 shards | 1,749,025 B | 1,548.000 s |

H0 0 ns 的 attempt001 中断、attempt002 触发资源门失败，均保留在 pilot matrix manifest；attempt003 完成。该处理证明 pilot matrix 可按同一 case identity 重试，并没有把失败 attempt 记作通过。H0 的 `WITH_GAPS` 不是确认性 H0 事件率结果。所有 pilot compact ZIP 和完整归档 set manifest、逐 shard 身份、资源采样记录及 matrix manifest 均保留；展开 raw 与 full shards 只在完整归档验证、compact 验证及清理台账之后回收。

这三次 pilot 不回答 Pd、Pfa、定位、速度或跟踪性能问题；`PERFORMANCE_VERIFIED=false`。P0 target-present +4 ns full ZIP 仍保留原路径，大小13,037,254,578 B，历史记录 SHA-256 为 `0974e1dfd1467cdd2bdced856e8c673d40c8966a53ab05261d0c5db2d99c4b02`。本阶段没有重新读取13 GB ZIP计算外层 SHA，也没有迁移或修改它。

## 4. 存储与逐 case 执行方式

原执行器为每个 case 保留完整 output-complete ZIP，240个 case 会产生不可接受的长期保留量。执行器现采用：

1. 将完整 case 分片归档；保存全量成员 manifest、分片 SHA 与身份；逐 shard 校验内容 SHA 和 ZIP CRC。
2. 从已验证 full shards 生成 V4 compact；验证 compact 的 SHA、成员清单和 CRC。
3. full shards 清理后，把完整 archive-set manifest 和分片/清理审计回执以 gzip-9 保留；回执内容解压后仍是原始完整 JSON。
4. 只有回执与 compact 重新验证通过后才删除 raw case；checkpoint 同时绑定 case ID、冻结 schema identity 和两类 archive 身份。
5. 中断恢复时先重建并验证压缩回执、compact 和 case identity；已归档 case 不会重跑。遇到失败或资源不足，按相同注册 case identity 续跑，不覆盖 attempt。

CPU 集成测试实际模拟“回执与 compact 已发布、checkpoint 尚未写入 ARCHIVED”后中断；续跑恢复该 case、验证归档并清理 raw，后续 batch 完成，已执行 case 没有重新调用 runner。这个测试验证事务恢复逻辑，不是 CUDA 证据。

## 5. 资源预算

最终保留物按 compact + gzip-9 full receipt 计算。三个 schema 的 receipt gzip 大小来自实际 pilot；target-present +4 ns compact 使用 P0 已审 compact。该 schema 的新 sharded receipt 未在真实 V4 运行中产生，所以用 P0 的4,461个完整输出成员、当前512 MiB分片计划及 pilot 的完整 receipt 结构生成可复现大小模型；模型 gzip-9 为723,929 B，再加10%并向上取整到百 KB，按800,000 B/ case 预算。该项明确是模型，不伪称实测；它不承担任何科研结论。

| Schema | Case 数 | 每 case compact + receipt 上界 | 全矩阵保留量 |
|---|---:|---:|---:|
| Target-present 0 ns | 90 | 3,852,669 B | 346,740,210 B |
| Target-present +4 ns | 90 | 18,403,158 B | 1,656,284,220 B |
| H0 0 ns | 30 | 428,428 B | 12,852,840 B |
| H0 +4 ns | 30 | 2,255,376 B | 67,661,280 B |
| **合计** | **240** | — | **2,083,538,550 B** |

全矩阵 compact+receipt 保留量加25%资源不确定余量为 **2,604,423,188 B**。最坏 P0 full-case 临时峰值 **29,821,104,128 B**，另保留 **10,737,418,240 B** 自由空间，因此正式矩阵首批的单文件系统最低空闲量为 **43,162,945,556 B**。2026-11-28 最新预算实测项目所在 ext4 可用 **58,355,437,568 B**，高于此代理 **15,192,492,012 B**；独立`/tmp`可用 **7,394,082,816 B**，不与ext4混算。P0峰值是主机文件系统free-space观测差，可能包含并发主机活动；它作为保守代理使用。

最新资源证据记录项目根 allocated bytes **17,372,565,504 B**。加上全矩阵 compact+receipt 预测和25%余量，最终预测 **19,976,988,692 B**，低于20,000,000,000 B上限 **23,011,308 B**。执行器会在新 case 前按授权中的全部 case 计算最终态；每个 case 完成归档校验与 raw 清理后再检查实际项目占用，并在报告 batch 完成前再次实测。超过cap会写入checkpoint并暂停；active raw中间空间由独立filesystem free-space gate控制。

先前三份CPU dry-run及本次被复审版替代的旧预检均只含可再生成计划/registry/checkpoint，不含实验数据；均在核验替代物并逐文件记录SHA后定向清理。第二次清理释放2,330,624 allocated bytes，记录见[复审版dry-run替换清理台账](evidence/storage_reclaim_20261128/executor_reviewed_dryrun_cleanup_ledger_20261128.json)；前一批清理见[原清理台账](evidence/storage_reclaim_20261128/superseded_dryrun_cleanup_ledger_20261128.json)。三份审计库存压缩替换释放11,386,880 allocated bytes；唯一P0、pilot compact/receipt、真值和失败证据保留。

三 schema pilot 的历史报告、脚本计算的 P0 receipt 模型、输入身份及当前机器预检见[机器资源预算](evidence/v4_phase_h/resource_budget_20261128.json)和[预算生成脚本](../../../../scripts/phase_r/r22_v4_resource_budget.py)。资源证据 `PASS_WITH_CONSERVATIVE_RECEIPT_MODEL` 只表示执行空间预算满足，不代表实验性能通过。

## 6. 运行时估计与机器状态

以每类一个 pilot/P0 单 case 耗时线性外推，240 case 约 **49.41 小时**；加25%时间规划余量约 **61.76 小时**。这是单次观测的调度估算，没有覆盖外部 GPU 负载变化、系统活动和失败重试，不是运行时承诺。

2026-11-28 13:19:57+08:00 的只读刷新显示 Native CUDA GPU 为 NVIDIA GeForce RTX 3050 Laptop GPU，UUID `GPU-a424cfba-0a51-fa83-c922-973306e571a5`，总显存4,096 MiB、已用1,392 MiB，GPU利用率34%、显存控制器利用率1%、P8，44°C，SM/显存时钟210/405 MHz，功耗6.28 W。`nvidia-smi --query-compute-apps`没有列出CUDA计算应用；显示进程清单为桌面程序。本阶段未启动GPU实验，也未终止或修改任何进程。当前Stage2文件SHA-256为`47fa5ec34ca15cdc4edeb35226eefa941a78ee344aa2311e5e7984c811dcb40f`，Production文件SHA-256为`adef89d1d3776864a5cddf36eacdd852eff495ae33cdf989642eef7eb4d7f78e`。完整快照见[只读GPU与binary身份记录](evidence/v4_phase_h/nvidia_smi_pre_authorization_snapshot_20261128_131957.txt)；用户授权后、实际启动前仍须刷新设备和binary身份。

## 7. 执行器与统计验证

- Full archive manifest 现在接受并绑定冻结 V4 case/schema identity；压缩 receipt 和 compact 共同构成可续跑的长期 archive record。
- checkpoint 的 `ARCHIVED`、resume、cleanup 和 evaluation 都要求验证 case ID、schema identity、full-manifest SHA、receipt SHA、compact SHA/manifest 和 ZIP CRC。
- `resource_evidence` 绑定四类 schema 的 compact+receipt 保留预算、P0 full-case峰值、冻结的10 GiB自由空间余量与最终20 GB cap；授权保留空间必须等于资源证据，不能设为0或缩小。
- 批次内部按 case 顺序执行；每个 case 验收、归档、清理并测量实际最终占用后，才启动下一个 case。resume先验证已归档 evidence 并允许完成恢复清理；新 case 仍受逐 case 自由空间门控。
- sharded archive cleanup 在删除 shard 前先持久化`IN_PROGRESS`；续跑可接管中断状态。只清理由精确命名模式识别的本执行器临时 shard；compact 文件名碰撞时逐成员校验 SHA、尺寸和CRC。
- 分析器消费已验证的 gzip archive-set receipt及其绑定的compact evaluator ZIP；V4 schema 仅放行 runner生成的两个根级来源文件，H0的非适用TO仅在完整固定标记下免于要求目标专属输出。
- Dry-run preview checkpoint 与执行 checkpoint 分离；资源预测覆盖授权内所有 case，即使本次只运行其中一个 batch。
- 每个case清理后及 batch 完成前均实测 project allocated bytes；超过20,000,000,000 B会持久化`PAUSED_PROJECT_FINAL_CAP`，不报告完成。
- 统计参数、seed registry 和 case identities 没有因 pilot 结果而重选或调门槛。

本阶段 V4 相关 CPU 回归为 **187 passed**。包含 compact/sharded schema、CUDA runner 质量门、case matrix、evidence、executor 归档与中断续跑、统计分析及执行计划；新增测试覆盖 dry-run→授权执行、真实 runner 根级产物、H0非适用TO、receipt+compact分析读取、首shard删除中断、孤儿临时文件、compact碰撞篡改、resume资源恢复、完整授权case预算、固定10 GiB reserve与最终20 GB实测门。CPU测试不替代真实Production/CUDA性能证据；本轮没有重跑CUDA pilot或启动正式矩阵。

## 8. 阶段状态与下一项决策

| 项目 | 状态 |
|---|---|
| 统计合同、设计和 seed 范围 | `FROZEN`；seed 冲突结论限本地可达范围 |
| V4 执行器与 compact/receipt 断点恢复 | `IMPLEMENTED=true`；`CPU_VERIFIED=true` |
| 三类 Native CUDA size pilot | `EXECUTED=true`；资源尺寸已测；H0 指标有缺口 |
| 240-case 确认矩阵 | `EXECUTED=false`；`EVALUABLE=false`；`PERFORMANCE_VERIFIED=false` |
| AI 训练 / Router | 未启动；`AI_ENTRY_GATE=FAIL` |
| 研究总纲 | blob 未变 |

下一项工作是按已冻结设计启动240-case Native CUDA 确认矩阵，逐case归档、校验、清理并断点续跑。开始前仍需用户对这一个矩阵作**单独、明确的最终授权**；授权后才生成绑定 GPU、二进制、registry、design、batches 和 resource evidence 的正式授权记录。若 GPU 状态、输入身份或资源门不合格，执行器停止在对应 case 前，保留已验收结果和失败状态。完整矩阵完成后再运行预先冻结的统计分析；不因单 seed 或 pilot 结果修改界值，不自动启动 AI。

本报告未修改研究总纲、实验参数、seed、方法定义或算法门槛；没有启动正式矩阵、AI训练或新研究方向。
