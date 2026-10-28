# R22-05：资源与执行记录

## 已完成的设备运行

seed900203 使用本地 Native CUDA：NVIDIA GeForce RTX 3050 Laptop GPU，运行前记录 P8、约 6.43 W、48°C、40% GPU utilization、SM 210 MHz。Stage2 与 Production 可执行文件来自 `/tmp/pgrcc-r21-native-cuda-build`，SHA-256 分别为：

- Stage2 `simulate_stage2_statistical`: `5dd0ac4e2536b8bcf2508cc53db8a30780eba75f40c025e13ffaa930f8ba7b9b`
- Production `GMTI_pipe_core`: `9ffc232966bd92a39edfb39b90730851014dfa1ae7237a4ac172a764f31cc566`，`USE_COREX=OFF`

manifest 记录 seed900203 Stage2 总耗时约 19.13 s、Production 分支耗时总计约 52.31 s；这些是本机单次 elapsed，不是性能基准。seed900101 smoke 的 Stage2 杂波生成为 2,600 packets / 13,195,469 samples，Production elapsed 约 10.92 s。

## 磁盘

- R22 开始时本地项目约 6.7 GB，低于 20 GB 上限。
- `seed900203_closure` 紧凑证据包 952 个文件，约 39.9 MB；raw IQ 和可再生大数组未归档。
- seed900202 紧凑证据约 11.1 MB；seed900101 preflight 保留配置、manifest、生产日志与诊断。
- 临时运行目录只有在紧凑证据逐项 SHA 验证完成后才按 [清理台账](evidence/cleanup_ledger.md) 删除。

## 完整矩阵未估算为可执行计划

初始草案至少包含 522 个主场景 seed-condition，尚未包括 H0、方法/角色分支与桥接重复。单个多目标 case 的 Production 数量依方法模式和 role 数变化；不能用 seed900203 的 52 秒简单外推完整 GPU 预算。Stage2 原始数据会迅速超过当前紧凑结果规模。PKR gate 失败前未冻结、未启动完整矩阵。

重新估算需要：冻结实际 runner 任务分解、从成功和失败日志分开估算每角色时长、记录原始 IQ bytes/保留策略、计算 CSI/CFAR 诊断空间，并设并发与磁盘上限。此估算留到 PKR gate 可行后进行。

## 2026-10-28 V1A 扩展矩阵静态预算（执行前估算）

机器可读设计 `R22_EXTENSION_20261028_V1A` 共 **594 cases**：engineering 12、screening 288、confirmatory 180、dense 48、bridge 6、H0 60。其中 294 个 0 ns case、300 个 +4 ns case。计数由当前冻结配置及 `r22_experiment_matrix.build_case_specs` 生成，不是已执行数量。

根据当前 runner 的 method contract 和角色展开，每 case 的 Production 分支数为：

| 时延条件 | 基础 branch 数 | 额外盲校正 branch | case 数 | branch 总数 |
|---|---:|---:|---:|---:|
| 0 ns | 36 | 0（校正不适用于无误差参考） | 294 | 10,584 |
| +4 ns | 45 | 固定 D3 3；Huber/M1 各最多 3 | 300 | 14,400–16,200 |
| 合计 | — | — | 594 | **24,984–26,784** |

Huber/M1 只有通过各自 support/qualification 才会运行其三个角色；表中下界已计入固定 D3。额外 `NOT_APPLICABLE` / `NOT_AVAILABLE` 记录不计作 Production 执行次数。该展开从 `run_production_hierarchical_calibration_pilot.py` 的当前方法集、`REFERENCE_FIT_METHOD_IDS`、每方法 OFF/ON/TO 角色和 R22 的 delay-specific method selection 计算得到。

Stage2 也必须按实际 simulator variant 计数，不能只报 594 个场景。V1A 的 0 ns 角色复用契约预计每 case 5 个唯一 Stage2 variant、+4 ns 6 个；在 10 个周期下对应约 32,700 个 variant-period 单元。0 ns 的五 variant 来源于上一失败 case 实际生成 50 份十周期 raw IQ（其 SHA/大小见该 case 的 `raw_cleanup.json`）；+4 ns 六 variant 是配置展开推算，需由首个成功 case manifest 核验。

### 历史时间与磁盘停止门（用户澄清前的规划，已被后文 supersede）

以下是 V1A 执行前按当时信息形成的静态预算与停止判断。用户后来明确：20 GB 只约束结果汇总、ChatGPT 审查和最终清理后的项目占用，不限制中间试验体积。因此以下 `<20 GB` 运行/启动门不再生效；实验期间按实际文件系统可用空间和运行错误管理，不因超过 20 GB 停止或缩减冻结矩阵。

- 旧 seed900203 单周期运行及首次失败 seed1930101（未进入 Production）不能可靠外推 V1A 的十周期成本。GPU/CPU 时间预估必须在 V1A 的首个 0/+4 ns case 和六 seed engineering batch 后分别更新；记录 Stage2 elapsed、Production branch elapsed、端到端耗时及设备状态。
- 首次失败 case 的 52 个可再生 `.bin` 已清理前，合计约 2.95 GB；单 case 是当前观测到的 raw peak 参考。成功 case 按 runner compact policy 清理 raw `.bin` 和 CSI full maps，但保留运行 XML、日志、CSI summary、CFAR geometry、关键任务 CSV、manifest 和 SHA 清单。
- 失败 case 的原目录在部分失败前保留约 112 MB；若将其直接按 594 case 外推，会超过 20 GB。因此不得启动 screening 直到工程批次实测成功 case 的 retained bytes、case 临时峰值和全项目占用可证明完整矩阵能按串行清理政策安全执行。该 112 MB 只是偏保守的单 case 观测，不是假定未来每个 case 大小相同。
- 启动每批前检查 `du -sh .` 与 `df -h .`；全项目目标 `<20 GB`。如果预测触顶，先审计可保留的 summary/identity 与可再生成中间产物，再形成独立、可审查的压缩/清理策略；不得删除唯一失败现场或高价值 R21 基线输入，也不得把资源问题转成减 seed、目标、周期或方法。
- 当前 worktree 约 6.9 GB，卷可用约 51 GB。`docs/` 约 5.5 GB，主要含 R21 高价值基线输入与审计证据；本轮已核验没有必要删除这些唯一输入。现有 `outputs/`、`.scratch/` 中未跟踪材料视为既有资料，未纳入清理或提交。

### 执行顺序

1. RTX 3050 空闲后，执行 V1A seed1930101 的 0 ns 与 +4 ns pilot，写入新证据根；每个 case 单独记录原始运行身份和清理台账。
2. 两个 case 都通过后，再完成 seed1930101–1930106 全部 12 个工程条件。若任一工程 gate 失败，停止受影响比较并处理根因，不开始 screening。
3. 六 seed 的耗时和空间数据齐备后，重新计算上述矩阵全量成本。如果在 `<20 GB` 工作区约束和现有设备窗口内不能安全执行，保留完整冻结设计并报告未完成成本；不静默下调规模。

## 2026-10-28 V1A 实测留存空间审计（执行后）

- V1A 第一个 engineering case 清理 raw binary 后，完整本地 engineering 输出树为 2,401 个文件、301,858,725 bytes（约 301.9 MB / 287.8 MiB）。其中 CSV 224,067,290 bytes、PNG 42,479,348 bytes、JSON 28,066,641 bytes、日志 4,446,891 bytes、XML 1,574,095 bytes、F32 917,504 bytes；未发现残留 `.bin` 或 `.npy`。
- 可供本轮独立审查的紧凑 ZIP v2 为 106 项、3,838,630 bytes，包含 manifest 身份、关键 XML/日志/诊断及四个 C4/PKR Production reference 分支实际生成的 `production_calibration_reference_gamma.csv` 和 `production_calibration_adapter.csv`；不包含 `.bin`/`.npy`。本机路径：`outputs/r22_chatgpt_review_evidence_v1a_20261028_v2.zip`；SHA-256：`e9098ac47070ce7ec461bdbcaba75cd0e59a6625d35b385f5b97f687daee7847`。
- 本次复测整个 worktree 为 7.2 GB，低于 20 GB 限额；没有为节省空间删除唯一失败证据或 R21 高价值基线。当前 V1A 全量 case 留存规模不能直接线性外推为完整 594-case 成本；由于首个 engineering gate 已失败，screening 未启动，也没有形成通过门控后的完整矩阵空间预算。

## 2026-10-28 V2 紧凑归档资源门（screening 前）

- V2 六 seed 工程批次的 12 个最终 case 均通过生产与评价门。由其 `r22_run_manifest.json` 汇总，Stage2 实测累计 `2,582.736 s`、Production 分支累计 `4,954.887 s`，合计 `7,537.622 s`；平均每 case `628.135 s`。按 582 个未执行 case 等比例外推约 `101.5 h`，只作为排期估计，不是性能基准；40-target dense、H0 及设备负载差异可能改变实际值。周期数仍为冻结设计的 10，不根据结果减少。
- 工程 case `seed1930106 / +4 ns` 的临时副本原始目录为 `466,572,428 bytes`。应用紧凑留存策略后，ZIP 为 `6,270,421 bytes`，包含 2,356 项 / 36,907,132 未压缩 bytes；排除 2,175 项 / 430,332,608 bytes 的可重建图像、重复角色输出和长明细，并逐项保留排除原因及 SHA。ZIP CRC、清单、每个纳入文件的长度和 SHA 均已核验；只删除了用于测量的临时副本，正式六 seed case 未改动。
- 本次 worktree 为 `8,323,450,052 bytes`。按该代表 case 的 ZIP 线性估算，594 cases 归档约 `3,724,630,074 bytes`，项目约 `12.05 GB`；再给新增归档量增加 50% 余量后约 `13.91 GB`。这是汇总后最终紧凑归档体积的预算，不是运行峰值上限；也不是全矩阵实际占用，screening case 完成后需按真实 ZIP 更新估算。
- 矩阵 runner 现将成功 case 写入独立临时根，验证紧凑 ZIP 后再从临时根删除原始 case；失败 case 留在临时根作为失败证据。矩阵 manifest 先持久化通过状态，再归档和删除原始目录，故归档中断可从该 case 恢复而不重跑 CUDA。归档损坏或 SHA 不一致时 fail closed。
- **用户后续澄清：** `20 GB` 是结果汇总、审查和清理完成后的最终项目占用上限；运行中的原始 case、中间试验和临时产物可以超过该值。此前设置的 `18 GB` 项目体积停止门不再适用。运行时只依据文件系统实际可用空间及 runner 自身错误处理保障写入；不因项目临时体积超过 20 GB 而中断或缩减冻结实验。case 完成并核验后按归档策略及时移除可重建大数组；审查材料完成后再按清理台账将最终项目压至 20 GB 以下，不删除唯一失败现场或高价值审计记录。

## 2026-10-28 V2 screening 首批 CUDA 运行与内存复核

冻结 V2 设计共 288 个 screening case。以下运行表和结论记录的是 attempt004 启动前的历史 checkpoint；最新执行状态见本文件末尾的“attempt004 延续执行”。此处历史结果不是完整 screening 结果，也不支持统计性能结论。

### 运行与门控记录

| 条件 / attempt | 实际状态 | 证据与判定 |
|---|---|---|
| 0 ns / attempt 001 | `INTERRUPTED`，未进入 Production | kernel 于 09:39:09 记录 global OOM，杀死 Python（anon RSS `4,745,708 KiB`）。根因是因果闭合审计同时物化 ON/OFF/TO 全扫描 complex128 数组；该路径已由 `audit_additive_triplet_periods` 改为逐 packet 流式审计。失败归档 `outputs/r22_full_v2_compact/failures/screening/screening__seed_1932001__vr_0d5__snr_10__delay_0ns__targets_20__periods_10__attempt_001.zip`，SHA-256 `95f030db4db2b5356cfc648ea0c029640facdcc8983dd41320530fcc2546bd9b`。 |
| 0 ns / attempt 002 | `PASSED`，`IMPLEMENTED/EXECUTED/EVALUABLE=true`，`PERFORMANCE_VERIFIED=false` | Stage2 A0_ON 面积杂波 `1,300` packets / `6,508,782` samples，面积均功率 `0.0221596696662`，噪声均功率 `0.00100004623594`，lag-1 rho `0.98001127856`；面积杂波门通过。ideal/none/known 三组 ON−OFF−TO 闭合均通过，样本数 `61,464,000`，最大误差 `1.090781284690147e-5`、RMS `6.091604287700934e-7`、容差 `5e-5`。32 个 Production 分支通过，12 条 ON evaluator 行可评估，0 个 invalid truth opportunity；这是单案例可行性证据。紧凑归档 `outputs/r22_full_v2_compact/case_archives/screening/screening__seed_1932001__vr_0d5__snr_10__delay_0ns__targets_20__periods_10__attempt_002.zip`，3,939,828 bytes，SHA-256 `467eb75a9e1aff833a4ef76a37dd83129a5305ae4865d146e3ab72e7264c7fcb`，归档内无 `.bin/.npy/.npz`。 |
| +4 ns / attempt 001 | `INTERRUPTED`，Stage2 后、盲估计阶段中断，未准备 Known 输入、未启动 Production | kernel 于 10:28:00 记录 global OOM，杀死 Python（anon RSS `2,500,672 KiB`；主机 swap 已满）。六个 Stage2 场景均有报告和日志，OFF 合并输入已生成；没有 Production branch 或 runtime XML。控制流将中断定位在 truth-blind `estimate_blind_delay`：旧实现通过 `_load_fused_from_periods` 一次载入 F1/F2 全扫描矩阵，随后创建全尺寸安全输入和 FFT 工作区。失败归档 `outputs/r22_full_v2_compact/failures/screening/screening__seed_1932001__vr_0d5__snr_10__delay_4ns__targets_20__periods_10__attempt_001.zip`，SHA-256 `8977aeb8a960d469687cc1a981e1a824d4b96d88ab5a4f3dd023b1d2fe49d15e`。 |

两次失败 attempt 的 `.bin` 均先逐文件写入 SHA-256 清单和 cleanup ledger，再清除可由冻结 seed/config 重建的二进制；Stage2 配置、XML、报告、日志、truth/reuse 映射、运行身份及 OOM 记录保留在失败 ZIP 中。0 ns attempt 001 清理 81 个文件 / `4,428,403,200` bytes；+4 ns attempt 001 清理 71 个文件 / `3,936,358,400` bytes。没有清理输入基线、历史证据或其他任务目录。

### 第二处内存峰值修复与资源状态

- 盲估计现在通过 `_iter_fused_packets_from_periods` 按 protocol packet 提供 F1/F2，并由 `delay_method_suite_streaming` 累加原有 D1/D2/D3、D4、线性互相关、GCC-PHAT 和过采样互相关所需的 cross-spectrum / PHAT 统计量；保留原频谱权重、支持阈值、相关定义、lag 符号和 truth-blind 输入规则。
- 批处理与流式实现的七种方法已在含非有限样本的分块测试中逐项比对，状态、支持数、输入有限样本数和 pulse/sample count 一致，时延及相位残差在预设数值容差内一致。协议文件 packet iterator 也与现有 F1/F2 批加载器对照通过。
- 冻结尺寸资源检查使用合成流输入（1,300 packets × 11,820 samples，七种方法均返回 `ok`）：CPU elapsed `5.96 s`，进程最大 RSS `87,320 KiB`。这是算法内存路径的合成资源验证，不是 CUDA/Production 证据，也不替代 +4 ns 实际 case 重跑。
- 本次相关测试：`tests/test_delay_stage1_core.py`、`tests/test_delay_stage1_contract.py`、`tests/test_production_hierarchical_calibration_pilot.py`、`tests/test_r22_experiment_matrix.py`，共 `135 passed`。CUDA 回归尚待补跑。
- **历史 checkpoint（attempt004 前）：** 矩阵为 `INTERRUPTED_REVIEW_REQUIRED`：screening 计划 288 个 unique case，0 ns 有 1 个通过、+4 ns 有 1 个中断、其余 286 个尚未开始。代码修复并通过真实 Native CUDA +4 ns 配对门之前不继续其他 screening case。周期、seed、目标数、方法、PKR support 和 CFAR/beam quality gate 均未改变。此状态已由下方的新执行记录更新。
- 该 checkpoint 的两份失败包与一个通过包已压缩留存；当时 worktree 约 `8.33 GB`。这一历史体积不代表后续 CUDA 运行峰值，也不构成 20 GB 的运行限制。

## 2026-10-28 attempt004 延续执行（已闭合 checkpoint）

- 冻结设计仍为 V2：288 个 screening、594 个全设计 case；未减少 seed、目标、period、方法或质量门。0 ns 配对 case 已通过单 case 工程门，`PERFORMANCE_VERIFIED=false`。
- +4 ns attempt001 因主机 OOM 中断；attempt002 记录 `OSError: [Errno 122] Disk quota exceeded`；attempt003 在 evaluator 完成前被操作中断，状态保留为 `INTERRUPTED`，并已用原始数组 SHA 清单及失败 ZIP 归档。不得把这些 attempt 记作通过。attempt004 使用项目本地临时 raw 根目录继续运行。
- attempt004 的生产输入来自 seed1932001、0.5 m/s、10 dB、20 targets、10 periods、+4 ns。实际为本机 Native CUDA、`USE_COREX=false`；执行源码基线 `5a60a514cf53cab9f23b17ab29992fd4485876fa`。Stage2 binary SHA-256 `47fa5ec34ca15cdc4edeb35226eefa941a78ee344aa2311e5e7984c811dcb40f`，Production binary SHA-256 `d9e4d2e0b8429c5415e94be1af0230f3c0148455c394dc6d661e7e6c13a4ad1e`。矩阵结果 `PASSED`；54/54 method/mode/role branches 通过，`IMPLEMENTED/EXECUTED/EVALUABLE=true`、`PERFORMANCE_VERIFIED=false`。R21 task evaluator 汇总 54 task rows、18 ON rows、7,200 target-period opportunity rows、0 invalid truth opportunities、0 independent H0 rows；经验 Pfa 为 `NOT_EVALUABLE`。
- attempt004 的 `source_identity.tracked_dirty_status` 记录执行时有治理/研究文档修改，未记录代码改动；七个参与执行脚本均有逐文件 SHA。全 screening 后续 case 仍会逐例记录代码文件 SHA 与 worktree 状态；应区分文档 dirty 状态和实际执行源码身份。
- Stage2 area gate 通过：1300 continuous-area packets / 6,508,782 samples，平均面积功率 `0.0221596696662`、平均噪声功率 `0.00100004623594`、lag-1 rho `0.98001127856`；仿真 slant range 为 84.5–85.5 km，报告 ground range 为 84.287–85.289 km。Ideal/None/Known/Blind causal triplet 均通过，容差 `5e-5`，每组 61,464,000 samples，最大误差依次约 `1.091e-5`、`1.073e-5`、`1.283e-5`、`1.103e-5`。面积杂波和闭合数据详见归档中的 case manifest 与 Stage2 report。
- Blind delay estimator 使用 OFF=C+N 流式输入，manifest 标记 `truth_used_in_estimator=false`。D1/D2/D3/D4 点估计分别约 3.289/3.289/3.244/3.727 ns；这些单 seed 估计值不构成算法优劣结论。Known/盲对任务性能的推断须等冻结的多 seed screening 完成。
- 残余闭合审计报告 `GO_EQUIVALENT_CALIBRATION_ONLY`；`additional_residual_suppression_db=-0.012629`，定义为 `PK residual − PKR residual`，本 case 中数值接近零且为负。单 case 结果不支持声称 PKR 杂波抑制改善。
- 紧凑归档为 `outputs/r22_full_v2_compact/case_archives/screening/screening__seed_1932001__vr_0d5__snr_10__delay_4ns__targets_20__periods_10__attempt_004.zip`，5,775,941 bytes，SHA-256 `cde0e8d0b4521cd1e856c217a6b40606ad0bb313dc59d18df176cc1237756cdf`。ZIP CRC 通过，矩阵 SHA 一致，2,356 个纳入文件逐文件大小/SHA 全部匹配；2,175 个可重建/重复明细文件按理由排除，归档不含 `.bin/.npy/.npz`，raw case 已删除。该 ZIP 可审查保留证据，不能重放省略的 raw IQ。
- **历史快照（截至 UTC 11:16）：** 20 GB 是最终汇总和审查完成后清理得到的项目占用上限。attempt004 清理后 worktree 回到约 `8.75 GB`；随后完整 screening runner 已恢复执行，seed1932001、0.5 m/s、20 dB、0 ns case 已 `PASSED` 并归档。矩阵当时为 `RUNNING`；该运行后来于 11:34:54 UTC 因方法级 gate failure 停止，当前状态见本文件末尾。

## 2026-10-28 V2 screening 延续快照与源码可比性审计

截至 UTC 11:16，冻结的 288 个 screening case 中 17 个已 `PASSED`，1 个 `RUNNING`，manifest 另保留 4 个早期失败/中断 attempt；当前活动 case 为 `seed1932001 / 2.5 m/s / 30 dB / +4 ns`。失败 attempt 不计入 288 个唯一 case。17 个通过 case 均为同一 Stage2 binary (`47fa5ec34ca15cdc4edeb35226eefa941a78ee344aa2311e5e7984c811dcb40f`) 和 Production binary (`d9e4d2e0b8429c5415e94be1af0230f3c0148455c394dc6d661e7e6c13a4ad1e`)，Native CUDA 且 `USE_COREX=false`；各 case area gate 通过，连续面积注入为 1,300 packets / 6,508,782 samples。该批目前只覆盖一个 seed，尚不能形成跨 seed 推断；独立 H0 未运行，Pfa 不可评价。

逐 ZIP 比对 `source_identity` 后发现，首个 0 ns screening case (`0.5 m/s / 10 dB`) 执行 commit `7e5a7ee8ebd6c460622425e3d05831b25603e7ec`，后续已通过 case 执行 commit `5a60a514cf53cab9f23b17ab29992fd4485876fa`。两者冻结设计与方法配置、Stage2/Production binary 相同；提交间相关差异是 truth-blind delay estimator 从整幅 F1/F2 批处理改为 packet streaming，并新增流式等价测试。`tests/test_delay_stage1_core.py::test_streaming_delay_suite_matches_batch_methods_and_metadata` 对各方法的状态、support/sample counts 作精确相等检查，并要求 delay 与 phase residual 满足 `rel=1e-9, abs=1e-10`。较早 case 的 `zero_delay_blind_correction_policy` 为 `estimate_only_no_physical_correction`，故该源码变化没有改变它的校正处理条件；其 blind-estimator 诊断数值需保留源码版本标签，不与后续 streaming 诊断无条件合并。基于这一边界，当前不为该已通过 case 重跑 CUDA；若后续方向判定依赖零时延估计器诊断，再单独复算该诊断或重跑该 case。

17 个通过 case 的实测 wall time 合计 `16,447.7 s`，均值 `967.5 s/case`、中位数 `987.0 s`，范围 `626.5–1,650.9 s`。按当前均值线性外推，剩余 screening 约 `72.8 h`；594-case 全设计约 `159.6 h`。40-target、H0、方法分支数量和设备状态会改变耗时；这只是排期估算，不是完成承诺或缩减矩阵的依据。

本快照的矩阵 manifest 为 `outputs/r22_full_v2_compact/matrices/screening_diskquota_retry/matrix_manifest.json`，逐 case 证据为其 `compact_archive.archive_path` 指向的 ZIP。11:16 UTC 临时 raw 根目录约 6.6 GB，文件系统可用约 29 GB；这是活动 case 生成中的占用快照，不触发清理或缩减实验。20 GB 仅是全部结果汇总、独立审查和最终清理完成后的上限。

## 2026-10-28 V2 screening gate failure：C4 Mode-B ON support 不足

### 终态与执行身份

- `screening_diskquota_retry/matrix_manifest.json` 终态为 `GATE_FAILED`，planned 288 unique cases；已有 17 个 `PASSED`，当前 1 个 `GATE_FAILED`，另有 4 条此前 OOM / quota / interruption attempt；无活动 case。runner 于 2026-10-28 11:34:54 UTC 退出码 2，未自动重试或继续后续条件。
- 失败 case：`screening__seed_1932001__vr_2d5__snr_30__delay_4ns__targets_20__periods_10`，执行 commit `5a60a514cf53cab9f23b17ab29992fd4485876fa`，Native CUDA，`USE_COREX=false`；Stage2 SHA-256 `47fa5ec34ca15cdc4edeb35226eefa941a78ee344aa2311e5e7984c811dcb40f`，Production SHA-256 `d9e4d2e0b8429c5415e94be1af0230f3c0148455c394dc6d661e7e6c13a4ad1e`。V2 设计与方法配置 SHA 仍分别为 `4bd2014b1fe1eab5c8cbcca1a380638231cf26dddf5de683a086f1b1b91a7a1c`、`588c36904f62febad8476ddccb789ba67de2e9c23dc2ff12b52418eb06706bbc`。
- Stage2 面积杂波门通过：1,300 continuous-area packets / 6,508,782 samples，平均面积功率 `0.0221596696662`，平均噪声功率 `0.00100004623594`，lag-1 rho `0.98001127856`；slant-range 窗 84.5–85.5 km，地面距离约 84.287–85.289 km。Ideal/None/Known/Blind 四个 causal triplet 均通过；每组 61,464,000 samples，最大误差约 `1.82e-5`，低于冻结容差 `5e-5`。盲估计器 `truth_used_in_estimator=false`，无 fallback。
- Production 算子实际运行；C4 Mode-B ON 的 Production return code/status 为 passed，输出了 CSI、CFAR geometry 和检测文件。整体 case `implementation_result=completed_with_gaps`、`EVALUABLE=false`、`PERFORMANCE_VERIFIED=false`；54 task rows 中有 17 个 ON evaluator rows、6,800 target-period opportunities、0 invalid truth opportunities、0 independent H0 rows。唯一缺失的 ON 条件使有效 CUT / unique-hit union gate 无法闭合，故整体矩阵 fail closed；这不是 CUDA 启动失败或面积杂波未注入。

### 直接根因与配对证据

- 冻结配置 `range_band_bins=133`、`min_support=8` 未改。C4 Mode-B ON 的 robust reference 共 1,560 组，其中 1,559 组 `OK`；唯一失败行是 period 5 / `GMTI05` / beam 50 / az 52 / range bins 1065–1197，`support_count=7`、phase coherence `0.11720003232345`、reason `insufficient_robust_support`。该 period adapter 为 `PARTIAL`，155/156 groups valid。reference validator 对任何非 `OK` group 都 fail closed，随后 C4 Mode-B ON 和依赖该 reference 的 OFF/TO 行标记 `NOT_EVALUABLE`；没有 fallback 到 Current。源代码 gate 位置为 `scripts/run_production_hierarchical_calibration_pilot.py` 的 reference validation。
- 同 seed、速度、+4 ns、同一目标 range/trajectory 下，SNR20 配对 case 的相同 group support 为 25、1,560/1,560 groups 全部 `OK`；SNR30 case support 降为 7。SNR30 的 period-5 目标 `R22_T010` 位于 range bin 1175，即失败 group 内。target-free OFF 在同 period/group support 为 55。SNR20 与 SNR30 的 area-scatterer CSV SHA 均为 `d309e99080e154f13620de7fc1fb8eef64ca561f25e81d2bce6ac3a675e4f0d7`，背景 period-generation stats SHA 均为 `f84a2949f5c7d7e1dc666a818e079c6bc03ff59170f81edc7b088989c6b38727`；两条件按 target ID/period 对齐后的 200 行目标状态/几何字段（除 case label、SNR 外）逐项相同。这个配对证据与高 SNR 目标污染 ON 侧盲参考、导致局部 robust support 不足相符；它只来自一个 seed 和一个失败 case，不能据此推断通用 SNR 临界值或长期研究方向。
- 因果上的直接失败项是 `support=7 < min_support=8`；目标污染是当前 paired evidence 支持的机制解释。不得把单例推成跨 seed 规律，也不得通过降低 `min_support`、移除 C4 或跳过失败 case 来让筛选继续。

### 失败证据保留与阶段裁决

- 失败压缩审查包：`outputs/r22_full_v2_compact/failures/screening/screening__seed_1932001__vr_2d5__snr_30__delay_4ns__targets_20__periods_10__GATE_FAILED_attempt_001.zip`，6,729,552 bytes，SHA-256 `be76237104d6c6f11f1681de556e49ecad5e694658e8d5c94ce3e7c05850890b`，archive-manifest SHA-256 `cc21231237d14b6dd8e1115e0ea891f6ad2361d2105dee95f21f4032cd12916a`；包含 2,284 个记录项，ZIP CRC 与纳入文件 SHA/大小已由归档器校验。失败 raw case 目录仍保留；runner 已记录清理 1,781 个可再生数组、`11,320,004,032` bytes，归档检查确认剩余 `.bin/.npy/.npz` 为 0。未触碰其他项目数据。
- 本阶段停在 screening gate。未启动其余 screening、180 confirmatory、48 dense、6 bridge 或 60 H0 cases；未降低任何质量门，未修改算法、冻结配置或研究总纲，未训练 AI。后续只能在独立审查后决定：将 C4 Mode-B 高 SNR 支持不足作为方法能力边界，或提出保持 `min_support=8` 的新分组/目标保护 amendment；若需修改冻结设计，先冻结新版本并重新评估受影响 case，不从当前失败结果直接继续矩阵。
- 20 GB 是汇总、ChatGPT 审查和最终清理后的上限。当前 raw/compact 中间产物继续保留到审查完成；本次 gate failure 不触发项目级清理。
