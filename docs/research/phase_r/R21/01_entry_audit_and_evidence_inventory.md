# R21 入口审计与证据清单

## 仓库与总纲身份

| 项目 | 入口状态 |
|---|---|
| Repository | `https://github.com/shy-sgsg/PGRCC` |
| Branch | `codex/unknown-system-error-next-stage` |
| Entry HEAD | `646088b37431703b20a59d1acc424b57ffe011f2` |
| Research Master Plan | `docs/research/RESEARCH_MASTER_PLAN.md` |
| Entry Master Plan blob | `e637e3769bde7814298ff012d0f42301884e2985`（须在交付前再次核对） |
| Master Plan 修改 | 本阶段禁止 |
| Worktree size at entry audit | `2.1 GB`（`du -sh .`，低于 20 GB；设备文件系统可用空间约 71 GB） |

工作树有大量先前存在的未跟踪文件和实验产物。R21 只在审查后新增自身文件，不批量清理或提交其它产物；如清理空间，必须先列目标、确认已归档的高价值证据和清理理由，并在清理审计中记录。

## 必读科研治理与方向证据

| 文件 | 用途与 R21 要点 |
|---|---|
| `docs/research/RESEARCH_MASTER_PLAN.md` | 长期 GMTI 目标和研究路线；本次只读。 |
| `docs/research/RESEARCH_STATUS.md` | 动态当前状态；R20 是最近阶段，R21 由用户当前指令启动。 |
| `docs/research/RESEARCH_DECISION_LOG.md` | R20 结论为有限 seed 下 +4 ns 任务损失、static delay 仍是 baseline，暂无 AI 入口；R21 用户请求限定继续复验。 |
| `docs/research/CHATGPT_REVIEW_PROTOCOL.md` | ChatGPT 先读总纲，再进行阶段正确性与总体科研价值双层审查。 |
| `docs/research/CODEX_TASK_PROTOCOL.md` | 科研任务先与长期目标对齐，并设证据与停止条件。 |
| `AGENTS.md` / `README.md` / `docs/research/phase_r/README.md` | 工程、Git、CUDA、空间和研究阶段记录规范。 |

以上当前治理文件已在任务入口阅读。没有修改研究总纲、研究状态、决策日志或其他科研方向文件。

## 历史审计入口

### R20 v2

- 实现：`scripts/phase_r/r20_task_evaluator.py`、`r20_historical_evidence_adapter.py`、`r20_harmonized_task_review.py`。
- 测试：`tests/test_r20_task_evaluator.py`、`test_r20_harmonized_task_review.py`、`test_r20_historical_evidence_adapter.py`。
- 历史结果：`docs/research/phase_r/R20/evidence/results/`、`evidence/manifests/r20_input_identity_manifest.json`、`evidence/audits/r20_offline_validation.log`。
- 紧凑输入：`docs/research/phase_r/R20/evidence/inputs/`，R20 记录 566 个文件、约 30 MB；不含 raw IQ。
- 固定兼容门：R19 84 个 R18 ON opportunity 记录的 hit/opportunity/miss/fraction 不变；R19/R20 历史 evaluator 与 CSV 保持只读。

### 已证实的 R20 evaluator 问题

1. `summarize_track_lineage_v2()` 将任何含未匹配 confirmed payload 的 track ID 计入 `false_confirmed_track_count`，不能区分 never-target-matched ID 与同一 ID 也曾匹配目标的 mixed lineage。R12 seed404 M1 的原始 TrackManager 行需由 R21 重放，报告 old-vs-corrected ID 集合和匹配周期；不推断原因。
2. `expected_doppler_row_from_physics()` 使用有符号 spacing 做 row conversion，但 `_doppler_audit_row()` 重新计算轴 PRF closure 时采用 `N * spacing - PRF`，负向轴会错误失败。R20 `_evaluate_bundle()` 主要检查 axis 是否存在，没有要求每个有效 truth opportunity 的独立物理映射审计通过后才纳入任务分母。
3. `_truth_validity()` 只要求 truth `range_bin` 是有限数值，未拒绝小数、负数、超界或不符合真实场景距离 bin 轴的值，也没有禁止在真值字段上强制修正。
4. `_metric_evaluability_rows()` 的 post-confirmation metrics 在有 TrackManager 文件但没有 target-confirmed output 时可能继承小写 `evaluable`，而指标值为空。R20 历史 CSV 已实查出 6 行 R8/R12 B `status=evaluable` 且 value 为空（`post_confirmation_match_rate`、`target_confirmation_delay_periods`）。

上述问题须在新增 R21 v3 中修复；不得回写 v2 或历史 CSV。R20 的 `r20_metric_evaluability.csv` 本身已经证明第 4 项；其余问题在 WP1 以测试和完整历史重放复核。

## 新 seed / 实验输入待核清单

| 项目 | 入口状态 | 进入 Production 前的要求 |
|---|---|---|
| Seed 1001 | 当前文件路径、文本记录和本地 Git 提交差异中均未发现既往使用记录 | 以 `1001/1002` 的 seed 专用路径及 `seed_1001`、`seed1001`、`--seed 1001` 等字段形式扫描 `docs/configs/scripts/tests/outputs`，并对本地 `git --all` 历史做 pickaxe 搜索；seed1001/1002 只在 R21 计划中作为候选出现。 |
| Seed 1002 | 当前文件路径、文本记录和本地 Git 提交差异中均未发现既往使用记录 | 同上。该结论限于当前仓库可访问文件和本地 Git refs，不推断未连接的外部副本。 |
| R12 物理与配置 | 历史基线可从 `docs/research/phase_r/evidence/r12_physics_matched_delay_20261024/` 与 compact bundle 恢复 | 固定 6.7 m/s、30 dB、+4 ns、3 periods、连续面积 clutter；比较语义配置、Stage2 geometry、输入/F1/F2 身份。 |
| A0 / B / Known / D3 / M1 | R12 历史 runner 有对应生成逻辑 | 每个新 seed 有成对 OFF/ON，Known 真值只进入 evaluator-only correction；处理算子可比。 |
| Huber D3 | 满足进入 R21 新 seed pilot 的输入边界；新 seed 的实际估计与 Production 执行仍待验证 | R12 `run_manifest.json` 与 `R12_blind_validation.md` 记录 D3 `3.5880556759918814 ns`、输入角色 `A1_Current_unknown_error_ON_raw_C1_C2_only`、同一批 390 pulse/2364 支撑点和 raw/XML SHA。新 pilot 从 B ON C1/C2 与该分支报告几何重新计算；真实 delay 只在 evaluator 使用。历史 raw IQ 当前已不在工作区，历史数值不可重新计算。R21 D3 如不能经同一 `rewrite_float32_protocol_delay` 校正器和 Production 路径，则为 `NOT_AVAILABLE`。 |
| Runtime / CSI / CFAR | 当前没有 R21 新运行证据 | 真实本地 Native CUDA Production；保留实际 XML、runtime identity、逐 beam/stage、CSI taps、CFAR geometry 和完整日志。 |

## 历史研究结论边界

- R8 seed202/303：每 seed A0/B/Known 为 2/0/2 与 3/0/3 命中；seed101 因缺 Production Doppler axis 不可评价。R8 raw IQ 不在 compact bundle。
- R12 seed404：A0/B/Known/R9 fixed D3/M1 分别 2/0/2/3/3 命中。Known closure 数来自保存 manifest，不是 R20 raw-IQ 重算。
- R12 Known common-success 参数 RMSE 与 D3/M1 的差距、delay estimates 和 closure 值仅作本次实验设计的历史参照；不得回写历史结论。
- R20 的 statistical Pfa `NOT_EVALUABLE`；空间相关 CUT observed fraction 不能当作 Pfa。
- 每个 seed 三个 target periods，不是三个独立 seed；最多两个新 seed 只支持有限复验，不支持统计显著 Pd/Pfa 声明。

## 清理与磁盘记录

- 初始工作树占用约 `2.1 GB`，满足 `<20 GB`。
- 本次入口审计未删除文件；R20/R19 历史结果、失败案例和 manifest 保留。
- 后续只清理可证明为新产生、可重建且无唯一审计信息的临时产物；原始输入、失败现场及尚未汇总的证据不得删除。

## R21 v3 历史回放（WP1）

- 只读重放产物保存在 `evidence/historical_replay/`；输入为 R18/R8/R12 当前保留的紧凑 Production 证据，不运行 Stage2/CUDA。
- 210/210 条分支的 Scene 与 Production 距离 crop 轴一致；R18/R12/R8 seed202/303 的 570 个物理 Doppler 机会通过逐机会闭合。R8 seed101 的 18 个机会因缺保存的 Production Doppler 轴而继续排除。
- R19 冻结的 336 个任务指标与 R19、R20 完全一致；R8/R12 对照 84 项中 66 项数值不变，18 项两版均不可评估，无数值变化和可评估性反转。
- 命中、漏检和命中率仅在至少一个物理验证机会存在时提供数值。seed101 无有效机会时三项均为空且为 `NOT_EVALUABLE_NO_VALID_PHYSICS_VERIFIED_OPPORTUNITIES`。
- 所有 724 条指标状态记录中，`EVALUABLE` 或 `EVALUABLE_WITH_EXCLUDED_OPPORTUNITIES` 且值为空的条数为 0。
