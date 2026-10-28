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

### 时间与磁盘停止门

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
- 本次 worktree 为 `8,323,450,052 bytes`。按该代表 case 的 ZIP 线性估算，594 cases 归档约 `3,724,630,074 bytes`，项目约 `12.05 GB`；再给新增归档量增加 50% 余量后约 `13.91 GB`，低于 20 GB 约束。此为预算而非全矩阵实际占用，首个 screening case 完成后必须用其真实 ZIP 更新估算。
- 矩阵 runner 现将成功 case 写入独立临时根，验证紧凑 ZIP 后再从临时根删除原始 case；失败 case 留在临时根作为失败证据。矩阵 manifest 先持久化通过状态，再归档和删除原始目录，故归档中断可从该 case 恢复而不重跑 CUDA。归档损坏或 SHA 不一致时 fail closed。
- 每一阶段按批检查项目大小，`18 GB` 为停止并复核的操作门，避免接近用户的 `20 GB` 上限；同时继续逐 case 串行写入。若单 case 实际留存显著超过样本，先停止并更新估算，不删唯一失败现场、不减 seed/目标/周期/方法，也不覆盖历史 baseline。

## 2026-10-28 V2 screening 首批 CUDA 运行与内存复核

冻结 V2 设计共 288 个 screening case。当前只完成 seed `1932001`、0.5 m/s、10 dB、20 targets、10 periods、beam 50 的 0 ns case；+4 ns 配对 case 的第一次运行被主机 OOM 中断，screening 随即停止。此处记录不是完整 screening 结果，也不支持统计性能结论。

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
- 当前矩阵 checkpoint 为 `INTERRUPTED_REVIEW_REQUIRED`：screening 计划 288 个 unique case，0 ns 有 1 个通过、+4 ns 有 1 个中断、其余 286 个尚未开始。代码修复提交并重新通过真实 Native CUDA +4 ns 配对门前，不继续其他 screening case。周期仍为 10，seed、目标数、方法、PKR support 和 CFAR/beam quality gate 均未改变。
- 本次两份失败包与一个通过包已压缩留存；测得整个 worktree 约 `8.33 GB`，低于用户 `20 GB` 上限。screening 完成后须按真实 ZIP 大小更新余量估计，并继续逐 case 检查 18 GB 操作复核门。
