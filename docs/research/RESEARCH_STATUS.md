# PGRCC 动态研究状态

本文件记录会随研究阶段变化的状态，不重新定义长期研究目标。研究主题、总体目标、科学问题、研究内容和长期技术路线以[研究总纲](RESEARCH_MASTER_PLAN.md)为准；本文件不能覆盖、重新定义或暗中替代总纲中的长期目标或路线。

## 状态身份

- **2026-11-14 R22-V4 周期因果源码审计：** 固定只读源码快照 `1063d67c96af7dbbe7fe871b3a56be0ca453b9b2` 显示，当前 R21/R22 的 M1 与 Huber 都以 full-case batch 输入估计；R22 Production correction runner 对所有周期使用单一 case 级校正标量。现有 Huber 候选是 M1 同一 pooled input、M1选出的第80百分位 support mask与几何模型上的幅度加权 robust phase-slope D3；R21有效性门没有D3时延范围检查。V4 的 `{0…k}` 前缀和逐周期校正尚未实现/执行/评价；已将共享mask与Huber范围/fallback决策列为冻结前事项。未运行任何实验，CUDA/AI仍未授权。
- **2026-11-14 R22-V4 契约草案修订：** 将同信息的 `HUBER_D3_CAUSAL_PREFIX` 加入预注册次要对照，以检验 M1 是否优于使用相同 ON C1/C2 周期前缀的传统估计器；Fixed D3 固定值 `3.4099650318669545 ns` 及 R9/R3 来源已写明。机器计数为240个 case identity、约1,470条 method×role Production branch。V3.3 的 M1−Huber `−6.524 ± 0.771 m` 仅作混合筛选条件的设计背景，不作阈值或确认结论。草案仍未冻结：主比较 MDE、覆盖率非劣界值、周期因果 runner/逐周期 correction 实现与边界检查、输出尺寸 pilot 和用户 CUDA 授权未闭合。项目目录约5.3 GiB；清理仅移除一个 `not_started`、0-case 的 scratch manifest（13,597 bytes），其余唯一运行/审计证据保留。详见[更新草案](phase_r/R22/31_v4_causal_output_complete_confirmatory_contract_draft_20261113.md)、[机器设计](phase_r/R22/config/r22_v4_causal_output_complete_confirmatory_draft_20261113.json)与[ChatGPT审查请求](phase_r/R22/CHATGPT_REVIEW_REQUEST_V4_CONTRACT_DRAFT_20261113.md)。
- **筛选精度附记：** 将历史数据限制到 V4 计划的 `+4 ns / 20 dB / {0.5,2.5,6.7 m/s}`，12-seed batch-screening 的位置差为 M1−Fixed `−11.146 ± 4.797 m`、M1−Huber `−5.603 ± 0.985 m`；检测覆盖差为 `+0.083 ± 0.495 pp`、`0.000 ± 0.333 pp`。按旧 SD 投影到30 seed的均值区间半宽约1.79m/0.37m和0.185pp/0.124pp，仅作粗略精度参考；不代表因果前缀表现，不是功效分析，不据此选 MDE 或非劣界值。

- **2026-11-13 R22-V4 契约草案：** 根据 V3.3 独立审查建议，形成周期因果、output-complete 的确认性设计草案和机器可读配置；范围提案为180个 target-present case 加60个全新 H0 case。M1 仅读取当前及过去 ON C1/C2 周期，batch-10 仅作离线诊断。位置 MDE、检测覆盖率非劣界值、因果估计器/完整输出实现审计、资源 pilot 与用户 CUDA 授权仍未闭合；`DRAFT_NOT_FROZEN`、`CUDA_AUTHORIZED=false`、`AI_TRAINING=false`。研究总纲 blob 未改。契约见[草案](phase_r/R22/31_v4_causal_output_complete_confirmatory_contract_draft_20261113.md)、机器定义见[JSON](phase_r/R22/config/r22_v4_causal_output_complete_confirmatory_draft_20261113.json)、独立审查提示词见[R22-V4 契约审查请求](phase_r/R22/CHATGPT_REVIEW_REQUEST_V4_CONTRACT_DRAFT_20261113.md)。本次只审计空间，项目目录约5.3 GiB；保留用于当前研究的 V3.2 归档，本次未删除结果。
- **2026-11-10 R22 V3.3 Production 输出审计补充：** 对同一组144个 V3.2 `+4 ns` compact ZIP 做只读离线复算；1,872/1,872 个方法×case Production 输出均有10周期记录，Detection 数、selected candidates 与 cluster lineage 逐周期闭合。1,841个方法×case具完整冻结 evaluator 真值关联，31个仅报告 Production 输出；P2/C4/PKR Mode-B关联覆盖分别为137/144、129/144、135/144。真值未关联 Detection 行不等于 false cluster/Pfa，非目标 cluster→truth lineage仍不可评价。未运行CUDA/Stage2/Production/AI，未改写或删除144个ZIP。紧凑证据约6.96 MB，工作树约4.8 GiB。源码审计身份见[V3.3 manifest](phase_r/R22/V3_3_residual_task_gap_audit/evidence/manifest.json)，详细表见[Production输出分析](phase_r/R22/V3_3_residual_task_gap_audit/03_detection_and_opportunity_overlap.md)。研究总纲blob不变；阶段仍非确认性性能验证，AI entry gate `FAIL`。
- **2026-11-10 R22 V3.3 离线任务残差审计补充完成：** 复核 144 个 `+4 ns` compact case ZIP，SHA 与冻结索引 144/144 一致；未复制、解压、删除或改写 ZIP。使用冻结 evaluator 保存的逐机会 `target_associations.csv.position_error_m`，补出全十周期位置共同成功比较；与 `parameter_metrics.csv` 做 576 个 case×方法 parity，576/576 通过。四方法共同位置成功为19,682/28,800（68.340%）；M1 相对 PK Known 的位置 RMSE 高18.387±4.503 m，较 Fixed D3 低13.979±4.133 m，后者差值在12/12 seed同向。全周期角度/斜距/Doppler共同成功、共同地速、有效径向速度和目标复数幅相仍不可评价。检测结论维持筛选描述：Current 11.694%，PK 69.729%，target-present deterministic 69.705%–69.767%；`PERFORMANCE_VERIFIED=false`，不作显著性主张。Huber/M1 用完整十周期、1,300 pulses batch/offline拟合，实时因果可用性与估计耗时未验证。AI entry gate仍为`FAIL`。详细见[V3.3最终报告](phase_r/R22/V3_3_residual_task_gap_audit/FINAL_RESEARCH_REPORT.md)和[证据导航](phase_r/R22/V3_3_residual_task_gap_audit/README.md)。研究总纲未修改；只建议先冻结 output-complete 确认性契约，未授权 CUDA。
- **2026-11-09 R22 V3.2 全量 Screening 完成：** Native CUDA 执行冻结的 288/288 个 case，4,320/4,320 个适用 ON 方法单元已分类；255 个 case `PASSED`、33 个 `PASSED_WITH_METHOD_GAPS`。49 个方法单元因 `insufficient_robust_support` 保持不可评价。288 个最新 ZIP 的 SHA/CRC 均核验，raw 已清理；`PERFORMANCE_VERIFIED=false`，独立 H0/Pfa不可评价。逐项结论见[R22 V3.2 执行报告](phase_r/R22/27_v3_2_screening_execution_report_20261109.md)、[方向评估](phase_r/R22/28_v3_2_preliminary_direction_assessment_20261109.md)及[精简审查证据](phase_r/R22/evidence/v3_2_screening_20261106/README.md)。研究总纲 blob保持不变。
- **2026-11-06 R22 V3.1 全量 Screening 早停：** 本机 Native CUDA 实际尝试6/288个case：5个case通过、SNR30 dB/+4 ns case按冻结门控 `UNKNOWN_STOP`；282个case未执行。90个已访问适用方法单元中87个可评价、3个未知，4,230个未访问。连续面积杂波 gate 在6个case均通过（每case 1,300 packets、6,508,782 samples、平均功率约0.02215967）；失败case有3个 Production 分支退出码1，其中 C2 Mode-B ON 日志确认 CUDA 显存分配 OOM，另2个根因 `NOT_VERIFIED`。所有6个 compact ZIP CRC通过，合计约29.8 MB；cleanup ledger记录移除8,826个文件、47,246,962,032 bytes（逻辑大小，不等于可用磁盘物理回收量），残留raw文件为0。初审曾将 manifest 规范化哈希 `696dce…` 与 V3.1 JSON 原始字节 SHA `26e202…` 混比并误判设计身份未闭合；后续更正确认规范化设计内容哈希与 manifest 一致，原始文件路径/SHA 未在旧 manifest 单独记录。V3.1终态不得续跑；未降低门槛、未做性能排名。详见[V3.1早停审计](phase_r/R22/24_v3_1_screening_early_stop_20261106.md)、[哈希语义更正](phase_r/R22/26_v3_1_design_digest_semantics_correction_20261106.md)、[紧凑证据摘要](phase_r/R22/evidence/v3_1_early_stop_20261106/case_summary.json)和[失败case完整compact归档](phase_r/R22/evidence/v3_1_early_stop_20261106/failure_case.zip)。`PERFORMANCE_VERIFIED=false`；研究总纲 blob保持不变。
- **2026-11-06 R22 V3 早停与设备状态重放：** V3 按冻结门控在第4个唯一 case 以 `UNKNOWN_STOP` 终止；3个 case 通过、1个 case 的54个 Production branch 中53个通过。之后同条件诊断重放完成9/9 branch，包含原失败的 `P2 / Mode-A / ON`，运行前空闲显存1215 MiB；这使设备资源状态成为合理解释，但根因仍未证明。原V3状态与失败归档保持不变。V3.1独立设计本计划288个case和4,320个适用ON方法单元；V3/V2历史case与诊断重放均不计入覆盖率。V3.1实际执行状态见上条及对应审计报告。详见[R22 V3早停与重放审计](phase_r/R22/21_v3_screening_early_stop_20261106.md)、[V3.1契约](phase_r/R22/23_v3_1_post_replay_screening_contract_20261106.md)和[V3.1设计](phase_r/R22/config/r22_extension_experiment_design_v3_1_resource_replay_20261106.json)。
- **2026-11-05 S1e 独立审查完成：** 用户提供的固定快照审查报告（研究快照 `d2462657803375c3b0ebf45076d8eb4b94d63a8e`）给出 `PASS_WITH_LIMITATIONS`。审查独立复核 C4 ON/OFF/TO support 为7/55/14，确认单组 support 受CSI phase-vector来源影响；目标污染仍 `NOT_PROVEN`，没有GMTI任务收益结论。R22 V2 target-present screening 继续保持 `GATE_FAILED / INCOMPLETE`。18个终态归档的离线重分类为270个适用ON方法单元中269个可评价、1个 C4 Mode-B 不可评价；全方法 CUT/unique-hit gate 仍不完整，不恢复排名。当前只形成 [V3方法级可评价性契约草案](phase_r/R22/20_v3_method_level_evaluability_contract_draft_20261105.md) 与[离线复核报告](phase_r/R22/19_v2_screening_method_level_offline_dry_run_20261105.md)，尚未实现 runner 或启动 CUDA。研究方向 `CONTINUE`，`AI_ENTRY_GATE=FAIL`，研究总纲 blob 未变。
- **2026-11-04 离线资源重估：** 按 target-present screening 最新矩阵 manifest，冻结计划为288个case；22条attempt对应18个唯一case，其中17个通过、1个因C4 gate失败，另有270个case尚未开始。17个通过case的case级执行时长合计4.57小时，均值16.13分钟、中位数16.45分钟、范围10.44–27.52分钟；紧凑归档合计约87.0 MB，均值5.12 MB/case。按此首批单seed观测线性外推，288个case约需77.4小时case执行时间，剩余271个等效执行约72.8小时，归档约再增加1.39 GB。该估算仅用于资源规划：17个case都来自seed1932001，不是独立seed性能证据，也不包含C4门控处置、新设计重跑、其他矩阵阶段、等待和分析成本。当前矩阵为`GATE_FAILED`，runner禁止从该状态`--resume`；S1e独立审查完成前不启动新实验。数据快照见[冻结 screening manifest](phase_r/R22/evidence/screening_checkpoint/screening_final_manifest.json)，细节见[R22资源与执行记录](phase_r/R22/05_resource_and_execution_plan.md)。
- **2026-10-31 S1e 阶段结果：** 用户授权后，S1e 在当前 Mode-B Native CUDA Production 中完成配对 ON/OFF/TO 三角色单周期诊断。ON/OFF/TO 的 C4 support 为7/55/14；raw 复数分量 `ON−OFF−TO` 的 F1/F2 相对 L2 为0.00937/0.01806，逐角色 CSI phase correction 后 F2 闭合残差为0.78770。TO能量主要落在11个目标真值距离格，但ON/OFF CSI phase-vector差在目标格没有更大（1.727 vs 1.833 rad）。这加强了角色相关CSI相位校正为近端机制的证据，仍不能唯一归因于目标污染，也没有任务性能验证。R22 target-present matrix 仍为 `GATE_FAILED / INCOMPLETE`；双层独立审查待完成。详见 [S1e 结果](phase_r/R22/support_failure_diagnostic/S1E_MODEB_ROLE_TRIPLET_RESULT.md)、[紧凑证据包](phase_r/R22/support_failure_diagnostic/evidence/s1e/)、[S1d 结果](phase_r/R22/support_failure_diagnostic/S1D_PHASE_VECTOR_CROSSOVER_RESULT.md)和[固定快照 ChatGPT 独立审查请求](phase_r/R22/support_failure_diagnostic/CHATGPT_REVIEW_PROMPT_S1E_20261031.md)，审查快照为 `d2462657803375c3b0ebf45076d8eb4b94d63a8e`。
- R18 执行源码版本：`014352ace1c4ad0bade0d759a099a47fe9ecfaa9`（工作分支 `codex/unknown-system-error-next-stage`）；完整阶段报告随原交付提交。
- 研究总纲版本：Git blob `e637e3769bde7814298ff012d0f42301884e2985`；本次 H0 工作未修改总纲。
- 本状态整理日期：2026-11-10。
- 最近完成阶段：R22 V3.3 V3.2 archive 离线残差审计补充；保留全周期 position audit、parity、汇总及报告，不改写 V3.2 历史归档；
- 当前阶段状态：V3.2 screening 及 V3.3 离线审计已完成，但不是确认性性能验证。V2/V3/V3.1 历史失败或早停状态保持不变。全周期位置差距现可评价，角度/斜距/Doppler联合机会、独立 H0、目标专属复数幅相、地速共同成功和有效径向速度仍未闭合。不得自动启动新实验、AI训练或修改研究总纲。

## R22 H0 独立虚警侧矩阵历史结果（2026-10-31）

H0 矩阵包含 seed1935001–1935030 的 30 个独立 seed，每个 seed 配对 0 ns 与 +4 ns，共 60 个唯一 case。矩阵 `COMPLETED`，最终 60 个 case 均 `H0_EVALUABLE_WITH_GAPS` 并有 compact archive；统计分析为 `COMPLETE_EVALUABLE`。推断单位是 seed，CUT、距离单元、Doppler 单元和处理周期不是独立统计重复。

- **H0 物理输入：** 所有 60 个 `A0_ON` Stage2 场景均通过连续面积杂波 gate，每 case 1,300 packets / 6,508,782 samples；30 个同 seed 0/+4 ns 配对的杂波、几何、时间相关和实测噪声摘要字段完全一致。面积功率范围 `0.0221935448633–0.023308819045`，实测噪声功率范围 `0.000999645705034–0.00100025854155`。`A0_OFF/A0_TO` 是关闭杂波的对照 variant，不能用于否定 `A0_ON` 中有效杂波。
- **H0 统计输出：** 1,800 行 per-seed CUT-hit fraction；72 个方法/条件汇总中 60 个完整、12 个零时延不适用；36 个配对汇总中 24 个完整、12 个不适用。95% percentile bootstrap 按 seed 聚类，10,000 次，NumPy PCG64 seed 22029。两种 CUT 区域的时延差方向不一致，结果不作系统 Pfa 或跨方法排名。
- **科学结论边界：** H0 只补充无目标条件的 false-alarm-side 经验 CUT-hit fraction。它不提供 target Pd、低速目标保持、速度/方位估计或跟踪性能证据，也不改变 R22 screening `GATE_FAILED`。未知系统误差是否降低 GMTI 目标任务表现仍需 target-present evidence；AI 必要性未得到支持。
- **执行身份：** 源码 commit `a688ca52616cc380b07810d4f8fbd79ed73d46bc`；本机 Native CUDA；Stage2/Production binary SHA 与 60 case/archive SHA 索引见最终分析 manifest。恢复期间外部训练进程仍运行，未观察到 OOM；设备观察不是性能基准。
- **空间状态：** 完成 case 的可重建 raw 已逐 case compact、归档并删除；最终 60 case ZIP 合计约 180.9 MB，仍留在本机 `outputs/` 以支持用户安排的 ChatGPT 审查。用户规定的 `<20 GB` 是审查完成后的最终占用上限；此处尚未执行审查后最终清理。
- **审查入口：** [V4 ChatGPT 独立审查提示词](phase_r/R22/CHATGPT_REVIEW_PROMPT_V4.md)、[H0 最终报告](phase_r/R22/18_h0_false_alarm_checkpoint_20261030.md)和[精简证据包](phase_r/R22/evidence/h0_final_20261031/)。
- **状态标签：** H0 `IMPLEMENTED=true`、`EXECUTED=true`（60/60）、`EVALUABLE=true`（分析 `COMPLETE_EVALUABLE`）、`PERFORMANCE_VERIFIED=false`（未评价目标检测任务）。Target-present screening 仍 `GATE_FAILED`，confirmatory/dense/bridge 未启动，`AI_ENTRY_GATE=FAIL`。研究总纲 blob 保持 `e637e3769bde7814298ff012d0f42301884e2985`。

## 历史阶段：R22 V1A 工程 gate 停止（2026-10-28）

R22 扩展目标是验证多目标、连续扫描、实际杂波和独立 seed 下的传统方法能力边界。研究总纲正文未修改，Git blob 仍为 `e637e3769bde7814298ff012d0f42301884e2985`。

- **实际运行：** 实验源码 commit `91cb5b1de8c2831073465e9192c1a385f0e0e25a`。V1A 首个 0 ns case 为 seed1930101、2.5 m/s、20 dB、20 targets、10 periods。Stage2 连续面积地杂波为 1300 packets / 6,508,782 samples，实测平均功率 `0.0225128995081`；area gate 和 causal triplets 通过。
- **Production gate：** 41 条方法/角色记录中 24 个分支完整通过；4 个 C4/PKR reference 分支实际执行且 Production return code 为 0，但 gamma reference 验证失败；8 个依赖分支未执行；5 个零误差物理校正记录标为 `NOT_APPLICABLE_ZERO_DELAY_REFERENCE`。在 `range_band_bins=133`、`min_support=8` 下，Mode-A/OFF 有 15/12,090 行 support 3–7，Mode-B/ON 有 11/12,090 行 support 5–7，原因为 `insufficient_robust_support`。源码表明这些行在相位筛选前已有不少于 8 个非零交叉项，但经冻结的 ±0.35 rad robust phase consistency 筛选后只剩 3–7 个；整份 reference 按 fail-closed 规则拒绝。该直接门控原因已确认，底层物理原因未确认。
- **可评价性：** case `EVALUABLE=false`、`PERFORMANCE_VERIFIED=false`；有 41 条 task rows、3200 个 target-period opportunity rows、8 条 ON rows 可评估、0 个独立 H0 seed。有效 CUT/unique-hit union 与经验 Pfa 未形成全方法可比结果。没有可报告的 Pd/Pfa、已知校正恢复量或盲方法差距。
- **停止范围：** V1A matrix 状态 `GATE_FAILED`，计划 12 个工程 case 中只完成 1 个；+4 ns、其余工程 seeds、screening 288、confirmatory 180、40-target stress 48、3-target bridge 6 与独立 H0 60 cases 均未启动。未降低 support threshold 或 Production/CFAR quality gate，未继续算法比较。
- **当前裁决：** `INCONCLUSIVE / STOP_AT_ENGINEERING_GATE`。本次确认了有效杂波和 CUDA 执行，确认 133-bin groups 中少数行经 ±0.35 rad 相位一致性筛选后保留支持数不足；现有证据不能将其归因为几何稀疏，也不能确定低一致性的物理来源；没有回答未知时延是否降低任务性能，也没有证明 Known recovery、盲估计差距或 AI 必要性。低速保护与复杂杂波优先级也未被本次实验区分。
- **下一步候选：** 先完成 ChatGPT 独立审查；只有用户决定继续后，再做一项离线 support-group 覆盖与 reference contract 审计。若提出新的分组/有效区域规则，需明确空间分辨率代价、缺组处理和评价分母，并作为新版本预注册；不得静默放宽当前门槛。完整结果见 [V1A 工程门复测记录](phase_r/R22/11_v1a_engineering_gate_result_20261028.md) 与[精简审查证据](phase_r/R22/evidence/extension_engineering_v1a/seed1930101/README.md)，提示词见 [R22 ChatGPT 提示词](phase_r/R22/CHATGPT_REVIEW_PROMPT.md)。
- **磁盘：** V1A 完整 engineering 输出树实测 2,401 个文件、301,858,725 bytes（约 301.9 MB）；清理当时 worktree 约 6.9 GB，本次复测约 7.2 GB，均小于 20 GB。原始路径/SHA 清理台账在本机输出目录，精简关键证据已归档。
- **状态标签：** `IMPLEMENTED=true`；`EXECUTED=true`；`EVALUABLE=false`；`PERFORMANCE_VERIFIED=false`。

## 历史阶段：R21 完成；固定时延作为有界确定性候选

R21 在 seed1001/1002、6.7 m/s、30 dB、+4 ns、连续面积地杂波下完成六方法 × ON/OFF/TO 的 Native CUDA Production pilot。B 的正确目标命中为 1/3、0/3；A0 和 Known 均为 2/3、2/3。Known F2 相对 A0 的闭合误差约 `8.8e-5`，通过冻结 `1e-3` 门；M1 在两个 seed 的共同成功机会角度/位置 RMSE 和 F2 残差均优于 R9 fixed D3 与 Huber D3，但仍落后 Known。ground-speed 样本少且排序不稳定；radial velocity 与统计 Pfa 不可评价。

- **裁决：** R21 `PASS_WITH_LIMITATIONS`；固定 delay estimator `CONTINUE`，仅作为有界候选/传统 baseline，不确认论文创新或总体 GMTI 增益。广义未知物理状态 `NOT_VERIFIED`；AI entry gate `FAIL`。低速保护与复杂杂波建模未在本轮区分，不能据此升级为新主线。
- **状态标签：** `IMPLEMENTED=true`；`EXECUTED=true`；`EVALUABLE=true`（仅逐机会有效且字段齐备的描述指标）；`PERFORMANCE_VERIFIED=false`（两个 seed 不支持统计显著性）。
- **执行异常：** seed1001 的外层包装器在 18/18 CUDA branches 完成后出现 `KeyError('case_manifest')`，保留 failed 状态；修复后的 CPU evaluator replay 只重新评价已完成的 CUDA 输出。seed1002 outer run 完成。
- **空间清理：** R21 文本审计 ZIP CRC 正常；原工作区约 16 GB，R21 二进制去重后约 6.7 GB。保留 A0/B 闭合输入、六方法 ON 合并复数输入及 ON CSI arrays；清理清单见 [R21 cleanup ledger](phase_r/R21/evidence/cleanup_ledger.md)。
- **来源：** [R21 检测与任务结果](phase_r/R21/07_detection_and_task_results.md)、[参数与物理闭合](phase_r/R21/08_parameter_track_and_physical_closure.md)、[科研价值与方向裁决](phase_r/R21/10_scientific_value_and_direction_decision.md)、[实验 manifest](phase_r/R21/evidence/results/r21_experiment_manifest.json)。

## 历史研究阶段：R20 完成；未知 +4 ns 任务损失获有限支持

R20 使用冻结的 `r20-task-eval-v2` 离线评价器对 R18、R8、R12 保存证据复评。本阶段执行 43 个 R20 evaluator/adapter/review 测试和 29 个冻结 R19 evaluator 回归测试，三个 R20 Python 文件通过 `py_compile`；没有 CUDA/Production 重放、原始 IQ 重算、算法修改或参数调整。

- **Doppler gate：** R18 seed606 / 6.7 m/s / 30 dB target-only TO 3/3 落在原有 ±2 行门内；三期 truth 分量与 Production 轴/PRF 均闭合。A0 ON 的三期轴/真值映射均有效，其中 2/3 有命中。R8 seed101 缺 Production axis，三条件任务结果标记 `DOPPLER_TRUTH_MAPPING_NOT_VERIFIED`。
- **R19 回归：** R18 12 场景、84 条 ON condition 中 strict hits、valid truth opportunities、misses 和 hit fraction 与 R19 一致；14/84 的 post-confirmation track match rate 从 1.0 更正为 0.5。新增独立的 confirmed unique-period 统计，避免把 state observations 误叫 periods。
- **非零误差证据：** R8 seed202、303 的 Current +4 ns 都为 0/3；A0/Known 分别为 2/3、3/3。按 `L=A0−B`、`G=Known−B`、`E=Known−A0` 定义，两 seed 的 L/G 分别为 2/3、3/3，E 均为 0，恢复比均为 1；两 seed 中位 L/G=5/6、范围 2/3–1。R12 seed404 的 B 为 0/3、A0/Known 为 2/3、固定 D3 与 M1 为 3/3。R8 seed101 因缺 Production axis 不纳入 R20 detection 主结论。所有数字是逐场景描述证据，不是统计显著性或总体 Pd。
- **Known / 确定性估计边界：** 可评价的 R8 seeds 中 Known 回到 A0 命中数；R12 的 M1 与固定 D3 比 Known 多 1 个机会，但共同成功机会的角度/位置误差仍高于 Known，且 M1 F2 closure `2.6840%`、Known `0.00873%` 只能引用历史 manifest，raw IQ 不可重算。R12 只有一个 seed。
- **方向：** 有证据继续有限的未知物理误差表征；不恢复静态时延估计为论文主要创新，不启动 AI。低速与复杂杂波不是本阶段因果变量。OFF/ON/TO 现有 CFAR geometry diagnostics 含有效 CUT 及 hit counters，但统计 Pfa 仍因缺独立 H0 抽样单位/统计设计而不可评价。唯一建议是用户审查后再考虑两个独立 seed 的窄 M1 验证；R20 本阶段已停止。
- **证据身份与可复现限制：** R20 identity manifest、逐机会 CSV、R19 v1/v2 差异和测试入口在 `phase_r/R20/evidence/`；另有 566 文件、约 30 MB 的 R8/R12 历史输入包及逐文件 SHA，可离线重生成评价表。R8/R12 raw IQ 文件不可用，历史输入 IQ SHA 只作声明；Known closure 未用 raw IQ 重算。
- **磁盘与保留：** 本机工作树约 2.1 GB，小于 20 GB；R20 未删除数据，新增紧凑审计和高价值结果。
- **状态：** `IMPLEMENTED`=R20 offline evaluator/adapter；`EXECUTED`=R18/R8/R12 saved-data re-evaluation 与正控制门；`EVALUABLE`=符合 axis/identity/field 要求的上述逐项指标及描述性 CUT 计数；`PERFORMANCE_VERIFIED=false`=没有新 CUDA/足够独立重复，不能宣称统计性性能改善。

## R19 历史阶段记录：未知误差问题当时仍未闭合

R19 仅对 R18 已保存数据离线重评，没有运行 CUDA、生成新 IQ、训练 AI 或改 Production 算法。完整报告从 [R19 总实施计划](phase_r/R19/00_master_execution_plan.md)开始，冻结契约、结果、机制边界、方向评估和 ChatGPT 审查请求列于 [R19 导航](phase_r/README.md)。研究主题和长期目标仍由研究总纲定义。

- **输入实验身份：** R18 runner commit `014352ace1c4ad0bade0d759a099a47fe9ecfaa9`，Release build 源码 commit `4c6d4d303621324f5e174db0ba1811c5691df2e2`，Production binary SHA `3328d7c61bc36b6d3b906692e355c46c328150c0399fa457e6f871bcb79abaf0`；R18 的配置和输入身份见 R19 evaluation manifest。R19 evaluator 源码 SHA 另见 [R19 执行审计](phase_r/R19/09_execution_and_quality_audit.md)。
- **复评范围：** 12 个 seed/speed/SNR 场景，delay error 全部为 0 ns；按冻结的一对一任务门重算最终 Detection、TrackManager 和字段可用的参数指标。高速度 Mode-A/B 在命中、Gamma 与下游候选数量上出现描述性差异；Mode-A 使用 paired OFF，是 privileged reference。旧 range/position 门的候选数量远高于 strict gate：seed606、6.7 m/s、30 dB 下 C0 为 24→2 条、C3 Mode-B 为 23→1 条候选边；R18 旧命中与 R19 strict 命中之差不能当作新物理性能效应。
- **可评估性：** 3,336 项指标状态中，2,184 `EVALUABLE`、576 `EVALUABLE_DESCRIPTIVE_MIXED_CLUTTER_NOISE`、84 `EVALUABLE_DESCRIPTIVE_ONLY`、276 `NOT_EVALUABLE`、132 `REPLAY_REQUIRED`、84 `NOT_IDENTIFIABLE_FROM_CURRENT_DEBUG`。统计 Pfa、目标专属复 CSI/SCNR、CFAR 空间归因、radial velocity 与 ID-switch 根因未闭合。
- **方向裁决：** R19 不能回答非零未知误差损失、Known correction 恢复率或盲估计差距；也未证明 AI、低速保护或复杂杂波优先。唯一下一步建议为 `CONTINUE` 一项边界明确的 R8/R12 离线统一复评；R8 的原始 Detection/truth 文件目前可由本机旧 manifest 路径读取，但未纳入 Git evidence 包；记录不足时再提出最小 replay 设计。该建议未获自动执行授权。
- **磁盘与保留：** 工作树当前约 2.0 GB，低于 20 GB 目标；本阶段未删除数据。高价值 R19 结果、manifest、测试记录、审查图及报告位于 `phase_r/R19/`。
- **证据状态：** `IMPLEMENTED`=R19 offline evaluator/contract；`EXECUTED`=12-case saved-data reevaluation；`EVALUABLE`=契约定义且字段完整的指标；`PERFORMANCE_VERIFIED=false`=本阶段无新 CUDA，也无未知误差因果验证。

## 最近完成阶段：R17

R17 审计 R14 与 R16 的 C0 Production CFAR 大幅分歧。审计没有运行 CUDA，没有改变算法、处理参数或历史实验结果。R14/R16 使用同一组 9 个 Stage2 role-period 输入；留存比较记录显示其 F1/F2 tap 与 CSI 输出实虚部 hash 相同、运行 XML 的算法参数相同，但 Production binary 和 CFAR maps 不同。

- **阶段进入条件：** R16 报告发现 R14/R16 九组 C0 CFAR map 均不同，且两次 Production executable SHA 不同；R17 获用户明确授权进行离线身份与数值机制审计。
- **阶段结论：** R14 `GMTI_pipe_core` SHA `f65a8aa4…` 与 R7 的 pre-fix GO-CFAR executable SHA 完全相同。R7 已证明该旧实现的 float32 前缀/积分累加会因消减误差产生负/零阈值和额外 hit。R14 虽声明源码提交晚于 R7 修复，实际运行二进制却是修复前产物；R16 记录了修复路径的方向重算诊断。R14 与 R16 的跨版本 CFAR / task-performance 数值不能直接比较。
- **阶段停止条件：** 已用 R7 pre-fix manifest 的相同 SHA 定位 R14 实际 executable 身份，并用 R7 根因报告和 R16 数值诊断交叉核对；R14 原始功率/阈值图已清理，无法逐 CUT 复算其错误门限数。离线审计到此停止，不补跑 CUDA。

R17 的状态为：`IMPLEMENTED` 不适用（未改算法/软件）；`EXECUTED` 已完成仓库、manifest、XML、源码与历史证据审计；`EVALUABLE` executable 身份差异及其与 GO-CFAR 已知数值缺陷的关联可评估，R14 每个 CUT 的具体门限错误数不可评估；`PERFORMANCE_VERIFIED=false`。本次没有新的 GMTI 性能运行。

## 最近完成的任务与关键结论

- R16 在本机 NVIDIA GeForce RTX 3050 Laptop、`USE_COREX=OFF` Native CUDA 路径运行。连续面积杂波为 390 packets / 1,952,655 samples，测量平均功率 0.0234967；27 张 final-union CFAR map/geometry audit 全部通过。
- 同运行 C0/C1 共用 Stage2 inputs 与 F1/F2 tap。C1 ON/TO 各正确检测 1/3（C0 为 3/3）；C1 H0 active-support cancellation −2.453 dB（C0 +5.582 dB），TO target-only retention −18.223 dB（C0 −0.671 dB）。这些单 seed 数字不是统计显著性或 Pfa 结论。
- R14 的三期 C0 full-band CFAR hits 为 14,243 / 17,628 / 16,253，dynamic-band 为 18,982 / 16,158 / 19,297；R16 对应为 131 / 150 / 147 与 137 / 129 / 131。每图 valid CUT 数均为 527,280，GO、guard=4、background=16、alpha=13.44782257、几何一致。
- R14 的 Production binary SHA `f65a8aa446754cc79b6417f9275b3a01e73eb85098c6a4f9eaea0cb999c38962`，等于 [R7 pre-fix manifest](phase_r/evidence/r7_cfar_correctness_20261022/manifests/pre_fix_experiment_manifest.json) 中的 binary SHA。R7 数值根因报告记录旧版在独立配对图上产生负/零门限及错误额外 hit；该实现后来由 `04958a996f6a1449fa3d9f158a4765493c85038d` 修复，而该提交是 R14/R16 声明源码提交的祖先。
- R16 Production binary SHA 为 `ae41c79ee48b530fc71e07b4ade7dcf7095d2ae9e9ee388f0e5ed9943a8fca4f`，R16 build manifest 记录 Release Native CUDA 构建；R16 `cfar_numerical_diagnostics.csv` 记录每期约 1.55M 个 GO 方向直接重算。R14 未保留构建命令、历史 CMake cache / 编译器身份和 CFAR power/threshold map，故不能追算其 per-CUT 错误 hit 数，也不能确定旧 binary 被复用的具体构建环节。
- R18 在 seed606/707/808、6.7/0.5 m/s 与 30/20 dB 的 12 个零时延场景完成 252 个 Native CUDA Production branches。连续面积杂波、输入 causal triplet、valid-CUT geometry/分母与 union-hit gate 均通过；每个方法/速度/SNR 组合的 truth-period 检测观察为 9/9。经验 Pfa、PK/PKR residual closure、target-only 幅相/SCNR、ID-switch lineage 和 radial velocity 未闭合，故不报告性能胜负、显著性或未知误差收益。seed909 C3 参考因预注册 exact-support gate 不通过而停止评价。详见 [R18 任务结果](phase_r/R18/06_signal_and_task_results.md)与[方向裁决](phase_r/R18/09_next_research_direction.md)。
- R14/R15 所记录的检测、候选、航迹与反事实仍是当时真实运行的历史观察，但建立在修复前 CFAR executable 的输入集合上；不得将其直接当作修复后 Production 的 C2/C3 任务表现。R15 回放复用 R14 检测，也继承该限定。R16 同运行 C0/C1 的比较仍是同一 executable 内的冻结场景结果。
- R14/R16 的零时延结果不回答未知系统误差因果效应；AI 必要性仍未建立，也没有证据把低速目标或复杂杂波升为优先长期方向。

## 已停止或未成立的研究假设

- 停止把修复前 R14/R15 检测集上的单次 track switch 当作修复后 Production 通用行为或 Robust DDC 独立算法创新证据。
- 固定 Gamma=1 C1 在 R16 场景中目标漏检及杂波残余代价较大；停止将该固定参数方法视为该场景的可接受整体替代，但不外推至所有盲确定性估计器。
- R14/R15/R16 的零时延证据不支持未知系统失配的因果结论，也不能据此否定该方向。
- 当前证据没有建立 AI 的必要性，也没有证明低速目标或复杂杂波已经成为优先研究瓶颈。

## 尚未解决的主要问题

- P1/P2 时延估计使用配对 target-free OFF 观测；目标存在时、仅用部署可得观测的估计能力尚未验证。
- C4、PKR、P2 Mode-B 有 49 个 robust-support 方法级不可评价单元，覆盖率应与任务结果共同报告。
- V3.2 target-present screening 没有匹配的独立 H0 条件，当前 false-CUT 只能作描述，系统 Pfa 不可评价。
- 参数 RMSE 只统计成功估计机会，径向速度无有效 seed；目标专属复数幅相保持尚未建立。
- CSI tap 强功率 ROI 不是最终 SCNR；该指标与检测收益之间的物理因果链仍未闭合。
- 现有仿真只覆盖冻结的连续面积杂波模型，尚未证明复杂非平稳杂波是主要 GMTI 瓶颈。

## 当前唯一优先任务与阶段门

- **当前唯一优先任务：** R22-V4 output-complete 确认性契约草案已形成，等待 ChatGPT 独立审查与用户决策。
- **契约状态：** `DRAFT_NOT_FROZEN`。拟议范围为30个预留 seed × 3个速度 × SNR20 × 0/+4 ns（180个 target-present case），加30个全新 H0 seed × 2个时延（60个 case）。主要对比为周期因果 M1 对 Frozen Fixed D3 的全周期共同成功位置误差；Known 只作 evaluator-only 上限，batch-10 M1 排除在线排名。M1 每周期只读当前和过去 ON C1/C2，估计失败显式零校正并纳入全策略分母。
- **冻结前置门：** 工程 MDE、检测覆盖率非劣界值、因果估计器和全周期输出实现/源码审计、最终 seed 碰撞核查、用户授权的完整输出尺寸 pilot 均未完成。对应契约见 [R22-V4 草案](phase_r/R22/31_v4_causal_output_complete_confirmatory_contract_draft_20261113.md)。
- **停止条件：** 不把 V3.3 screening 差值用作工程门槛；不把共同成功 RMSE 单独解释成完整任务收益；不以 CUT 分数代替 H0 beam-period false-alarm 指标；不自动启动 AI、Router、Dense 或 CUDA 矩阵；研究总纲正文仍受用户明确审批保护。
- **后续任务授权：** 用户本次授权的是契约草案与审查材料；尚未授权实现、CUDA pilot 或完整矩阵。`CUDA_AUTHORIZED=false`，`AI_TRAINING=false`。

## 总纲修订讨论

R17 对总纲第 9 节的恢复仅限当时用户明确批准范围。R18 执行、清理、阶段报告和方向建议均未改动研究总纲；其 blob 仍为 e637e3769bde7814298ff012d0f42301884e2985。未来如有新证据提示总纲其他内容需要修改，应先在动态状态或单独提案中列明证据、拟改范围及对既有成果的影响，并等待用户对具体内容或清晰范围明确批准。
