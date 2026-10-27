# PGRCC R22 延续阶段 ChatGPT 独立科研审查提示词

使用前请将审查 commit 替换为 Codex 最终交付的完整 40 位 SHA。所有结论必须以该 commit 中的代码、配置、manifest、日志和结果为准；不得把计划数量当作已执行数量，也不得沿用旧失败快照中的结论代替本轮证据。

---

## 可直接提交给 ChatGPT 的提示词

【PGRCC R22 科研阶段独立审查请求】

请先从 GitHub 仓库读取下列指定 commit 的 `docs/research/RESEARCH_MASTER_PLAN.md`，完整理解 PGRCC 的研究主题、总体研究目标、核心科学问题、研究内容、总体研究思路和长期技术路线。随后结合这些内容审查 R22 阶段成果。不能只评价阶段任务是否完成，还必须判断本阶段是否推动 GMTI 总体研究目标、当前研究方向是否值得继续。

仓库：<https://github.com/shy-sgsg/PGRCC>
分支：`codex/unknown-system-error-next-stage`
审查 commit：`[替换为最终审查 commit 的完整 40 位 SHA]`
实验执行源码 commit：`[填写本次运行实际源码 commit 的完整 SHA；如与审查 commit 不同，分别审查]`
研究总纲路径：`docs/research/RESEARCH_MASTER_PLAN.md`
研究总纲基线 blob：`e637e3769bde7814298ff012d0f42301884e2985`
阶段：PGRCC Phase R / R22 代表性扩展实验与研究方向评估

若指定 commit 或研究总纲不可读取，说明具体缺失文件，并将总体方向、总纲合理性相关结论标记 `NOT_VERIFIED`。若远端 HEAD 与指定审查 commit 不同，报告两者 SHA，并只把指定 SHA 作为审查快照。不得以 Codex 的文字摘要替代实际读取。

### 必读顺序

1. 指定 commit 中的 `docs/research/RESEARCH_MASTER_PLAN.md`；
2. `docs/research/RESEARCH_STATUS.md`、`docs/research/RESEARCH_DECISION_LOG.md`；
3. `docs/research/CHATGPT_REVIEW_PROTOCOL.md`、`AGENTS.md`、`README.md`、`docs/research/phase_r/README.md`；
4. `docs/research/phase_r/R22/README.md`、R22 实验设计与执行契约、预算/门控说明；
5. 本次最终 manifest、逐 case manifest、失败案例、日志、运行时配置和清理审计；
6. R22 runner、scenario factory、evaluator、被测生产实现及与结论直接相关的测试；
7. 必要的历史对照注册表与 R13–R21 证据。历史记录只能按其源码、配置和输入身份支持相应比较，不可当成本轮执行结果。

本次 V1A 首个 case 的精简复核入口是 `docs/research/phase_r/R22/11_v1a_engineering_gate_result_20261028.md` 和 `docs/research/phase_r/R22/evidence/extension_engineering_v1a/seed1930101/README.md`。请优先读取该包内的 `case_summary.json`、`branch_status.csv`、Stage2 证据、Production 日志/XML/CSI/CFAR 以及 robust gamma CSV，再对照本机完整 case manifest 路径记录核验。

### 当前执行快照的待核实线索

以下为 Codex 对本次 manifest 的初步摘录，必须独立核验：

- V1A engineering 矩阵状态为 `GATE_FAILED`；计划 12 个配对工程 case，只完成 seed1930101 的 0 ns case。该 case 是 2.5 m/s、20 dB、20 targets、10 periods；+4 ns 和其余全部 screening/confirmatory/stress/bridge/独立 H0 条件未执行。
- Stage2 面积杂波门通过：1300 个 continuous-area packets、6,508,782 个 samples，实测平均功率 0.0225128995081；因而本次停止原因不是面积杂波为零。
- 41 条方法/角色记录中，24 条分支完整通过；4 条 C4/PKR 主 reference 分支实际运行且 Production return code 为 0，但 reference 验证失败；8 条依赖分支未运行；5 条零时延物理校正记录为 `NOT_APPLICABLE_ZERO_DELAY_REFERENCE`。
- C4/PKR 使用 133-bin group 和 `min_support=8`。Mode-A/OFF reference 有 12,090 行，其中 15 行因 `insufficient_robust_support` 只有 3–7 个 support；Mode-B/ON 有 11 行低于门槛（support 5–7）。验证器拒绝整份 reference，matrix fail-closed。请判断这是输入/覆盖事实、门控/数据契约问题还是算法问题，不能因 Production exit 0 宣称通过，也不能建议降低支持阈值或静默回退 Current。
- Case 总状态 `EVALUABLE=false`、`PERFORMANCE_VERIFIED=false`；没有独立 H0 seed，valid-CUT / unique-hit union 与经验 Pfa 不能形成全方法可比的统计结论。
- V1A 未回答未知时延是否降低 GMTI 任务性能，也未测得已知校正恢复量或盲方法差距。本次失败不能作为 AI 训练必要性证据。

### 本轮实验范围与必须核验的计划

本阶段旨在评估真实系统时延误差是否造成任务损失、已知物理校正可恢复多少、确定性盲估计与已知校正上限差距多大，并据此判断是否值得继续研究未知系统状态估计。不得训练 AI、开发 Router、扩展六参数联合估计或重跑传统 Baseline 全矩阵。

核对冻结设计是否包含且实际完成下列工作。以下是计划规模，不是执行事实；请从 manifest 和产物独立统计实际值：

- 工程门：6 个独立 seed；
- screening：24 个条件 × 12 个 seed，共 288 case；
- confirmatory：6 个条件 × 30 个独立 seed，共 180 case；
- 40-target stress：48 case；
- 3-target bridge：6 case；
- 独立 H0：两个时延条件各 30 个 seed，共 60 case；
- 合计 594 case。每个主要 case 使用 20 targets、10 periods；如使用 5 periods，必须检查其理由是否在 screening 前写入并冻结。

若实际规模缩减、暂停或失败，明确列出“计划数 / 已启动数 / 完成数 / 可评价数”，指出依据和原因；不得把子集改称完整实验，不得在看到结果后改设计或质量门。若全部计划运行会违反已记录的 `<20 GB` 项目空间约束，应如实报告未执行部分及资源门，不得静默缩小实验。

### 执行正确性与证据审查

逐项核验并区分 `IMPLEMENTED`、`EXECUTED`、`EVALUABLE`、`PERFORMANCE_VERIFIED`：

1. 是否在本机真实 CUDA Production 路径执行；检查 GPU 设备、实际二进制、完整源码 SHA、运行时 XML、逐 beam stage/log、CSI tap、CFAR geometry、检测/航迹和评价输出。编译、CPU 模拟、软件测试或历史运行均不能冒充本轮 Production 证据。
2. Stage2 是否真正注入连续面积地杂波；检查 packets、samples、杂波功率、有效距离单元、F1/F2 输入及条件间可比性。`area_enabled=true` 本身不足以证明有效注入。
3. H0/OFF 与 H1/ON 的配对、随机 seed、真值绑定、独立 H0 seed、输入哈希及数据复用是否满足设计。检查盲估计是否泄漏真实误差、目标真值、无误差配对参考或其他 oracle 信息。
4. 0 ns 条件下的估计/校正处理是否符合冻结的零时延参考规则；核对 P1/P2 等诊断估计与物理校正的区别、4 ns 条件下已知校正与盲确定性校正的数据流。不得用估计值强制归零或事后放宽物理边界门。
5. A0/PK 物理校正闭合、Mode-B ON/OFF 处理算子一致性、有效 CUT 定义、CFAR geometry 指针生命周期、目标自对消及 Scan beam quality gate 的根因是否有当前运行证据支持。
6. 分析输出记录是否完整，评价分母、目标机会数、统计单位、检测匹配、虚警计算与缺失值处理是否正确；不得把 beam、period 或 target 当作独立 seed 夸大样本量。
7. 审查磁盘清理是否保留足以支持结论、复现和失败定位的记录，并检查清理前后清单与空间占用。不得删除唯一原始证据后声称可复现。

### 科学结果与统计解释

按条件与实验层级分别核验并比较：

- 杂波残余功率；
- 目标幅度、相位保持与目标自对消；
- Output SCNR；
- 检测与可评价条件下的虚警统计；
- 径向速度和方位估计误差。

分别报告 `Known-error correction upper bound − Current+unknown-error`（recoverable space）、`Estimated-error correction − Current+unknown-error`（actual recovered）及恢复比例；明示指标方向、配对单位、seed 数、缺失 case 和不确定性区间。对照口径须相同，任何不可比项需标注并解释。

单 seed 只能支持可行性或正确性判断，不能宣称具有统计显著性的 Pd/Pfa 改善。若 C 相对 B 没有可重复的任务性能改善，讨论是否应暂停未知系统失配主线；若 C 有稳定改善而 D 明显落后，讨论传统盲估计的限制，但不得据此直接断言需要 AI。AI 只有在任务退化、已知恢复空间和盲估计残差证据成立后才可讨论；本阶段不得启动 AI 训练。

### 请完成两层审查

**第一层：阶段执行与结论正确性。** 核查物理模型、算法/数据流、实验设计、实际运行身份、输入与配对、质量门、信息隔离、评价分母、统计方法、复现证据、失败案例及结论边界。逐项给出证据路径或 manifest 字段，标明无法核验之处。

**第二层：总体科研价值与方向。** 独立判断本阶段是否推动长期 GMTI 目标；未知系统误差是否真实降低任务性能；已知校正恢复空间是否可重复；盲确定性方法的差距是否充分；问题是否有真实任务价值、是否值得继续；是否存在更简单的传统方法或更重要的替代瓶颈；总纲所定义的问题和路线是否仍符合最新证据。不得因为阶段通过或已有投入而默认继续，也不得因单次失败贸然 pivot。

若认为研究总纲需要修改，只提出具体修订建议、证据、科学意义和对既有成果的影响；用户明确批准前不得编辑总纲，也不得通过动态状态文件暗中改变长期目标。

### 输出格式

1. 审查快照：仓库、分支、审查 SHA、执行源码 SHA、远端 HEAD、实际读取文件；
2. 总结裁决：`PASS / PASS_WITH_LIMITATIONS / INCONCLUSIVE / FAIL`；
3. 状态表：`IMPLEMENTED / EXECUTED / EVALUABLE / PERFORMANCE_VERIFIED`，每个结论注明适用范围；
4. 执行规模：计划数、启动数、完成数、可评价数及失败/缺失数；
5. 事实核验表：主张｜证据路径/字段｜判断｜限制；
6. CUDA Production、地杂波注入、配对与物理校正的审查；
7. 各性能指标、统计方法、效应量与不确定性；不可评价项及禁止支持的主张；
8. 两层结论：阶段正确性，以及总体研究价值；
9. 方向裁决：`CONTINUE / STOP / PIVOT / INCONCLUSIVE`；AI 必要性：`ESTABLISHED / NOT_ESTABLISHED / NOT_VERIFIED`；
10. 唯一下一步建议：具体工作、决策价值、验收标准和停止条件；如需用户批准，明确指出。

阶段门未通过不等于整个研究方向已被否定。请用仓库中的本轮证据独立核验，不把计划当执行、不把 Stage2 当 Production、不把单 seed 当统计结论，也不把实验成功等同于研究创新性。
