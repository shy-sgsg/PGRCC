# R3：评价流水线审计与最小修复

审计基线为 `1dcea9250c3cbe8655723a210e29638d11d6c095`，其上有未提交的 R3 源码/测试差异；本文不把该工作树差异说成已提交代码，也不把旧运行结果归因到当前差异。结论：评价定义和诊断记录有可定位的软件缺陷，已做最小修复并完成软件验证；历史 production pilot 的关键门控、残差、CUT 与航迹证据仍不完整，因此当前没有新的 production 性能结论，AI 必要性仍未建立。

## 身份与证据边界

| 证据 | 源码身份与运行状态 | 本文用途 |
|---|---|---|
| Production Hierarchical Calibration formal pilot：`docs/evidence/equivalent_vs_physical_pilot/` | manifest 标记 clean CUDA formal，源提交 `6fa1a5f92c210c499a6d0112c05d065bb7f06dc9`；12 cases 均 `completed_with_gaps`，`algorithmic_results_claimed=false` | 说明历史评估缺口，不是当前 R3 修复后的运行 |
| 4 个 `.scratch/task5_clean_gpu_*` 定向 CUDA 检查：`task5_clean_gpu_pathcheck`、`task5_clean_gpu_after_compact_0ns`、`task5_clean_gpu_after_gamma_fix_0ns`、`task5_clean_gpu_gamma_fix_pathcheck` | 2026-09-28/29；均为 seed 101、目标速度标量 6.7 m/s、SNR 30 dB、3 periods；各跑 0 ns 或 −2/0/+2 ns。源码提交为 `f928e91…` 或 `9d24b21…`；后两次 `9d24b21…` run 另带 dirty runner/test snapshot。四份 manifest 均为 `completed_with_gaps`、`algorithmic_results_claimed=false`、analysis `NOT_EVALUABLE` | 补充说明已有路径检查，不是 R3 当前差异后的复跑；残差闭合缺失/缺 PKR tap，empirical Pfa、waterfall、ID-switch 均不可评估；不能充当方法性能证据 |
| GO-CFAR 分母闭合：`outputs/unknown_system_error_pfa_closure_20260914/` | 独立合成 IID/结构杂波 CUT 试验，manifest 源提交 `5c5a6a498dc429023bde209495bc8081b8846455`；CPU/合成几何，不是 production CUDA | 验证定义和分母流程，不替代生产链结果 |
| `.scratch/task5_targeted_gpu_zero_retry/` | 外层 manifest 为 `not_started`、`cases=[]`，但目录实际含 21 份 branch manifest 和生产日志；详见下文 | 属于 2026-09-22、R3 前且源码 dirty 的单 case 部分运行；不能当作 R3 修复后验证或完整性能结论 |
| 当前 R3 工作树 | 基线 `1dcea925…` 上 9 个相关 tracked 源码/测试文件有差异；此前全 Python 与 CTest 验证针对该差异 | 说明实现与软件验证状态；差异后没有新的 CUDA production 重跑 |

## `task5_targeted_gpu_zero_retry` 分支级复核

该目录的外层 `manifest.json` 创建于 2026-09-22，写着 `status=not_started`、`cases=[]`；但同目录实际有 21 份 branch manifest、21 份 `gmt_pipe_core.log` 和 17 份 `cfar_geometry_diagnostics.csv`。因此外层状态不能单独代表目录内没有运行。该单 case 选择为 base、seed 101、目标速度标量 6.7 m/s、SNR 30 dB、delay 0 ns、3 periods；源码提交 `9be5882…` 且 tracked 工作树 dirty（runner、analyzer、相关测试有改动），早于当前 R3 基线/修复。

21 个分支记录中 17 个 `production_status=passed`，4 个为 `failed`（`C4_modea_OFF`、`C4_modeb_ON`、`PKR_modea_OFF`、`PKR_modeb_ON`）。4 个失败日志都到达 `stage=p38_csi` 后触发扫描门控 `valid=0/1 required=1 min_ratio=0.95`；不得将此解释为算法性能失败。通过分支的日志记录 `channel_count=4`、`four_channel_fusion=enabled`、`new_protocol_gpu_preprocess=enabled`，并留有三周期生产执行输出；例如 `C2_modeb_ON` 三周期均成功。故这是一份真实但部分失败、非 R3 后的 production-path 运行，不是单纯准备目录，也不是修复后的验证。

该次分析产物把全部 17 个通过分支的 CFAR geometry 标成 `NOT_EVALUABLE`、汇总分母 0，原因均为 `cut_band_end must be non-negative`。抽查原始 CSV 可见 `union_dynamic_in_band` 行保持在结构体默认值（`height=0,width=0,cut_band_end=-1`），而同周期 `union_full_band` 行有实际计数；例如 `C2_modeb_OFF` 的 period 0 / beam 50 行记录 `valid_cut_count=527280`、`hit_cut_count=17957`。日志同时显示 `union` 模式实际 dynamic interval 为 `[46,84]`，所以该零尺寸行不能解释成“本来禁用的空分支”。

源码对照解释了这组产物：旧运行提交 `9be5882…` 的 normal 与 fusion-cache 路径均先后两次调用会 `emplace_back()` 并返回 `&back()` 的单行 helper；第二次 vector 扩容可能使第一次返回的指针失效。旧 CSV 中 dynamic 行未被填充、full-band 行已填充，与这一指针生命周期缺陷相符。当前 R3 工作树的 C++ 改动改为先追加一对记录，再按索引取得两条记录的地址，并有 C++ selftest；这支持“修复已实现并通过软件回归”，但尚未由修复后 CUDA production run 验证。Python 校验器对旧 CSV 的 `cut_band_end=-1` 报错是正确的 fail-closed 行为，不是需要放宽的解析缺陷。

保留下来的 `union_full_band` 数值只支持该几何分支的描述性 hit fraction；不能据此重建最终 `max(dynamic_hits, full_hits)` detector 的唯一 hit union，也不能称 empirical Pfa。该 target-off 分支汇总还记录 406 个 cluster/production detection 行和 146 个 confirmed/output track IDs，但这是单 case 产物计数，不等于已完成因果或性能验收。四个失败分支没有 CFAR geometry CSV。整组材料不能并入 `6fa1a5f…` 的 12-case formal pilot，也不能充当 R3 修复后 CUDA 验证或 AI 收益证据。原 scratch 产物保留在工作树，审查 ZIP 只打包上述摘要，不复制原始日志和 IQ。

旧 pilot 报告和 manifest 保持原样，以保留其当时的代码身份与结论。特别是旧报告将 `hit_cut_count / valid_cut_count` 称为 “Cell Pfa”（`docs/evidence/equivalent_vs_physical_pilot/report.md:6`）；按当前指标定义，这只能是测试 CUT 命中比例，缺少 H0/false-hit 定义时不能称 empirical Pfa。此处是新审计中的 framing correction，不是对旧历史结果的回写。

## 关键发现与修复

### 1. CFAR 诊断指针生命周期：已修复的软件缺陷

`split` 和 `union` 检测分支在同一个 `std::vector<CfarGeometryDiagnostics>` 中先后追加两行。旧写法在追加第一行后立即保存 `back()` 指针，再追加第二行；若 vector 扩容，第一根指针失效。当前实现先追加一对并取得行索引，完成两次扩容后才取得两根指针；normal 与 fusion-cache 两条处理路径均已使用该方式（`include/cfar_geometry.hpp:115-129`；`src/processOnePeriod.cpp:2652-2679,2684-2707,4228-4294,4300-4320`）。

修复只改变诊断行的分配和引用方式，没有改 CFAR 阈值、CUT 选择、split/union 融合或 Production Current 的目标选择逻辑。C++ selftest 覆盖配对行字段保存，Python schema tests 覆盖行校验和零有效 CUT；Release 构建与 23 个 CTest 通过。修复后的生产诊断尚未用新 CUDA run 验证，所以不能据此断言旧诊断已恢复或检测性能改变。

### 2. CUT 命中比例与 empirical Pfa：定义已拆分

几何计数器把 `threshold_test_count`、排除区/频带规则后的 `valid_cut_count` 分开；CUDA kernel 将实测命中数和命中索引 hash 写回诊断行（`include/cfar_geometry.hpp:14-75`；`src/gpu/gpu_kernels.cu:2694-2725`）。每行 `hit_cut_count / valid_cut_count` 是该几何分支中实际测试 CUT 的 hit fraction；C++ 输出对零分母写 `NA,NOT_EVALUABLE`，并将 false CUT/cluster/track 计数留为 `NOT_EVALUABLE`，等待真值关联（`src/processOnePeriod.cpp:322-357`）。

当前 pilot analyzer 明确保留 `tested_cut_hit_fraction`，只在有 truth-filtered `false_cut_hit_count` 和有效 H0 分母 `valid_h0_cut_count` 时才计算 empirical cell Pfa；分母缺失、非正或计数越界均为 `NOT_EVALUABLE`（`scripts/analyze_production_hierarchical_calibration_pilot.py:51-98`）。这阻止了用 configured Pfa 或全部 hit/valid 比值冒充实测 Pfa。`scripts/cfar_geometry_audit.py:62-122` 同时验证字段完整性、计数范围并逐行保留 geometry branch 身份。

分支计数不能在不检查重叠的情况下当作最终 detector 的唯一 hits/CUTs：当前 `union` 对同一像素取两个 CFAR 分支 hit 的 `max`，split 路径也有独立分支及边界处理（`src/processOnePeriod.cpp:2684-2707,2815-2920`；配置当前为 `split`，见 `config/gmti.xml:111-115`）。因此 delay-stage target-off 汇总现会保留各 branch 的分数；多个 detector branch 或重复 `(period, beam, branch)` 没有唯一 hit union 时，聚合状态为 `NOT_EVALUABLE`，而不是把分支计数相加成最终输出率（`scripts/run_delay_stage1_formal.py:715-829`）。覆盖该情形的回归测试见 `tests/test_delay_stage1_contract.py:202-282`。

`aggregate_branch_cfar_geometry()` 对一条 production run 中导出的各行仍生成 `tested_cut_hit_fraction` 汇总，但 `cell_pfa` 固定为空且 `pfa_status=NOT_EVALUABLE`（`scripts/run_production_hierarchical_calibration_pilot.py:393-449`）。若运行含多个重叠 detector branch，这个 pooled 值只可解释为跨 branch CUT tests 的描述性比例，不能解释为唯一最终检测 hit fraction 或 Pfa；branch 原始行仍保留供审计。主评估的目标关闭 false-alarm waterfall 必须经唯一 branch/union 门控后才可作为最终 detector 统计。

另有一项不同来源的合成分母闭合：H0 使用 3 个 seed、每 seed 1,000,000 个独立 CUT，合计 3,000,000 valid CUT、4 hits，估计为 `1.33e-6`，Wilson 95% CI 为 `[5.19e-7, 3.43e-6]`；置信区间仍较宽，不能宣称已统计证明达到 `1e-6`。H1–H5 记录的是结构杂波 false-hit fraction，不是 Pfa。输入是独立合成控制，不含真实 IQ/完整生产链，故只支持指标闭合和 synthetic GO-CFAR 行为检查（`outputs/unknown_system_error_pfa_closure_20260914/manifest.json`、`pfa_aggregate.csv`）。该结果不能与上述 `6fa1a5f…` 的 12 个 CUDA pilot case 合并。

### 3. A0/PK 与 PK/PKR：物理闭合和额外对消分开

当前 closure 定义：A0 与 PK 比较物理校正正确性；PKR 必须存在且功率有限，`PK residual − PKR residual` 单独报告额外抑制。只有 A0–PK 差值用于 physical-correction closure，不再要求 PKR 残余等于 A0/PK；因此额外对消不会因偏离 A0 被误判为物理校正失败（`scripts/analyze_production_hierarchical_calibration_pilot.py:144-204`）。实际计算只读 production CSI tap 的强杂波 ROI，并校验 branch 唯一性、每个 period/beam 唯一性、正且有限的功率；缺 tap 或行无效时闭合仍为 `NOT_EVALUABLE`（同文件 `:207-319`）。

历史 12-case manifest 中，A0 和 PK 的记录不能补齐 PKR 的 `csi_metric_tap_summary.csv`；逐 case closure 明确报 `csi_metric_tap_summary_missing:PKR`，`actual_production_artifacts=false`。因此既不能声称 PKR 没有额外收益，也不能基于现存产物闭合 A0–PK 后就给整个三条件对照判定通过（`docs/evidence/equivalent_vs_physical_pilot/manifest.json:160-184`）。

### 4. Mode-B 反事实可比性与 truth leakage

Mode-A 从 OFF 估计并固定应用于 ON/TO；Mode-B 从 ON 估计，随后用同一 ON estimator artifact 应用于 OFF/TO。当前 runner 按 mode/role 核对 execution contract、`reference_source`、无 Current fallback、估计调用标记和实际 reference path；任何缺失或不匹配都会阻断 causal metric（`scripts/run_production_hierarchical_calibration_pilot.py:228-289,1972-2020`）。相同 ref 与错误 Mode-B OFF control 的通过/失败回归见 `tests/test_production_hierarchical_calibration_pilot.py:312-400`。此门控是必要的，因为 ON 与 OFF 用不同处理算子会把算子差异混进目标因果差。

估计器数据契约中 truth、目标位置、注入误差标签和已知误差均禁止作为估计输入；adapter 每行必须带合法 truth marker，标记为 true、缺失或无效均不能通过，不回退到 Current（`scripts/run_production_hierarchical_calibration_pilot.py:191-225,292-389`；对应缺失/true marker 测试 `tests/test_production_hierarchical_calibration_pilot.py:428-462`）。Known-error 条件限定为 evaluator-only，估计器输入角色为空（同文件 `:121-145` 配置与 `:228-289` contract）。这是当前代码/adapter provenance contract 的证据，不等于已证明历史外部产物中从未泄漏；修复后尚无新 production artifact 供逐 case 核验。

历史 formal run 的 ON−OFF−TO 输入因果 triplet gate 均为 `passed`，这只说明配对输入关系成立。它不单独证明同一算子处理后的目标保持，也不证明检测/Pd/航迹因果效果（`docs/evidence/equivalent_vs_physical_pilot/report.md:15-18`）。当前 analyzer 分开检查 ON−OFF 与 TO 指标以及 processing comparability（`scripts/analyze_production_hierarchical_calibration_pilot.py:320-349,461-507`）。

### 5. CFAR→cluster→protocol→TrackManager/PIPE 下游证据

runner 分开收集 CFAR geometry、detection CSV、`track_frames.csv`、association audit、track state 和 payload（`scripts/run_production_hierarchical_calibration_pilot.py:922-944`）；analyzer 不用 cluster 或协议行数替代 CUT hit，也要求 track 关联/state/payload provenance。若无法从现有 debug 判定 ID switch，应显式标 `NOT_IDENTIFIABLE_FROM_CURRENT_DEBUG`；缺少 provenance 本身则为 `NOT_EVALUABLE`（`scripts/analyze_production_hierarchical_calibration_pilot.py:358-425,428-547`）。正式复核应继续复用生产 `TrackManager` 的 `track_debug` 和 payload audit，并反查到当前周期 `Confirmed + matched_this_frame` 的 detection；不能用简化 tracker 代替生产证据。

历史报告显示四层 false-alarm waterfall 未闭合，ID-switch 为 `NOT_EVALUABLE`，原因 `track_provenance_missing_or_empty`。所以不能从 CFAR 或 detection 文件存在推导 track/PIPE 成功，也不能宣称误关联或 ID-switch 的算法变化（`docs/evidence/equivalent_vs_physical_pilot/report.md:8-10,18,29-31`；manifest `:536-567`）。

## 历史 production pilot 复核

正式历史矩阵包括 12 个 CUDA case：delay error `−4/+4 ns`、seed `101/202/303`、名义目标速度 `6.7/12.0 m/s`、SNR `30 dB`、每 case 5 periods。12 个 runner 返回 0，但 12 个 case 均 `completed_with_gaps` 且 `algorithmic_results_claimed=false`。C1/C2/C3 汇总有 180 条可评估 Gamma reference rows；C4/P2/PKR 的 72 条 reference rows 因 quality gate `valid=0/1 required=1 min_ratio=0.95` 不可评估（`docs/evidence/equivalent_vs_physical_pilot/report.md:13-18`；manifest `:1-45`）。

当前工作区另外保留四个较早的定向 CUDA path-check manifests（上表列出）。它们覆盖 0 ns 单 case 和 −2/0/+2 ns 单 seed 小矩阵，状态同样是 `completed_with_gaps`，并没有补齐 PKR tap、经验 Pfa、虚警 waterfall 或航迹 provenance。它们的源码基线早于当前 R3 工作树差异，且只有一个 seed；故仅作为故障定位历史，不与 12-case formal pilot 或 R3 修复后未来运行合并。

该 `valid=0/1` 来自扫描波束融合质量门：worker 每波束失败会清空对应缓存槽，扫描聚合按 `ceil(fusion_min_valid_beam_ratio × beam_count)` 计算最低成功数；不满足时扫描失败（`src/processPeriodsParallel.cpp:429-485`）。它是 scan-beam 处理成功数，不是 CFAR 有效 CUT 数，也不是校准器误差或检测率。现存 compact report 只有门控日志，没有足够逐波束 stage/log 证据判定究竟是输入、某一生产子阶段还是运行时配置造成失败。故分类为“运行/输入根因尚未查明”，不将其当方法失败，也不通过降低 0.95 门槛制造 evaluability。

每个历史 case 的 ON/OFF/TO 输入 triplet 通过，但 A0/PK/PKR closure 缺 PKR CSI tap；生产 valid-CUT denominator 和四层 waterfall 不足；ID-switch provenance 缺失。以上缺口分别限制物理校正归因、经验 false-alarm 评价和航迹评价，不应合并成一个“算法失败”结论（`docs/evidence/equivalent_vs_physical_pilot/report.md:15-18`）。

## Velocity/yaw compact blind-estimator pilots：补充数据完整性与源码身份审计

Velocity study 的 manifest 记录 108 个 Stage2 case 全部完成、108/108 `FALLBACK_MODEL_MISMATCH`，`core_rows=0`；yaw study 记录 240 个 Stage2 case 全部完成、240/240 同类 fallback，`core_skipped=true`。两者都不是 production CSI→CFAR→TrackManager/PIPE 端到端性能证据。零误差/基线也回退，故单看 fallback 不能证明未知误差难以估计，更不能证明 AI 有优势。

Yaw compact manifest 的 240 条记录含 240 个不同的 `off_raw` 路径字符串，但只有 2 个不同的 `off_raw_sha256`：seed 101 的 120 条均为 `acb63e4b…d227d`，seed 202 的 120 条均为 `e948a0dd…f72b`。每个 seed 内，哈希在 target-free/moving-target、baseline/servo/yaw、误差值、纹理和距离变化下保持不变。原始 `cases/` 目录已不存在；因此目前不能从字节重算哈希，也不能查看逐 case `scenario.run.json`、resolved scenario、日志或 XML。此处是 manifest 可见的输入完整性异常，不是已经查明的“注入未生效”结论。

进一步按 compact manifest 核对 moving-target 的 120 组配对记录：120/120 的 `on_raw_sha256` 均不同于各自 `off_raw_sha256`；ON 共 36 种哈希（seed 101、202 各 18 种）。按 condition 汇总，baseline/servo/yaw 分别有 4/20/20 种 ON 哈希。该变化与 ON 含目标回波相容，但不能单独证明背景杂波随配置变化，也不能证明 yaw/servo/纹理/距离字段实际到达回波生成；因为逐 case IQ、resolved 配置及原始日志已缺失，当前只可缩小“整批 ON 都是同一字节内容”的解释范围，不能据此确认根因或估计器性能。

静态代码显示设计上每个 case 使用 `case_<index>` 独立目录，runner 为该 case 生成唯一 output path，并分别更新 seed、scene range、clutter texture、平台 heading 和 servo error；Stage2 JSON parser 有对应字段读取路径，生成 clutter 时将 `trueServoAzimuth(...)` 传入连续面积杂波模型（`scripts/run_yaw_error_study.py:357-430,655-708`；`simulator/stage2_statistical_sim/stage2_config.cpp:1464-1504,1531-1565`；`simulator/stage2_statistical_sim/simulate_stage2_main.cpp:786-814,1163-1214`）。这些代码证明预期数据流，不证明已删除的历史运行时文件实际含有对应值。

对照保留的 `prepared_template.json` 与当前 Stage2 生成链，预期配置为 `scene_mode=full`、area enabled、`continuous_texture`、`raw_lfm`；Stage2 仅在 scene mode、area 开关和 continuous model 同时满足时启用该路径，并在每个 packet 调用 `addContinuousAreaClutter()`（`outputs/yaw_error_formal_compact_20260915/prepared_template.json:4,124-145`；`simulator/stage2_statistical_sim/simulate_stage2_main.cpp:828-845,1206-1217`）。杂波 cell 只有通过 range/visibility/sample-window 检查才计入 `injected`；当 `injected>0` 时才增加 continuous-area packet 与 raw-LFM packet 计数。`continuous_area_samples` 实际累计的是通过检查的 cell 数，不是 IQ waveform sample 数（`simulator/stage2_statistical_sim/stage2_lfm_forward.cpp:800-816,868-892`）。Stage2 log/report 会输出这些计数（`simulate_stage2_main.cpp:1637-1645`；`stage2_validator.cpp:108-116`），但 yaw compact manifest 没有保存它们，历史 `stage2.log`、report 和 per-case resolved config 均已缺失。因此当前能确认“源代码预期启用并存在可判别计数”，不能确认历史每个 OFF 是否实际注入了 area clutter；重复 OFF hash 仍无法区分零 cell 注入、运行配置未到达生成器、产物/hash 路径问题或其他原因。

该 pilot 的来源记录还存在独立的 reproducibility 缺口：manifest 将 `source_commit_before_run` 记为 `478bb9c…`、tracked status 记为空/clean；但 yaw runner 是在 `d722a5c…`（其 parent 正是 `478bb9c…`）才首次纳入 Git，提交时间为 2026-09-15 12:25:28。现存 runner 文件时间为 12:19，manifest 时间为 12:22；随报告记录的正式运行命令没有 `--source-commit` 或 `--worktree-dirty-before` override。manifest 生成代码仅调用 `git status --short --untracked-files=no`，所以未跟踪 runner 不会反映在 clean 标志中（`scripts/run_yaw_error_study.py:810-835,879-916`）。这与“run 时 HEAD 为 478bb，runner 尚未入库”的历史顺序一致；但没有 runner 源 hash 或原始运行目录，无法证明运行时 Python 文件与后来 `d722a5c` 入库版本逐字节一致。

manifest 记录的 simulator SHA-256 与当前保留的 `build/simulate_stage2_statistical` 相同（`5dd0ac4e…b9b`），但二进制 mtime 为 11:07，早于 `478bb9c` 提交时间 12:11；没有构建 manifest 将该二进制绑定到具体源码树。现有证据因而锁定了二进制身份，却未完全锁定其构建源码身份。报告中列出的 240 个文件路径均不同，降低了“manifest 单纯重复同一路径”的可能性；但原始字节已清理，不能排除当年 runner 哈希/路径解析差异、运行配置未到达回波生成、或其他模拟器行为。

**判定与后续边界：** 将这组历史 yaw 结果限定为“历史 manifest 报告了 240 次 Stage2 成功和 fallback，但输入字节与 runner provenance 未完成独立核验”；估计器有效性及扰动响应暂为 `INCONCLUSIVE`。下一步可以在新方向冻结后，用保留二进制/明确源码构建和新的独立输出目录做最小 no-error 与单个非零扰动输入传播审计，保留每 case 输入 JSON、resolved JSON/XML、日志、raw size/SHA 与生成配置快照；本审计本身不授权启动该运行，也不回写/覆盖旧结果。

## 四条件 raw-IQ Stage2 baseline pilot：面积杂波未注入

`outputs/unknown_system_error_pilot_20260913_v5/manifest.json` 记录四条件相位残差：Ideal/No-error `0`、Current+unknown-error `0.2004197259 rad`、Known-error correction upper bound `9.87e-9 rad`、Estimated-error correction `1.01e-8 rad`，方向归一化恢复率约 `0.9999999991`。该指标是 `phase_residual_to_ideal_rad`（越低越好）；manifest 中 `*_minus_current` 字段保存的是按指标方向归一化后的恢复量，不应按普通有符号减法误读。Estimated 条件使用配对无误差参考，虽未直接把真值交给估计器，但不是 operational blind 估计器。

这组结果的物理场景不含预期地面面积杂波：四个 Stage2 输入配置均为平台高 `6000 m`、地面高 `0 m`、斜距窗 `[2500,5000) m`、area enabled 且 `continuous_texture`。Stage2 `fixedPointGeometry()` 令 `range_m=norm(target-platform)`；地面单元斜距至少为 `6000 m`。`addContinuousAreaClutter()` 又拒绝 `range >= scene.range_max_m`，因此该窗不可达。四份 raw Stage2 报告也分别记录 `continuous_area_packets=0`、`continuous_area_samples=0`、`continuous_area_raw_lfm_packets=0`；噪声开启，ON 输入有目标注入，但无面积杂波。配置与报告见 `outputs/unknown_system_error_pilot_20260913_v5/configs/*.run.json` 和 `raw/{ideal_off,unknown_off,ideal_on,current_unknown}/reports/stage2_simulation_report.md`；源码见 `simulator/stage2_statistical_sim/stage2_lfm_forward.cpp:97-124,800-816`。

因此近 1 的恢复率只验证了这个单 seed、小规模、配对参考下的确定性 baseline-phase 校正计算；它不验证复杂杂波条件的通道相干性恢复、CSI 杂波对消、检测/虚警/航迹收益、生产 CUDA 表现或 AI 必要性。2026-09-14 的 `unknown_system_error_pilot_20260914_clean` 报告重复同一平台/斜距冲突和零注入计数，并非修正后的 clutter-valid 重跑。独立 servo pilot 的有效斜距控制（高 `6000 m`、地面斜距窗 `5000–12000 m`）有 `650` 个 area packets 和 `5,640,524` 个通过检查的 cell；它表明该生成路径在可达范围下能注入杂波，但不是四条件 pilot 的等价复验。原始 IQ 不纳入外审包。

## 状态总表

本文使用四个互不替代的标签：`IMPLEMENTED` 表示代码/契约存在；`EXECUTED` 表示对应软件测试或运行命令实际执行；`EVALUABLE` 表示必要分母、配对条件与 provenance 齐备；`PERFORMANCE_VERIFIED` 表示在规定输入和生产链路上有可归因的结果。

| 对象 | IMPLEMENTED | EXECUTED | EVALUABLE | PERFORMANCE_VERIFIED |
|---|---|---|---|---|
| CFAR 配对 geometry diagnostic 指针修复 | 是 | Release build、selftest/CTest | 软件回归可评估；修复后 CUDA geometry 产物未验证 | 否 |
| hit fraction 与 empirical Pfa 分离 | 是；H0 分母/false-hit 缺失时 fail-closed | Python tests；另有独立合成 H0 denominator run | 合成 H0 可按其定义计算；历史 production Pfa 不可评估 | 不能据此宣称 production Pfa/算法收益 |
| A0/PK/PKR residual closure | 是；A0–PK 与 PK–PKR 分开 | Python tests；历史 CUDA pilot 已运行 | 历史 pilot 不可评估，PKR CSI tap 缺失 | 否 |
| Mode-B ON/OFF/TO comparability 与 truth marker | 是；contract 与 fail-closed 校验 | Python tests | 测试夹具可评估；修复后 production comparison 未运行 | 否 |
| C4/P2/PKR formal branches | 旧实现已执行 12-case matrix | 是，历史 CUDA source `6fa1a5f…` | 72 Gamma rows 因 scan gate 不可评估 | 否 |
| 目标检测/航迹/ID-switch 因果结果 | 收集和分析路径已实现 | 历史 pilot 有部分 downstream files | waterfall、ID-switch provenance 未齐 | 否 |
| 整体 R3 后的 production 效果 | Current pipeline 未改算法阈值；diagnostic/evaluator 已修 | 尚无修复后 production CUDA run | 否 | 否 |

软件验证快照：`python3 -m pytest -q` 为 393 passed、18 skipped；Release 构建成功，CTest 23/23 通过；相关 Python `py_compile` 和 `git diff --check` 通过。未在修复后运行新的 CUDA production 实验。该验证支持软件实现与回归契约，不支持算法性能判断。

## R3 结论与最小后续条件

1. 不改 scan-beam `fusion_min_valid_beam_ratio`、CFAR threshold、minimum support 或其他性能门槛。下一次已注册 pilot 必须保留逐 beam 失败 stage、实际 XML/runtime config 和完整日志，以定位 `valid=0/1` 的子阶段根因；没有有效输入就停止该 case 并记 `NOT_EVALUABLE`。
2. 若继续单误差 production 因果链，必须为 A0、PK、PKR 都留存可审计 CSI tap；报告分别给 A0–PK 物理闭合与 PK–PKR 额外抑制。缺 PKR 时保持对应比较不可评估。
3. CFAR 需保留每 branch、每 period/beam 的 `threshold_test_count`、`valid_cut_count`、`hit_cut_count` 和 hash；若检测模式多 branch，最终 detector hit/CUT 必须用唯一 hit union，不能将分支计数相加。Empirical Pfa 另需适用 H0/false-hit 定义、真实 valid H0 分母和置信区间；名义 `1e-6` 不等于已验证 `1e-6`。
4. 目标保护比较要同时有配对 OFF/ON/TO 和同一处理参考；生产 TrackManager 验证需保留 association/state/payload，并从 PIPE 载荷反查当前周期已确认且本周期命中的 detection。
5. 当前修复之后未运行 CUDA production。依据 Phase R 顺序，先完成并冻结 `05_experiment_design.md`，再做最小 pilot；不得把历史 clean-source run 或 scratch 目录中的旧运行改标为修复后的结果。

**R3 判定：** 评价软件存在的定义/聚合/诊断问题已有最小代码修复并通过软件测试；历史实验中未修复前的证据仍有输入和产物缺口。算法收益 `NOT_EVALUABLE`，并非算法失败；生产性能 `PERFORMANCE_VERIFIED = false`。不支持也不否定 Physics-AI，AI 必要性仍未建立。
