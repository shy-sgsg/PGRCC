# R22 导航：多目标 Production pilot 与科研方向审查

> **2026-11-28 当前状态｜Phase H size pilot、资源闭环与冻结准备完成：** 三类缺失schema Native CUDA size pilot已逐case执行；V4统计参数、seed范围和240-case/120-batch设计已冻结。最终CPU干跑registry SHA为`1546ed2c90836c6cf163547080352f1bd16b93c18c1e28f0ff6011f7347cd57c`，状态`NOT_AUTHORIZED`；未启动正式矩阵、Stage2、Production或AI。执行器可逐case验证分片、gzip-9回执和compact，归档后清理raw，并从checkpoint续跑；首次授权执行无dry-run checkpoint冲突，分析器兼容receipt+compact，清理中断可恢复，保留固定10 GiB reserve并在case后核验实际项目占用；最终保留有20,000,000,000-byte项目上限，峰值临时空间独立门控。目标present +4 ns receipt预算是由P0 manifest产生的模型。四份旧CPU dry-run已按台账清理，P0和pilot高价值证据保留。详细阅读[报告57](57_v4_phase_h_size_pilots_and_matrix_freeze_20261128.md)、[机器资源预算](evidence/v4_phase_h/resource_budget_20261128.json)、[最终冻结dry-run](evidence/v4_phase_h/preflight_20261128_executor_reviewed/confirmatory_dry_run.json)、[ChatGPT独立审查请求](CHATGPT_REVIEW_REQUEST_V4_PHASE_H_FREEZE_READINESS_20261128.md)。正式矩阵仍须用户单独授权，AI仍未获准。

> **2026-11-26 历史快照｜Phase H 持续执行：** 统计参数包已按用户批准固定，seed registry 已完成本地可达范围核验并哈希；确认性设计 240-case/120-batch CPU dry-run为`NOT_AUTHORIZED`。三类 Native CUDA size pilot已获批准，逐 case磁盘门已补入执行器；项目根占用18,632,269,824 bytes，ext4可用17,161,711,616 bytes、`/tmp`可用7,971,377,152 bytes，均未达到单文件系统40,558,522,368-byte保守代理，故**在首个 case 前暂停，未启动 GPU/Stage2/Production**。有足够本地可写空间后可按原 manifest 续跑。参见[报告56](56_v4_phase_h_continuous_execution_20261126.md)、[历史归档兼容性审计](evidence/v4_phase_h/historical_archive_compatibility_20261126.json)、[机器资源预检](evidence/v4_phase_h/resource_preflight_20261126.json)和[240-case dry-run](evidence/v4_phase_h/executor_dry_run_20261126.json)。

> **2026-11-26 Phase H Task 3.1/Task 4 空间预检（报告55形成时的历史快照）：** 已比对本机 P0 `r22_v4_output_complete` ZIP 与17.6 MB独立compact；完整ZIP为13,037,254,578 bytes，compact不能作完整矩阵预算。仅清理旧 V2 failure case 中520张manifest标记可再生的per-case PNG，释放77,737,984 allocated bytes；失败ZIP、unique truth-lineage和完整case manifest保留。项目根现为18,631,753,728 bytes；外部94.1 GB卷只读。其他三类schema未测，size pilot等待授权和可写工作位置，原始ZIP迁移等待用户指定可写目标。见[报告55](55_v4_phase_h_resource_and_execution_report.md)、[机器证据](evidence/v4_phase_h/archive_profile_comparison.json)和[清理台账](evidence/cleanup_ledger.md)。

> **2026-11-26 R22-V4 Phase H Task 1（报告54形成时的CPU/统计契约历史快照）：** H0 主端点已统一为独立 seed 十周期 any-event，缺失航迹输出不记零；离散覆盖区间完成六场景 synthetic 审查，`paired_t` 仅是待批准候选；新增基于 SHA/CRC 已验证归档的 CPU 矩阵分析入口和完整/缺失数据夹具。V4仍`DRAFT_NOT_FROZEN`，没有运行真实矩阵、Stage2、CUDA、Production、size pilot或AI。后续用户批准的参数、seed核验和size pilot状态以报告56及动态研究状态为准。详见[54号Task 1报告](54_v4_phase_h_final_contract_and_freeze.md)与[Phase H机器验证摘要](evidence/v4_phase_h/task1_cpu_validation.json)。研究总纲blob保持不变。

> **2026-11-24 R22-V4 Phase G 统计契约闭合与最终决策准备：** 修正 bootstrap-t 上端点说明，零方差与非有限重抽样 fail-closed；支持下限未批准时不纳入正式推断；补齐 H0 seed-any-event 和 false-confirmed-track-event 的 seed级 exact 上界，并把统计规则绑定进默认拒绝的执行器。V3.3历史支持切片下限1/5/10均36/36通过，无法区分，未替用户选值。只读 GitHub refs 和既有本地seed扫描未见候选冲突，但外部registry仍未验证。项目根约18.71 GB allocated，低于20 GB最终上限；仅 target-present +4 ns schema有完整尺寸测量。没有运行Stage2/CUDA/Production/确认矩阵/AI，没有删除或移动P0，也未使用Drive。V4仍`DRAFT_NOT_FROZEN`、矩阵`NOT_AUTHORIZED`、`PERFORMANCE_VERIFIED=false`。阅读[52统计闭合报告](52_v4_phase_g_statistical_contract_closure_20261124.md)、[53方向与决策](53_v4_phase_g_research_direction_and_final_decision_20261124.md)、[Phase G机器证据](evidence/v4_phase_g/README.md)和[Phase G ChatGPT独立审查请求](CHATGPT_REVIEW_REQUEST_V4_PHASE_G_FINAL_DECISION.md)。固定审查 commit 写在提示词中。

> **2026-11-24 R22-V4 Phase F 冻结准备与独立审查入口：** 修正定位MDE假设与判据，重算 seed-cluster bootstrap-t 与定位/覆盖联合通过概率，闭合缺失机会、独立H0、四类schema及默认拒绝执行器；从真实P0归档生成并复核17,603,158-byte compact，11张核心表重算一致。R12/R21/R22 CPU回归254项通过。240-case dry-run为`NOT_AUTHORIZED`，本阶段未运行Stage2、Production、CUDA矩阵或AI。设计仍`DRAFT_NOT_FROZEN`，待用户/需求方决定MDE、检测非劣、航迹/H0界值和seed数量。当前项目目录实测约18.38 GB，低于最终20 GB限额；原始P0 ZIP与compact ZIP保留在本机`outputs/`，没有上传GitHub。报告见[47统计修正](47_v4_statistical_hypothesis_and_power_correction.md)、[48 compact验证](48_v4_compact_archive_validation.md)、[49执行器闭合](49_v4_confirmatory_execution_runner_closure.md)、[50冻结选项](50_v4_confirmatory_final_freeze_options.md)、[51方向裁决](51_v4_scientific_direction_and_next_stage_decision.md)，机器证据见[evidence](evidence/v4_final_freeze_readiness/README.md)。ChatGPT审查固定到`1a085d253c5ef467429744b1d55a17e465dd22e4`，提示词见[Phase F 独立审查请求](CHATGPT_REVIEW_REQUEST_V4_FINAL_FREEZE_READINESS.md)。

> **2026-11-23 V4 P0 离线独立重分析完成：** 不重跑 GPU，重算单 seed P0 任务、轨迹 lineage、CFAR、目标响应、V3.3 统计敏感度和 240-case 资源模型。Current 26/200、Known/M1 140/200、Fixed/Huber 142/200；只作单场景描述证据。V4 仍 `DRAFT_NOT_FROZEN`，AI 入口未通过。阅读[41–46 号报告](41_v4_p0_independent_evidence_reanalysis_20261123.md)、[证据导航](evidence/v4_confirmatory_freeze_readiness/README.md)及[固定快照审查请求](CHATGPT_REVIEW_REQUEST_V4_FREEZE_READINESS_20261123.md)。完整 13.037 GB P0 ZIP 未上传 GitHub，保留到审查结束。

> **2026-11-23 R22-V4 P0 Native CUDA pilot 已完成：** seed1937003、+4 ns、20 dB、20目标、10周期、beam50，Stage2面积杂波有效，18/18 Production 分支通过。Production/CSI/因果估计/检测航迹证据与 12.14 GiB compact ZIP 均已验证；archive-pending 断点恢复只复核归档、没有重跑 CUDA。单 seed 显示 Current 26/200 命中，Known 140/200，M1 140/200，Huber 142/200；只作工程可行性和描述性观察，PERFORMANCE_VERIFIED=false。确认准备报告补充了基于 V3.3 历史 seed 方差的 MDE/覆盖率非劣功效敏感性，不替用户决定阈值。V4确认矩阵仍未冻结、未启动，AI未训练；项目目录实测18.68 GB，低于20 GB上限。详见[预检与授权](37_v4_p0_preflight_and_authorization_20261123.md)、[执行报告](38_v4_p0_native_cuda_execution_20261123.md)、[科研评估](39_v4_p0_preliminary_research_assessment_20261123.md)、[冻结准备度](40_v4_confirmatory_freeze_readiness_20261123.md)、[紧凑机器证据](evidence/v4_native_cuda_pilot_20261123/README.md)与[ChatGPT独立审查请求](CHATGPT_REVIEW_REQUEST_V4_P0_PILOT_20261123.md)。研究总纲blob保持不变。

> **2026-11-23 V4 冻结前预检补充：** 本机 R22 seed 审计确认 V3.2 实际 Screening 使用 1932001–1932012；V3.2 预留 Confirmatory 1933001–1933030 未用于该 Screening。尺寸 pilot 候选 seed 为1937001，仍待全项目 registry/外部归档最终复核。主要 MDE 与检测覆盖率非劣界值为空，当前卷和`/tmp`可用空间均低于8.25 GiB输入下界加输出余量，故最终裁决`BLOCKED`，未运行CUDA。单 case完整输出、停止门和命令见[预检审计与 pilot 提案](36_v4_preflight_audit_and_cuda_pilot_proposal_20261123.md)、[seed审计](evidence/v4_integration_closure_20261123/seed_registry_audit.json)与[尺寸pilot草案配置](config/r22_v4_size_pilot_draft_20261123.json)。

> **2026-11-23 V4 CPU 集成闭合：** 110项受影响 CPU 回归通过；静态展开得到240个 case、1,470个注册槽位、2,520次计划 Production 调用。所在卷可用空间约8.38 GiB，单个+4 ns case输入/校正数据下界约8.25 GiB，仅余约0.13 GiB，尚未计Production/CSI输出和归档并存空间，故未启动 Stage2、CUDA、pilot或确认矩阵。V4仍`DRAFT_NOT_FROZEN`，总纲blob不变，`PERFORMANCE_VERIFIED=false`。详见[集成闭合](33_v4_integration_closure_20261123.md)、[CPU验证](34_v4_cpu_integration_validation_20261123.md)、[pilot readiness](35_v4_cuda_pilot_readiness_20261123.md)、[机器证据](evidence/v4_integration_closure_20261123/README.md)和[ChatGPT审查请求](CHATGPT_REVIEW_REQUEST_V4_INTEGRATION_CLOSURE_20261123.md)。

> **2026-11-22 V4 冻结前工程闭合：** 源码提交 `bbb609cc31c659c5cb7963519784a2d856b701db` 实现周期因果前缀估计、逐目标/周期/方法/角色 `CSI_AFTER` 复数 ROI、完整输出归档及 fail-closed 续跑验证。67项定向 CPU/模拟测试通过；本阶段没有运行 Stage2、CUDA、Production 或实验矩阵，不能据此声称任务性能已验证。V4 仍 `DRAFT_NOT_FROZEN`，MDE/覆盖率非劣界值、独立代码审查、seed身份审计和单独授权的尺寸 pilot 未完成。总纲 blob 不变，未删除历史证据。详见[冻结前工程闭合报告](32_v4_pre_freeze_technical_closure_20261122.md)、[机器可读设计](config/r22_v4_causal_output_complete_confirmatory_draft_20261113.json)和[ChatGPT独立审查请求](CHATGPT_REVIEW_REQUEST_V4_PREFREEZE_IMPLEMENTATION_20261122.md)。

> **2026-11-14 R22-V4 契约草案修订：** 在周期因果 M1、180个 target-present + 60个独立 H0 case identity 和完整 Detection/TrackManager/target-only complex 输出的基础上，新增同输入/同周期前缀的 Huber D3 次要对照；Fixed D3 固定值 `3.4099650318669545 ns` 与 R9/R3来源已明确。源码审计快照 `1063d67` 显示现有 M1/Huber 为 full-case batch，Production runner 对全周期应用 case 级校正标量；V4前缀估计与逐周期校正尚未实现。追加输出审计确认 Production 可生成全期 Detection/TrackManager debug，但默认 compact archive 会删除 TrackManager 目录和 GMTI09 之前的 ON Detection；现有 `_target_transfer` 只保留 CSI 5×5 ROI 汇总，缺少逐格复数值和完整 ROI/注入真值映射。冻结前须实现 V4 专用 archive allowlist 与 target-response schema，并通过完整输出 pilot 测量体量。Case identity 对应约1,470条方法×角色 Production branches，须纳入获授权后的尺寸 pilot。MDE、覆盖率非劣门、因果/输出实现、资源 pilot 与 CUDA 授权未闭合，仍为 `DRAFT_NOT_FROZEN`。项目约5.3 GiB；本轮未删数据，因为较大 R22/V3.2/V3.3 证据仍支持当前审计。见[契约草案](31_v4_causal_output_complete_confirmatory_contract_draft_20261113.md)、[机器设计](config/r22_v4_causal_output_complete_confirmatory_draft_20261113.json)和[ChatGPT独立审查请求](CHATGPT_REVIEW_REQUEST_V4_CONTRACT_DRAFT_20261113.md)。不改 V3.2/V3.3历史证据，不授权CUDA。

> **2026-11-10 V3.3 离线残差与 Production 输出审计：** 144 个 V3.2 `+4 ns` compact case 归档身份均匹配；Current命中率11.694%，Known69.729%，target-present Fixed D3/Huber/M1约69.71%–69.77%。全十周期位置共同成功19,682/28,800，576/576 position evaluator parity通过。新增13方法×144 case十周期Production Detection/cluster计数，1,872/1,872输出完整；1,841个method-case真值关联可评价，31个仅评价Production计数。未关联检测行不是false cluster/Pfa，完整非目标cluster真值关联不可评价。`PERFORMANCE_VERIFIED=false`，AI entry gate `FAIL`。建议先冻结输出完整的确认性契约；本次没有启动CUDA。见[最终报告](V3_3_residual_task_gap_audit/FINAL_RESEARCH_REPORT.md)、[逐方法输出与关联覆盖分析](V3_3_residual_task_gap_audit/03_detection_and_opportunity_overlap.md)、[紧凑证据导航](V3_3_residual_task_gap_audit/README.md)与[固定快照ChatGPT审查请求](V3_3_residual_task_gap_audit/CHATGPT_REVIEW_REQUEST.md)。

> **2026-11-09 V3.2 全量 Screening 终态：** Native CUDA 完成 288/288 个 case、4,320/4,320 个适用 ON 方法单元分类；255 个 `PASSED`、33 个 `PASSED_WITH_METHOD_GAPS`，49 个方法单元因 `insufficient_robust_support` 不可评价。288 个最新 ZIP 已通过 SHA/CRC 校验，raw 已按 ledger 清理。目标检测与 Known/estimated recovery 只作 Screening 描述，`PERFORMANCE_VERIFIED=false`、本矩阵无独立 H0/Pfa。详见[执行与结果报告](27_v3_2_screening_execution_report_20261109.md)、[方向评估](28_v3_2_preliminary_direction_assessment_20261109.md)、[ChatGPT 审查提示词](CHATGPT_REVIEW_PROMPT_V3_2_SCREENING_20261109.md)和[精简证据包](evidence/v3_2_screening_20261106/README.md)。完整 288-case ZIP 留在本机 outputs，未上传 GitHub。

> **2026-11-06 V3 早停/重放状态：** V3 在第4个 case 的 `P2 / Mode-A / ON` 分支因 `raw_align`/beam quality gate 失败而以 `UNKNOWN_STOP` 终止，历史矩阵保持终态。随后同场景诊断重放在约1215 MiB空闲显存下9/9分支通过，包含该 P2 ON 分支；这使设备资源状态成为合理解释，但根因仍未证明。其后另建V3.1并尝试完整 Screening，最新结果见上条。V3早停详见[审计报告](21_v3_screening_early_stop_20261106.md)，原V3契约见[历史V3契约](22_v3_screening_contract_20261105.md)。

> **2026-11-05 独立审查更新：** S1e 固定快照审查裁决为 `PASS_WITH_LIMITATIONS`；角色相关 CSI phase-vector 是该组 C4 support 变化的近端机制，但目标污染未证明，任务收益未验证。V2 target-present screening 仍为 `GATE_FAILED / INCOMPLETE`。18个终态 case 的只读复核保留269/270个适用ON方法单元，C4 Mode-B的1个缺口不再抹掉其他方法的归档行；全方法 CUT/unique-hit 汇总仍不可评价，不恢复排名。见[离线复核](19_v2_screening_method_level_offline_dry_run_20261105.md)和[V3契约草案](20_v3_method_level_evaluability_contract_draft_20261105.md)。V3尚未冻结或实现，本次没有启动CUDA。

> **2026-11-05 V3 设计更新：** 完成历史 Baseline、Stage2 面积几何、Native CUDA Production 与 V2 compact archive 的交叉审计。V2 仍只读保留；新 V3 使用独立设计和输出根，对288个 screening case 全量重跑，旧V2归档不并入V3覆盖率。V3只允许已确认的单方法 reference/estimator 缺口在公共输入门通过时隔离，其余未知或共享故障仍停机。见[历史 Baseline/仿真/Production 审计](21_historical_baseline_simulator_production_audit_20261105.md)和[V3 Screening 契约](22_v3_screening_contract_20261105.md)。

> **最新状态（2026-10-31）：** 冻结的 R22 H0 矩阵已按 seed-level case retry 恢复并完成：30 个独立 seed、60/60 个唯一 0/+4 ns case，分析状态 `COMPLETE_EVALUABLE`。最终结果、配对杂波/噪声审计和精简审查证据见[最终报告](18_h0_false_alarm_checkpoint_20261030.md)及[最终证据包](evidence/h0_final_20261031/)；可直接交给 ChatGPT 的固定审查提示词见 [V4](CHATGPT_REVIEW_PROMPT_V4.md)。

> **仍有效的 Screening 门控：** R22 target-present V2 screening 仍为 `GATE_FAILED / INCOMPLETE`。seed1932001、2.5 m/s、30 dB、+4 ns 的 C4 Mode-B ON reference support=7，小于冻结的8。S1c 证明选定支持数对 CSI phase vector 敏感；补充审计确认目标响应与失败组/行重合，但目标真值距离单元只占 SNR20-only 支持格的3/24，具体相位变化贡献和任务收益仍未唯一确定。H0 无目标矩阵不解除该门控。详见 [S1c 结果](support_failure_diagnostic/S1C_SHARED_OFF_PHASE_RESULT.md)、[目标重合交叉表](support_failure_diagnostic/evidence/s1c/v2/s1c_target_overlap_audit.csv)与[固定快照审查提示词](support_failure_diagnostic/CHATGPT_REVIEW_PROMPT_S1C_20261031.md)。

> **S1e 当前 Mode-B 三角色诊断（2026-10-31）：** 同一 Native CUDA binary 对配对 ON/OFF/TO 输入各执行1个 GMTI05 Production 分支，support 为7/55/14；相位校正前 `ON−OFF−TO` F1/F2 复数闭合相对 L2 为0.00937/0.01806，逐角色 CSI range-phase 校正后 F2 闭合残差为0.78770。TO能量主要集中在目标真值距离格，但ON/OFF CSI phase差在目标格没有更大。该结果加强“角色相关 CSI 相位校正是 support 变化近端机制”的证据，不唯一证明目标污染，也不代表任务收益。原支持门仍失败；详情见 [S1e 结果](support_failure_diagnostic/S1E_MODEB_ROLE_TRIPLET_RESULT.md)、[紧凑证据](support_failure_diagnostic/evidence/s1e/)和[固定 ChatGPT 独立审查提示词](support_failure_diagnostic/CHATGPT_REVIEW_PROMPT_S1E_20261031.md)。

> **S1d 补充诊断（2026-10-31）：** 使用 period-correct GMTI05 输入，同一 Native CUDA binary 完成7分支相位向量交叉实验。SNR30 support 为自身 ON=7、SNR20 ON 交叉=11、配对 OFF=22；后者22格中有4格与目标真值距离坐标重合，交叉 SNR20 的11格均为其他距离格。离线 S1b Mode-A ON/OFF/TO 对照显示 TO 能量集中在目标真值格，但 ON/OFF 相位差没有在目标格变大；因源码/binary 版本不同，仅作旁证，未关闭根因。冻结门槛未变，SNR30 原生失败仍保留。完整边界见 [S1d 结果](support_failure_diagnostic/S1D_PHASE_VECTOR_CROSSOVER_RESULT.md)、[精简审查证据](support_failure_diagnostic/evidence/s1d/)与[固定 ChatGPT 独立审查提示词](support_failure_diagnostic/CHATGPT_REVIEW_PROMPT_S1D_20261031.md)。本实验不解除 target-present Screening gate，也没有 GMTI task performance 证据。

> **历史检查点（2026-10-30）：** 当时 29/60 case 完成，seed1935015/+4 ns 的 attempt001/002 尚未完成；其归档与检查点数据保留在[历史证据目录](evidence/h0_checkpoint_20261030/)。最终终态以 2026-10-31 报告及其 matrix audit summary 为准。

> **最新 V2 状态（2026-10-28 11:34:54 UTC）：** screening matrix 为 `GATE_FAILED` 并已停止：17 个通过，1 个方法级不可评估 case，4 条更早中断 attempt；无活动 case。根因是 `seed1932001 / 2.5 m/s / 30 dB / +4 ns` 的 C4 Mode-B ON robust reference 有一组 support=7 < 8。详情、失败 ZIP 和当前审查入口见 [05 资源与执行记录](05_resource_and_execution_plan.md) 与 [ChatGPT checkpoint 提示词](CHATGPT_REVIEW_PROMPT_SCREENING_CHECKPOINT_20261028.md)。后续旧的“screening 正在执行”提示属于较早 checkpoint。

**早期 pilot 状态（2026-10-27）：** seed900101/900202/900203 的本地 Native CUDA 工程运行已归档；PKR 支持量 gate 失败后停止了当时的比较。阶段 `INCOMPLETE / INCONCLUSIVE`，`PERFORMANCE_VERIFIED=false`。这些是历史结果，不代表本次 2026-10-28 延续工程 case 已进入 Production。

> **历史 V2 screening 延续快照（截至 2026-10-28 11:16 UTC；随后已停止）：** 固定设计共 594 个 case。seed1932001、0.5 m/s、10 dB、20 targets、10 periods 的 0 ns 与 +4 ns 配对 case 均已通过单 case 工程门；+4 ns attempt004 有 54/54 method/mode/role branches 通过，紧凑 ZIP 与逐文件 SHA 已核验。单 seed 仍不能支持统计性能结论。该时间点完整 288-case screening 已恢复运行，但随后因 C4 Mode-B ON reference support=7<8 触发 `GATE_FAILED` 并停止；当前状态见本文件顶部及最新 matrix manifest。中间实验目录允许超过 20 GB；20 GB 是结果汇总、ChatGPT 审查完成并按清理台账删除可再生大数组后的最终项目占用上限。当前 checkpoint 的独立审查材料见 [V2 screening ChatGPT 审查提示词](CHATGPT_REVIEW_PROMPT_SCREENING_CHECKPOINT_20261028.md)。

> **2026-10-28 V1A 延续执行状态：** 首个 0 ns case（seed1930101）已进入真实 Native CUDA Production，但 C4/PKR robust gamma reference 有少数 133-bin groups 未达到 `min_support=8`，矩阵按 fail-closed 规则 `GATE_FAILED` 并停止。Stage2 连续面积杂波有效。该 case 不可评价，不能据此比较任务性能。早先盲校正预处理失败仍保留在 [原工程门失败记录](10_engineering_gate_failure_20261028.md)；本次实际生产执行、gate 根因和[精简审查证据](evidence/extension_engineering_v1a/seed1930101/)见[V1A 工程门复测记录](11_v1a_engineering_gate_result_20261028.md)。其余工程 seeds、screening、confirmatory、dense、bridge 和独立 H0 均未启动。

## 建议阅读顺序

1. [00 总执行计划](00_master_execution_plan.md)：范围、门控与真实执行状态。
2. [01 实验设计](01_experiment_design.md)：研究问题、信息条件、比较边界。
3. [02 Pilot 结果](02_screening_experiment.md)：两 seed 单周期 CUDA 观察、有效面积杂波与停止根因。
4. [03 确认实验状态](03_confirmatory_experiment.md)与[04 统计分析计划](04_statistical_analysis_plan.md)：未执行部分及正确推断单位。
5. [05 资源与执行计划](05_resource_and_execution_plan.md)：设备、耗时和磁盘记录。
6. [06 科学方向裁决](06_scientific_decision.md)：阶段结论及后续边界。
7. [07 执行与质量审计](07_execution_and_quality_audit.md)：源码/二进制身份、门控、验证和限制。
8. [08 下一实验契约](08_frozen_next_experiment_contract.md)：PKR 修复前的冻结状态。
9. [2026-10-28 延续执行契约](09_extension_execution_contract.md)：冻结门控、扩展矩阵、资源和保留策略。
10. [工程门失败记录](10_engineering_gate_failure_20261028.md)：本次 seed1930101 的真实执行、输入配对、盲估计和失败边界。
11. [V1A 工程门复测记录](11_v1a_engineering_gate_result_20261028.md)：真实 CUDA Production 执行、133-bin support gate 失败和结论边界。
12. [ChatGPT 独立审查提示词](CHATGPT_REVIEW_PROMPT.md)：要求 ChatGPT 读取指定 commit，并审查当前 V1A gate failure 与总体研究价值；交付后填入完整审查 commit SHA。
13. [V2 距离支持域修订](12_v2_range_support_domain_amendment_20261028.md)：说明由面积杂波/目标物理 ROI 推导的共同闭区间 `[666,1197]`、V1A 与 V2 的版本隔离及六 seed 工程门。V2 配置和设计分别为 [方法配置](config/r22_method_config_v2_range_roi.json) 与 [机器可读设计](config/r22_extension_experiment_design_v2_range_roi.json)。
14. [V2 首个配对工程 seed 结果](13_v2_engineering_seed1930101_pair_20261028.md)：seed1930101 的 0 ns / +4 ns 本机 Native CUDA 配对结果；这是一个 seed 的可行性证据，不是统计验证。对应的 [ChatGPT 独立审查提示词](CHATGPT_REVIEW_PROMPT_V2.md) 要求先读指定研究总纲，并附带压缩证据包 `outputs/r22_chatgpt_review_evidence_v2_engineering_pair_20261028.zip`（约 23 MB）。
15. [V2 六 seed 工程结果](15_v2_six_seed_engineering_results_20261028.md)：六个 seed、12 个最终有效 CUDA Production case 的描述统计、审计边界和资源门；显式记录 seed1930101 与后续 seed 的 Stage2 binary 版本差异。
16. [V2 六 seed ChatGPT 独立审查提示词](CHATGPT_REVIEW_PROMPT_V3.md)：用于审查总体科研价值、六 seed 结果、Stage2 Rmin 精度修复及是否需要重跑 seed1930101；精简审查包位于 `outputs/r22_chatgpt_review_evidence_v3_six_seed_20261028.zip`。
17. [V2 screening checkpoint ChatGPT 独立审查提示词](CHATGPT_REVIEW_PROMPT_SCREENING_CHECKPOINT_20261028.md)：可直接复制给 ChatGPT，要求先读取本次研究总纲并核查最新矩阵 manifest、运行证据和总体科研价值；本地 `outputs/` 中的 manifest、日志与 case ZIP 需一并附上。
18. [R22 H0 检查点与最终报告](18_h0_false_alarm_checkpoint_20261030.md)：保留 2026-10-30 的 29/60 检查点，并记录 2026-10-31 恢复后的 60/60 最终结果。
19. [V4 ChatGPT 独立科研审查提示词](CHATGPT_REVIEW_PROMPT_V4.md)：固定审查 commit、优先读取总纲并开展阶段正确性与总体科研价值双层审查。最终 CSV、manifest、矩阵审计与清理记录见 [H0 最终证据包](evidence/h0_final_20261031/)；60 个 compact case ZIP 与 raw IQ 未上传，限制已写入提示词。
20. [S1c 共同 OFF phase 结果](support_failure_diagnostic/S1C_SHARED_OFF_PHASE_RESULT.md)、[目标重合交叉表](support_failure_diagnostic/evidence/s1c/v2/s1c_target_overlap_audit.csv)与[S1c 补充证据独立审查提示词](support_failure_diagnostic/CHATGPT_REVIEW_PROMPT_S1C_20261031.md)：固定审查到明确 commit，评估 phase-vector 干预、目标坐标/响应及当前因果解释边界。
21. [S1d phase-vector crossover 结果](support_failure_diagnostic/S1D_PHASE_VECTOR_CROSSOVER_RESULT.md)、[紧凑 Production 证据](support_failure_diagnostic/evidence/s1d/)与[固定快照 ChatGPT 独立审查提示词](CHATGPT_REVIEW_PROMPT_S1D_20261031.md)：固定审查到 `781bf3d96bb327a423eab944ce09a13a65c85ad9`，检查 S1d 执行正确性、S1c/S1d 因果边界及对整体 GMTI 方向的价值。
22. [S1e 当前 Mode-B ON/OFF/TO 三角色诊断](support_failure_diagnostic/S1E_MODEB_ROLE_TRIPLET_RESULT.md)、[紧凑 CUDA Production 证据](support_failure_diagnostic/evidence/s1e/)与[固定快照 ChatGPT 独立科研审查提示词](support_failure_diagnostic/CHATGPT_REVIEW_PROMPT_S1E_20261031.md)：审查快照固定为 `d2462657803375c3b0ebf45076d8eb4b94d63a8e`；检查三角色运行、逐格复数闭合、CSI phase-vector 作用、C4 support gate 及整体研究价值。
23. [V2 screening 方法级可评价性离线复核](19_v2_screening_method_level_offline_dry_run_20261105.md)：对18个终态case归档做只读重分类，报告CRC/SHA、269/270个ON方法单元可评价及全方法比较仍不完整的边界。
24. [V3 方法级可评价性契约草案](20_v3_method_level_evaluability_contract_draft_20261105.md)：提出场景级完整性与方法级状态分离、覆盖率分母和公平比较规则；状态为草案，不修改V2、不实现runner，也不授权CUDA。
25. [V3 方法级契约 ChatGPT 独立审查提示词](CHATGPT_REVIEW_PROMPT_V3_METHOD_LEVEL_20261105.md)：固定审查快照为 `3b99486406d9d224f4968df23bbcfeec1315e378`，先读该版本研究总纲，再审查契约正确性与整体GMTI研究价值；提示词明确本机 ZIP/IQ/真值不可从GitHub快照独立验证。
26. [V3早停与重放审计](21_v3_screening_early_stop_20261106.md)、[V3.1全量 Screening 契约](23_v3_1_post_replay_screening_contract_20261106.md)与[V3.1设计 JSON](config/r22_extension_experiment_design_v3_1_resource_replay_20261106.json)：保留V3终态失败、后续同条件重放、全量288-case新版本边界和磁盘清理身份。
27. [V3.1全量 Screening 早停审计](24_v3_1_screening_early_stop_20261106.md)、[精简case摘要与matrix manifest](evidence/v3_1_early_stop_20261106/)、[完整失败case compact归档](evidence/v3_1_early_stop_20261106/failure_case.zip)和[ChatGPT独立审查提示词](CHATGPT_REVIEW_PROMPT_V3_1_EARLY_STOP_20261106.md)：记录6/288停止、运行OOM与未知失败、raw清理和方向结论。初审中的哈希语义误判由下一条更正说明纠正。
28. [V3.1设计摘要哈希语义更正](26_v3_1_design_digest_semantics_correction_20261106.md)：确认 manifest 使用规范化 JSON 哈希并与已提交V3.1设计一致；区分已验证的设计内容身份与旧manifest未记录的原始文件路径/字节SHA。
29. [V3.2身份闭合 Screening 契约](25_v3_2_identity_closed_screening_contract_20261106.md)与[机器可读设计](config/r22_extension_experiment_design_v3_2_identity_closed_20261106.json)：记录冻结时的独立288-case矩阵、设计哈希、资源与磁盘身份。该条只描述2026-11-06冻结时状态；后续执行结果见本页顶部的[最终报告](27_v3_2_screening_execution_report_20261109.md)。
30. [V3.3离线残差与Production输出审计报告](V3_3_residual_task_gap_audit/FINAL_RESEARCH_REPORT.md)、[逐方法输出/关联覆盖分析](V3_3_residual_task_gap_audit/03_detection_and_opportunity_overlap.md)、[紧凑证据导航](V3_3_residual_task_gap_audit/README.md)与[ChatGPT独立审查请求](V3_3_residual_task_gap_audit/CHATGPT_REVIEW_REQUEST.md)：涵盖144个`+4 ns` compact case、全十周期位置共同成功及Production Detection/cluster输出；明确31个method-case association缺口、非目标cluster不可评价和不作系统Pfa解释。证据快照 commit 固定写在审查请求中。

31. [R22-V4 周期因果、输出完整的确认性契约草案](31_v4_causal_output_complete_confirmatory_contract_draft_20261113.md)、[机器可读设计](config/r22_v4_causal_output_complete_confirmatory_draft_20261113.json)与[ChatGPT独立审查请求](CHATGPT_REVIEW_REQUEST_V4_CONTRACT_DRAFT_20261113.md)：只定义待审查方案；MDE/检测非劣阈值、实现、资源 pilot 与用户 CUDA 授权均未闭合。
32. [R22-V4冻结前工程实现闭合](32_v4_pre_freeze_technical_closure_20261122.md)：记录周期因果估计和完整输出的CPU/模拟集成证据。
33. [R22-V4 CPU集成闭合裁决](33_v4_integration_closure_20261123.md)、[CPU验证](34_v4_cpu_integration_validation_20261123.md)、[pilot资源readiness](35_v4_cuda_pilot_readiness_20261123.md)与[ChatGPT独立审查请求](CHATGPT_REVIEW_REQUEST_V4_INTEGRATION_CLOSURE_20261123.md)：110项受影响回归通过；静态计划已核实；因卷空间低于单case输入下界，未运行CUDA或Stage2，V4未冻结。


34. [R22-V4 Phase H size pilot、资源闭环与冻结准备报告57](57_v4_phase_h_size_pilots_and_matrix_freeze_20261128.md)：记录三类size pilot、逐case归档与续跑、容量模型、最终240-case CPU dry-run及未授权边界。
35. [R22-V4 Phase H ChatGPT独立审查请求](CHATGPT_REVIEW_REQUEST_V4_PHASE_H_FREEZE_READINESS_20261128.md)：固定审查commit；要求先读取该commit的研究总纲，再审查执行器、实验设计、资源预算、阶段价值和正式矩阵授权边界。

## 关键证据

- [R13–R21 baseline 注册表](evidence/results/baseline_registry.csv)与[可比性矩阵](evidence/results/baseline_comparability_matrix.csv)
- [R22 机器可读实验 manifest](evidence/results/r22_experiment_manifest.json)
- [逐条件关键指标](evidence/results/r22_key_metrics.csv)
- [两 seed 描述性检测图](evidence/results/r22_descriptive_target_hits.png)及[生成脚本](../../../../scripts/phase_r/plot_r22_descriptive_hits.py)
- [面积杂波与配对审计](evidence/results/area_pairing_audit.json)
- [seed900101 Native CUDA 20 目标生成/Production smoke](evidence/preflight/seed900101/preflight_manifest.json)
- [seed900202 紧凑 pilot 证据](evidence/preflight/seed900202/README.md)
- [seed900203 PKR gate 失败证据](evidence/preflight/seed900203_closure/README.md)
- [V1A seed1930101 精简工程审查证据](evidence/extension_engineering_v1a/seed1930101/README.md)：Stage2 杂波报告、实际 XML/log、CSI tap、CFAR geometry、robust gamma failure rows、case/matrix 摘要与 cleanup ledger。
- [清理台账](evidence/cleanup_ledger.md)
- [2026-10-28 历史大数组清理记录](14_storage_cleanup_20261028.md)
- [2026-10-30 H0 检查点审查证据](evidence/h0_checkpoint_20261030/README.md)
- [2026-10-31 H0 最终精简审查证据](evidence/h0_final_20261031/README.md)

## 历史 preflight 结果边界（2026-10-27）

seed900202/900203 均为 20 个目标、单 beam、单周期、+4 ns 的工程 pilot。A0/C0 和盲方法命中数可作描述性观察，不能把目标机会当独立 seed。seed900203 面积杂波有效且配对复用可追溯；PKR 六条角色记录不可评价，导致 A0/PK/PKR residual closure 不闭合。H0 经验 Pfa、confirmed track、径向速度与跨周期任务表现均未验证。Stage R22 的完整筛选和确认矩阵尚未启动。

## V2 六 seed 工程结果历史边界（2026-10-28）

六个 seed 的 0 ns / +4 ns 工程 case 已完成，结果见 [V2 六 seed 工程结果](15_v2_six_seed_engineering_results_20261028.md)。最终 12 个 case 均 `IMPLEMENTED / EXECUTED / EVALUABLE`，但 `PERFORMANCE_VERIFIED=false`。seed1930101 使用精度修复前的 Stage2 binary；seed1930102 修复重跑及 seed1930103–1930106 使用修复版。该二进制版本边界和是否需要补跑 seed1930101，须在下一步决策前审查。**当时**完整594-case设计的screening尚未启动；后续启动后停在C4 Mode-B support gate，当前状态见导航页顶部和[05资源与执行记录](05_resource_and_execution_plan.md)。没有 AI 训练或 Router 开发。
