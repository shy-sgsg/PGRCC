# PGRCC 研究决策日志

> 本日志只追加，不回写旧决定。以下条目根据各阶段原报告整理；它们记录“当时依据当时证据作出的决定”，不是补造新实验。当前有效的研究计划见 [RESEARCH_MASTER_PLAN.md](RESEARCH_MASTER_PLAN.md)。每次新增方向决定，须记录决策编号、日期、代码/证据身份、原假设、问题、关键发现、`CONTINUE / STOP / PIVOT`、对总体目标与研究计划的影响、是否需要更新总纲、停止条件和下一项优先工作。新决定可以取代当前方向，但不删除旧条目。

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
