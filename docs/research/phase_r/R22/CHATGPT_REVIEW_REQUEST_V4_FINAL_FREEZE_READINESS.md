# R22-V4 Phase F：最终冻结准备度独立科研审查请求

**请首先读取本次 Git 提交对应版本的 `docs/research/RESEARCH_MASTER_PLAN.md`，完整理解 PGRCC 的研究主题、总体研究目标、核心科学问题、研究内容、总体研究思路与长期技术路线，然后结合这些内容审查本阶段的代码、实验、数据和科研结论。不能只评价阶段任务是否完成，还必须判断本阶段是否推动了整个 GMTI 研究目标，以及当前研究方向是否需要调整。**

请按下面的固定快照独立审查，不要只复述报告，也不要把阶段工程完成等同于科研假设成立。

## 固定审查身份

| 项目 | 身份 |
|---|---|
| 仓库 | `https://github.com/shy-sgsg/PGRCC` |
| 分支 | `codex/unknown-system-error-next-stage` |
| **固定审查 commit** | **`1a085d253c5ef467429744b1d55a17e465dd22e4`** |
| 本阶段实现、统计及证据快照 | `1a085d253c5ef467429744b1d55a17e465dd22e4` |
| P0 Native CUDA 执行源码 commit | `715639c8e9aad7359eb1a5058f9238c0d51f99a1` |
| 实施基线 commit | `a81bdce906037d6a68c5e15cfe2d1578c8baedf4` |
| 研究总纲 blob | `e637e3769bde7814298ff012d0f42301884e2985` |

固定快照入口：

`https://github.com/shy-sgsg/PGRCC/tree/1a085d253c5ef467429744b1d55a17e465dd22e4`

所有结论应以固定审查 commit 的文件为准。若远端分支 HEAD 更新，单独说明两者差异，不要混用后续内容。P0 执行源码早于本次离线实施快照；不要将本阶段的 CPU 统计/归档工具说成 CUDA 执行代码。

## 强制阅读顺序

1. 在固定 commit 中读取完整 `docs/research/RESEARCH_MASTER_PLAN.md`，并确认 blob 为 `e637e3769bde7814298ff012d0f42301884e2985`。
2. 读取 `docs/research/RESEARCH_STATUS.md`、`docs/research/RESEARCH_DECISION_LOG.md`、`docs/research/CHATGPT_REVIEW_PROTOCOL.md`、`AGENTS.md`、`README.md`、`docs/research/phase_r/README.md` 和 `docs/research/phase_r/R22/README.md`。
3. 阅读本阶段报告：
   - `docs/research/phase_r/R22/47_v4_statistical_hypothesis_and_power_correction.md`
   - `docs/research/phase_r/R22/48_v4_compact_archive_validation.md`
   - `docs/research/phase_r/R22/49_v4_confirmatory_execution_runner_closure.md`
   - `docs/research/phase_r/R22/50_v4_confirmatory_final_freeze_options.md`
   - `docs/research/phase_r/R22/51_v4_scientific_direction_and_next_stage_decision.md`
4. 阅读机器证据导航 `docs/research/phase_r/R22/evidence/v4_final_freeze_readiness/README.md`，再核对相应 JSON、CSV 和 SVG，重点包括：
   - `statistical_hypothesis_comparison.json`、`position_power_corrected.csv`、`position_precision_by_n.csv`；
   - `joint_position_coverage_power.csv`、`bootstrap_coverage_audit.json`、`statistics_generation.json`；
   - `h0_statistical_contract.json`、`missing_opportunity_policy_candidates.json`；
   - `compact_archive_validation.json`、`compact_archive_manifest.json`、`compact_archive_parity.csv`；
   - `compact_schema_by_case_type.json`、`seed_registry_local_audit.json`；
   - `confirmatory_execution_plan.json`、`confirmatory_case_registry.json`、`confirmatory_authorization_template.json`、`confirmatory_checkpoint.json`、`confirmatory_dry_run.json` 和 CPU 测试摘要。
5. 为判断实现是否匹配报告，读取固定 commit 中的统计、schema、归档和 executor 源码及测试：
   - `scripts/phase_r/r22_v4_confirmatory_statistics.py`
   - `scripts/phase_r/generate_r22_v4_final_freeze_statistics.py`
   - `scripts/phase_r/r22_v4_case_schema.py`
   - `scripts/phase_r/r22_v4_confirmatory_executor.py`
   - `scripts/phase_r/r22_v4_p0_compact_archive.py`
   - `scripts/phase_r/build_r22_v4_p0_compact_archive.py`
   - `scripts/phase_r/reanalyze_r22_v4_p0_compact.py`
   - 与之对应的 `tests/test_r22_v4_*.py`，以及变更的 R22 runner/archive 代码。
6. 需要历史背景时，回看报告31和41–46、V3.3 seed-level 数据，以及 V4 P0 既有执行报告。不要把 V3.3 batch 方差当作 V4 causal-prefix 的已验证方差。

如果某文件无法读取，明确标记对应判断为 `NOT_VERIFIED`，不要声称已检查。

## 本阶段做了什么、没有做什么

本阶段实现并执行的是离线统计模拟、P0 compact 归档生成与独立复算、四类输入 schema 的 CPU fixture 验证、240-case 执行器/授权/断点门控和 CPU dry-run。记录的 R12/R21/R22 相关回归为 **254 passed、0 failed、0 skipped**。

本阶段**没有**运行新的 Stage2、Production、Native CUDA、240-case 确认矩阵或 AI 训练。240-case dry-run 返回 `NOT_AUTHORIZED`；设计仍为 `DRAFT_NOT_FROZEN`，MDE、覆盖非劣界值、航迹/H0 接受界值和正式资源证据仍未闭合。CPU 测试只证明软件逻辑，不证明物理模型、目标任务性能或统计显著性。

## P0 与科研证据边界

已完成的 P0 是一个 seed（1937003）、+4 ns、2.5 m/s、20 dB、20 目标、10 周期、beam 50 的 Native CUDA 工程 pilot，`USE_COREX=OFF`。本阶段报告记录的单 seed 检测数为：Current 26/200，A0/Known/M1 各 140/200，Fixed D3/Huber 各 142/200。共同成功位置结果为：M1 对 Fixed D3 低 12.500 m（135 个共同机会）；对 Huber 低 7.245 m（136 个机会）；对 Known 高 17.188 m（134 个机会）。M1 覆盖率70%，Fixed/Huber为71%。这些是固定场景的描述值，不是统计显著性、跨 seed 保证或论文创新证明。

本地唯一完整 P0 ZIP：

`outputs/r22_v4_p0_seed1937003_20261123/archives/engineering/engineering__seed_1937003__vr_2d5__snr_20__delay_4ns__targets_20__periods_10__attempt_001.zip`

SHA-256：`0974e1dfd1467cdd2bdced856e8c673d40c8966a53ab05261d0c5db2d99c4b02`，大小13,037,254,578 bytes。独立生成的 P0 target-present、+4 ns compact ZIP 也只保留在本机 `outputs/`，大小17,603,158 bytes，SHA-256 `71b50b6110e04403b2152f8e292bb3e7df9ae4201fd2790d9d97595e7b12ae5a`。

**上述两个 ZIP 均未上传 GitHub，也未包含在固定 commit 中。** 固定快照只含机器校验结果、manifest、核心表复算 parity 和报告。除非审查时用户另行附上归档，否则你无法独立读取 ZIP 成员、重放原始 IQ 或验证本机文件；请把这些部分标为 `NOT_VERIFIED`，并将归档检查表述为“核对已提交的验证记录”，不能声称亲自打开了本地 ZIP。

报告记录 P0 Stage2 连续面积杂波为1,300 packets / 6,508,782 samples；单 case 并没有独立 H0 truth join，不能从配置 Pfa、CUT 数或目标存在条件计算系统经验 Pfa。周期/CUT/目标机会不是独立 seed。P0 的有限证据提示固定场景中未知+4 ns条件存在明显任务损失且 Known 有恢复；外推范围必须限定在该仿真场景和单 seed。

## 请重点审查的统计与工程主张

### 1. 定位 MDE 与假设是否一致

主差值定义为：

`Δ_s = RMSE_M1,s − RMSE_Fixed,s`，负值有利于 M1；先对每个 seed 三个速度层的共同成功差值等权平均，再以独立 seed 推断。

- **方案 A：** `H0: μΔ ≥ 0` 对 `H1: μΔ < 0`。统计通过要求95%两侧 seed-cluster studentized bootstrap-t 区间上界 `< 0`；另要求点估计改善达到用户批准的 MDE，并通过覆盖/航迹门。点估计达到 MDE 不等于统计证明真实改善超过 MDE。
- **方案 B：** `H0: μΔ ≥ −MDE` 对 `H1: μΔ < −MDE`，同一上界必须 `< −MDE`。这是更强的工程效果量主张。
- 审核区间端点、拒绝域、bootstrap 重抽样单位、临界概率、Monte Carlo 误差及其对确认性主张是否匹配。正式候选与 t-reference 规划网格必须区分；27,000行网格不是精确 bootstrap 功效。
- 历史 V3.3 的 M1−Fixed seed SD 约4.797 m来自全周期 batch estimator；V4 改为 causal-prefix。检查其迁移只作为敏感性假设，不应被当作 V4 方差事实。

报告中的模型校验包括：30 seed 下真实改善3 m、MDE3 m时，A 的位置统计门约90.76%，点估计工程门约50.70%，覆盖非劣约99.90%，联合约50.64%；B 在真实改善恰3 m（原假设边界）约2.42%，真实改善6 m、MDE3 m时联合约91.00%。请核查实现、假设和数值，并指出这些概率不能说明真实算法一定会通过。

### 2. 检测覆盖、航迹与联合成功

检查共同成功机会的定位 RMSE、每 seed 三速度等权、定位/覆盖联合概率和相关性模拟。覆盖非劣必须在独立 seed 层定义，且算法漏检不能从覆盖分母消失。评估“任何必需速度层没有共同成功机会时该 seed 主定位值不可评价”的规则：不删 seed、不替换 seed、不将缺失位置当作0；其对覆盖、机会分母与整体解释是否透明。

另外检查正确确认航迹/航迹保护是否应作为硬门及其预注册定义；PIPE payload 必须追溯到本周期已确认且实际关联的 detection。独立 H0 的候选主单位为 seed-any-event，同时报告 period-event fraction 和唯一 false-confirmed track IDs。0/30 H0 seed-any-event 对应单侧95%上界约9.503%，只对应 beam50/十周期任务，不是全机或全扫描系统 Pfa。H0 接受界值和 false-confirmed track 界值仍待用户/需求方指定。

### 3. compact 证据与可复现性

核查 compact allowlist、ZIP CRC/SHA、430个成员、11个核心表的逐字节复算 parity 及 source 未被重写的审计链。注意 compact ZIP 不在 GitHub；机器验证文件虽在提交中，但不等于 ChatGPT 已独立打开它。四种 case schema 中的 CPU synthetic fixtures 通过，不代表四种完整输出归档大小已实测：目前只有一个 target-present +4 ns P0 compact 有实测容量。

检查目录磁盘记录：P0 原 ZIP 保留；确认重复旧 compact 副本释放约35.2 MB；项目目录约18.38 GB，小于最终20 GB上限。矩阵资源 proxy 约2.46–5.28 GB，叠加当前目录预计超限；用户允许运行过程暂时超过20 GB，最终必须在结果汇总、审查后按清理账本删除可再生中间数据。是否把这一策略、临时峰值和逐 case 清理闸门写清楚？

### 4. 执行器、授权与矩阵公平性

确认240-case展开为180 target-present +60独立H0、120 batches、1,470注册科学槽位、2,520次Production调用、1,200–1,440次Stage2调用的关系；核查方法/角色失败语义、断点续跑身份绑定、归档验证和默认拒绝授权是否闭合。

确认 `execution_authorized=false`、`cuda_authorized=false`、`ai_training=false`、阈值未填、`NOT_AUTHORIZED` dry-run 状态。资源门仍缺四种 schema 的完整输出尺寸/临时峰值，seed registry 仅审计了本地材料，外部/远端库存没有完成。不能从本次 CPU dry-run 推断 Native CUDA 可执行、生产矩阵成功或性能已验证。

## 请完成两层独立审查

### 第一层：阶段正确性与证据质量

1. 统计假设、区间、MDE解释、联合门和缺失规则是否数学一致、可执行且可预注册？
2. 关键数值、文件身份、seed/场景单位、H0分母与方法间公平比较是否正确？
3. compact独立重算、schema fixture、executor授权/恢复与磁盘资源证据各自能支持到什么结论？
4. 报告是否严格区分 `IMPLEMENTED / EXECUTED / EVALUABLE / PERFORMANCE_VERIFIED`？
5. 指出阻止设计冻结的关键缺口，区分必须修复项与用户/工程需求方必须决策项。

### 第二层：总体 GMTI 研究价值与方向

1. Phase F 是否推动了总纲的物理模型驱动 GMTI 目标？它贡献的是评价/统计基础、传统方法边界证据还是算法收益证据？
2. 单 seed P0 是否足以支持有限的未知误差损失/已知校正可恢复空间结论？外推边界是什么？
3. 当前确认性假设是否值得保留？选择 A 或 B 将分别支持什么科研表述；是否有更合适的最小方案？
4. 现有证据是否足以把低速目标保护或复杂/非平稳杂波提升为主线？
5. 是否已满足 AI 进入条件？请检查是否已证明跨独立场景稳定的任务级残差、强传统方法不足、确定性改进上限和部署信息可得性。
6. 给出总体方向 `CONTINUE / STOP / PIVOT` 及一项唯一下一步建议；明确用户需决定的 MDE、检测覆盖非劣界值、航迹/H0边界、seed数量和资源授权。

## 输出格式与治理边界

请给出阶段裁决（例如 `PASS_WITH_LIMITATIONS / INCOMPLETE / INCONCLUSIVE`）、总体研究方向、关键数值复核、限制与 `NOT_VERIFIED` 项、是否建议冻结、唯一下一步任务。区分“设计/软件门控正确”与“GMTI性能经过确认”。不要把单 seed 改善、测试通过或合成蒙特卡洛结果写成统计显著的实际算法收益。

不得修改 `docs/research/RESEARCH_MASTER_PLAN.md`。如建议修改总纲，列出具体章节、证据、替代表述和对既有研究的影响，交给用户讨论；未经用户明确批准不得编辑。此快照任务不授权新 Stage2、Production、CUDA确认矩阵、算法调参或AI训练。
