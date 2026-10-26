# PGRCC R19 ChatGPT 独立科研审查请求

使用前，将下方 `审查 commit` 填为 Codex 最终交付回复提供的完整 40 位 SHA。该 SHA 必须对应同时包含本提示词、R19 报告、评价脚本和结果证据的提交。ChatGPT 若不能访问该 commit，必须明确标注未读取的文件并将总体方向判断设为 `NOT_VERIFIED`。

```text
【PGRCC 科研阶段独立审查请求｜R19】

请先 checkout 或读取 GitHub 仓库指定 commit 中的：

docs/research/RESEARCH_MASTER_PLAN.md

请完整理解该版本的研究主题、总体研究目标、核心科学问题、研究内容、总体研究思路与长期技术路线。不能只读阶段报告或根据本提示词推定总纲内容。

仓库：https://github.com/shy-sgsg/PGRCC
分支：codex/unknown-system-error-next-stage
审查 commit：[填入最终回复提供的完整 40 位 SHA]
研究总纲：docs/research/RESEARCH_MASTER_PLAN.md（必须来自上述审查 commit）
研究总纲 blob（预期）：e637e3769bde7814298ff012d0f42301884e2985
阶段：R19，R18 saved-data 的离线任务复评与方向审查

读取顺序：
1. 上述审查 commit 中的 RESEARCH_MASTER_PLAN.md；
2. docs/research/RESEARCH_STATUS.md；
3. docs/research/RESEARCH_DECISION_LOG.md；
4. docs/research/CHATGPT_REVIEW_PROTOCOL.md 与 AGENTS.md；
5. docs/research/phase_r/R19/00_master_execution_plan.md 与 01_evidence_inventory.md；
6. docs/research/phase_r/R19/02_frozen_task_evaluation_contract.md；
7. R19 的 03–09 报告、10 本提示词及 evidence/results/formal_matrix/evaluation_manifest.json；
8. 与关键判断相关的 evaluator、29 项测试、`candidate_gate_audit.csv`、关键 CSV、图表和 R18 原始阶段报告。

R19 范围：只对 R18 已保存数据做离线重新评价。R19 不运行 CUDA、不生成新 IQ、不训练 AI、不改信号处理算法或实验参数。检查审查 commit 与实际 R18 runner/build/binary/config 身份是否区分清楚。实验执行源码、评价器源码和最终审查 commit 是不同身份时，必须分别说明。

请完成两层审查：

第一层：阶段任务正确性
- 严格一对一 Detection/truth matcher 是否遵守冻结机会定义、字段合法性、门控与参数分母规则？
- 是否把 Production Detection 嵌入的 truth 字段排除在真值来源之外？
- Mode-A paired-OFF 与 Mode-B ON-only 的信息条件是否分层？Mode-B 是否有真值/OFF/TO 泄漏？
- H0 observed false-hit fraction 是否与统计意义 empirical Pfa 区分？
- Detection、CFAR、TrackManager、参数估计的单位、分母和可评估状态是否正确？
- Track 启动 latency 的 `−1.90735e−7 s` 是否有 timestamp-resolution 证据支持按零差解释？event 的一基 result ID 与零基 truth period 是否在评价时正确映射？
- 是否把 old R18 3/3 counter 与 R19 strict opportunity fraction 区分？
- seed606、6.7 m/s、30 dB 的 C0 `24→2` 与 C3 Mode-B `23→1` 旧/新候选边变化是否能由独立 truth、逐期 Detection 与冻结 gate 复算？是否说明候选数不是命中或虚警数，首拒门采用互斥计数？
- seed909 的历史 exact-support gate 是否被如实保留？对 gate 方法的评论是否未追溯改写其 NOT_EVALUABLE 结果？
- 当前 snapshot 缺少完整 CSI complex maps、CFAR hit/power/threshold maps 时，机制定位措辞是否恰当？
- 检查代码、测试、manifest、输出表、图和结论之间是否一致；找出遗漏、计算错误或夸大结论。

第二层：总体科研价值与方向
- R19 实际推动了研究总纲中的哪个 GMTI 目标？其边界是否清楚？
- 因 R18 的 12 个场景 delay error 都是 0 ns，R19 是否避免把它们用于未知误差因果结论？
- 对历史 R8/R12 +4 ns 证据的描述是否有正确的来源、分母与“不属于本次统一复评”的限定？
- 已知校正恢复量、传统盲估计差距是否仍未被 harmonized re-evaluation 闭合？
- 是否有足够证据进入 AI？低速保护或复杂杂波是否已成为更优先路线？
- R19 推荐先离线统一复评 R8/R12 是否必要、最小且有停止门？是否存在更好的传统或工程路径？
- `08_next_research_direction.md` 是否给出可证伪假设、竞争解释、最小输入、强传统基线、任务指标、预注册规则、独立验证、停止条件、方向变化规则与潜在论文贡献？R8/R12 原始文件是否由审查 commit 包含，还是只在 Codex 本机 outputs 可读？
- 请独立指出继续、停止或转向的证据，并评价其潜在科研贡献；阶段工作完成不代表总体方向正确。

请分别给出：
1. 阶段任务结论：PASS / PASS_WITH_LIMITATIONS / FAIL；
2. 研究方向建议：CONTINUE / STOP / PIVOT，以及证据；
3. 关键数值与复算方式，指出任何不一致；
4. 未闭合指标、需要重放的数据及其影响；
5. 对“未知通道误差是否降低 GMTI、Known correction 恢复多少、传统盲估计差距、AI 是否必要、低速/复杂杂波是否应转为主线”的逐项回答；
6. 唯一优先下一步及其进入/停止条件；
7. 你实际读取的 commit 和文件清单、读取失败项及审查限制。

不得把测试通过当成算法性能证据，不得把 R18 的零时延结果说成未知误差实验，不得把 observed hit fraction 当作统计 Pd/Pfa。不得在没有证据时把阶段描述性差异表述为总体方法胜负。

若认为研究总纲需要修改，请只提出具体修改建议、证据和对既有成果的影响；用户明确批准前不得修改总纲，也不得把建议当作授权。不要自行启动 CUDA、训练 AI 或其他新实验。
```
