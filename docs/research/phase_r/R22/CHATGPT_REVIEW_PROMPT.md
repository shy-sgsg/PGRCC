# PGRCC R22 独立科研审查提示词

请先把 Codex 最终交付消息中的**完整 40 位 R22 审查 commit SHA**填入下方字段，再开始审查。必须在 GitHub 仓库中检出或读取该 SHA 对应的文件；不得以“最新 HEAD”代替审查快照。

```text
【PGRCC R22 阶段独立科研审查请求】

请首先读取审查 commit 对应版本的：
docs/research/RESEARCH_MASTER_PLAN.md

完整理解 PGRCC 的研究主题、总体研究目标、核心科学问题、研究内容、总体研究思路和长期技术路线，然后结合这些内容审查 R22 的代码、实验、数据和科研结论。不能只评价阶段任务是否完成，还必须判断 R22 是否推动整个 GMTI 研究目标，以及当前研究方向是否需要调整。

审查 commit（完整 40 位 SHA）：<粘贴 Codex 最终回复中的 R22 审查 commit>
仓库：https://github.com/shy-sgsg/PGRCC
分支：codex/unknown-system-error-next-stage
阶段：R22 多目标 Native CUDA Pilot 与历史 Baseline 证据审计
R22 入口 commit：6fdc4a9d08d91293d47ba6bdd967e8a22a688c4e
研究总纲入口 blob：e637e3769bde7814298ff012d0f42301884e2985
R21 证据基线：61da2ae010adb72ab824869031fa5c6a5df69ad4

阅读顺序：
1. docs/research/RESEARCH_MASTER_PLAN.md（必须首先读取审查 commit 内版本）
2. docs/research/RESEARCH_STATUS.md
3. docs/research/RESEARCH_DECISION_LOG.md
4. docs/research/CHATGPT_REVIEW_PROTOCOL.md 与 AGENTS.md
5. docs/research/phase_r/R22/README.md 和本目录内的实验设计、结果、统计计划、方向裁决、执行审计及冻结契约
6. docs/research/phase_r/R22/evidence/results/ 与 evidence/preflight/seed900202、seed900203_closure 中的 manifest、源码/二进制身份、运行时 XML、Stage2 报告、逐 beam Production 日志、CSI tap、CFAR geometry、检测和失败证据
7. R22 使用的 runner、scenario factory、evaluator 及对应测试

如果审查 commit 或研究总纲不可读取，请逐项说明缺失文件，并将总体方向结论标记为 NOT_VERIFIED；不得用本提示词里的概述代替实际阅读。若远端 HEAD 已高于审查 commit，请分别报告两者 SHA，并以审查 commit 作为本次证据快照。

【阶段执行事实与限制】

- R22 的 Native CUDA 工程 pilot 使用 seed 900202 与 900203，均为 2.5 m/s、20 dB、+4 ns、20 个真值目标、仅 1 个处理周期；Production 后端为本地 Native CUDA，USE_COREX=OFF。
- seed900202 执行了 A0、C0、C2、C3 子集；seed900203 追加 PK、PKR 和盲确定性候选，成功结束 30/36 个 Production 分支，6 个 PKR 相关记录因参考校准不可评估并触发生产 beam-quality gate。任务级总体状态为 EXECUTED=true、EVALUABLE=false、PERFORMANCE_VERIFIED=false。该工程 pilot 不等于预注册筛选或确认实验；不得声称统计显著的 Pd/Pfa 或性能改善。
- 在 seed900203 可评估的 ON 任务机会中，A0 16/20、未知 +4 ns Current C0 1/20、Known PK 16/20、R9 固定 D3 17/20、Huber D3 16/20、M1 Physics Matched 16/20；C2/C3 的 Mode-A/Mode-B 分支各为 1/20。每项只有一个周期，且整体 residual-closure 门因 PKR 失败未闭合；数字只能作为本 seed 的描述性 pilot 观察。
- seed900202 的独立单周期观察为 A0 17/20、C0 4/20、R9/Huber/M1 各 17/20；该 seed 没有 PK/PKR，不能证明 Known correction closure。
- 每个可评估分支 `union_dynamic_in_band` 与 `union_full_band` CFAR geometry 均有 527,280 valid CUT。H0 记录只有描述性观察，不具备独立 H0 seed 支持，故不能称为统计 Pfa。
- seed900203 Stage2 A0_ON 直接生成连续面积杂波 130 packets、650,892 samples，均值功率 0.0227564、功率 CV 2.04352、rho(lag1)=0.98006、CTDR lag=2；A1 的 Stage2 报告显示 direct injection 为 0，因为 A1 OFF/ON/TO 复用 A0_ON 生成的配对 C+N background。请核验 scenes manifest、scenario background path、background_reuse_mapping、因果 triplet 和输入 hash 后，再判断有效地杂波是否真实存在及各条件是否可比，不能只看 `area_enabled` 或每个报告的 direct packet 计数。
- PKR 的参考参数 `research_calibration_min_support=8`，参考生成使用 range band 宽 6；reference gamma 分组 support_count 实际为 6，全部因 `insufficient_phase_support` 标记 NOT_EVALUABLE。PKR Mode-A OFF / Mode-B ON 的 Production 日志出现 `scan beam quality gate failed: valid=0/1 required=1 min_ratio=0.95`，导致 CSI summary 缺失，A0/PK/PKR residual closure 不成立。不得通过降低 min-support 或质量门槛来制造成功。请判断根因是在参考分组/支持量契约，还是生产路径，并建议最小且可审查的修复方向。
- 一个周期不足以形成确认航迹；径向速度/地速和跨周期连续性没有有效样本。检测数不能直接代表虚警率，也不能代表最终航迹收益。
- 盲方法不得读取真实误差、目标真值、配对无误差参考、Known 输出或其他 oracle。请审查其配置和输入可见性；如果不能证明盲方法信息隔离，应标记该条件 NOT_EVALUABLE，而非接受结果。

【请完成两层审查】

第一层：阶段任务与证据正确性

1. R22 历史 Baseline 注册与可比性分类是否有证据支持？源码、Production binary、配置、evaluator、输入身份差异是否被正确处理？
2. 多目标场景生成、target ID、连续周期/随机子流、目标状态及因果配对是否真实实现并可复现？指出哪些只是已实现、哪些已真实运行。
3. 是否确实执行了本地 Native CUDA Production？核对运行 manifest、GPU、Stage2/Production binary SHA、运行 XML、日志、CSI tap、CFAR valid-CUT、目标输入/真值和源码 dirty 状态。
4. 面积地杂波是否在生产输入中真实存在？A1 的零直接注入报告是否由共享 A0 背景解释？共享数据是否足以保持 A0/A1 的因果比较？
5. Mode-A / Mode-B、ON/OFF/TO 的信息条件和处理链是否公平？参数估计是否存在真值泄漏或参考数据泄漏？
6. PKR 参考失败的根因链是否由配置、reference_gamma、Production 日志、beam gate 和缺失 CSI summary 共同证实？保持门槛不变的前提下，哪一处最小修正合理？
7. 哪些指标 EVALUABLE，哪些只能描述、哪些 NOT_EVALUABLE？检测、H0 false hit、valid CUT、参数误差、航迹与统计推断是否使用了正确分母和统计单位？
8. 归档证据是否足以从紧凑包复查结论？列出缺失或应追加的最小证据。

第二层：总体科研价值与研究方向

1. 结合你实际读取的研究总纲，本阶段是否推进了 GMTI 总体目标？R22 的多目标扩展是否值得作为下一项主工作？
2. R22 单周期描述数字是否支持未知 +4 ns 会损害检测、Known 校正可恢复该损失、传统盲估计与 Known 之间存在差距？每个判断的证据边界是什么？
3. 现有证据是否足以继续静态时延估计主线，还是应先解决多目标场景与 PKR residual-closure 的工程/评价缺口？
4. 是否有证据需要转向低速目标保护、复杂杂波建模或其他 GMTI 瓶颈？若没有，请明确说明证据不足。
5. AI 是否有必要？检查确定性估计器、Known 上限、盲估计残差及实际任务收益证据；不可因项目包含 AI 或一个单 seed 指标变化而建议训练。
6. 给出唯一、有限、带进入条件和停止条件的下一步建议。不要自动扩展 seed、矩阵、训练 AI 或启动新 CUDA。

【裁决输出格式】

- 总体结论：PASS / PASS_WITH_LIMITATIONS / INCONCLUSIVE / FAIL
- 证据状态：IMPLEMENTED、EXECUTED、EVALUABLE、PERFORMANCE_VERIFIED 分别判定并说明依据
- 事实核验表：主张、证据文件/行或字段、判断、限制
- 物理与实验设计审查：重点覆盖杂波共享、配对、盲估计信息隔离和 PKR gate
- 可评估性表：指标、统计单位、当前状态、可支持的主张
- 总体研究价值：对应研究总纲的具体目标与支持/反证
- 研究方向：CONTINUE / STOP / PIVOT，及其证据
- AI 必要性：ESTABLISHED / NOT_ESTABLISHED / NOT_VERIFIED
- 下一步：仅一个有限任务、必要输入、验收门、停止条件、是否需要用户批准
- 需要修订研究总纲时，只提出具体建议、依据、影响及建议范围；必须等待用户明确批准，不得自行修改 `RESEARCH_MASTER_PLAN.md`。

请优先给出可复核的证据和反例。阶段任务通过不等于研究方向正确；一个 seed 的检测差异不等于统计显著性或一般性能改善。
```
