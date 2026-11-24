# R22-V4 Phase G：ChatGPT 独立科研审查请求

请把本请求作为固定快照的独立审查任务处理，不要只复述 Codex 的裁决。

> **请首先读取本次 Git 提交对应版本的 `docs/research/RESEARCH_MASTER_PLAN.md`，完整理解 PGRCC 的研究主题、总体研究目标、核心科学问题、研究内容、总体研究思路与长期技术路线，然后结合这些内容审查本阶段的代码、实验、数据和科研结论。不能只评价阶段任务是否完成，还必须判断本阶段是否推动了整个 GMTI 研究目标，以及当前研究方向是否需要调整。**

## 固定审查身份

| 项目 | 身份 |
|---|---|
| 仓库 | `https://github.com/shy-sgsg/PGRCC` |
| 分支 | `codex/unknown-system-error-next-stage` |
| Phase G 审查快照 commit | `2355118855fc4dfc61aa290c1f7f707d51216981` |
| Phase F 基线 commit | `ddbf057d8d944c54f94af54e239cc00644988f1a` |
| 研究总纲 blob | `e637e3769bde7814298ff012d0f42301884e2985` |
| V4 设计 | `docs/research/phase_r/R22/config/r22_v4_causal_output_complete_confirmatory_draft_20261113.json`，状态 `DRAFT_NOT_FROZEN` |

请只以表格中的固定审查快照为证据版本。当前 HEAD 如已前进，应分别说明审查快照与远端 HEAD，不能混用文件。

## 必读顺序

1. `docs/research/RESEARCH_MASTER_PLAN.md`
2. `docs/research/RESEARCH_STATUS.md`
3. `docs/research/RESEARCH_DECISION_LOG.md`
4. `docs/research/CHATGPT_REVIEW_PROTOCOL.md` 和 `AGENTS.md`
5. `docs/research/phase_r/R22/52_v4_phase_g_statistical_contract_closure_20261124.md`
6. `docs/research/phase_r/R22/53_v4_phase_g_research_direction_and_final_decision_20261124.md`
7. `docs/research/phase_r/R22/evidence/v4_phase_g/README.md` 及其四份 JSON
8. 对应的统计代码、执行器、测试、草案配置和 Phase F 的47–51号报告

若无法实际读取研究总纲或某项证据，请标记 `NOT_VERIFIED`，不得用 Codex 摘要替代。

## Phase G 完成内容

- 修正 bootstrap-t 上端点分位数的错误说明；确认区间反演时上端点使用 `q_0.025(T*)`，端点置信度为97.5%。
- 全相同 seed、零观测方差、零标准误重抽样和非有限统计量不再产生退化“零不确定性”结论；异常 bootstrap/Monte Carlo 结果 fail-closed。
- 每速度共同成功最小支持数变成显式设计输入。未批准时只报告描述性值，不进入确认性推断。
- `_frozen_thresholds()` 把位置方案、支持下限、区间算法/参数、缺失规则、H0指标、superiority/NI/joint gate 和可选航迹门一起绑定；未定字段仍为空，授权验证拒绝未定合同。
- H0 明确按独立 seed 报告 seed-any-event 和至少一条 false-confirmed track 的事件率；重复周期/轨迹不当作独立 seed。
- 只审查候选 seed 的 GitHub 分支身份和既有本地 registry 扫描；明确保留外部/离线注册表限制。
- 定向 CPU 回归统计34项、执行器15项通过。没有运行新的 Stage2、CUDA、Production、确认矩阵或 AI。

## 需要你独立核验

### 第一层：阶段实现和统计正确性

1. bootstrap-t 的 (T^*)、区间反演、上下端点分位数、置信水平与代码是否一致？
2. 零观测方差、零标准误 bootstrap 样本和非有限统计量的拒绝规则是否保守且无误导？
3. 共同成功位置 RMSE 的门槛是否与全计划机会覆盖率并列报告，是否会引入选择偏差或只保留容易场景？
4. `_frozen_thresholds()` 是否确实绑定所有会影响 confirmatory claim 的科学判据，且在 `null`/非法阈值时 fail-closed？
5. H0 seed-any-event、false-confirmed-track-event、period fraction、unique track count 的独立统计单位、缺失规则和区间是否合适？
6. 机器 seed 审计能支持到什么程度？外部归档、GitHub artifacts/LFS 的限制是否报告准确？
7. 磁盘数据能否支持当前资源判断？P0 原件和 compact 是否被正确保留，20 GB 是最终清理上限而非运行期上限？

### 第二层：总体 GMTI 研究价值

1. R22-V4 的因果盲估计确认问题是否仍服务于总纲的长期 GMTI 目标？
2. 单 seed P0 的 Current/Known/M1/Fixed/Huber结果能支持什么，不能支持什么？
3. 是否有足够证据继续投入完整确认矩阵；是否存在更简单、价值更高的传统方案？
4. 是否有证据将方向转为低速目标、复杂杂波或 AI？请区分 `NOT_VERIFIED` 和已证伪。
5. 是否应调整总纲？若是，只提出具体建议和证据，不能直接修改总纲；用户明确批准前不得改写正文。

## 当前未决门槛

设计没有选择：位置方案 A/B、工程 MDE、每速度共同支持数、检测覆盖非劣 margin、航迹保护是否为硬门、两个 H0 上界、确认 seed 数。历史 V3.3 的12个seed支持量敏感性中，候选1/5/10均为36/36通过，不能据此选值；P0 单例不得用来反推门槛。

确认矩阵的四类 case schema 只有 target-present +4 ns 有完整输出尺寸记录；其余三类及峰值临时磁盘空间未测。项目当前低于20 GB最终限制，但资源 pilot 和确认矩阵都没有因此获得授权。

## 请按此格式给出裁决

1. `PASS / PASS_WITH_LIMITATIONS / FAIL`：Phase G 统计契约和执行器修订是否正确？逐条写关键依据。
2. `READY_FOR_USER_DECISION / BLOCKED_BY_USER_DECISION / BLOCKED_BY_RESOURCE_EVIDENCE / BLOCKED_BY_STATISTICAL_CONTRACT / BLOCKED_BY_EVIDENCE_IDENTITY`：V4 是否可冻结？允许多项 blocker 并存。
3. 对 bootstrap-t、共同支持、检测覆盖NI、航迹保护、H0两项事件率及样本量的建议；标注哪些必须由工程需求方定值，不能伪装成用户已经批准。
4. 对单 seed P0 的独立科研解释，以及 R22-V4 相对于总体 GMTI 研究目标的价值。
5. 长期方向 `CONTINUE / STOP / PIVOT`，并说明未知误差、低速目标、复杂杂波、AI分别是已支持、未验证还是已否定。
6. 是否建议进行四 schema size pilot；若建议，指出准确测量项、存储限额策略与单独授权边界。
7. 是否建议启动确认性 CUDA 矩阵。除非统计阈值、seed身份、四schema资源、设计冻结和用户单独授权全部闭合，否则不得建议直接开跑。

不得把 CPU 回归通过说成 CUDA 性能证据；不得把统计模拟说成 GMTI 实验；不得替用户批准阈值、冻结设计、启动 CUDA、清理 P0 原件或修改研究总纲。
