# PGRCC R22-V4 Phase H 冻结准备：ChatGPT 独立科研审查请求

## 固定审查身份

- 仓库：<https://github.com/shy-sgsg/PGRCC>
- 分支：`codex/unknown-system-error-next-stage`
- **审查快照 commit：** `0d2f8ba3e3e3a5fe8ae2b7ba822774a184321328`
- 阶段报告：`docs/research/phase_r/R22/57_v4_phase_h_size_pilots_and_matrix_freeze_20261128.md`
- 研究总纲 blob：`e637e3769bde7814298ff012d0f42301884e2985`
- 冻结设计 SHA-256：`5f3cfa00f0409ea0129fea24b799bab8b6648cad2790d3accfe0c9399fb68455`
- Dry-run registry SHA-256：`1546ed2c90836c6cf163547080352f1bd16b93c18c1e28f0ff6011f7347cd57c`
- Runtime source identity SHA-256：`8cb61445ab92f83ff89c05755f6385bcb135c5bc51dec69851a59aaafac2bb66`
- Seed registry SHA-256：`ec73bb776882a25cde4250d0092fec24157b7d38aaea6961729a909f1826abfd`

## 必须遵守的读取顺序

请先打开并完整阅读**上述精确 commit**中的：

[`docs/research/RESEARCH_MASTER_PLAN.md`](https://github.com/shy-sgsg/PGRCC/blob/0d2f8ba3e3e3a5fe8ae2b7ba822774a184321328/docs/research/RESEARCH_MASTER_PLAN.md)

核对其 Git blob 是否为 `e637e3769bde7814298ff012d0f42301884e2985`。然后按顺序阅读同一 commit 中的：

1. [`docs/research/RESEARCH_STATUS.md`](https://github.com/shy-sgsg/PGRCC/blob/0d2f8ba3e3e3a5fe8ae2b7ba822774a184321328/docs/research/RESEARCH_STATUS.md)
2. [`docs/research/RESEARCH_DECISION_LOG.md`](https://github.com/shy-sgsg/PGRCC/blob/0d2f8ba3e3e3a5fe8ae2b7ba822774a184321328/docs/research/RESEARCH_DECISION_LOG.md)
3. [`docs/research/CHATGPT_REVIEW_PROTOCOL.md`](https://github.com/shy-sgsg/PGRCC/blob/0d2f8ba3e3e3a5fe8ae2b7ba822774a184321328/docs/research/CHATGPT_REVIEW_PROTOCOL.md)
4. [`AGENTS.md`](https://github.com/shy-sgsg/PGRCC/blob/0d2f8ba3e3e3a5fe8ae2b7ba822774a184321328/AGENTS.md)
5. [`README.md`](https://github.com/shy-sgsg/PGRCC/blob/0d2f8ba3e3e3a5fe8ae2b7ba822774a184321328/README.md)
6. [`docs/research/phase_r/README.md`](https://github.com/shy-sgsg/PGRCC/blob/0d2f8ba3e3e3a5fe8ae2b7ba822774a184321328/docs/research/phase_r/README.md)
7. 本请求所指向的阶段报告57及其机器证据。

若精确 commit、总纲或任何关键文件不可访问，请指出具体缺失项，并将依赖它的结论标为 `NOT_VERIFIED`。不得用当前远端 HEAD、其他 commit、摘要转述或搜索结果代替固定快照。若远端分支 HEAD 已前移，请分别报告快照身份与当前 HEAD，不要混用内容。

## 阶段背景与研究问题

本阶段属于研究总纲中的传统方法能力边界与实验基准建设。它完成三类此前缺失 schema 的 Native CUDA size pilot，并修复、复审和验证 V4 的逐 case 执行、分片归档、compact/receipt 验证、清理和断点续跑链路，为已冻结的 240-case 研究方向筛选矩阵准备可审查的运行条件。

筛选设计包含 target-present 180 cases（30 seeds × 3 个速度 × 2 个时延）和独立 H0 60 cases（30 seeds × 2 个时延），共120个配对 batch。用户已冻结统计参数：Scheme A、位置 MDE 3 m、每速度共同成功支持至少10、检测覆盖率非劣界1个百分点、正确确认航迹覆盖仅作完整描述、seed-cluster studentized bootstrap-t、检测/航迹覆盖 paired t、两个独立 H0 seed 事件率的单侧95% exact 上界均严格小于10%，以及既有缺失数据与 fail-closed 规则。请审查实现是否忠实执行这些冻结条件；不得建议为“通过”而事后调整阈值或 seed。

## 关键证据入口

请在固定 commit 中查阅下列报告与文件，并沿报告中的链接核对实现细节：

- 阶段报告与实验身份：[`57_v4_phase_h_size_pilots_and_matrix_freeze_20261128.md`](https://github.com/shy-sgsg/PGRCC/blob/0d2f8ba3e3e3a5fe8ae2b7ba822774a184321328/docs/research/phase_r/R22/57_v4_phase_h_size_pilots_and_matrix_freeze_20261128.md)
- 冻结 dry-run：[`confirmatory_dry_run.json`](https://github.com/shy-sgsg/PGRCC/blob/0d2f8ba3e3e3a5fe8ae2b7ba822774a184321328/docs/research/phase_r/R22/evidence/v4_phase_h/preflight_20261128_executor_reviewed/confirmatory_dry_run.json)
- 资源预算：[`resource_budget_20261128.json`](https://github.com/shy-sgsg/PGRCC/blob/0d2f8ba3e3e3a5fe8ae2b7ba822774a184321328/docs/research/phase_r/R22/evidence/v4_phase_h/resource_budget_20261128.json)
- GPU 预授权快照：[`nvidia_smi_pre_authorization_snapshot_20261128_131957.txt`](https://github.com/shy-sgsg/PGRCC/blob/0d2f8ba3e3e3a5fe8ae2b7ba822774a184321328/docs/research/phase_r/R22/evidence/v4_phase_h/nvidia_smi_pre_authorization_snapshot_20261128_131957.txt)
- 冻结设计：`config/r22_v4_causal_output_complete_confirmatory_draft_20261113.json`
- CPU 回归：报告57列出的 R22 V4 执行器、归档、case schema、统计、分析和计划测试。
- 执行器、分片归档、资源预算、case schema、统计分析器及 dry-run/runner 源码：请从固定 commit 的 `scripts/phase_r/`、`scripts/` 和测试文件中定位，并以实际读取的路径报告。

报告记录了三项 Native CUDA size pilot 均已执行：target-present 0 ns 为 `PASSED`；H0 0 ns 和 H0 +4 ns 为 `H0_EVALUABLE_WITH_GAPS`，仅完成资源测量，不构成确认性 H0 结果。报告还记录了原始输入/归档大小、compact 和 receipt 留存、峰值空间、耗时以及失败重试。请独立核对这些区分是否清楚，尤其不能把 H0 的 `WITH_GAPS` 写成统计通过。

## 请完成两层审查

### 第一层：阶段任务与工程正确性

重点核验：

1. 三类 pilot 的运行身份、配对与归档证据是否足以支持报告中的**资源测量**结论；区分记录存在与独立复跑。
2. case schema、完整 archive-set manifest、分片、gzip receipt、compact、checkpoint、resume、cleanup 和 evaluator 之间的身份绑定是否闭合；检查是否可能漏验、误认文件或重跑已完成 case。
3. 逐 case 顺序执行、资源不足暂停、失败 attempt 保留和断点恢复是否安全，是否存在覆盖有效结果或错误报告完成的路径。
4. 240-case 资源预算是否覆盖授权内所有 case；检查固定 10 GiB 文件系统 reserve、case 后实际项目占用检查、20,000,000,000-byte 最终保留 cap 及其暂停行为。
5. 冻结统计合同、缺失数据规则、seed registry 与实现/分析器是否一致，是否存在事后筛选共同样本、把方法不可评价误报成目标失败、或不公平地提升排名的风险。
6. 复审修复是否有针对性回归覆盖，是否有未覆盖的高风险恢复/清理边界。报告称本次 V4 相关 CPU 回归为187项；请查看固定 commit 的测试证据。CPU 测试和 dry-run 不能替代 CUDA/Production 证据。
7. 明确列出无法独立核验的内容，包括完整 P0 ZIP 是否重新计算哈希、seed registry 仅覆盖本地可访问范围、机器与归档模型限制、任何缺少原始文件的事项。不要把这些局限泛化成无证据的缺陷。

请特别检查阶段代码提交中的下列问题闭环是否真实成立：

- dry-run checkpoint 与首次正式执行 checkpoint 分离；
- runner 生成的根级文件仅按明确 schema 放行；
- H0 非适用 TO 分支只在精确标记下跳过目标专属输出要求；
- gzip archive receipt 与 compact ZIP 的分析器读取及双重身份验证；
- shard 清理在 unlink 前持久化 `IN_PROGRESS`，并可恢复；
- orphan 临时 shard 只按精确 runner-owned 命名清理；
- compact 目标文件碰撞时验证成员内容、尺寸与 CRC；
- receipt 清理恢复不受授权总空闲空间门错误阻塞，新 case 仍逐 case 受资源门约束；
- 预算覆盖完整授权 case 集合，且保留固定 10 GiB reserve；
- 每个 case 清理后按实际 allocated bytes 强制执行最终20 GB cap。

### 第二层：总体科研价值与方向

请结合总纲判断：

1. size pilot 和执行器改造是否实质性解除研究执行瓶颈，还是仅增加准备工作？
2. 240-case 设计是否能够回答“在既定物理失配下，传统未知误差修正相对 Current 和固定 D3 是否有可重复任务收益”，以及它不能回答什么？
3. 单次 size pilot 是否只支持运行/存储容量规划，而不支持 Pd、Pfa、位置、速度、跟踪或算法优越性结论？
4. 30+30 seeds、冻结终点和 H0 事件门对筛选目的是否合理；单地理/仿真模型等外推风险如何表述？
5. 研究总纲是否仍与当前证据一致，是否有充分理由提出方向调整；若建议调整，请列出支持证据、替代方向、对历史成果的影响及其不确定性。
6. 是否已经满足 AI 进入条件？请依据稳定任务缺口、强传统方法比较、可恢复空间和部署可用信息作出判断，不因本阶段完成而推定 AI 必要。

## 资源与执行准备的专项判断

报告给出的规划值包括：全矩阵 compact+receipt 预计约2.084 GB，加25%余量约2.604 GB；最坏单 case 临时峰值约29.821 GB；文件系统最低空闲代理约43.163 GB；2026-11-28 05:56 UTC ext4 可用约58.351 GB；项目最终保留预测约19.978 GB，仅比20 GB cap 少约21.8 MB；运行时估算约49.41小时，加25%约61.76小时。Target-present +4 ns receipt 容量是依据 P0 manifest 产生的保守模型，不是新 pilot 实测；P0 峰值是可能受主机并发活动影响的 free-space 差值代理。

请评估这些估算是否适合支持“可进入最终授权决策”，并明确指出哪些数值在正式运行前必须刷新（特别是实时 GPU 状态、可用磁盘和实际项目 allocated bytes）。最终 cap 的约21.8 MB预测余量很薄，请检查报告是否如实揭示该边界、执行器是否会逐 case fail-closed，以及这是否要求重新优化留存量/空间而非放宽 cap。不得把预授权 GPU 快照当作启动时状态。

## 状态词必须严格区分

请在审查结论中分别使用并解释：

- `IMPLEMENTED`：相关代码或设计已存在；
- `CPU_VERIFIED`：指定 CPU 测试/静态核验已运行并通过；
- `EXECUTED`：真实 Native CUDA size pilot 有本次阶段记录；
- `EVALUABLE`：具体 case/指标满足既定评价条件；H0 `WITH_GAPS` 不能等同完整可评价；
- `PERFORMANCE_VERIFIED`：有满足设计的任务级确认性证据。

当前已知边界：240-case 正式矩阵未启动；当前 dry-run 为 `NOT_AUTHORIZED`；本轮代码修复和资源复核没有重新运行 Stage2/Production/CUDA；三类已批准 size pilot 的 Native CUDA 执行记录见报告57；AI 未启动；尚无本确认矩阵的任务性能结论。请核对而非照抄以上陈述。

## 请给出可执行的审查结论

开头给出结论之一：

- `READY_FOR_FINAL_USER_AUTHORIZATION`
- `REVISE_BEFORE_AUTHORIZATION`
- `NOT_READY`

然后简洁报告：

1. 你实际读取的 commit、总纲 blob 和文件；未读取的项目及影响。
2. 阶段正确性审查的结论、严重度排序的具体发现和证据路径/行号。
3. 总体科研价值判断、当前主线是否合理，以及你认为下一步应做什么。
4. 统计设计、资源预算、工程断点恢复和证据保留各自的风险与需要补齐的条件。
5. 是否建议用户现在对正式240-case矩阵作最终授权；说明这是建议，不是授权。
6. AI 是否有证据必要性；不要根据执行器工作量推导研究价值。

若给出 `REVISE_BEFORE_AUTHORIZATION` 或 `NOT_READY`，请将必须修复项与可接受限制分开，给出最小闭环动作；不要重开已完成的无关历史审计。若给出 `READY_FOR_FINAL_USER_AUTHORIZATION`，也必须明确真实 CUDA 性能验证尚未发生，且用户的最终授权仍是单独门槛。

**本审查请求不构成正式矩阵执行授权，不授权 AI 训练，也不授权修改 `RESEARCH_MASTER_PLAN.md`。研究总纲任何正文修改仍须用户事先针对具体范围明确批准。**
