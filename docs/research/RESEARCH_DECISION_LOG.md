# PGRCC 研究决策日志

> 本日志只追加，不回写旧决定。以下条目根据各阶段原报告整理；它们记录“当时依据当时证据作出的决定”，不是补造新实验。长期研究主题、目标和技术路线以 [RESEARCH_MASTER_PLAN.md](RESEARCH_MASTER_PLAN.md) 为准；当前阶段与短期任务以 [RESEARCH_STATUS.md](RESEARCH_STATUS.md) 为准。本日志记录已作出的方向决定，不自动授权修改总纲，也不能单方面覆盖总纲中的长期目标。历史条目中的“总纲变更”描述其记录时的历史事件，不构成未来修改授权，且本次未改写这些结论。任何总纲正文修改（包括阶段状态、当前任务、下一步、标题、链接和文字整理）都必须在编辑前获得用户对具体内容或明确范围的批准。每次新增方向决定，须记录决策编号、日期、代码/证据身份、原假设、问题、关键发现、`CONTINUE / STOP / PIVOT`、对总体目标与研究计划的影响、是否建议更新总纲、停止条件和下一项优先工作。新决定可以取代当前方向，但不删除旧条目。

## 2026-10-22｜R3/R4：阶段状态 INCONCLUSIVE，AI 必要性未建立

- **当时问题：** 修复后的生产链中，未知通道误差是否造成任务损失，或零误差下低速/杂波是否已成为更重要瓶颈？
- **证据：** R3 在受控 C2 `+4 ns` 条件下，对 seed101/202/303 观察到若干任务参数误差的同向退化与 Known physical correction 恢复；R4 seed101 的低速 ROI、最终 CFAR unique-hit 聚合和部分方法评价未闭合。
- **判断：** 特定时延注入值得继续验证，但当时不能确定整个研究主线；低速和杂波候选也不能排序。AI 不具备必要性证据。
- **影响：** R3/R4 不构成低速或未知误差的论文级胜出结论；保留失败和 `NOT_EVALUABLE` 项，不降低门槛。详见 [R3/R4 方向决策](phase_r/07_research_direction_decision.md)、[R4 评价审计](phase_r/R4_CFAR_H0_audit.md)。
- **当前状态：** 作为历史阶段判断保留；后续 R8–R12 增加了特定时延场景证据，但仍未解决候选方向的公平 Baseline 排名。

## 2026-10-23｜R8/R9：继续确定性盲校正的有限任务评估

- **当时问题：** 修复 CFAR 后的 `+4 ns` 退化能否跨独立 seed 重现，Known correction 是否恢复任务表现，固定/盲 D3 与 Known 尚有多大差距？
- **证据：** R8 三 seed 的正确检测机会 A0/B/C 为 `8/9、0/9、8/9`，已知校正输入闭合；R9 的固定与在线 D3 在这组留出场景中恢复了部分检测计数，但估计精度、参数误差和任务指标仍未全面达到 Known。结果仍受场景、目标机会和统计限制约束。
- **判断：** 有理由继续做一次边界明确的确定性物理估计与误差传播审查；没有理由开始 AI 训练或把盲估计普遍化。
- **影响：** R10/R11 聚焦正确目标关联、相位到参数误差传播与模型边界；这是一段具体证据追踪，不把固定时延提升为长期课题目标。
- **来源：** [R8 方向裁决](phase_r/R8_research_direction_decision.md)、[R9 方向裁决](phase_r/R9_research_direction_decision.md)。

## 2026-10-24｜R11：当时建议一次物理匹配估计验证（后由 R12 取代）

- **当时问题：** 在冻结的同一目标任务门下，物理模型匹配的确定性时延估计能否改善相对固定 D3 的校正精度？
- **证据：** R11 单 seed、单 period 的固定目标 cell 扫描中，C2/F2 输入、CSI/CTDR 相位及测角定位随校正量接近已知 `4 ns` 而闭合；径向速度仍不可评估，且未证明 blind estimator 可达到 Known。
- **当时判断：** 只建议一个有限的物理匹配估计 pilot；不自动运行、扩 seed 或训练 AI。
- **当前状态：** 该建议已由 R12 执行并由下条决定替代。R11 的机制证据仍保留在其单场景范围内。
- **来源：** [R11 方向裁决](phase_r/R11_research_direction_decision.md)、[R11 后更新方向快照](phase_r/Phase_R_updated_research_direction_decision.md)。

## 2026-10-24｜R12：PIVOT 到公平 GMTI Baseline 评价

- **代码与数据身份：** R12 seed404 Native CUDA Production；一个 C2 `+4 ns` 条件、三个目标机会，15 条分支。详细输入、源码、配置和运行身份见 [R12 紧凑证据包](phase_r/evidence/r12_physics_matched_delay_20261024/README.md)。
- **研究问题：** 在已知校正上限、物理匹配 M1 与固定/同输入 D3 对照后，未知系统固定时延估计是否仍值得作为主要创新方向？
- **直接证据：** 未校正 B 为 `0/3` 正确检测，A0/Known 为 `2/3`；M1 与固定 D3 各为 `3/3`。M1 同输入时延误差约 `0.2406 ns`，D3 约 `0.4119 ns`，但 M1 的角度/位置与地速指标方向不一致，且 M1 F2 输入 RMS 残差仍约 `2.684%`，未达到 Known 闭合。单 seed 不支持统计性 Pd/Pfa 或可重复性能判断；CFAR unique-union hit-cell、经验 Pfa 和径向速度 RMSE 未评估。
- **判断：** `PIVOT`。固定静态时延估计停止作为博士论文主要创新方向，并保留为确定性 Baseline。当前转向其他 GMTI 瓶颈的公平 Baseline 评价；AI 必要性未建立；不预选低速目标或复杂杂波为胜出方向。
- **对路线的影响：** 后续不自动追加时延 seed/R13，不开始 AI 训练，不把低速/复杂杂波的潜在难点写成已证瓶颈。比较应先审计共享评价条件，并区分不同信息自由度。
- **当前唯一优先任务：** 冻结小规模传统 GMTI Baseline 比较的共享评价契约，先审计现有 CFAR unique-union false-hit/Pfa 与径向速度可评估性，再据此限定可公平比较的任务和场景。若 Pfa 仍不可评估，限制相关主张，不把它伪装成通过，也不阻塞其它有效指标支持的受控比较。
- **停止/重开条件：** 若后续公平比较显示固定时延校正对其他主要瓶颈的归因不可缺少，可重新提出时延方向；若某候选方向缺少有效任务分母、真实信息输入或可重复收益，则停止对该候选排名。AI 只有满足总纲第 4 模块的证据门后才另行评审。
- **来源：** [R12 最终方向裁决](phase_r/R12_final_research_direction_decision.md)、[R12 生产任务比较](phase_r/R12_production_task_comparison.md)、[R12 盲估计验证](phase_r/R12_blind_validation.md)。

## 2026-10-24｜历史决定结构化索引补充

> 本节为上方已记录决定补充稳定编号和标准字段，不替代、删改或重新解释上方的历史条目。详细证据与限定仍以各自阶段报告为准。

| 决策编号 | 对应条目 | 原假设与关键发现 | 方向裁决 | 对总体目标/研究计划的影响 | 是否需要修改总纲 | 停止条件与下一项优先任务 |
|---|---|---|---|---|---|---|
| `D-2026-10-22-R3R4` | R3/R4 | 原假设是未知误差可能造成任务损失，低速/杂波也可能是候选瓶颈；R3 有窄场景时延方向证据，R4 低速 ROI 与 CFAR unique-hit 聚合未闭合。 | `CONTINUE` 有界评价与证据核查；总体瓶颈判断仍 `INCONCLUSIVE`。 | 不改变总体目标，不将时延、低速或复杂杂波升为唯一主线。 | 当时无总纲；本次总纲只把该结果作为历史证据。 | 若目标、分母、物理输入或生产门不成立，停止性能排名；当时按 08 计划先修评价并重审现有 case，后续已由 R8–R12 新证据取代。 |
| `D-2026-10-23-R8R9` | R8/R9 | 原假设是时延损失可跨 seed 重现，确定性盲校正可能回收部分任务差距；R8 的 A0/B/C 检测机会为 `8/9、0/9、8/9`，R9 校正仍未全面达到 Known。 | `CONTINUE` 有界确定性估计与误差传播审查；不启动 AI。 | 只把固定时延当作待检的中期问题，支撑后续 R10/R11 局部归因，不锁定长期课题。 | 当时无总纲；本次总纲记录为已完成历史阶段。 | 若盲估计不闭合或正确关联任务没有改善，停止时延方法扩展；当时后续为 R10/R11 归因，已由 R12 取代。 |
| `D-2026-10-24-R11` | R11 | 原假设是物理模型匹配的确定性估计有机会改善相对固定 D3 的校正精度；R11 只支持单 seed/period 的时延—相位—角度/定位关系，径向速度不可评估。 | `CONTINUE` 一个受限 M1 pilot；不自动扩 seed 或训练 AI。 | 这是阶段性验证选择，不把时延估计升为总体科学目标。 | 当时无总纲；之后由 R12 实验结果和当前总纲吸收/取代该建议。 | 若 M1 不能闭合到 Known 或任务指标不一致，停止作为主创新的论证；当时唯一后续为 R12 pilot，现已执行完毕。 |
| `D-2026-10-24-R12` | R12 | 原假设是固定时延盲估计可能达到足够 Known-like 校正并支撑主要创新；实际单 seed 中 M1 比同输入 D3 更接近时延真值，但 F2 未闭合、参数指标不一致，且只有三个目标机会。 | `PIVOT` 到传统 GMTI Baseline 公平评价；固定时延仅保留为确定性 Baseline。 | 保持 GMTI 物理模型与任务性能的长期目标，改变中期优先方向；AI 必要性和低速/复杂杂波优先级均未建立。 | **是。** 本次总纲明确 PIVOT、阶段 A–E、评价门和唯一下一任务；不改变长期研究对象。 | 若唯一 hit-cell、Pfa 或径向速度门仍不可评估，收窄可作结论的任务，不伪报指标；当前优先任务是冻结小规模 Baseline 共享评价契约，再设计受控比较。 |
| `D-2026-10-25-R13` | R13 | 零时延 seed101 Production 比较中，正常速度 TO target retention 出现候选差异；C2/C3 使用 paired OFF 参考、C0 不用；低速样例无稳定方法差异，统计 Pfa/radial RMSE 未闭合。 | `CONTINUE` 一次有界独立 seed 复核；R13 pilot 完成并停止，不自动续跑。 | 继续用可信 Production Baseline 识别任务瓶颈；不提升未知误差、低速、复杂杂波或 AI 为已证主线。Known bound 仅是特定校正问题参考。 | **是。** 更新 AI 适用范围、Known correction 比较边界与当前唯一建议；长期 GMTI 目标不变。 | 新 seed 复核正常速度 C0/C2/C3 配对 OFF/ON/TO；C2/C3 作同信息类别主比较。差异不复现、只有中间量改善或任务代价不可接受则停止。 |

## 2026-10-25｜R13：完成最小传统 Baseline CUDA 比较，建议一次有界独立 seed 复核

- **代码与运行身份：** source commit `3289c13bab6457bd1b190e393ddb01d51e9448e5`；Native CUDA、`USE_COREX=OFF`；RTX 3050 Laptop GPU；两场景 seed101，各 3 period；24/24 Production branches 通过。紧凑证据包含 manifest、逐 branch runtime/config/log、CFAR geometry、CSI tap、final detection、TrackManager CSV 和机器汇总。
- **研究问题：** 在固定零时延 baseline 场景，Current CSI、SCC、Robust DDC 是否出现值得复核的任务差距；区分处理算法和 paired OFF 参考信息。
- **直接证据：** continuous-area clutter 由 A0_ON 生成 390 packets/1,952,655 samples、mean power 0.0223353；C+N OFF 每期 IQ hash 与实际 background 文件相同。72/72 final-union CFAR map 与 geometry/log/valid CUT 闭合；18 个 role/period 组的 F1/F2 power-map hashes 在 A0/C0/C2/C3 间完全一致。正常速度 TO 中 C0 correct detection 为 2/3、target-only CSI retention −18.18 dB；C2 为 3/3、−4.11 dB；C3 为 3/3、+3.25 dB。ON 三者均 3/3，错误输出计数高。C2/C3 消费 OFF 标定参考、C0 不消费；低速挑战场景 CSI/检测差异未出现。单 seed 的三个 period 不独立，统计 Pfa、radial RMSE 和性能验证均未完成。
- **判断：** `CONTINUE` 一个严格限定的新独立 seed 复核正常速度 target-preservation 候选。C2 对 C3 是同信息类别比较；C0 仅作生产 Current operational anchor。若任务收益不复现或只有中间 CSI 改善，停止该候选方向。阶段 R13 本身已完成，未自动运行后续实验。
- **对路线的影响：** R13 完成 R12 后的 Baseline 评价 pilot；不证明未知系统误差普遍导致损失，也不回答 Known correction 或 blind estimator 与 Known 的差距。未证明低速/复杂杂波为主线，未证明 AI 有必要。Known-error correction upper bound 只作为特定误差校正问题参考，普通 Baseline 比较不强制套用四条件。
- **停止/重开条件：** 新 seed 的 C2/C3 target preservation 若不复现、对检测/参数无任务收益，或输出代价不可接受，则 `STOP` 此候选，不扩完整矩阵。若复现，仍需先审查任务重要性和替代解释，再决定研究方向；不自动训练 AI。
- **来源：** [R13 评价契约](phase_r/R13_evaluation_contract.md)、[R13 结果](phase_r/R13_minimal_comparison_results.md)、[R13 方向评估](phase_r/R13_research_direction_assessment.md)、[R13 证据包](phase_r/evidence/r13_baseline_cuda_20261024/README.md)。

## 2026-10-25｜R14：继续有限的 SCC / Robust DDC 任务级复核

- **决策编号与身份：** `D-2026-10-25-R14`。seed505、6.7 m/s、30 dB、0 ns delay、3 periods；执行源码基线 `095221bbfa458fd5f65bc1d095401cd04445aa9f`，R14 单场景 evaluator 的 tracked dirty 改动已纳入阶段变更。Native CUDA、`USE_COREX=OFF`；12 个 A0/C0/C2/C3 × OFF/ON/TO branches 通过。清理后的机器证据见 [R14 紧凑证据包](phase_r/evidence/r14_scc_robust_ddc_seed505/README.md)。
- **研究问题：** seed101 的 Robust DDC 相对 SCC 的 target-only CSI retention 候选差异，是否能在独立 seed 上复现并影响最终检测/航迹任务？
- **直接证据：** seed505 Stage2 真实生成 390 continuous-area packets、1,952,655 samples，平均功率 0.0234967；paired OFF/ON 与 C2/C3 F1/F2 identities 闭合，12 branches 及 36 张 CFAR map audit 通过。C3−C2 TO retention 为 +7.175 dB；ON target detection 两者均 3/3，C3 的非目标 detection rows 少 289（29.3%），false confirmed current-frame associations 少 177（38.5%）。但共同 ON 机会地速 RMSE C2/C3 为 1.086/9.259 m/s（n=2），末期目标候选 track ID 从 C2 的 6 变为 C3 的 64。经验 Pfa 与径向速度均不可评估，OFF 统计是 reference-in-sample。R13 seed101 同方向 retention 和 ON non-target rows 差异复现；两个 seed 仍不支持统计显著性或总体性能 claim。
- **判断：** `CONTINUE`，只继续有界的 C2/C3 确定性任务权衡复核。C3 的 false-candidate reduction 值得验证；地速/track continuity 代价、相对 C0 Current 的输出水平及 paired OFF 的部署可得性尚未闭合，故不判定 C3 整体胜出。
- **对总体目标与研究计划的影响：** R14 delay=0 ns，不改变 Unknown System Error Characterization 长期主线，也不能回答未知失配是否损害 GMTI。继续传统方法审查，不训练 AI、不开发 Router；R14 阶段到此结束，建议不自动执行。
- **下一项优先工作：** 若用户后续启动，优先用预注册的有限独立 seed 集合核验 C2/C3 的 ON 非目标候选与 current-frame false associations，并把 target track identity continuity 和 ground-speed error 作为共同任务指标；评估前确认 target-free paired OFF 的现实来源。H0/CFAR 仍须显式区分 in-sample hit fraction 与真实 Pfa。
- **停止条件：** 若 C3 false-candidate/target-retention 差异不再复现，或其 tracking/速度代价持续抵消收益，则停止把 C3 排在 SCC 前；只有多 seed 任务证据和可部署配对 reference 均成立时，才重开广泛研究价值评估。此证据不足以启动 AI。
- **来源：** [R14 设计](phase_r/R14_experiment_design.md)、[结果](phase_r/R14_minimal_comparison_results.md)、[方向评估](phase_r/R14_research_direction_assessment.md)、[证据包](phase_r/evidence/r14_scc_robust_ddc_seed505/README.md)。


## 2026-10-25｜R15：STOP 将 seed505 track switch 作为 Robust DDC 创新依据

- **决策编号与身份：** `D-2026-10-25-R15`。基线 `2b1942453b002045aaba4c1e35a8b93e75d0c38b`；复用 R14 seed505、0 ns delay 的 Native CUDA Production 记录；R15 固定检测回放为 CPU TrackManager counterfactual，没有新跑 CUDA。源码身份、输入 SHA 和回放配置见 [R15 证据包](phase_r/evidence/r15_seed505_track_association/README.md)。
- **研究问题：** C2/C3 面对相同正确目标 Detection 时，为何末期使用不同 track，且 C3 地速 RMSE 增大？这是否证明 DDC 有独立任务瓶颈？
- **直接证据：** C2/C3 三期 truth-matched D0 坐标相同。R15 Greedy replay 逐条复现生产 Detection-to-track edges：C2 `990/990`、C3 `701/701`。p0/p1 非目标候选使 track 7/64 的空间历史不同；p2 D3 与 D0 竞争时 C2 track 7 先取 D3、track 6 取 D0，C3 track 6 取 D3、track 64 取 D0。`updateMotionHistoryFromEN` 使用保留测量历史计算地速。p2 目标地速误差 C2/C3 为 1.523/13.092 m/s。Hungarian 改变 582/990 与 483/701 条关联；C3 末期误差下降到 5.389 m/s，C2 则升到 6.138 m/s，仍有 ID 切换且方向不一致。C3 的 151,087 CFAR unique hits 经聚类输出 701 detections，C2 的 97,155 hits 输出 990；不能据此宣称 Pfa 下降。
- **OFF reference 与限制：** C2/C3 Mode-A OFF hashes 与 H0 背景逐期相同，属于 reference-in-sample；实机 paired OFF 来源未验证。C0 不需 OFF、C1 ordinary subtraction 已实现但尚未运行。一个 0 ns seed 的关联反事实不能证明跨场景性能或未知通道误差效应。
- **判断：** `STOP` 把此次 track switch 当作 Robust DDC 本身的独立创新依据。它是候选生成、既有 Greedy 关联与历史地速估计的常规处理链交互；Hungarian 不是本例的通用修复。保留更宽的杂波抑制—候选分布—航迹连续性关系为待证问题，不因单例启动算法创新。
- **对总体目标与研究计划的影响：** 研究总纲维持广义 GMTI 物理处理链与任务瓶颈识别；未知系统误差仍是候选模块之一，不再写成唯一长期主线。R14/R15 的零时延 evidence 不能支持或反驳未知误差方向。AI 必要性未建立。
- **下一项建议与停止条件：** 若后续启动最小 Production 对照，优先评估 C1 `Gamma=1` reference-free ordinary subtraction 的 OFF/ON/TO 三角色，与 seed505 已有 C0/C2/C3 结果并列看 target preservation、非目标 Detection、current-frame association 和地速；只作单 seed 可行性检查，不自动扩 seed或完整矩阵。真实 paired OFF 获取方案不清楚时，先设计测量，不把仿真 OFF 当作实机能力。
- **总纲变更：** 是。更新 R14/R15 当前状态、删除过期的 seed505 复核建议，并明确广义 GMTI 目标及待用户启动的 C1 最小候选。
- **来源：** [R15 航迹关联分析](phase_r/R15_track_association_divergence_analysis.md)、[R15 CFAR 传播分析](phase_r/R15_cfar_detection_track_propagation.md)、[R15 OFF reference 审查](phase_r/R15_off_reference_deployability_review.md)、[R15 阶段裁决](phase_r/R15_research_direction_assessment.md)。

## 2026-10-25｜R16：STOP 将固定 C1 Gamma=1 作为本场景可接受替代

- **决策编号与身份：** `D-2026-10-25-R16`。seed505、6.7 m/s、30 dB、zero-delay、3 periods；本机 Native CUDA、`USE_COREX=OFF`。执行前 commit `2d2992b8b1fe54d42789c7250917ebac3c658de3`，runner/evaluator 与两组测试在执行时为 dirty；其运行版本源码快照、SHA-256、Production binary identity、device 与 toolchain 见 [R16 证据包](phase_r/evidence/r16_c1_gamma1_seed505/README.md)。A0/C0/C1 × OFF/ON/TO 共 9 branches 均通过。
- **研究问题：** 不依赖额外 OFF reference 的普通固定复数相减 C1（`Gamma=1+j0`）在 Production GMTI 链路的任务边界如何？
- **直接证据：** Stage2 连续面积杂波注入 390 packets、1,952,655 samples，测量平均功率 0.0234967；逐角色 3 periods 的 input 与 F1/F2 tap identities 闭合。27 张 CFAR final-union map/geometry audit 通过，每图 527,280 valid CUT。C1 ON/TO 目标正确检测各 1/3，C0 各 3/3；C1 检测行更少，但该值不是 Pfa。C1 H0 cancellation 为 −2.453 dB（C0 +5.582 dB），TO CSI retention −18.223 dB（C0 −0.671 dB），evaluator SCNR proxy 65.013 dB（C0 86.962 dB）。经验 Pfa 与 radial velocity `NOT_EVALUABLE`，单 seed `PERFORMANCE_VERIFIED=false`。
- **可比性限制：** R14 与 R16 的九个 Stage2 period inputs 及 C0 的 CSI before/F1/F2/after hashes 匹配，但 Production binary SHA 不同，C0 的 final-union CFAR maps 9/9 不同。因此 R14 C2/C3 不用于给 R16 C1 排名；不在本阶段重跑 C2/C3。
- **判断：** `STOP` 把固定 Gamma=1 C1 当作该场景的可接受整体替代；保留其 reference-free 传统基线结果。C1 非目标 Detection 减少不能宣称 Pfa 改善。该 zero-delay 单 seed 不回答未知失配因果效应，不否定合理盲估计器，也不证明 AI、低速保护或复杂杂波方向有必要。
- **对总体目标与研究计划的影响：** 不改变长期 GMTI 研究主题与科学目标；R16 是零时延传统 baseline 边界数据，不构成 Unknown System Error 的 A/B/C 证据。没有自动启动新实验或 AI。
- **下一项建议与停止条件：** 当前阶段停止。若用户之后启动新的方法比较，先统一 Production executable/source 身份和可评价任务门，再决定是否开展同 binary 的有限对照；不可把历史不同 binary 结果并列排名。若经验 Pfa/目标分母或处理链身份未闭合，收窄对应主张并停止排名。
- **总纲变更：** 仅依本阶段授权更新第 9 节当前状态与建议；其它章节未改。
- **来源：** [R16 冻结设计](phase_r/R16_experiment_design.md)、[R16 结果](phase_r/R16_minimal_c1_results.md)、[R16 方向评估](phase_r/R16_research_direction_assessment.md)、[R16 证据包](phase_r/evidence/r16_c1_gamma1_seed505/README.md)。

## 2026-10-25｜R17：定位 R14 使用修复前 GO-CFAR executable，停止跨版本排名

- **审计身份：** 基于工作分支 `codex/unknown-system-error-next-stage`、起始 commit `5d6d237732871ea98d27398754d02f4454198bf9`。本阶段仅做离线 manifest/XML/源码/证据审计；未运行 CUDA、未修改算法或参数。
- **研究问题：** R14 与 R16 的同输入 C0 Production CFAR maps 为什么相差约两个数量级，最早可以把分歧定位到哪一级？
- **关键证据：** R14 Production binary `f65a8aa446754cc79b6417f9275b3a01e73eb85098c6a4f9eaea0cb999c38962` 与 R7 `pre_fix_experiment_manifest.json` 的旧版 executable SHA 完全相同。R7 已证明该旧版 GO-CFAR float32 prefix/integral subtraction 会形成负/零门限并制造额外 hit。修复提交 `04958a996f6a1449fa3d9f158a4765493c85038d` 早于 R14/R16 所声明的源码提交；R16 数值诊断则记录 GO 方向直接重算路径。Stage2 输入、F1/F2 taps、CSI 输出 real/imag hashes、CFAR XML 参数和 geometry 均匹配，R14/R16 C0 CFAR maps 9/9 不同。
- **结论与边界：** 已确认 R14 实际使用修复前 binary，足以解释它与 R16 修复后处理路径不可比。R14 专属 power/threshold maps 已清理，无法逐 CUT 计量 R14 的旧版门限错误数或将每个 hit 标为错误；R14 复用该 executable 的具体构建/缓存操作因旧 CMake cache、编译器和完整构建日志未留存而不可追溯。
- **研究裁决：** `STOP` 将 R14 与 R16 的跨 executable C2/C3/C1 结果并列排名，也停止把 R14/R15 的 track replay 当作修复后 Production 证据。保留各报告为其真实历史运行记录。R16 同 binary C0/C1 的冻结单 seed 比较不受该跨版本身份问题影响。
- **对总体方向的影响：** 改善 GMTI Production 结果的数值与可比性审计；不改变长期研究主题，不回答未知系统失配因果问题，不建立 AI 必要性，也不确认低速/复杂杂波主线。
- **下一项建议（未授权）：** 若用户继续 C0/C2/C3 比较，先用同一个修复后 Native CUDA executable 完成一个 seed505 OFF/ON/TO 有界复验，并保留实际 CFAR 输入、power、threshold、geometry 和 map 身份；不得自动运行。
- **总纲治理记录：** 本 R17 任务获得用户对总纲第 9 节原 R16 前三段的明确、有限恢复授权。现已恢复到 R16 前 blob `e637e3769bde7814298ff012d0f42301884e2985`，仅该范围有改动；R16 实验事实和报告未被回写。之后任何总纲正文改动仍须事先获得用户明确批准。
- **来源：** [R17 binary 身份审计](phase_r/R17_production_identity_audit.md)、[R17 CFAR 分歧分析](phase_r/R17_CFAR_divergence_analysis.md)、[R17 可比性契约](phase_r/R17_baseline_comparability_contract.md)、[R17 方向评估](phase_r/R17_research_direction_assessment.md)、[R7 数值根因](phase_r/R7_CUDA_CFAR_numerical_root_cause.md)、[R14 证据包](phase_r/evidence/r14_scc_robust_ddc_seed505/README.md)、[R16 证据包](phase_r/evidence/r16_c1_gamma1_seed505/README.md)。

## 2026-10-25｜R18：完成零时延传统 Production 矩阵；停止性能排名

- **决策编号与执行身份：** `D-2026-10-25-R18`。Native CUDA Release，`USE_COREX=OFF`；实验 runner 源码 commit `014352ace1c4ad0bade0d759a099a47fe9ecfaa9`，Release build 来源 commit `4c6d4d303621324f5e174db0ba1811c5691df2e2`；Production binary SHA `3328d7c61bc36b6d3b906692e355c46c328150c0399fa457e6f871bcb79abaf0`。R18 配置 SHA `3af60966332877095e5447247ab08cb39a54ef7a15681f37adf33cc251082ae1`。
- **研究问题与范围：** 在固定零通道误差的 area-clutter Production 场景中，核验 C0/C1/C2/C3 的处理和任务记录。12 场景为 seed 606/707/808 × 6.7/0.5 m/s × 30/20 dB，每场景 21 branches，共 252 个 Native CUDA Production branches。Mode-A 的 C2/C3 使用 paired-OFF 参考，Mode-B 使用 ON 可观测数据，分层报告。
- **直接证据：** 12/12 场景都实测生成 390 continuous-area packets、1,952,655 samples；测量面积杂波均功率 0.0227470–0.0232009。12/12 因果 triplet、valid-CUT geometry/分母和 final-union-hit 审计通过。每方法/速度/SNR 组合观察到 9/9 truth-period target matches；该计数是描述值，不是统计 Pd。
- **未闭合项：** 真实 empirical Pfa、PK/PKR residual closure、目标专属幅相/SCNR、径向速度误差及 ID-switch lineage 不可评估，因此 `PERFORMANCE_VERIFIED=false`。seed909 的 area 与预注册 Stage2 统计门通过，C2 support 一致；C3 的 117 个 group 有 116 个 support count 不匹配冻结的 exact-support 门。按预注册规则停止评价，没有放宽门槛，未得到独立任务性能差值。
- **裁决：** `INCONCLUSIVE`；完成阶段并停止本轮方法性能排名。零时延结果不能回答未知通道误差是否降低 GMTI、Known correction 恢复多少或盲估计距 Known 多远。当前证据不支持低速目标、复杂杂波或 AI 主线优先级变化。
- **对总体目标与总纲的影响：** 不改变研究主题或长期路线。Master Plan blob `e637e3769bde7814298ff012d0f42301884e2985` 完全不变。没有启动下一阶段、AI 训练或新实验。
- **后续建议与停止条件：** 若用户继续，先授权限界的现有证据/评价契约审查，确认 H0 false-CUT、PK/PKR residual 与目标/Detection/TrackManager lineage 的可补性；再决定是否设计单一非零物理误差的四条件因果试验。此建议不是启动授权。
- **清理：** 保留每场景的非数组审计、manifest、XML/log、CSI/CFAR/Detection/TrackManager 输出和 summary；删除可由 seed/config 重建的原始 arrays，详见 [R18 cleanup ledger](phase_r/R18/evidence/cleanup_ledger.md)。整个项目根目录当前约 2.8 GB，低于 20 GB。
- **来源：** [R18 结果](phase_r/R18/06_signal_and_task_results.md)、[方向裁决](phase_r/R18/09_next_research_direction.md)、[质量审计](phase_r/R18/05_execution_and_quality_audit.md)、[完整 evidence 导航](phase_r/README.md#r18-native-cuda-传统方法-production-矩阵-2026-10-25)。

## 2026-10-26｜R19：严格复评 R18 saved data；零时延数据不回答未知误差

- **决策编号与身份：** `D-2026-10-26-R19`。审查基线 `095d40996bd2a85c5d04b3c8db1b1e9528737b9d`，研究总纲 blob `e637e3769bde7814298ff012d0f42301884e2985`。R18 runner commit `014352ace1c4ad0bade0d759a099a47fe9ecfaa9`，Release build source commit `4c6d4d303621324f5e174db0ba1811c5691df2e2`，Production binary SHA `3328d7c61bc36b6d3b906692e355c46c328150c0399fa457e6f871bcb79abaf0`。
- **研究问题与范围：** 对 R18 seed606/707/808 × 6.7/0.5 m/s × 30/20 dB 共 12 个已保存场景，按冻结 `r19-task-eval-v1` 重算 ON final Detection 的 strict truth opportunities，并审查 OFF/TO、Gamma、CSI summaries、CFAR counts、TrackManager 与参数字段。没有 CUDA、没有新 IQ、没有 AI 训练。
- **直接证据：** 6.7 m/s、30 dB 的 9 个目标周期机会中，A0/C0 为 5/9、C1 3/9、C2-A 9/9、C2-B 6/9、C3-A 9/9、C3-B 3/9；20 dB 为 8/9、5/9、9/9、9/9、9/9、4/9。低速两档均为 9/9。各值为描述计数，不是统计 Pd。Mode-A 使用 paired OFF，不能按盲在线方法解释。R18 旧门 vs R19 strict 审计显示 seed606、6.7 m/s、30 dB 下 C0 为 24→2 条候选边、C3 Mode-B 为 23→1 条；旧 `3/3` 与 strict 命中差异来自门控定义，不能当成此次新实验中的物理退化。
- **质量/限制：** evaluator 29 项测试通过；12 个场景离线重算完毕。H0 observed false-hit fraction 仅作描述，empirical Pfa `NOT_EVALUABLE`；target-only complex CSI/SCNR 与 CFAR per-CUT 空间归因需重放；radial velocity `NOT_EVALUABLE`；缺少 `track_association_audit_v2`，ID-switch 根因不可识别。R18 所有 delay error 均为 0 ns。
- **判断：** 阶段 `PASS_WITH_LIMITATIONS`；研究问题的科学结论仍 `INCONCLUSIVE`，但下一步建议为 **CONTINUE（仅限一项离线证据统一复评）**。R19 不能回答未知通道误差是否降低 GMTI、Known correction 恢复多少或盲估计差距。历史 R8/R12 +4 ns 数据提供方向信号，但尚未在 R19 契约下统一复评。未建立 AI、低速目标保护或复杂杂波主线优先级。
- **下一项建议与停止条件：** 按 R19 `r19-task-eval-v1` 离线复评 R8 三 seed 与 R12 单 seed的 +4 ns 记录；身份/真值/逐期 Detection 不足时逐项标不可评估并提交最小 replay 设计，不自动运行 CUDA。R8 的 54 份 Detection 与 12 份 truth 路径当前在本机可读，但未纳入 Git evidence 包。若 strict 结果不能重现失配损失或 Known 恢复，暂停未知状态估计主线并重新评估核心瓶颈；建议未授权自动执行。
- **总纲与容量：** 未修改 Master Plan；blob 保持 `e637e3769bde7814298ff012d0f42301884e2985`。工作树约 2.0 GB，低于 20 GB；本阶段未清除用户现有数据。
- **来源：** [R19 总实施计划](phase_r/R19/00_master_execution_plan.md)、[复评结果](phase_r/R19/04_r18_baseline_task_reevaluation.md)、[机制审查](phase_r/R19/05_mode_a_mode_b_failure_mechanism.md)、[方向评估](phase_r/R19/07_research_opportunity_assessment.md)、[执行审计](phase_r/R19/09_execution_and_quality_audit.md)。

## 2026-10-26｜R20：未知 +4 ns 对任务有有限因果证据，静态估计不升为主创新

- **决策编号与研究总纲：** `D-2026-10-26-R20`；研究总纲 blob 保持 `e637e3769bde7814298ff012d0f42301884e2985`，没有修改正文。R20 评价器为 `r20-task-eval-v2`，与 R19 v1 分离。
- **问题与方法：** 修正 TrackManager result ID/period 归属、truth 与参数分母、present-invalid range_bin fallback、identity schema、ground-speed payload 资格/重复规则及航迹 lineage 定义；以物理 Doppler truth、独立斜距率 Doppler 和保存的 Production axis/PRF 正控，离线统一重评 R18、R8、R12。
- **直接证据：** R18 12-case 的 strict hit/opportunity/miss/hit fraction 与 R19 数值一致；14/84 条 R18 condition 的 post-confirmation match rate 从 1.0 更正为 0.5。Doppler TO 正控制 3/3 通过冻结 ±2 行门，A0 ON 轴/真值映射 3/3 闭合；588 行独立 range-rate 与 truth 频率的最大差为 `0.001013469 Hz`，冻结容差 `0.01 Hz`。R8 seed202/303 的 B 均为 0/3、A0/Known 分别 2/3 与 3/3；失配损失 L 中位数 5/6、范围 2/3–1，Known 回收量 G 同值，E=0。R12 seed404 为 B 0/3、A0/Known 2/3、D3/M1 各 3/3。
- **限制：** R8 seed101 缺保存的 Production Doppler axis，三个条件均不纳入统一命中结论；R8/R12 raw IQ 当前不可读，Known closure 只引用留存 manifest。CFAR OFF/ON/TO 有效 CUT counters 和 observed fractions 已从 diagnostics 提取，但统计 Pfa 仍因独立 H0 sampling unit/统计设计不足而不可评价。M1 跨 seed 重复性和真实硬件适用性未验证。单 seed 三个 target opportunities 不是三份独立重复。
- **裁决：** `CONTINUE` 仅限用户独立审查后考虑两个独立 seed 的窄 M1 validation；R20 任务本身结束。+4 ns 物理失配在三个可评价 seed 的相同场景中均对应 B=0/3，Known 在可评价 R8 seeds 与 R12 回到 A0 hit count，支持该场景存在任务损失及可恢复空间；这不构成总体 Pd/Pfa/统计显著性结论。R12 M1/D3 检测各比 Known 多 1 个机会，但共同成功机会参数 RMSE、输入 F2 closure 仍落后 Known，方向不翻转为静态估计主创新。
- **对长期目标与 AI：** 不调整 GMTI 研究总目标；维持未知物理状态 characterization 作为待验证模块，固定静态 delay estimator 保留作传统 baseline。没有确定性方法稳定失败证据，不训练 AI；低速与复杂杂波也不是本次变量，不能据此改变排序。
- **下一项与停止条件：** 只有用户/ChatGPT 审查确认后，才考虑固定场景的两独立 seed A0/B/Known/M1 Production 复核，并保存实际 axis、raw inputs、identity、Detection/TrackManager。若失配与 Known 恢复不重复，停止该 error→task claim；若 M1 不重复收益或持续有参数/closure gap，停止扩张窄估计支线并回到公平传统 Baseline 评价；无稳定确定性 residual 时不进入 AI。
- **来源：** [R20 方向裁决](phase_r/R20/09_research_opportunity_and_direction_decision.md)、[R8 复评](phase_r/R20/07_r8_harmonized_nonzero_delay_causal_review.md)、[R12 复评](phase_r/R20/08_r12_deterministic_estimation_capability_review.md)、[机器结果与身份及紧凑输入包](phase_r/R20/evidence/)。

## 2026-10-27｜R21：固定 +4 ns 时延任务损失在两 seed 重现；M1 作为有界确定性候选保留

- **决策编号与总纲：** `D-2026-10-27-R21`。研究总纲 blob 保持 `e637e3769bde7814298ff012d0f42301884e2985`；本阶段未修改总纲正文。
- **问题与范围：** 以 `r21-task-eval-v3` 修正 TrackManager lineage、Doppler 逐机会映射、truth range-bin 与 evaluability 状态；对两个独立 seed 的 6.7 m/s、30 dB、+4 ns 连续面积地杂波场景执行六方法 × ON/OFF/TO Native CUDA Production pilot。没有增加 seed、训练 AI、修改算法或启动 R22。
- **直接证据：** seed1001/1002 的 A0、Known 各 2/3 命中，B 分别 1/3、0/3；R9 fixed D3、Huber D3、M1 分别 2/3、3/3。Known F2 相对 A0 RMS 约 `8.76e-5`、`8.82e-5`，通过 `1e-3` 门；B F2 RMS 约 0.328。M1 在两个 seed 的共同成功机会 angle/position RMSE 以及 F2 残差均优于 R9/Huber，但角度/位置仍差于 Known，F2 RMS 为约 0.0123/0.0402。ground-speed 仅 n=1/2 且排序不稳；radial velocity 无有效估计。统计 Pfa 因缺独立 H0 抽样单位不可评估。
- **实验身份与异常：** 两 seed 各 18/18 Production branches passed；seed1001 外层 wrapper 在分支完成后因 `KeyError('case_manifest')` 报失败，状态原样保留，并用修复后的 evaluator replay 评估已完成 CUDA 产物；seed1002 外层运行完成。Binary SHA 为 `9ffc232966bd92a39edfb39b90730851014dfa1ae7237a4ac172a764f31cc566`，`use_corex=false`。
- **裁决：** 阶段 `PASS_WITH_LIMITATIONS`。固定静态时延估计 `CONTINUE` 仅限作为有界确定性候选/传统 baseline；不认定论文创新或总体 GMTI 性能收益。广义未知物理状态 `NOT_VERIFIED`；`AI_ENTRY_GATE=FAIL`；`PERFORMANCE_VERIFIED=false`。两个 seed 只提供描述性可行性证据。
- **方向与停止条件：** 先完成 ChatGPT 独立审查；若用户决定继续，只建议先离线分解 M1–Known 的 F2 复数残差，不新增 CUDA/seed。若离线残差结构不能支持稳定、任务相关且超越简单 D3 的确定性改进，则停止静态时延研究重点并回到共同 Production 条件下的强传统 GMTI baseline 瓶颈判别。低速保护和复杂杂波本轮未区分，不提升为新主线。
- **测试与结果：** 定向 R19/R20/R21 测试共 `111 passed`；结果、配置、报告和图见 [`phase_r/R21/README.md`](phase_r/R21/README.md)。
- **空间清理：** 通过 CRC 和 SHA 审计后清理了两 seed run 中可再生的重复 `.bin/.npy`，保留闭合必需的 raw A0/B、六方法 ON input 和 ON CSI arrays；工作区由约 16 GB 降至约 6.7 GB，详见 [`phase_r/R21/evidence/cleanup_ledger.md`](phase_r/R21/evidence/cleanup_ledger.md)。

## 2026-10-27｜R22：停在 PKR reference support gate，不外推单周期 pilot

- **阶段状态：** `INCONCLUSIVE / STOP_AT_ENGINEERING_GATE`。研究总纲 blob `e637e3769bde7814298ff012d0f42301884e2985` 未修改。R22 完整 screening/confirmatory matrix 未执行。
- **真实执行：** seed900101 完成零时延 20 目标 Native CUDA smoke；seed900202/900203 为 2.5 m/s、20 dB、+4 ns、20 目标、单 beam、单周期的 Native CUDA pilot。seed900203 manifest 有 36 条方法/角色记录，30 passed、6 `NOT_EVALUABLE`。
- **有效杂波：** seed900203 A0_ON 连续面积杂波为 130 packets / 650,892 samples，均值功率 0.0227563850584。A1 的直接注入计数为零，是因其复用 A0_ON 生成的 C+N 背景；场景 manifest、背景路径、`matched` mapping 和 causal triplet 支持配对来源，详见 R22 `area_pairing_audit.json`。
- **停止根因：** PKR 配置 `range_band_bins=6`、`min_support=8`；两个 estimator 分支生成 26,637 reference groups，support=6 的 26,598 组、support=3 的 39 组全部 `insufficient_phase_support`，valid groups 为 0。生产 scan beam quality gate 失败，未输出 CSI tap，A0/PK/PKR residual closure 不可评价。没有降低 min-support 或生产质量门。
- **方向边界：** 两个 seed 的一周期 C0 hit 分别为 4/20、1/20；对应 A0 为 17/20、16/20。Known 仅在 seed900203 有 16/20，盲方法也仅作 seed900203 描述。不能据此报告统计 Pd/Pfa、一般 Known recovery 或盲估计差距；AI 必要性未建立；低速/复杂杂波优先级没有被本实验检验。
- **下一步：** 先做 ChatGPT 独立审查。若用户决定继续，仅建议一项离线 PKR grouping/support contract 审计，保持两个门槛不变；在该问题闭合前不扩 seed、不跑完整矩阵、不训练 AI。
- **证据：** [R22 导航](phase_r/R22/README.md)、[阶段方向裁决](phase_r/R22/06_scientific_decision.md)、[执行质量审计](phase_r/R22/07_execution_and_quality_audit.md)与[ChatGPT 审查提示词](phase_r/R22/CHATGPT_REVIEW_PROMPT.md)。

## 2026-10-28｜R22 V1A：133-bin robust gamma reference coverage gate 未通过

- **决策编号与总纲：** `D-2026-10-28-R22-V1A`。研究总纲 blob 保持 `e637e3769bde7814298ff012d0f42301884e2985`，没有修改总纲正文。
- **问题与执行身份：** 评估 0 ns 无误差参考下，20 targets/10 periods 的 Native CUDA Production 链能否通过已冻结的 C4/PKR robust gamma support gate。源码 commit `91cb5b1de8c2831073465e9192c1a385f0e0e25a`；V1A 设计 SHA `b7613779c4031036e34b8929bfd9525e1f846d8d87d616e00050ffe34b4b5c29`。Stage2 与 Production binary SHA、设备和执行记录见 V1A 复测报告。
- **直接证据：** Stage2 生成 1300 continuous-area packets / 6,508,782 samples，实测均值功率 `0.0225128995081`，area gate 与 causal triplets 通过。41 条方法/角色记录中 24 条通过；4 个 C4/PKR reference 主分支实际运行且 Production return code 为 0，但 reference validation 不通过；8 个依赖分支因此未运行；5 个 0 ns 物理校正记录正确标为不适用。133-bin 分组下，Mode-A/OFF 有 15/12,090 行在 robust 相位筛选后 support 为 3–7，Mode-B/ON 有 11/12,090 行为 5–7；源码显示这些行筛选前均已有至少 8 个非零交叉项，冻结 `min_support=8` 未放宽。直接触发点为 ±0.35 rad 相位一致性筛选后的支持不足，底层物理原因未确认。
- **裁决：** V1A engineering matrix `GATE_FAILED`，12 个工程 case 计划中仅一个 0 ns case 执行。`EVALUABLE=false`、`PERFORMANCE_VERIFIED=false`；+4 ns、其余工程 seeds、screening、confirmatory、40-target stress、3-target bridge 和独立 H0 均未启动。停止算法性能比较，不将 case 结果解释成未知时延任务退化、Known 恢复量、盲估计差距或 AI 证据。
- **对研究方向的影响：** 本次确认该 seed 少数 133-bin group 在 robust phase consistency 筛选后无法提供完整 reference；这不是几何稀疏的证据，且低相位一致性的物理原因尚未确定；它不是对未知误差科学问题的否定，也不支持改投低速目标或复杂杂波研究。AI 必要性仍未建立。
- **下一步与停止条件：** 先进行 ChatGPT 独立审查。只有用户决定继续后，才建议离线审查 support-group 覆盖、输入资格与 reference contract；任何新分组/有效区域规则需记录空间分辨率代价、缺组语义和评价分母，形成新设计版本后再运行。不得降低 support、CFAR 或 beam-quality gate，不得静默回退 Current。
- **证据与空间：** [V1A 工程门复测记录](phase_r/R22/11_v1a_engineering_gate_result_20261028.md)及[精简 Production 审查包](phase_r/R22/evidence/extension_engineering_v1a/seed1930101/README.md)。删除 921 个可再生 binary 共 `4,429,702,312` bytes，原始 SHA 清理账保留于本机输出目录；worktree 清理后约 6.9 GB，小于 20 GB。完整 ChatGPT 审查要求见 [R22 ChatGPT 提示词](phase_r/R22/CHATGPT_REVIEW_PROMPT.md)。

## 2026-10-29｜R22-S1：完成组级离线审计，逐单元归因待补

- **决策编号与依据：** `D-2026-10-29-R22-S1`。用户转交的 R22 screening 独立审查和 [R22-S1 离线诊断](phase_r/R22/support_failure_diagnostic/README.md)；总纲 blob `e637e3769bde7814298ff012d0f42301884e2985` 未变。
- **原假设与证据：** 单 seed 的高 SNR 目标可能污染 C4 ON reference。1,560 组 ON20、ON30 与 OFF30 gamma 身份可对齐；失败组支持数分别为 25、7、55。失败目录的 `.bin/.npy/.npz` 和逐单元 failure-cell 记录不可用，相位校正 `.f32` 不能代替 F1/F2；组级记录不足以唯一确认目标污染。
- **裁决：** `CONTINUE` 总纲既定的传统方法能力边界与瓶颈归因；R22 screening 保持 `GATE_FAILED / INCOMPLETE`，不覆盖原 C4 失败。AI entry gate 仍为 `FAIL`；不改变长期目标，不建议据此修改总纲。
- **下一步与停止条件：** 离线 S1 已记录资料不足与最小补采方案，等待用户决定是否批准新诊断实验。若获批准，先用固定选区旁路 tap 采集配对输入，验证身份、非侵入性与原失败重现；若这些门不通过，停止归因并保留失败记录。未执行新 CUDA、未修改算法及冻结阈值，不自动重启矩阵。

## 2026-10-29｜R22-S1c：共同 OFF CSI 相位缩小单组 C4 support gap

- **决策编号与依据：** `D-2026-10-29-R22-S1C`。用户批准 ChatGPT 独立审查提出的 S1c 局部反事实；研究总纲 blob `e637e3769bde7814298ff012d0f42301884e2985` 不变。
- **执行身份：** 分支 `codex/unknown-system-error-next-stage`；Production 执行源码 commit `0189933420d73bb04c3a466e633bd664a23871c0`；Native CUDA `USE_COREX=OFF` binary SHA `16cb0f2d35596ff98ab8bdcbc9571b972b99e217053cf0b1b7f6b49168c8c998`。只复用 seed1932001、2.5 m/s、+4 ns、20 targets、SNR20/30 的 GMTI05 输入；Stage2 未重跑。
- **科学问题：** 将两种 SNR 的 ON CSI range-phase 统一为相同 target-free OFF 向量后，选定 C4 Robust DDC-RB group 的 support=25→7 是否仍出现？
- **关键执行证据：** 五个单期 Native CUDA Production 分支均 return code 0；实际 XML 保留 `min_support=8`、相位门限0.35 rad、133-bin 分组。十期 OFF 输入在两场景的 SHA 列表逐项一致。每个 CSI/Gamma trace 有五阶段×133个单元；Production 输出的 support/coherence/Gamma 与基于 selected-cell CSV 的 C++ 规则重算一致。counterfactual 使用的 CSI phase 与 OFF 输出逐字节相同，raw phase 与各自 ON baseline 字节相同。
- **直接结果：** 各自 ON CSI phase 下 SNR20/30 support 为25/7，coherence为0.2028995671/0.1172000323；共同 OFF phase 下为21/22，coherence为0.1855220752/0.0228710435。两条件 support gap 从18降到1。SNR30 在 privileged common OFF phase 条件下跨过冻结support门；此事实不能把其原始 ON reference 状态改写为成功。
- **因果边界：** 可评估的是这一固定输入组的 support 对 CSI phase 向量替换敏感；该介入解释了大部分观测到的组间 support 差。不能唯一判定 ON phase 向量为何偏离 OFF（目标分量、CSI估计过程或交互），不证明目标污染唯一根因；paired OFF 是 privileged diagnostic reference，不代表在线部署能力。没有 task-Pd/Pfa、幅相保持、SCNR或速度/方位性能比较。
- **方向与停止：** R22 screening 仍 `GATE_FAILED / INCOMPLETE`；本次 `PERFORMANCE_VERIFIED=false`。不降低门槛、不修订算法、不恢复全矩阵、不训练AI。先提交 [S1c 结果](phase_r/R22/support_failure_diagnostic/S1C_SHARED_OFF_PHASE_RESULT.md)作 ChatGPT 独立双层审查，再由用户决定是否开展更窄的 ON CSI phase 误差来源诊断。
- **来源：** [S1c 报告](phase_r/R22/support_failure_diagnostic/S1C_SHARED_OFF_PHASE_RESULT.md)、[紧凑 CUDA 证据包](phase_r/R22/support_failure_diagnostic/evidence/s1c/v2/)、[首轮失效说明](phase_r/R22/support_failure_diagnostic/evidence/s1c/ATTEMPT1_INVALIDATION.md)。


## 2026-10-31｜R22 H0：冻结的 30-seed 独立虚警侧矩阵完成

- **决策编号与范围：** `D-2026-10-31-R22-H0`。按用户指示恢复冻结的 case-level matrix 并清理已归档 raw 数组；没有改变设计、门槛或算法。研究总纲 blob `e637e3769bde7814298ff012d0f42301884e2985` 保持不变。
- **执行身份：** 分支 `codex/unknown-system-error-next-stage`；矩阵/Production 执行源码 commit `a688ca52616cc380b07810d4f8fbd79ed73d46bc`；seed-clustered 分析脚本随本次审查交付提交提供；本机 Native CUDA，`USE_COREX=false`。Stage2 SHA-256 `47fa5ec34ca15cdc4edeb35226eefa941a78ee344aa2311e5e7984c811dcb40f`；Production SHA-256 `d9e4d2e0b8429c5415e94be1af0230f3c0148455c394dc6d661e7e6c13a4ad1e`。
- **完成状态：** 30 个独立 seed、60/60 唯一 0/+4 ns case；matrix `COMPLETED`，analysis `COMPLETE_EVALUABLE`。每 seed 按 `observed_cut_hit_count / valid_cut_count` 计算经验 CUT-hit fraction；10,000 次 seed-level bootstrap，配对重抽样保留 seed 对应关系。60/72 方法/条件摘要、24/36 配对摘要可评价，其余为 0 ns 不适用项。
- **杂波与可比性：** 60 个 `A0_ON` case 均通过面积杂波 gate，每 case 1,300 packets、6,508,782 samples；30/30 seed 对的面积功率、几何、相关性和噪声审计字段匹配。结果只覆盖无目标的经验 CUT-hit fraction，不是系统 Pfa 或 target Pd；不同 CUT 区域方向并不一致。
- **执行与清理：** 恢复按 case 创建新 attempt，未实现单个 Production branch 的内部断点续算。Matrix 保留 63 条 attempt 记录，3 个中断现场有 compact audit archive。清理 72,060 个完成 case 生成路径（472,370,816,936 bytes）及 3 个中断 attempt（21,830,424,640 bytes）；60 个 compact case ZIP 共 180,896,331 bytes 暂留本机供独立审查，未上传 GitHub。外部训练期间 GPU 高负载，最低可用显存约 393 MiB；无 OOM/CUDA 错误，不将设备观察当作性能基准。
- **裁决：** H0 `IMPLEMENTED=true`、`EXECUTED=true`、`EVALUABLE=true`；GMTI target-task `PERFORMANCE_VERIFIED=false`。R22 target-present screening 仍 `GATE_FAILED / INCOMPLETE`（C4 Mode-B ON support=7<8），H0 不解除门控。`AI_ENTRY_GATE=FAIL`；未知误差目标任务收益、低速目标和复杂杂波优先级仍未由本 H0 证明。
- **下一步与停止：** 仅提交精简证据和固定 commit 给 ChatGPT 完成双层独立审查；审查返回前不启动实验。审查结束后再按 cleanup ledger 清理本地 case ZIP，并核验项目目录低于 20 GB。没有修改研究总纲、没有启动 AI 或其他新研究任务。
- **证据：** [H0 最终报告](phase_r/R22/18_h0_false_alarm_checkpoint_20261030.md)、[V4 独立审查提示词](phase_r/R22/CHATGPT_REVIEW_PROMPT_V4.md)、[最终精简证据](phase_r/R22/evidence/h0_final_20261031/README.md)。

## 2026-10-31｜R22-S1d：CSI 相位向量来源改变单组 frozen support

- **决策编号与总纲：** `D-2026-10-31-R22-S1D`。用户批准的范围是对 R22 C4 support 失败做局部因果诊断；研究总纲 blob `e637e3769bde7814298ff012d0f42301884e2985` 未修改。
- **执行身份：** 分支 `codex/unknown-system-error-next-stage`，源码 commit `461fbd949ea7c024c28f30e7aea36ae9642a4456`；Native CUDA Release、CUDA 12.4、架构75、`USE_COREX=OFF`；Production binary SHA-256 `c7db0d12f930b297851f1b3b53f3e56de5caf0fa90394595de0b8babf7d7c9de`。只复用 period_0004 / GMTI05 的 SNR20、SNR30 与 paired OFF 输入，没有重跑 Stage2。
- **直接证据：** 7个 Production 分支均返回0；选定组为 beam50/az52/range1065–1197，冻结 `min_support=8`、phase residual `0.35 rad`、133-bin 不变。SNR20自身ON、SNR30自身ON、SNR20向量应用于ON20、OFF向量应用于ON20、SNR20向量应用于ON30、OFF向量应用于ON30、OFF自身拟合的 support 分别为25、7、25、21、11、22、55。逐cell按 Production 输入复数值重算的支持数与 CUDA 记录一致。
- **裁决边界：** SNR30 的 ON 观测在交叉应用 SNR20 ON / paired OFF 相位向量后 support 由7变为11/22，证明该固定组对向量来源敏感。保留集合并不等同，SNR30 native 7格与 OFF向量22格无交集。该反事实不能识别 ON CSI 相位变化的唯一物理原因；paired OFF 是 privileged diagnostic reference。无 Detection、Pd/Pfa、SCNR、目标保持或估计误差比较，`PERFORMANCE_VERIFIED=false`。
- **状态与下一步：** R22 target-present Screening 仍 `GATE_FAILED / INCOMPLETE`；matrix runner 明确拒绝对该状态执行 `--resume`，本次没有续跑矩阵或跳过失败 case。先提交 [S1d 结果](phase_r/R22/support_failure_diagnostic/S1D_PHASE_VECTOR_CROSSOVER_RESULT.md)及紧凑 Production 证据供 ChatGPT 双层审查。审查前不扩大实验、不改 C4、不训练 AI；审查完成后按清理台账删除本地无效周期试跑和已归档可再生输出，并复核项目占用低于20GB。

## 2026-10-31｜R22-S1e：当前 Mode-B 配对 ON/OFF/TO 相位与复数闭合诊断

- **决策编号与总纲：** `D-2026-10-31-R22-S1E`。用户授权在 GPU 并发使用期间运行一个最小配对三角色诊断；不恢复 target-present matrix、不改研究总纲。总纲 blob 仍为 `e637e3769bde7814298ff012d0f42301884e2985`。
- **执行身份：** 实验输入/报告 HEAD `9bb8c351d2d4ca8910a92f079d239a74ccbdddb2`；运行时 runner SHA-256 `f1aa85ec70454932622145bdb0278273e524b612af5fb4378abdcbb990dbae4f`。Production source commit `461fbd949ea7c024c28f30e7aea36ae9642a4456`，Native CUDA Release、`USE_COREX=OFF`，binary SHA-256 `c7db0d12f930b297851f1b3b53f3e56de5cafef0fa90394595de0b8babf7d7c9de`。复用 seed1932001/SNR30/+4 ns 的 GMTI05 配对输入，未重跑 Stage2。
- **执行与评价：** ON/OFF/TO 三个当前 Mode-B Production 分支均 return code 0。C4 support 为7/55/14，Production Gamma 与 selected-cell 独立重算一致；ON 继续 `NOT_EVALUABLE`，冻结门槛8未降低。CFAR geometry 每支有527,280 valid CUT，false counts 因需要 truth join 均不可评价。
- **机制证据：** 相位校正前 `ON−OFF−TO` F1/F2 相对 L2 为0.00937/0.01806；各角色 CSI range-phase 校正后 F2残差为0.78770。TO-only 能量约71%落在目标真值距离格，但 ON/OFF phase vector差在目标格均值1.727 rad，其他格1.833 rad。支持角色相关 CSI phase correction是近端机制；不唯一判定目标污染、全局 CSI 响应或目标/背景交互各自贡献。没有 GMTI task-performance 结论。
- **裁决与下一步：** `IMPLEMENTED=true`、`EXECUTED=true`、`EVALUABLE=true`（仅固定组机制指标）、`PERFORMANCE_VERIFIED=false`。R22 Screening 保持 `GATE_FAILED / INCOMPLETE`，`AI_ENTRY_GATE=FAIL`。唯一下一步是提交 S1e 固定快照供 ChatGPT 双层审查；审查返回前不再启动实验。审查后按 cleanup ledger 清理可再生本地输出，并实测项目占用是否低于20 GB。
- **证据：** [S1e 结果报告](phase_r/R22/support_failure_diagnostic/S1E_MODEB_ROLE_TRIPLET_RESULT.md)、[精简运行证据](phase_r/R22/support_failure_diagnostic/evidence/s1e/)和待发布的固定快照审查提示词。
