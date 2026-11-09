# R22 V3.3｜ChatGPT 独立科研审查请求

## 固定审查快照

**请首先读取以下固定 commit 中的完整研究总纲，再阅读本阶段材料。不要以本提示词中的概述代替实际读取。**

| 项目 | 固定身份 |
|---|---|
| 仓库 | <https://github.com/shy-sgsg/PGRCC> |
| 分支 | `codex/unknown-system-error-next-stage` |
| 本阶段审查快照 commit | `376e9f45fa8dd46a2a5b3d50a46b1082929af0ff` |
| 研究总纲 | [`docs/research/RESEARCH_MASTER_PLAN.md`](https://github.com/shy-sgsg/PGRCC/blob/376e9f45fa8dd46a2a5b3d50a46b1082929af0ff/docs/research/RESEARCH_MASTER_PLAN.md) |
| 研究总纲 blob | `e637e3769bde7814298ff012d0f42301884e2985` |

若不能读取上述 commit 或总纲，明确列出无法读取的文件；不得声称已完成整体研究方向审查。不要把远端当前 HEAD 的后续材料混入固定快照。

## 阅读顺序

1. `docs/research/RESEARCH_MASTER_PLAN.md`
2. `docs/research/RESEARCH_STATUS.md`
3. `docs/research/RESEARCH_DECISION_LOG.md`
4. `docs/research/CHATGPT_REVIEW_PROTOCOL.md` 与 `AGENTS.md`
5. `docs/research/phase_r/README.md` 与 `docs/research/phase_r/R22/README.md`
6. `docs/research/phase_r/R22/V3_3_residual_task_gap_audit/README.md`
7. `FINAL_RESEARCH_REPORT.md`、`00`–`07`、`09` 阶段文件
8. `evidence/manifest.json`、`summary.json`、相关 CSV 和图表
9. 分析器 `scripts/phase_r/analyze_r22_v3_3_residual_task_gap.py`，以及 manifest 中记录的 V3.2 evaluator 源文件身份

## 固定科研身份与边界

- V3.2 实验源码 commit：`74bccce61f3ec2f74746512d67271c815ce6a3a8`
- 冻结设计 ID：`R22_EXTENSION_20261106_V3_2_IDENTITY_CLOSED_FULL_SCREENING`
- 设计 SHA-256：`9fcc56e27c3a78a96ccfbecdf6ef1b3098fb1298d4bac9886058ea9af6081063`
- Archive index SHA-256：`a5bd66302acc12ac410ad4f8560ecd7e137ba40d4e54a09abd7eaadcf4d8bdd1`
- V3.3 离线分析器 SHA-256：`9cccfc8f3b0745c67171ade7d8397cebc9e8db22a81e06beedeb1fc91643150d`
- 研究总纲 blob：`e637e3769bde7814298ff012d0f42301884e2985`

本阶段只对 V3.2 的 144 个 `+4 ns` compact case 归档做离线审计。没有运行 Stage2、Production、CUDA、新实验或 AI；没有复制、解压、删除或改写 V3.2 ZIP。完整 ZIP 和原始 IQ 留在本机 `outputs/`，不在 GitHub 审查包中。因此只能核验已提交的派生表、manifest、输出摘要和归档 SHA 身份；不能声称独立读取或重跑了本机 ZIP/原始 IQ。

## 要求的双层审查

### 第一层：本阶段任务正确性

请独立核查：

1. 144 个 case、12 个独立 seed、速度/SNR/时延覆盖及有效面积杂波统计是否与 manifest 和报告一致。
2. A0、Current、Known、Fixed D3、Huber、M1 及 paired-OFF 方法的信息条件是否明确、公平；是否把 paired OFF 估计误写成 target-present 部署性能。
3. 检测分母、seed 统计单位、恢复空间/恢复比例、同机会 overlap 是否定义正确；均值略高于100%的 recovery ratio 是否被正确限制解释。
4. A0/PK 的闭合是否只称“近似”而非逐机会完全一致。
5. 参数误差是否基于共同成功机会；period 8 子集、own-success 汇总和完整十周期共同机会是否被清楚区分。
6. TrackManager 汇总、false-confirmed tracks、经验虚警和系统 Pfa 是否严格区分。
7. 时延估计残差的单位、符号和可归因范围是否正确。
8. `IMPLEMENTED`、`EXECUTED`、`EVALUABLE`、`PERFORMANCE_VERIFIED` 状态是否与证据一致；是否有过度声称、漏报失败或不一致的文件身份。
9. manifest 的脚本/输出 SHA、archive identities 和总纲 blob 是否自洽。可以读取提交的派生证据；不要把缺失的原始 ZIP/ IQ 当作已检查。

### 第二层：总体科研价值与方向

结合研究总纲，独立回答：

1. 固定仿真 `+4 ns` 是否在本任务证据范围内降低 GMTI 任务表现？边界是什么？
2. Known-error correction 在检测任务上恢复多少？哪些参数/航迹指标仍未闭合？
3. target-present deterministic estimation 距 Known 的差距是否显示为稳定、任务相关、可恢复的研究缺口？证据强度够不够？
4. 是否有理由继续未知物理状态估计；是否应转向低速目标保护或复杂杂波建模？
5. AI 是否达到进入门？请具体指出还缺哪些证据或传统方法对照。
6. 研究总纲是否仍合理？若建议修订，先列明证据、拟改范围、对既有研究影响和备选方向；**不得把建议当成用户批准，也不得直接修改总纲。**
7. 是否同意“先冻结 output-complete 确认性契约，再由用户决定是否启动；本阶段后停止”的下一步建议？如果不同意，请给出有证据支持的唯一优先替代方案及停止条件。

## 复核时重点对照的结果

报告中的核心 screening 命中率为：A0 69.729%、Current 11.694%、PK Known 69.729%、Fixed D3 69.705%、Huber D3 ON 69.767%、M1 69.767%。Known−Current 平均恢复空间为58.035个百分点。所有这些都只能解释为冻结仿真矩阵内的筛选描述，不是统计显著性或真实机载泛化。

period 8 四方法共同成功参数机会为1,982/2,880；完整十周期共同参数、共同地速、有效径向速度和 target-only 复数幅相为 `NOT_EVALUABLE`。本阶段 `PERFORMANCE_VERIFIED=false`，`AI_ENTRY_GATE=FAIL`。

请优先从 `evidence/paired_recovery_screening_summary.csv`、`seed_method_detection_summary.csv`、`parameter_fourway_common_success_by_seed.csv`、`parameter_common_success_summary.csv`、`track_screening_summary.csv`、`delay_estimation_summary.csv` 复算或交叉核对，不要只复述报告摘要。

## 最终回复格式

请依次给出：

1. 实际读取的固定 commit、研究总纲 blob 与文件列表；无法读取项和审查限制。
2. 阶段任务正确性裁决：`PASS` / `PASS_WITH_LIMITATIONS` / `FAIL`，逐项说明关键证据。
3. 总体科研价值裁决：`CONTINUE` / `STOP` / `PIVOT`，及其与长期研究目标的联系。
4. 对未知误差因果效应、Known 恢复、确定性估计差距、AI 必要性、低速/复杂杂波方向的逐项回答。
5. 你建议的唯一下一步、最小必要证据、验收指标和停止条件；明确其不构成 CUDA 执行授权。
6. 是否建议修改研究总纲。若建议，提交单独的修订提案，等待用户明确批准。

请区分已验证事实、有限推断与 `NOT_VERIFIED`。测试/归档复核成功不等于性能验证；单次筛选改善也不等于统计显著或论文创新。
