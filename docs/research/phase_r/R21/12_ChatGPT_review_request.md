# PGRCC R21 固定通道时延 CUDA pilot：ChatGPT 独立审查提示词

复制下方提示词交给 ChatGPT。审查快照固定为 R21 证据提交 `a74fff44c386245db9e30cf8965e88806e70b5ed`；本提示词文件在该证据提交之后单独加入仓库。请审查指定快照，不要把提示词后续提交误当作实验执行版本。

---

```text
你是 PGRCC 项目的独立科研审查者。请对 R21“固定通道时延 CUDA 因果 pilot”做证据驱动的双层审查：先判断阶段实验与结论是否可信，再独立判断它是否推进了整个 GMTI 研究目标、研究问题是否值得继续，以及当前研究路线是否仍合理。不要只做代码检查，也不要把软件测试通过等同于任务性能改善。

【审查身份与冻结快照】
仓库：https://github.com/shy-sgsg/PGRCC
分支：codex/unknown-system-error-next-stage
本次证据/报告 commit（完整 SHA）：a74fff44c386245db9e30cf8965e88806e70b5ed
该快照研究总纲 blob：e637e3769bde7814298ff012d0f42301884e2985
研究总纲：docs/research/RESEARCH_MASTER_PLAN.md
R21 入口：docs/research/phase_r/R21/README.md
审查规范：docs/research/CHATGPT_REVIEW_PROTOCOL.md
动态状态：docs/research/RESEARCH_STATUS.md
方向决策日志：docs/research/RESEARCH_DECISION_LOG.md

请以固定 commit 的 GitHub 文件为准。第一步必须打开并完整阅读该 commit 的研究总纲：
https://github.com/shy-sgsg/PGRCC/blob/a74fff44c386245db9e30cf8965e88806e70b5ed/docs/research/RESEARCH_MASTER_PLAN.md
先理解 PGRCC 长期研究主题、总体目标、核心科学问题、研究内容、总体思路和长期技术路线，再按以下顺序阅读同一 commit 中的动态状态、决策日志、AGENTS.md、README.md、ChatGPT 审查规范、Phase R 导航和 R21 报告。之后沿决定性主张核对 manifest、逐条件结果表、审计包和必要源码。

必须报告你实际打开的 commit 和文件。若 GitHub、指定 commit、总纲或某项产物无法读取，明确列出，并把依赖它的判断标为 NOT_VERIFIED；不得假称读过，也不能用本提示词中的摘要代替总纲原文。当前远端 HEAD 若不同，只用于说明差异；本次审查始终以指定快照为准。

【审查问题与实验边界】
R21 检验冻结场景中固定 +4 ns 通道时延是否造成 GMTI 任务退化、Known-error correction 能恢复多少，以及盲确定性估计与 Known 之间还有多大差距。两个独立 seed（1001、1002），每 seed 三个目标周期；平台高度 6000 m，目标地速 6.7 m/s、SNR 30 dB。面积地杂波距离窗为物理可达的 84.5–85.5 km。六个处理条件为 A0、B(+4 ns 未校正)、Known 物理校正、R9 fixed D3、Huber D3、M1。每个 seed 运行六方法 × ON/OFF/TO，共 18 个 Native CUDA Production 分支；总计 36 个分支。

六条件的信息边界不同：A0 是零误差参考；B 不校正且不读 truth/A0/Known；Known 使用真实 +4 ns，仅由 evaluator correction adapter 使用；R9 fixed D3 使用历史固定校准量，不是本次盲估计；Huber D3 仅从同 seed B/ON 的 C1/C2 估计；M1 从同 seed B/ON 的 C1/C2 与 B Production 几何估计。Huber 和 M1 均不得读注入真值、A0/Known IQ、配对 OFF、truth 或 detection。所有校正方法使用同一个 float32 delay-rewrite 算子。请核对 `04_baseline_methods_and_input_fairness.md` 与机器可读 information contract，独立检查这些边界有没有实际执行证据。

关键执行身份和审计入口：
- Production binary SHA-256：9ffc232966bd92a39edfb39b90730851014dfa1ae7237a4ac172a764f31cc566；backend 为本机 Native CUDA，use_corex=false。
- seed1001 runner 源码 commit：6f261b218691cf99f2e1d90c2ed5cda70a96a4f4；seed1002：0957f49c028377cbd7a15c2ff0b2eda3f75ce9a9。
- Stage1 config SHA-256：ad47b0d02b05ddc4839da39a7498faf1af25ca3ba8eec678597c0ce7a0f651f6；Stage2 template SHA-256：1f4f9c8b9383614601dc019414b8c8a8788b195301526438b95a672b1ee13998。
- 运行与方法 manifest：docs/research/phase_r/R21/evidence/results/r21_experiment_manifest.json；逐输入身份：r21_input_identity.csv；评价身份：r21_evaluation_manifest.json。
- Runtime XML、逐 beam/stage 日志摘要、CSI tap、CFAR geometry、Stage2 杂波与逐机会审计归档：docs/research/phase_r/R21/evidence/cuda_pilot/r21_cuda_text_audit_evidence_20261027.zip，SHA-256 e1907ada95980a7a6adfec0e1a5e44e06ec645f8b13c5e1c661f4385f58f535a。
- 完整逐项表格：docs/research/phase_r/R21/evidence/results/；六张图：docs/research/phase_r/R21/evidence/figures/。
- 关键报告依次为 05_frozen_experiment_contract.md、06_cuda_execution_and_identity_audit.md、07_detection_and_task_results.md、08_parameter_track_and_physical_closure.md、09_failure_mechanism_and_baseline_analysis.md、10_scientific_value_and_direction_decision.md、11_execution_and_quality_audit.md。
- 回归测试记录为 docs/research/phase_r/R21/evidence/test_logs/r21_final_pytest.txt：111 passed。它证明相关代码回归通过，不代表 GMTI 性能验证。

seed1001 的外层 runner 在所有 18 个 Production 分支成功后因 KeyError('case_manifest') 失败；该包装错误被保留。随后仅对已有真实 CUDA 输出做 evaluator replay。seed1002 外层运行完成。不得把 CPU evaluator replay说成 CUDA 执行，也不得隐去 seed1001 的包装器失败。

面积杂波的实际注入已从 Stage2 记录确认：两个 seed 各 390 packets、1,952,655 samples，实测功率分别为 0.111666783002 和 0.110645343505，背景映射 scale=1。请检查这些证据是否足以支持“杂波确实存在且条件间可比”，不能只看 area_enabled 标志。

【报告中汇总的主要观测，须回到结果表核对】
- 正确目标命中机会数（每 seed 每方法 3 次）：A0 为 2/3、2/3；B 为 1/3、0/3；Known 为 2/3、2/3；M1 为 2/3、3/3。两种 D3 分别为 2/3、3/3。只有两个 seed、每个三次机会，所有命中比例均为描述性结果，不能称为统计显著 Pd 改善。
- B 的 F2 相对 RMS 为 0.327672、0.328735；Known 为 0.00008760、0.00008818，满足冻结的 1e-3 闭合门。M1 为 0.012317、0.040184，虽比 R9 fixed（约 0.0636）及 Huber（约 0.0223、0.0597）更低，仍远高于 Known，未闭合。
- 共同成功机会上的 M1 方位/位置 RMSE 两个 seed 均低于 R9 fixed 与 Huber，但仍高于 Known。样本仅 n=2–3；Ground-speed 样本 n=1/2 且排序不一致；radial velocity 无可评价机会。
- seed1002 中 M1 与 D3 命中 3/3，而 Known 为 2/3。小样本命中计数不能解释为盲方法超过 Known 校正上限，也不能单凭 Known 的校正身份推断它是每个随机检测实现的逐样本命中率上界。
- H0 的 observed CFAR hit fraction 约 0.000188–0.000244 只能描述。CUT 相关且缺少独立 H0 抽样单位，统计 Pfa 为 NOT_EVALUABLE。未匹配 confirmed tracks、false track IDs 和 TrackManager payload 计数不是 Pfa。
- Output SCNR proxy、target-only CSI retention 及复杂传递量均需结合自适应 CSI 的条件依赖解释，不能单独证明目标自消或最终任务改善。请查验 seed1001 中 B 的 SCNR proxy 高于 A0 而命中更少这一反例。
- 最终阶段裁决是 PASS_WITH_LIMITATIONS；固定静态时延确定性估计仅保留为有界候选；广义未知状态估计 NOT_VERIFIED；AI entry gate FAIL；PERFORMANCE_VERIFIED=false。请独立判断这些裁决是否被证据支持。

大体积原始 IQ/CSI 二进制不在 Git。必要子集仅保留于执行机器；清单为 docs/research/phase_r/R21/evidence/inputs/r21_retained_binary_manifest.json，清理记录为 evidence/cleanup_ledger.md。若你无法访问这些本地文件或无法下载/检查审计归档，指出哪些涉及原始复数数组的判断因此为 NOT_VERIFIED；可继续审查仓库内报告和文本审计，但不要声称独立核验了不可见二进制。

【必须完成的两层审查】
第一层——阶段正确性：
1. 核对物理模型、时延注入/校正、频域/通道关系、Stage2 地面散射几何和有效杂波；检查 Known 相对 A0 的 F1/F2 物理闭合是否成立。
2. 检查 A0/B/Known/D3/M1 的输入、信息来源、ON/OFF/TO 配对和 Production 链是否公平；M1 是否使用任何真实误差、目标真值、配对 A0 reference 或其他 oracle 信息。把“Known-error correction upper bound”限定为已知参数校正参考，不把它和统计上限混为一谈。
3. 检查 Production source/binary/input identity、seed1001 外层失败和 evaluator replay 的边界，确认 CUDA 与离线分析表述没有混淆。
4. 核对 CFAR valid CUT、命中分母、truth-range/Doppler mapping、目标匹配、目标保持、虚警统计单位、参数可评估性及失败机会；明确哪些是有效评价、描述性统计或不可评价。
5. 将核心主张分别标为 IMPLEMENTED、EXECUTED、EVALUABLE、PERFORMANCE_VERIFIED，并给证据路径或 manifest 字段。不要把 111 个软件测试通过写成性能验证。

第二层——总体科研价值与方向：
1. 结合你实际读取的研究总纲，解释 R21 对长期 GMTI 目标提供了什么新证据，哪些关键目标仍未推进。
2. 回答固定 +4 ns 失配在本冻结场景是否降低任务性能、Known 校正恢复多少、M1 等传统盲估计离 Known 多远。区分检测机会计数、物理闭合、角度/位置、地速、径向速度、SCNR 与 Pfa 的不同含义。
3. 判断固定时延估计目前是否仍值得作为有界传统候选；确定性估计的不足是否已经被充分证明；是否有必要研究广义未知系统状态估计。
4. 独立判断是否有证据将主线转为低速目标保护或复杂杂波建模。不得因这些主题重要或本次指标缺项就自动选择它们。
5. 审查 AI 是否必要。要求先有稳定、可重复的任务退化，Known 校正揭示的可恢复空间，强确定性基线仍无法解释/恢复的任务相关残差，以及无泄漏、可部署输入和独立场景证据。现有 R21 是否满足这些门槛？
6. 判断总纲定义的科学问题、技术路线和创新边界是否仍符合全部可见证据；如建议修改，提出具体范围、证据、替代方案及对既有研究的影响，但只作为供用户讨论的提案。

证据表述须区分：仓库直接观察、理论推导、外部一手文献、分析推论、未验证设想。若用文献支持方向判断，请引用可核查的一手论文/官方资料并说明其系统、数据、对照和适用范围，不可把文献结果外推成本项目收益。

【严格输出结构】
1. 审查范围与身份：仓库、固定 commit、总纲 blob、实际读取材料及不可访问项。
2. 阶段任务可信度：物理、代码/运行身份、数据、信息隔离、公平比较、评价与复现逐项判断。
3. 状态表：核心主张 × IMPLEMENTED / EXECUTED / EVALUABLE / PERFORMANCE_VERIFIED，并附证据位置。
4. 相对研究总纲的实际进展及仍未回答的问题。
5. 结果不能支持的结论、样本限制与 NOT_EVALUABLE 项。
6. 可能改变结论的物理、算法、数据、指标或比较问题，按严重性排序。
7. 科研价值、潜在创新性、已有传统解法和最简单替代解释。
8. 总体主题、目标、模块或技术路线是否需要讨论调整；逐项说明证据强弱。
9. 明确给出 CONTINUE / STOP / PIVOT 三选一作为当前唯一裁决，说明依据与停止条件。
10. 下一步唯一优先任务：只建议一个有限任务，列输入、交付和验收/停止门。不得泛泛要求“多跑实验”。
11. 哪个具体结果会改变你的方向建议。
12. 如建议追加研究决策日志或提出研究总纲修订，说明具体内容、依据与对既有成果的影响。

不要启动新 CUDA、增加 seed、重跑 baseline 矩阵、训练 AI、启动 R22 或修改实验代码。任何研究总纲修改都必须等用户对明确修改范围作出批准；本次审查不构成批准。若你建议下一阶段工作，只给出方向和门控，不要擅自执行。
```
