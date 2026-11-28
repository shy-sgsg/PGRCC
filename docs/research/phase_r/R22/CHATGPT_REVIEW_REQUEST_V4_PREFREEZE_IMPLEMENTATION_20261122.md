# ChatGPT 独立审查请求：R22-V4 冻结前工程实现

请按下面固定的 Git 快照审查，避免把审查时较新的分支状态混入本次证据。

## 固定身份

- 仓库：`https://github.com/shy-sgsg/PGRCC`
- 分支：`codex/unknown-system-error-next-stage`
- **研究材料审查快照：** `8651b6217a599981b2a20aa5633f26f9ba33cad4`
- **工程源码提交：** `bbb609cc31c659c5cb7963519784a2d856b701db`
- **研究总纲 blob：** `e637e3769bde7814298ff012d0f42301884e2985`

## 审查前必读顺序

**请首先读取研究材料审查快照 `8651b6217a599981b2a20aa5633f26f9ba33cad4` 中的完整 `docs/research/RESEARCH_MASTER_PLAN.md`。** 先理解 PGRCC 的研究主题、总体研究目标、核心科学问题、研究内容、总体思路和长期技术路线，再评价 V4。不要用本提示词或阶段报告对总纲的摘要代替实际阅读。

随后按该快照读取：

1. `docs/research/RESEARCH_STATUS.md`
2. `docs/research/RESEARCH_DECISION_LOG.md`
3. `docs/research/CHATGPT_REVIEW_PROTOCOL.md` 和 `AGENTS.md`
4. `docs/research/phase_r/R22/31_v4_causal_output_complete_confirmatory_contract_draft_20261113.md`
5. `docs/research/phase_r/R22/config/r22_v4_causal_output_complete_confirmatory_draft_20261113.json`
6. `docs/research/phase_r/R22/32_v4_pre_freeze_technical_closure_20261122.md`
7. 工程源码提交 `bbb609cc31c659c5cb7963519784a2d856b701db` 中报告引用的 runner、V4 evidence extractor、archive、matrix 和相应测试。

## 可直接打开的固定快照文件

- [研究总纲](https://github.com/shy-sgsg/PGRCC/blob/8651b6217a599981b2a20aa5633f26f9ba33cad4/docs/research/RESEARCH_MASTER_PLAN.md)
- [动态研究状态](https://github.com/shy-sgsg/PGRCC/blob/8651b6217a599981b2a20aa5633f26f9ba33cad4/docs/research/RESEARCH_STATUS.md)
- [研究决策日志](https://github.com/shy-sgsg/PGRCC/blob/8651b6217a599981b2a20aa5633f26f9ba33cad4/docs/research/RESEARCH_DECISION_LOG.md)
- [V4 契约草案](https://github.com/shy-sgsg/PGRCC/blob/8651b6217a599981b2a20aa5633f26f9ba33cad4/docs/research/phase_r/R22/31_v4_causal_output_complete_confirmatory_contract_draft_20261113.md)
- [机器可读设计](https://github.com/shy-sgsg/PGRCC/blob/8651b6217a599981b2a20aa5633f26f9ba33cad4/docs/research/phase_r/R22/config/r22_v4_causal_output_complete_confirmatory_draft_20261113.json)
- [冻结前工程闭合报告](https://github.com/shy-sgsg/PGRCC/blob/8651b6217a599981b2a20aa5633f26f9ba33cad4/docs/research/phase_r/R22/32_v4_pre_freeze_technical_closure_20261122.md)
- [Production runner](https://github.com/shy-sgsg/PGRCC/blob/bbb609cc31c659c5cb7963519784a2d856b701db/scripts/phase_r/run_r22_cuda_pilot.py)
- [V4 复数响应提取器](https://github.com/shy-sgsg/PGRCC/blob/bbb609cc31c659c5cb7963519784a2d856b701db/scripts/phase_r/r22_v4_evidence.py)
- [V4 compact archive](https://github.com/shy-sgsg/PGRCC/blob/bbb609cc31c659c5cb7963519784a2d856b701db/scripts/phase_r/r22_compact_archive.py)
- [V4 matrix runner](https://github.com/shy-sgsg/PGRCC/blob/bbb609cc31c659c5cb7963519784a2d856b701db/scripts/phase_r/r22_experiment_matrix.py)

若任何文件无法从指定 commit 读取，请明确列出并将相关判断标为 `NOT_VERIFIED`。若当前远端 HEAD 已前进，请把它与固定审查快照分开报告。

## 已报告的本次实现与证据边界

本次修改实现了 M1/Huber `0…k` 周期因果前缀估计和当前周期校正、显式零校正失败策略、固定 truth-centered `CSI_AFTER` 逐格复数 ROI、V4 完整归档 profile、resume 前 SHA/manifest SHA/member inventory/CRC 验证及 CUDA driver identity 记录。报告记录定向 CPU/合成测试 `67 passed`、Python 编译、JSON 门控与差异检查通过。

**本次没有运行 Stage2、Native CUDA、Production、V4 pilot、确认矩阵或 AI。** 真实 case 的输出完整性、物理闭合、目标响应映射、检测/参数估计性能和资源表现均未被本次测试证明。V4 保持 `DRAFT_NOT_FROZEN`，`execution_authorized=false`、`cuda_authorized=false`、`ai_training=false`。

## 请完成两层独立审查

### 第一层：工程实现和实验契约正确性

重点核对并指出具体文件/函数/测试依据：

1. M1 与 Huber 是否只读取同一 case 的 ON C1/C2、周期 `0…k` 输入；频率支持、共同 mask、几何校正、物理 delay 边界是否相同；估计是否只用于当前周期；失效是否显式零校正并纳入分母；是否存在未来周期、配对参考、OFF/TO、真值或估计器间 fallback 泄漏。
2. Huber D3 的时延域边界、最小支持和周期前缀数据身份是否确实与 M1 的比较契约一致；测试是否覆盖越界、无效前缀和在周期间修改未来输入的反事实。
3. 目标响应提取是否采用固定且跨方法/角色一致的 ROI 和明确 `CSI_AFTER` tap；目标 ID、周期、真值来源、轴、中心和重叠是否可追溯。特别检查当前由 `A0_ON` 真值定位的实现：真实 target-only `TO`、`A1` 与 A0 之间是否需要逐目标/逐周期一致性审计。H0 没有 target truth 时的空输出语义是否严格，且不会把空 truth 当成功目标响应。
4. 是否正确区分 `TO` 信号响应与 ON/OFF 自适应处理输出；不得将自适应 ON/OFF/TO 的差异解释成线性分解或目标能量损失。
5. V4 archive 是否确实保留完整十周期 Detection、TrackManager/PIPE、CFAR 几何、CSI/目标复数响应、每个 branch 的 runtime XML/log/manifest、输入/Stage2 来源与背景复用映射、源码/binary 和设备身份。指出 required-member 验证与 archive allowlist 之间任何遗漏。
6. 中断、压缩损坏、manifest 不一致、成员缺失、source identity 改变和续跑时，runner 是否 fail closed；raw 数据是否只在 archive 和必要成员校验通过之后删除；压缩包大小和临时峰值是否被如实留给尺寸 pilot 测量。
7. 报告中的 `IMPLEMENTED / EXECUTED / EVALUABLE / PERFORMANCE_VERIFIED` 是否恰当区分了代码、合成测试与真实 Production 实验。

### 第二层：总体科研价值与下一步

请独立判断：

1. 这次工程工作如何、以及在什么意义上服务于研究总纲的 GMTI 科学目标？它是否只建立可审查基础，还是已回答了任何科学问题？
2. 当前 V4 的确认性问题是否足够有价值，是否仍缺少 MDE、检测覆盖率非劣界值或更简单传统方法对照的合理依据？
3. 在真实映射/身份审计、统计门和输出尺寸 pilot 闭合之后，是否值得进行一个单独授权的最小 complete-output CUDA pilot？给出必要条件和停止条件。
4. 当前证据是否支持任何总体研究方向变化或 AI 必要性判断？请严格区分“实现完成”与“性能改善”。

## 输出格式

请先给出总体裁决（例如 `PASS`、`PASS_WITH_LIMITATIONS`、`NEEDS_REVISION`），再分别给出：

- 阻断性问题、重要问题、建议项及其代码/文档证据；
- 第一层和第二层各自的结论与 `NOT_VERIFIED` 项；
- 是否建议进入“单 case 完整输出 CUDA 尺寸 pilot 的独立授权准备”；
- 不允许据本审查请求或实现提交将 V4 自动标为冻结、自动运行 pilot/矩阵或启动 AI；
- 如建议改动研究总纲，只提出有证据的修订方案并交用户讨论，不能直接改写总纲。

研究总纲正文在任何情况下仍须用户事先明确批准后才能修改。
