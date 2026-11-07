# PGRCC 科研阶段独立审查请求：R17

请首先读取本次 Git 提交对应版本的 `docs/research/RESEARCH_MASTER_PLAN.md`，完整理解 PGRCC 的研究主题、总体研究目标、核心科学问题、研究内容、总体研究思路与长期技术路线，然后结合这些内容审查本阶段的代码、实验、数据和科研结论。不能只评价阶段任务是否完成，还必须判断本阶段是否推动了整个 GMTI 研究目标，以及当前研究方向是否需要调整。

## 审查快照与任务定位

- 仓库：`https://github.com/shy-sgsg/PGRCC`
- 分支：`codex/unknown-system-error-next-stage`
- 审查 commit：`902572c7a9a6f7b91b14ff9db32514be664a58d0`
- 实验执行源码 commit：不适用；R17 未执行实验。所审历史运行源码提交为 R14 `095221bbfa458fd5f65bc1d095401cd04445aa9f`、R16 `2d2992b8b1fe54d42789c7250917ebac3c658de3`。
- 工作树状态：审查 commit 的 tracked tree 无未提交差异；共享工作区另有既存未跟踪 `.scratch/`、`outputs/`、`build_r7/` 与审查材料，未纳入该 commit。
- 研究总纲：`docs/research/RESEARCH_MASTER_PLAN.md`
- 研究总纲版本：blob `e637e3769bde7814298ff012d0f42301884e2985`；与 R16 前的目标文件 blob 完全一致。R17 按用户明确授权仅恢复第 9 节三段文字，R16 实验报告和证据未回写。
- 动态研究状态：`docs/research/RESEARCH_STATUS.md`，blob `6d44c3b1fbcf447ed824b9e78c53a6ff6a23b0aa`
- 决策日志：`docs/research/RESEARCH_DECISION_LOG.md`，blob `632823f91954843deb2b156693be7f2929d0d482`
- 阶段名称：R17 Production executable / GO-CFAR 跨版本身份审计
- 对应总体研究目标：保障 GMTI 物理处理链与任务评价可信，区分算法差异和处理实现/数值缺陷造成的结果差异。
- 本阶段科学问题：R14/R16 的相同场景 C0 Production CFAR hit 差异来自何处？可以把最早分歧定位到哪个处理层？

## 本阶段执行与证据

- 本次完成：离线核对 R14/R16 的 Production binary、声明源码版本、Stage2 输入/F1/F2/CSI hashes、runtime XML、CFAR geometry/numerical diagnostics，并对照 R7 已验证的 GO-CFAR 修复前后记录。没有运行 CUDA、重新生成输入或修改算法。
- 实验协议 / 设计：R17 是审计任务，没有新实验设计；审计方法与证据边界见 `docs/research/phase_r/R17_production_identity_audit.md`。
- 配置与关键 override：无新运行配置。R14/R16 C0 runtime XML 的 238 个标量叶子仅有 Stage2/truth/result/track-debug 路径差异；CFAR 类型、Pfa、alpha、训练窗、边界和 CUT geometry 一致。
- 源码、二进制与输入身份：
  - R14 Production binary SHA-256：`f65a8aa446754cc79b6417f9275b3a01e73eb85098c6a4f9eaea0cb999c38962`，与 R7 `pre_fix_experiment_manifest.json` 的 binary SHA 完全相同。
  - R16 Production binary SHA-256：`ae41c79ee48b530fc71e07b4ade7dcf7095d2ae9e9ee388f0e5ed9943a8fca4f`；R16 manifest 记录 Release Native CUDA build 与 toolchain。
  - R7 修复提交 `04958a996f6a1449fa3d9f158a4765493c85038d` 是 R14/R16 声明源码提交的祖先；R14/R16 的 `src/`、`include/`、`CMakeLists.txt`、`gmti.xml` 对比无差异。
  - R14/R16 的 9 个 Stage2 role-period 文件记录为 size/SHA 一致；C0 F1/F2 与 CSI 输出 real/imag hashes 也记录为 9/9 一致。原始文件已清理，当前引用的是封存比较表，无法重新散列。
- 执行后端与运行环境：R17 未执行后端或新运行。历史执行均为 Native CUDA Production；R14 的 GPU snapshot 和 R16 的 GPU/build provenance 分别见 R14/R16 evidence README 与 provenance。
- 关键日志与结果：
  - `docs/research/phase_r/R17_CFAR_divergence_analysis.md`
  - `docs/research/phase_r/evidence/r14_scc_robust_ddc_seed505/provenance.json`
  - `docs/research/phase_r/evidence/r7_cfar_correctness_20261022/manifests/pre_fix_experiment_manifest.json`
  - `docs/research/phase_r/evidence/r16_c1_gamma1_seed505/provenance.json`
  - `docs/research/phase_r/evidence/r16_c1_gamma1_seed505/production/C0_not_applicable_OFF/result/debug/cfar_geometry_diagnostics.csv`
  - `docs/research/phase_r/evidence/r16_c1_gamma1_seed505/production/C0_not_applicable_OFF/result/debug/cfar_numerical_diagnostics.csv`
- 评价产物与复现入口：`docs/research/phase_r/evidence/r16_c1_gamma1_seed505/analysis/r14_r16_input_identity.csv`、`r14_r16_c0_production_comparability.csv`、`c0_c1_runtime_xml_semantic_differences.csv`；本阶段为离线身份审计，没有本次 GPU 复现命令。
- 失败案例与未闭合项目：R14 原始 CFAR power/threshold/detector-input maps 已清理，因此 R14 每个 CUT 的非正门限数和错误 hit 数无法恢复。R14 的 build command、historical CMake cache/compiler/toolchain 也未保存，无法追出陈旧 binary 被复用的具体构建环节。

## 结果与阶段判断

- 主要结果：R14 实际运行了 R7 修复前 GO-CFAR executable。R7 已验证旧版 float32 前缀/积分图消减误差能产生负/零门限及额外 hit；R16 diagnostics 显示修复后的方向直接重算路径在运行。R14/R16 的 CFAR 和下游 Detection/TrackManager 结果不能跨 binary 排名。R16 同运行 C0/C1 比较仍是同一 executable 内的单 seed 结果。
- 证据状态：`IMPLEMENTED` 不适用（没有算法/软件改动）；`EXECUTED` 已执行只读证据审计，未执行本次 CUDA；`EVALUABLE` binary 身份根因已定位，R14 逐 CUT 门限错误数不可评估；`PERFORMANCE_VERIFIED=false`。
- 任务完成度：审计完成；R14 错误门限逐 CUT 数量、旧 build cache 和具体 stale-build 操作不可恢复。
- 科研价值判断：补上 Production 数值正确性和跨版本公平比较的身份证据，属于工程/评价可信度基础；没有新增物理误差因果证据或算法创新。
- 方向建议：`STOP` 把 R14 修复前与 R16 修复后结果并列排名，并停止把 R14/R15 检测/关联链视为修复后 Production 行为。长期 GMTI 研究主题不变。
- 总纲是否建议修订：否。唯一总纲改动是按用户已明确批准的有限范围恢复 §9 三段；其 blob 与 R16 前版本一致。
- 下一步建议：若用户希望重开 C0/C2/C3 比较，先在同一个修复后 Native CUDA executable 上做固定 seed505、OFF/ON/TO 的有限复验，并保存实际 CFAR complex input、power、threshold、geometry 与 map hash。Stage2 原始输入已清理，需重新生成并封存新身份。此项未获启动授权，R17 未执行。

## 请完成两层独立审查

1. **阶段正确性：** 核对本审计引用的 binary SHA 是否足以证明 R14 运行的是 R7 pre-fix executable；核对源码提交 ancestry、R7 根因报告、R16 fallback diagnostics、输入/XML/geometry 对齐是否支撑根因解释。特别区分“binary 与 pre-fix 完全相同”这个已证事实，与“R14 本场景每个具体 hit 都因负/零 threshold 产生”这个无法逐 CUT 复算的主张。
2. **总体科研价值：** 判断 R17 是否推动了总纲定义的 GMTI 研究目标；R14/R15 的结果在修复前处理链下还可支持哪些有限结论；R16 同运行 C0/C1 结果是否仍可解释；当前是否应优先做同一修复后 binary 的有限重比较；是否存在比这更简单、决策价值更高的工程或科研方案。不要把已知 CFAR 实现缺陷误判为 AI 需求。

请先报告你实际读取的仓库、branch、commit 和文件。若无法读取 commit `902572c7a9a6f7b91b14ff9db32514be664a58d0` 对应的研究总纲或证据，请列出缺失项，并将总体研究价值、方向一致性及总纲合理性判断标为 `NOT_VERIFIED`。不要仅凭本提示中的摘要代替读取总纲原文。完整规则见 `docs/research/CHATGPT_REVIEW_PROTOCOL.md`。

如建议修改研究总纲，请只给出带证据的具体提案，等待用户明确批准；不得直接编辑。不得因审查建议而自动启动 CUDA、扩展 baseline matrix 或训练 AI。
