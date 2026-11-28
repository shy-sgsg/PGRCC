# ChatGPT 独立审查请求：R22 V4 CPU 集成闭合

请审查 GitHub 仓库 `shy-sgsg/PGRCC` 的本次交付快照。**审查 commit SHA 由提交这份审查请求的消息提供；请只使用该完整 SHA，不用远端 HEAD 替代。**

## 必须优先阅读

先从指定 commit 读取完整的 `docs/research/RESEARCH_MASTER_PLAN.md`，理解总体 GMTI 研究目标、核心科学问题、研究内容和长期技术路线。然后依次读取：

1. `docs/research/RESEARCH_STATUS.md`
2. `docs/research/RESEARCH_DECISION_LOG.md`
3. `docs/research/CHATGPT_REVIEW_PROTOCOL.md`
4. `docs/research/phase_r/R22/33_v4_integration_closure_20261123.md`
5. `docs/research/phase_r/R22/34_v4_cpu_integration_validation_20261123.md`
6. `docs/research/phase_r/R22/35_v4_cuda_pilot_readiness_20261123.md`
7. `docs/research/phase_r/R22/36_v4_preflight_audit_and_cuda_pilot_proposal_20261123.md`
8. `docs/research/phase_r/R22/config/r22_v4_size_pilot_draft_20261123.json`
9. `docs/research/phase_r/R22/evidence/v4_integration_closure_20261123/README.md` 及该目录全部机器证据
10. 本 commit 的相关修改源码和测试，重点关注 V4 execution plan/identity、Production runner、目标响应提取、compact archive 和 matrix resume。

如果总纲、状态或源码不可读取，请明确指出并将总体方向判断标为 `NOT_VERIFIED`；不能以本提示词的摘要代替实际读取。

## 阶段身份

- 仓库：`shy-sgsg/PGRCC`
- 分支：`codex/unknown-system-error-next-stage`
- 审查 commit：使用附带本提示词的消息中的完整 SHA
- 实现基线：`ce7d1a96b6902b02b94408e5b2fabf02c275955b`
- 研究总纲 blob：`e637e3769bde7814298ff012d0f42301884e2985`
- 阶段：R22 V4 CPU Integration Closure / CUDA pilot readiness

## 审查问题

### 第一层：工程与证据正确性

1. V4 runner 是否只执行设计注册的方法？0 ns 与 +4 ns 的基础方法选择及附加因果方法是否与契约一致？M1 batch 是否确实排除在线排名？
2. 分支身份、Mode-A/B、role、case、method/config/runtime XML 的一致性校验是否可靠？能否发现漏分支、重复分支和未注册分支？
3. ON/OFF/TO 与 H0 真值/响应语义是否正确？seed和真值映射校验是否足够？
4. archive 发布、文件完整性校验、checkpoint、raw cleanup ledger 和断点恢复顺序能否避免损坏归档或过早删除 raw？请检查发布中断和清理中断的测试覆盖。
5. 110项 CPU 回归可以支持什么结论，不能支持什么结论？是否存在软件测试以外被误报为执行的结果？
6. 2,520次 Production 调用、1,200–1,440次 Stage2 调用及 8.856 GB 单 +4 ns case 输入下界是否从设计/源码正确推导？请复核其口径是否清楚地区分静态推导与实测。
7. 磁盘空间不足以覆盖该下界和归档峰值，选择暂缓 CUDA pilot 是否合理？清理范围是否只涉及本任务生成的临时文件？

追加审查问题：

8. seed 审计是否正确区分已执行 V3.2 Screening seeds `1932001–1932012` 与保留但未执行的 Confirmatory seeds `1933001–1933030`？候选 pilot seed `1937001` 的本地审计范围和“全局 registry 仍待复核”限制是否清楚？
9. 主要定位 MDE 与检测覆盖率非劣界值仍为 null 时，V4 冻结/确认性推断是否应判 `BLOCKED`？不得从 full-case batch 筛选结果替代门槛或功效分析。
10. 单 case pilot 提案、方法/角色、输出、测量、停止条件和命令是否与当前 matrix runner 的 V4 archive profile 行为相符？是否清楚标明命令未运行且仍需独立用户授权？

### 第二层：总体科研价值

1. 本阶段的工程闭合是否实质支撑总纲的 GMTI 科研目标？其贡献边界是否准确？
2. 当前材料是否支持未知通道误差的任务收益、Known recovery、blind estimator gap、低速目标或复杂杂波方向判断？
3. 当前 V4 统计门、资源门和 seed 身份门是否足以作为冻结前条件？是否有缺少的关键判断或更简单的执行路径？
4. 是否有理由调整研究总纲或长期研究方向？所有建议必须基于此 commit 的实际证据，且只能作为用户讨论建议。

## 必须遵守的结论边界

- 本阶段没有运行 Stage2、Native CUDA Production、V4 pilot 或确认性矩阵。
- `CPU_INTEGRATION_TESTED=true` 不等于 `EVALUABLE=true` 或 `PERFORMANCE_VERIFIED=true`。
- V4 仍为 `DRAFT_NOT_FROZEN`；不得将静态计划调用数称为真实运行数。
- 本地 seed 范围审计不等于全项目/外部归档 registry 已闭合；`1937001` 仍需最终全局核查。
- 单 case CUDA pilot 当前为 `BLOCKED`：统计门槛未批准，当前卷与 `/tmp` 空间均小于原始输入下界加输出余量。
- 不要因为 CUDA pilot 尚未执行就推导算法失败或 AI 必要。
- 研究总纲正文的任何修改建议必须先与用户讨论；用户未明确批准之前不得改写。

请给出审查裁决、发现的问题等级、可复现证据路径、需要修改的代码/文档（如有）及一项明确的后续建议。没有足够证据时标为 `NOT_VERIFIED`，不要用推测填补。
