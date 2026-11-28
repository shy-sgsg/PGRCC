# R22-V4 冻结准备度：ChatGPT 独立科研审查请求

请把这份请求作为独立审查任务处理，不要仅复述报告结论。

## 固定审查身份

| 项目 | 身份 |
|---|---|
| 仓库 | `https://github.com/shy-sgsg/PGRCC` |
| 分支 | `codex/unknown-system-error-next-stage` |
| **固定审查快照 commit** | **`c00315fdda39c841c10ae5db7baf77aad879eaba`** |
| P0 实验执行源码 commit | `715639c8e9aad7359eb1a5058f9238c0d51f99a1` |
| 离线分析基线 commit | `f84a3607a5b10c4f0a79dacb9d389f60e87abd06` |
| 研究总纲 blob | `e637e3769bde7814298ff012d0f42301884e2985` |

所有结论必须基于上表的固定 commit。若 GitHub 分支 HEAD 已变化，应明确区分该审查快照和远端当前 HEAD，不得混用后续文件或以“最新版本”代替固定身份。

## 强制阅读顺序

**第一步，请先读取固定 commit `c00315fdda39c841c10ae5db7baf77aad879eaba` 中完整的：**

`docs/research/RESEARCH_MASTER_PLAN.md`

先理解 PGRCC 的总体研究主题、目标、核心科学问题、研究内容、总体研究思路与长期技术路线，然后再审查阶段材料。总纲在该快照中的 blob 必须为 `e637e3769bde7814298ff012d0f42301884e2985`。

随后按顺序阅读：

1. `docs/research/RESEARCH_STATUS.md`
2. `docs/research/RESEARCH_DECISION_LOG.md`
3. `docs/research/CHATGPT_REVIEW_PROTOCOL.md`
4. `AGENTS.md` 和 `docs/research/phase_r/README.md`
5. `docs/research/phase_r/R22/README.md`
6. R22-V4 报告 41–46：
   - `41_v4_p0_independent_evidence_reanalysis_20261123.md`
   - `42_v4_physical_mechanism_and_baseline_gap_20261123.md`
   - `43_v4_confirmatory_resource_model_revision_20261123.md`
   - `44_v4_confirmatory_statistical_design_review_20261123.md`
   - `45_v4_confirmatory_freeze_candidate_20261123.md`
   - `46_v4_next_stage_research_decision_20261123.md`
7. `docs/research/phase_r/R22/evidence/v4_confirmatory_freeze_readiness/README.md`
8. 该证据目录中的 `analysis_summary.json`、`archive_validation.json`、`source_identity.json`、P0 配对结果/轨迹/CFAR/目标响应 CSV、V3.3 seed-level CSV、`power_precision_sensitivity.json`、`case_plan_counts.json` 与 `resource_model.json`。
9. 为核对定义和实现，再读固定 commit 中的 V4 契约草案 `31_v4_causal_output_complete_confirmatory_contract_draft_20261113.md`、机器配置 `config/r22_v4_causal_output_complete_confirmatory_draft_20261113.json`，以及报告记录的 R20/R21 evaluator、R7 Production task mapping 代码。

固定快照入口：

`https://github.com/shy-sgsg/PGRCC/tree/c00315fdda39c841c10ae5db7baf77aad879eaba`

## 必须说明的原始证据限制

完整 P0 ZIP 没有上传 GitHub，也没有附在本请求中。它仅在执行机器的本地路径：

`outputs/r22_v4_p0_seed1937003_20261123/archives/engineering/engineering__seed_1937003__vr_2d5__snr_20__delay_4ns__targets_20__periods_10__attempt_001.zip`

SHA-256：`0974e1dfd1467cdd2bdced856e8c673d40c8966a53ab05261d0c5db2d99c4b02`；大小：13,037,254,578 bytes。GitHub 快照含本次离线重算的报告、机器汇总和关键派生表，但不含该完整 ZIP、Stage2 raw IQ 或完整 CSI 数组。

因此，如果你无法通过当前交互实际读取本机 ZIP，必须明确将“原始 IQ 独立重放”和“本地完整归档所有成员独立复核”标为 `NOT_VERIFIED`。不能声称你已读取该 ZIP、复跑 CUDA 或独立验证原始 IQ；可以审查已提交的 hash/manifest、派生 CSV、源码和逻辑，并说明它们能支持到什么程度。

## 本阶段事实摘要（请独立核验）

- P0 是 seed `1937003`、2.5 m/s、20 dB、+4 ns、20 targets、10 periods、beam 50 的单 target-present 工程 case。Stage2 记录连续面积杂波 1,300 packets / 6,508,782 samples。
- 命中为 Current 26/200、A0/无误差 140/200、Known correction 140/200、Fixed D3 142/200、M1 causal prefix 140/200、Huber D3 causal prefix 142/200。单 seed 不能证明总体统计效应。
- A0 与 Known 在 140 个共同成功机会上的位置 RMSE 差约 `0.000063 m`；Current 与 Known 仅有 23 个共同成功机会，不能用该选择子集替代全体机会比较。
- M1 与 Fixed D3 在 135 个共同成功机会上的定位 RMSE 分别约 43.704 m 与 56.204 m，M1 低约 12.500 m；但 M1 命中覆盖率 70%，Fixed D3 为 71%。M1 与 Huber 在 136 个共同机会上的 RMSE 分别约 43.583 m 与 50.828 m，M1 低约 7.245 m；仍为单 seed。
- P0 18 个分支的 payload→TrackManager→Detection 与测量来源审计无违规，6 个 ON 方法的轨迹汇总与归档评价 parity 通过。该 parity 证明记录内部一致性，不证明跨 seed 性能。
- P0 full-band 每分支 10 周期共 5,272,800 次 CUT 测试；合计 CUT/周期/方法不是独立样本。没有独立 H0 truth join，不能报告经验系统 Pfa。该 case 没有有效径向速度估计机会。
- V3.3 历史 12-seed 数据使用全十周期 batch estimator；V4 使用 causal-prefix。名义 80% power 的 2.539 m MDE 和 0.262 pp coverage NI margin 只是方差迁移敏感度，不能当成 V4 功效保证、工程需求或冻结阈值。
- V4 草案为 30 个独立 seed、三个速度、0/+4 ns 配对、180 个 target-present identities 和 60 个独立 H0 identities。位置 MDE、检测覆盖非劣界值、航迹保护界值、H0 false-event 边界尚未确定，当前设计明确为 `DRAFT_NOT_FROZEN`。
- P0 完整归档 13.037 GB；422 个必要成员在 ZIP 内的压缩数据总和为 17.071 MB，但这不是一个另行生成并实测的 compact ZIP。240-case 结果空间与 +25% 余量是规划模型，不是实测承诺。
- 用户明确的 20 GB 是最终审查/清理后项目占用上限；运行中的临时实验数据可以超过。每个原子 case 仍须有实际 free-space、可恢复和验证后清理策略。

## 请完成两层审查

### 第一层：阶段证据与方法正确性

1. 固定 commit、P0 执行 commit、分析基线、总纲 blob 和派生证据身份是否清楚且一致？
2. 面积杂波、配对条件、Current/Known/A0 物理闭合、命中分母和 common-success 口径是否正确？
3. Production Detection、TrackManager payload/当前周期检测映射、目标关联、CFAR valid CUT、目标 ROI 复响应的评价证据是否足够？
4. 配置 Pfa、CUT 数、OFF 角色、未匹配 Track 是否被正确限制解释，没有冒充经验 Pfa 或统计样本？
5. V3.3 历史方差用于 V4 规划敏感度时，限制和推断单位是否讲清？
6. 资源估算是否把“P0 内部压缩成员字节合计”误当成“已测 compact ZIP”，是否合理处理了运行中空间与最终 20 GB 清理上限？
7. 指出任何阻止 V4 冻结的实质性方法问题或缺失证据，并区分 `IMPLEMENTED / EXECUTED / EVALUABLE / PERFORMANCE_VERIFIED`。

### 第二层：总体 GMTI 科研价值与方向

1. 本阶段是否推动了总纲规定的 GMTI 长期研究目标？实际贡献属于物理机制、传统方法边界、任务评价证据还是工程基础？
2. P0 是否支持“该固定场景中 +4 ns 降低任务性能、Known correction 恢复可恢复空间”的有限结论？可外推范围是什么？
3. V4 causal-prefix M1 相对 Fixed D3 / Huber 的单 seed 数值是否足以支持继续做预注册确认，还是应先补充其他证据？
4. 是否有充分理由转向低速目标保护或复杂/非平稳杂波？
5. 是否已经满足 AI 方法进入条件？如果没有，指出尚缺哪些稳定任务级残差证据和强传统基线比较。
6. 当前冻结候选是否合理？下一个最小且有科学价值的步骤是什么？哪些阈值/边界必须由用户或工程需求方决定？

请直接给出阶段裁决（例如 `PASS_WITH_LIMITATIONS / INCOMPLETE / INCONCLUSIVE`）、总体方向建议（`CONTINUE / STOP / PIVOT`）、关键依据、未验证事项和唯一下一步建议。不要把通过测试或单 seed 改善说成论文创新或统计显著性。

## 治理边界

- 不得直接修改 `docs/research/RESEARCH_MASTER_PLAN.md`。若认为总纲需调整，请指出具体章节、证据、替代方案和影响，先提交用户讨论；用户批准前保持总纲不变。
- 不要替用户批准 MDE、非劣界值、航迹/H0 门槛，不要把审查意见写成已冻结实验参数。
- 本次请求不授权 CUDA 尺寸 pilot、240-case 确认矩阵、算法修改、AI 训练或 Router 开发。
- 若固定总纲无法实际读取，应说明这一限制，并将总体科研方向判断标记为 `NOT_VERIFIED`；不能用 Codex 摘要代替总纲原文。
