# R22 磁盘清理台账

更新日期：2026-10-31。只清理本阶段明确生成、且高价值结果已归档验证的临时运行目录；既有 `outputs/`、`.scratch`、用户数据和任务开始时 4,007 项未跟踪/忽略资料未触碰。

## 保留

- `evidence/preflight/seed900101/`：20 target / 20 beam Native CUDA smoke 的配置、run/branch manifest、Stage2 报告、Production runtime/log、CSI summary 与 CFAR/Detection 诊断。
- `evidence/preflight/seed900202/`：紧凑单周期 pilot 汇总与 27/27 posthoc production gate 审计；约 11 MB。
- `evidence/preflight/seed900203_closure/`：953 个 manifest 登记项、约 39.58 MB payload；包括失败 PKR reference/adapter、Production logs/XML、Stage2 杂波/配对与输入身份、成功分支 CSI/CFAR/Detection/TrackManager 证据。所有登记文件经 SHA-256/大小核验。
- `evidence/results/`：baseline 注册/可比性矩阵、机器可读实验 manifest、逐条件任务指标、面积配对审计和一张明确标注为单周期描述统计的图。
- runner、scenario factory、实验配置、测试及报告。

## 已删除的本阶段临时结果

| 删除路径 | 删除前大小 | 原因与保留替代 |
|---|---:|---|
| `/tmp/r22_multitarget_method_preflight_seed900201` | 1,962,014,009 bytes apparent（此前已删除） | 较早的筛选前方法 smoke；由后续冻结的 seed900202/900203 生产分支审计与评估替代 |
| `/tmp/r22_runner_smoke_seed900202` | 3,181,607,889 bytes apparent（此前已删除） | 原始单周期 IQ/生产中间文件；compact seed900202 包保留 manifest、配置、日志、tap、CFAR、任务输出与输入 SHA/大小 |
| `/tmp/r22_runner_smoke_seed900203` | 2,107,212,248 apparent / 2,112,536,576 allocated bytes | 与 closure run 的 A0/A1 及 `A0_background` 七份 Stage2 `.bin` SHA 全部相同；Production 和 Stage2 binary SHA 相同；目标关联、参数、航迹、H0、Doppler 评估表与 closure 版一致（task 表仅多/少一个空 reason 列）。closure 版包含完整 PKR 失败现场，故旧目录冗余 |
| `/tmp/r22_runner_smoke_seed900203_closure` | 2,116,247,403 apparent / 2,121,658,368 allocated bytes | 953 项紧凑归档逐文件验证完成后删除；raw IQ、数组和图像不留在临时目录 |
| `/tmp/pgrcc-r22-mpl` | 286,460 apparent / 286,720 allocated bytes | 本任务生成的临时 Matplotlib 缓存，随运行 scratch 一并清理 |

此次本轮删除的临时目录共释放约 **4,234,481,664 allocated bytes（约 3.94 GiB）**。此前两项临时目录按记录共 5,143,621,898 apparent bytes，已在本轮之前删除。

## 输入身份与清理边界

未保留生成/校正后的大 `.bin`、`.npy`、`.f32` arrays、临时 PNG 或重复日志副本。七份 seed900203 Stage2 输入的 SHA-256、原路径与 49,204,480-byte 文件大小保存在 `preflight/seed900203_closure/stage2/input_file_identities.json`；Production 输入/输出身份、程序 binary SHA 与运行配置保存在原始 run/case manifest。输入内容未伪装为可从压缩包重建；若需信号级重放，应按清单重新运行 Stage2。

## 空间验收

清理后 nested Git worktree `du -sh .` 为约 **6.7 GB**，低于用户要求的 20 GB。`/tmp` 清理后可用空间约 7.2 GB。后续大型运行仍需在启动前核验容量并使用独立输出目录。

## 2026-10-28 R22 延续工程失败 case

- 保留：`extension_engineering/seed1930101/` 下的 case/run/matrix manifest、6 份场景配置、Stage2 解析配置、period-0 runtime XML、报告、日志、背景复用映射、周期清单、目标生成 manifest、盲估计结果、首样本边界诊断及逐文件 SHA-256。
- 删除：当前失败 case 中 52 个 `.bin`，共 **2,952,268,800 bytes**：50 份可再生 Stage2 raw IQ（2,460,224,000 bytes）、合并 C+N estimator 输入（492,044,800 bytes）和 1 个零字节失败校正输出。删除前逐项重新核验路径、大小和 SHA-256；清理记录见 `extension_engineering/seed1930101/raw_cleanup.json`。
- 理由：该 case 在 Production 前失败，Stage2 raw IQ 可由归档的配置、seed、源码与 binary 重建；本次不保留大体积派生二进制。其他 `outputs/`、`.scratch/`、R21 基线和已有用户资料未触碰。
- 项目 worktree 从清理前约 **9.7 GB** 降至清理后约 **6.9 GB**，满足小于 20 GB 的要求。

## 2026-10-28 V1A Production gate case

- 保留：`extension_engineering_v1a/seed1930101/` 的精简审查包，含 case/matrix/run 摘要、41 行分支状态、Stage2 场景与 runtime XML、杂波报告/日志、代表性 Production XML/runtime dump/逐周期日志/CSI tap/CFAR geometry、C4/PKR robust gamma reference failure rows，以及逐文件 SHA-256 清单。
- 本机完整 case manifest 和原始 cleanup ledger 位于 `outputs/r22_extension_20261028_v1a/engineering/.../attempt_001/`，不属于 GitHub 精简包；精简包明确保留输入 SHA 摘要及关键失败证据。
- Production 汇总后删除本次 case 产生的 921 个可再生 `.bin`，合计 **4,429,702,312 bytes**；删除清单逐项写入原始 `raw_cleanup.json`。CSI 全尺寸 `.npy` 未生成/未留存；CSI tap summary CSV 已保留。
- case 生成期间 worktree 峰值约 **12 GB**；清理后本次工程输出约 **296 MB**，当时 worktree 总占用约 **6.9 GB**；本次交付前复测约 **7.2 GB**，低于 20 GB 上限。其他 `outputs/`、`.scratch/`、用户输入与历史基线未清理。
- 失败原因是 C4/PKR 的 133-bin robust gamma reference 有少数组 support 低于 8，触发质量验证停止；未降低 `min_support`，未删除唯一失败审计，也未继续性能矩阵。

## 2026-10-30 独立 H0 interrupted attempts

用户授权恢复 H0 矩阵并清理无用数据后，先将 seed1935015 / +4 ns 的两次中断 attempt 压缩为可校验审查档，再删除精确对应的 raw attempt 目录。矩阵是 case-level retry：attempt001/002 的 raw 不能作为 runner 的断点继续输入，恢复会创建 attempt003；原始失败日志、配置、manifest 与已完成分支证据因此保留在压缩档中。

| 删除的 raw attempt | 清理内容 | 压缩审查档 | SHA-256 |
|---|---|---|---|
| `outputs/r22_h0_engineering_post_review_20261029/raw/h0/h0__seed_1935015__vr_0__snr_20__delay_4ns__targets_0__periods_10/attempt_001/` | 72 个可重建 `.bin`，合计 3,939,007,872 bytes | `evidence/h0_checkpoint_20261030/interrupted_attempt_archives/h0__seed_1935015__vr_0__snr_20__delay_4ns__targets_0__periods_10__attempt_001_INTERRUPTED.zip`（412,531 bytes，164 included / 1 excluded） | `7110bf311bb1bb68c0d2bb3b72f8dec9ca459116aca1e64550d55ba4be73cc9e` |
| `outputs/r22_h0_engineering_post_review_20261029/raw/h0/h0__seed_1935015__vr_0__snr_20__delay_4ns__targets_0__periods_10/attempt_002/` | 871 个生成二进制及 510 个内部 `.bin` symlink 路径，实际文件合计 9,841,040,440 bytes；未删除 CSI `.npy` | `evidence/h0_checkpoint_20261030/interrupted_attempt_archives/h0__seed_1935015__vr_0__snr_20__delay_4ns__targets_0__periods_10__attempt_002_INTERRUPTED.zip`（3,453,103 bytes，1,804 included / 1,309 excluded） | `cbd44572da8e39411c15c46fe2939c5dd1b5d4e3676c916478dd5f4969f625b4` |

两个 ZIP 均通过 CRC 检查，归档器在删除源目录前逐项核验纳入文件的 SHA-256 与大小；matrix manifest 中的 attempt 记录包含 archive 路径、SHA 和 CRC 状态。attempt002 的紧凑 case manifest 保留完整 1,381 条 raw-cleanup 记录。attempt001 没有完整 case manifest；其 20,629-byte `raw_cleanup.json` 被归档器的规则排除，archive manifest 保留了该文件的 SHA/大小，具体逐文件 cleanup 记录未保留；本台账保留其准确总文件数和总字节数。

此次释放 **13,780,048,312 bytes（约 12.83 GiB）** 可重建二进制数据。清理前文件系统可用约 7.1 GB，清理后约 20 GB。两份中断 ZIP、失败原因、branch/XML/日志和逐 case 分析汇总继续保留；29 个已完成 H0 case 的 compact ZIP 仍在本地 `outputs/.../archives/`，未全部复制进 Git。清理只针对上述两个 interrupted attempt，没有删除 29 个已归档 case、历史失败现场、配置或用户资料。

## 2026-10-31 R22 H0 seed1935029 interrupted attempt

- 保留：中断 attempt 的压缩审查档、外层运行 manifest、配置、逐分支审计、日志与 cleanup SHA/路径记录；归档通过 ZIP CRC 和纳入文件 SHA/大小校验。审查档：`docs/research/phase_r/R22/evidence/h0_checkpoint_20261030/interrupted_attempt_archives/h0__seed_1935029__vr_0__snr_20__delay_4ns__targets_0__periods_10__attempt_001_INTERRUPTED.zip`，SHA-256 `85fd6c32c8f66696a0bd92ec9ade3d2feeb2de3582d617a93a5fbc3b7907fa7f`。
- 删除：精确 case 内 1257 个生成 `.bin` 路径（实际文件 787 个、内部 symlink 470 个），实际二进制 8,050,376,328 bytes；未发现 `.npy/.npz`。清理明细含逐路径 SHA-256，保存在归档的 compact manifest 中。
- 原因：runner 已停止，case-level retry 不消费半成品；后续创建新 attempt。归档校验后才删除 attempt_001 raw 目录。
- 本次操作完成后工作区文件系统可用空间：13,609,340,928 bytes。


## 2026-10-31 R22 H0 matrix completed case compaction

- 60 个冻结 H0 case 均完成 compact archive 校验；每个 case 的 compact ZIP 在本机 `outputs/r22_h0_engineering_post_review_20261029/archives/h0/`，总计 **180,896,331 bytes**。60 个 ZIP 中未发现 `.bin/.npy/.npz` raw-array members；每 case cleanup 记录保存在 archive compact manifest 中。
- 60 个完成 case 共清理 **72,060 个生成 `.bin` 路径**、合计 **472,370,816,936 bytes**；其中 symlink 路径计入 path count，但不重复计入实际删除字节。未删除 CSI `.npy`（本矩阵未生成需保留/删除的 CSI map）。
- 3 个 interrupted attempt（seed1935015/+4 ns attempt001/002；seed1935029/+4 ns attempt001）另清理 **21,830,424,640 bytes**。删除前先生成并验证审查 ZIP；索引和各 ZIP SHA 见 `h0_checkpoint_20261030/h0_interrupted_attempt_archive_index.json`。三次中断不构成独立实验 seed，最终每 case 只采用最新完成 attempt。
- 本次合计清理生成 raw **494,201,241,576 bytes**。保留 60 个 compact ZIP、分析 CSV/manifest、30 对 Stage2 配对报告身份、3 个 interrupted archive 和清理账，待 ChatGPT 独立审查后按用户 20 GB 最终占用规则再清理本机 compact ZIP。
- 最小可审查汇总已写入 `evidence/h0_final_20261031/`；完整 case ZIP 不随本次 GitHub 精简证据提交，避免上传重复/较大逐分支包。

## 2026-10-31 R22-S1d 审查后的待清理项

- **继续保留到 ChatGPT 审查结束：** `outputs/r22_s1d_period_corrected_crossover_20261031/`（约8.2 MB，period-correct 的7分支原始运行归档）、`outputs/r22_s1d_phase_vector_crossover_20261031/`（约2.5 MB，周期错配的无效初试）及 `outputs/r22_s1d_phase_vector_self_controls_20261031/`（约2.5 MB，周期错配的无效对照）。无效目录不会用于结果或提交，保留是为了审查期间可核对失败更正过程。
- **Git 保留替代：** `docs/research/phase_r/R22/support_failure_diagnostic/evidence/s1d/` 中约3.2 MB 紧凑证据，包含7分支运行 XML、日志、manifest、selected-cell 复数值、CSI sidecar、CFAR/CSI tap 摘要、S1d support/目标距离格交叉结果，以及 S1b ON/OFF/TO 的9个紧凑 trace/phase-vector 源文件和逐文件 SHA-256。128个登记文件已逐项核验；原始 Stage2 `.bin` 不在 GitHub 包内。
- **审查后操作：** 删除三个 S1d 本地目录：`outputs/r22_s1d_period_corrected_crossover_20261031/`（约8.2 MB）、`outputs/r22_s1d_phase_vector_crossover_20261031/`（约2.5 MB，周期错配初试）和 `outputs/r22_s1d_phase_vector_self_controls_20261031/`（约2.5 MB，周期错配对照）。再评估较大的历史输入/结果目录：`outputs/r22_s1_c4_support_replay_20261029/`（约11 GB）、`outputs/r22_s1c_shared_off_phase_period5_20261029/`（约251 MB）及 `outputs/r22_s1b_phase_boundary_20261029/`（约18.7 MB）；这些目录在 ChatGPT 审查结束前保留，以便需要时核对原始输入、完整 Production 轨迹和失败现场。审查后按用户“只保留必要记录/配置/审计和高价值结果、最终项目占用小于20 GB”的要求清理可再生与重复产物，保留紧凑证据、配置、脚本和清理账，再复核实际项目体积。审查前不删除这些运行现场。
- **当前容量快照：** `du -shL .` 估算约20 GB，`df -h .` 可用6.7 GB；扫描遇到既有 broken symlink，因此此值只作近似，不作为最终验收。审查后执行清理并重新测量。

## 2026-10-31 R22-S1e 当前 Mode-B ON/OFF/TO 诊断

- **保留至本轮 ChatGPT 审查结束：** `outputs/r22_s1e_current_modeb_role_triplet_20261031/`，当前约4.0 MB；包含3个当前 Mode-B Native CUDA Production 分支及原始逐格/CSI/CFAR运行记录。Stage2 输入为指向既有49,204,480-byte role文件的符号链接，没有复制新的大 IQ。
- **Git 紧凑替代：** `support_failure_diagnostic/evidence/s1e/` 含72个登记文件、约1.5 MB；逐文件保留 SHA/大小与源路径，包括runtime XML、branch manifest、日志、phase trace、complex-cell audit、CSI tap、CFAR geometry、三个 Stage2 role 配置/报告/背景映射及A0_ON杂波生成证据。未纳入原始 Stage2 `.bin` 和全量 target truth CSV。
- **审查后清理：** 若 ChatGPT 确认紧凑证据足以支持审查，删除上述 S1e 本地输出目录，保留 Git 紧凑证据、复现脚本、分析表和清理记录。历史 S1b/S1c/S1d 输入与完整 Production 输出仍按前一节待审范围处理；删除前重新确认无其他未归档证据依赖。
- **最终空间门：** 本阶段尚未完成审查后清理，项目最终 `<20 GB` 尚未验证；中间占用允许超过该值。审查完成后再清理可再生/重复 raw 数据，并以实际 `du -shL` 与 symlink-aware 文件统计复核。

## 2026-10-31 用户授权清理旧运行产物

清理前确认没有对应的 R22/旧 pilot 运行进程。删除的是已完成、已复核或有紧凑证据替代的旧运行树与重复重试；未删除当前 S1b–S1e 审查现场、Screening `GATE_FAILED` case、H0 compact case archives、配置、脚本或研究报告。

| 删除路径 | 清理前分配空间 | 理由与保留替代 |
|---|---:|---|
| `outputs/r22_extension_20261028_v2_range_roi/` | 5,342,416,896 bytes | 六 seed 工程完整运行树已由 `docs/research/phase_r/R22/15_v2_six_seed_engineering_results_20261028.md` 和紧凑审查 ZIP 汇总；ZIP CRC 检查通过并保留在 `outputs/r22_chatgpt_review_evidence_v3_six_seed_20261028.zip`。保留逐 seed 任务指标、估计值、资源摘要、运行身份和历史结果，不再留 48,558 个重复/详细运行输出文件。 |
| `outputs/r22_extension_20261028_v1a/` | 310,095,872 bytes | V1A 失败条件和关键 Production/reference 证据已保存在 `docs/research/phase_r/R22/evidence/extension_engineering_v1a/seed1930101/`、`docs/research/phase_r/R22/11_v1a_engineering_gate_result_20261028.md` 与 `docs/research/phase_r/R22/12_v2_range_support_domain_amendment_20261028.md`；完整本机运行树已由精简审查证据替代。 |
| `outputs/r22_extension_20261028/` | 116,760,576 bytes | 首个工程失败的失败原因、执行身份和清理审计保存在 `docs/research/phase_r/R22/10_engineering_gate_failure_20261028.md` 与 `docs/research/phase_r/R22/evidence/extension_engineering/seed1930101/`；仅清除已归档的运行派生产物。 |
| `outputs/r22_v1a_gate_diagnostic_cells_20261028/pipeline/` | 203,317,248 bytes | 预审诊断运行数据可重建；保留根目录的 run manifest、场景模板和目标生成 manifest。 |
| `outputs/r22_v1a_gate_diagnostic_maps_20261028/pipeline/` | 205,877,248 bytes | 预校正 map 与实际 robust estimator 输入不一致，不能作为 estimator 输入证据。先将诊断评估、七份评价 CSV、配置、manifest、计划和清理记录共 17 个文件（3,833,777 bytes）逐字节归档到 `docs/research/phase_r/R22/evidence/v1a_gate_diagnostics_20261028/`，再清除此 pipeline；GPU 预检文本原件继续保留在该输出根目录。 |
| `outputs/phase_r_e1_e4_e5_cuda_pilot_seed101_20261022_retry1/` 及四个 `retry_v2/v4_*_cleanclone/` 目录 | 213,757,952 bytes | 旧 pilot 的失败/重复重试，无研究报告或后续任务引用；主 pilot 报告、最终输出和高价值审计资料保留。 |
| `outputs/r22_chatgpt_review_evidence_v3_six_seed_20261028/` | 7,139,328 bytes | 仅为保留 ZIP 的解包副本；ZIP 本身继续保留供审查使用。 |

本次共释放 **6,399,365,120 allocated bytes**。V1A map 诊断包中归档的 17 个文件复制后逐项比较大小和字节内容；GPU 预检原件留在本机诊断 metadata 根。六 seed 审查 ZIP 在清理前通过 CRC 检查。`.scratch/` 中仍被历史审计文档引用的 R3 失败现场/紧凑报告没有删除。当前 R22 的 S1 replay、S1e 输出、失败 Screening 输入、H0 compact archives 和紧凑汇总均保留供后续审查与分析。

清理后 `du -s -B1 .` 测得工作树占用 **14,588,624,896 bytes（14.59 GB 十进制）**，其中 `outputs/` 为 **13,282,574,336 bytes**；`df -B1` 显示所在文件系统可用 **19,259,727,872 bytes**。当前占用低于 20 GB。S1b–S1e 目录仍按待审记录保留；其后 ChatGPT 审查结束时，再按本台账后续记录决定是否清理并复验最终空间门。
