# R22-S1c：GMTI05 共同 target-free OFF CSI 相位反事实结果

## 阶段裁决

**`PASS_WITH_LIMITATIONS / MECHANISM_EVALUABLE`；整个 R22 Screening 仍为 `GATE_FAILED / INCOMPLETE`。**

选定组的 SNR20/SNR30 robust support 差从 **18**（各自 ON CSI 相位：25 对7）缩小到 **1**（共同 target-free OFF CSI 相位：21 对22）。同一 ON 输入、同一 raw range-phase、同一 C4 gate 下，只替换 CSI range-phase 向量就显著改变 support。因此这次数据支持：**本组的 SNR30 support 失败对 ON 自适应 CSI range-phase 向量高度敏感；相位向量差异足以解释大部分 25→7 的组间 support 差。**

这并不证明目标污染是 ON 相位向量差异的唯一来源，也不证明使用 OFF 向量是可部署算法或带来 GMTI 任务收益。SNR30 baseline 仍保留为 `support=7 / NOT_EVALUABLE`；门槛未降低、没有 fallback。没有执行大规模 Screening、统计性能比较或 AI 训练。

## 研究问题和冻结范围

- 长期目标：识别传统 GMTI 处理链中真实、可重复且影响任务的瓶颈。本次只服务于 R22 C4 Robust DDC-RB 的局部参考支持失败归因。
- 科学问题：在 `seed1932001 / 2.5 m/s / +4 ns / beam 50 / azimuth 52 / range 1065–1197`，把两种 SNR 条件的 CSI 距离相位统一为 target-free OFF 估计，是否会改变 C4 的 robust support？
- 输入 SNR：20 dB 和30 dB；20个目标、原场景10期。只将与 `GMTI05` 对应的 `period_0004.bin` 提供给每个 Production 分支。
- C4：`Mode-B / robust_ddc_rb`，分组133 bins，`min_support=8`，相位残差门限 `0.35 rad`。全部分支均未向 estimator 提供 truth。
- Stage2：复用已有输入，没有重跑。已有 Stage2 证据显示连续面积地杂波为1300 packets、6,508,782 samples，平均功率 `0.0221596696662`；OFF20/OFF30 的十期输入 SHA 列表完全一致。

## 执行身份

- 仓库/分支：`shy-sgsg/PGRCC` / `codex/unknown-system-error-next-stage`。
- 执行时 Git commit：`0189933420d73bb04c3a466e633bd664a23871c0`；tracked worktree 当时干净。研究总纲 blob 仍为 `e637e3769bde7814298ff012d0f42301884e2985`。
- Production：Native CUDA，`USE_COREX=OFF`；binary SHA-256 `16cb0f2d35596ff98ab8bdcbc9571b972b99e217053cf0b1b7f6b49168c8c998`。
- GPU：RTX 3050 Laptop。runner 首次快照为 P8、41°C、6.11 W、0%、2236/4096 MiB；完成快照为 P3、44°C、14.64 W、0%、2236/4096 MiB。这里仅记录运行身份，不报告性能基准。
- 单期输入 SHA-256：ON20 `dcd6cbfff80dfd68a24026782d5f8b3cae8074db58df28f340630998fbf4d024`；ON30 `d324584380c67b37ea19f3e7efb7ecfdc33ed7287b2e4c97fca5aa98e9d2f831`；target-free OFF `a8b253c035d387da24625f189a576fe41f56789fbc1c2870c652ef7a75e25f48`。
- S1c runner SHA-256 `3542ed8d230e56960f300097bc46f93c272c6482e536f9eaba976e71988da99f`；离线 analyzer SHA-256 `858d8b05219c6337c3134e8bf61a81def7bab72bc59961cece8d00e1048dd06d`。
- 五个 CUDA Production 分支均 `status=passed`、return code 0；每个分支实际 runtime XML、分支 manifest、Production log、CSI tap、CFAR geometry 和所选组逐级 trace 均已归档。

## 周期映射和 trace 完整性

输入目录仅含原 Stage2 `period_0004.bin`，其 SHA 在 manifest 中与原 SNR case 的 period 4 输入逐一核对。Production 的唯一输入槽为 `1=...`，输出 `result_id=GMTI01`；本次将该记录明确映射回原 Stage2 零基索引4 / `GMTI05`。trace selector 为 `1,50,52,1065,1197`。

CSI/γ trace 在五个阶段各有133行，共665行，覆盖选定组全部 range bins。CFAR geometry 和 CSI tap 文件自身使用 `period_id=0`，而 gamma/selected-cell/phase trace 使用 `period_id=1`、`result_id=GMTI01`。这些文件沿用各自局部编号；审计通过唯一输入文件 SHA 绑定原始 GMTI05，不跨文件混用内部编号。

## 选定组结果

| 条件 | CSI range-phase | 有效互谱单元 | support | 初始 phase coherence | 相位中心 rad | Gamma 状态 |
|---|---|---:|---:|---:|---:|---|
| SNR20 ON baseline | 本条件 ON 自估 | 133 | 25 | 0.2028995671 | 1.758323204 | `OK` |
| SNR30 ON baseline | 本条件 ON 自估 | 133 | 7 | 0.1172000323 | 0.980749850 | `NOT_EVALUABLE / insufficient_robust_support` |
| Target-free OFF | Mode-A OFF 自估 | 133 | 55 | 0.7418131251 | 1.345598337 | `OK` |
| SNR20 ON counterfactual | 两条件共同 OFF 向量 | 133 | 21 | 0.1855220752 | 0.576079629 | `OK` |
| SNR30 ON counterfactual | 两条件共同 OFF 向量 | 133 | 22 | 0.0228710435 | 0.870650561 | `OK` |

离线 analyzer 使用源码 `fitRobust()` 的规则，从 Production selected-cell CSV 重算 `F2·conj(F1)`、单位复相位中心及 `|residual|≤0.35 rad` 集合。五种条件的重算 support 与 CUDA Gamma summary 一致；可评价组的复数 Gamma 也在 `2e-6` 误差内闭合。

表中的 `phase_coherence` 是 robust 筛选前 133 个单位互谱相位的 resultant length；C++ 不用一个固定 coherence 值判定 group 是否通过。最终 gate 是相位中心周围 ±0.35 rad 内的保留数，因此 SNR30 counterfactual 的 coherence `0.0229` 与 support `22 / OK` 可以同时成立。

两种 SNR 的 ON baseline 与各自 counterfactual 使用完全相同的输入 SHA 和 raw-phase 向量 SHA。counterfactual 的 CSI-phase 输出与 target-free OFF 向量逐字节相同，SHA 均为 `dc6f84425613c0ba97526c02ef92b79fbcc8c8cad5f120b69c98df79e9b51747`。在133个目标距离 bin 上，OFF 向量相对 ON20/ON30 自估 CSI 向量的平均绝对 wrapped phase 差分别为 `1.7760 rad` 和 `1.8242 rad`。

因此，固定共同 OFF 向量时 SNR20 support 从25变21，SNR30 从7变22；SNR20/SNR30 support 差由18降至1。该对照将**相位向量替换对这一组 support 的因果影响**与其它算法改动分开；它没有将 OFF reference 变成盲在线估计，也没有识别 ON 相位差异究竟由目标成分、估计器非线性还是二者交互造成。

## CSI 与 CFAR 质量记录

- 五分支均有 CSI tap summary，四个 tap ROI 各记录20,748个有效单元。`active_support_unmasked` 状态为 `diagnostic_noise_and_clutter_mixed`；其 coherence/RMSE 不用作目标保持或杂波消除收益结论。
- 五分支的两种 CFAR geometry 均有 `527,280 / 527,280` valid CUT；输入面为130×4096，边缘无效格5,200。各分支的 CUT hit 数只作为原始诊断记录；没有独立 H0 truth join，所以 false CUT/cluster/track counts 与 Pfa 均 `NOT_EVALUABLE`。
- 当前任务不是检测性能评价。未计算或比较 Pd、Pfa、目标幅相保持、SCNR、速度/方位误差；不得从 geometry hit 数推断任务改善。

## 结论边界与阶段状态

- `IMPLEMENTED`：单期 runner 和离线逐单元核验脚本；v1 十期 attempt 因 phase sidecar 会跨期覆盖而明确作废，见 [首轮失效说明](evidence/s1c/ATTEMPT1_INVALIDATION.md)。
- `EXECUTED`：五个本地 Native CUDA Production 分支，使用已有 Stage2 输入。
- `EVALUABLE`：本组 support、phase-center、phase-vector intervention、所需输入身份、CSI taps 和 CFAR geometry 可评估；共同相位 counterfactual 可重复解释这一组的 gate 变化。
- `PERFORMANCE_VERIFIED=false`：单 seed、单 beam、单组机制诊断；没有目标任务收益或统计显著性证据。
- C4 的 SNR30 原始 ON reference 失败记录仍保留；共享 OFF 向量是带 privileged reference 的反事实，不构成 C4 部署修复。
- 目标污染解释仍为 `NOT_PROVEN`；不因此启动目标保护新算法。研究总纲不变，AI entry gate 仍为 `FAIL`。R22 full Screening、confirmatory、dense、bridge 和 H0 仍未完成。

## 证据和复核

- [紧凑 CUDA 证据包](evidence/s1c/v2/)：含五分支运行配置/XML/log、branch manifests、逐级 CSI/Gamma trace、逐单元 F1/F2、phase vectors、CFAR/CSI tap 汇总和 SHA 清单，约1.45 MB。GitHub 副本将文本统一为 LF、清理日志行尾空格；[evidence manifest](evidence/s1c/v2/evidence_manifest.json)逐文件记录原始源 SHA 与副本 SHA。`outputs/` 中原始 Production logs/CSV 未改。
- [Production 分析 JSON](evidence/s1c/v2/s1c_period5_analysis.json)
- [五条件 support 对照表](evidence/s1c/v2/s1c_period5_group_comparison.csv)
- [逐 bin 相位残差与保留集合](evidence/s1c/v2/s1c_period5_phase_bin_diagnostics.csv)
- [机器证据 manifest](evidence/s1c/v2/evidence_manifest.json)

实际执行命令：

```bash
python3 scripts/phase_r/run_r22_s1c_period5_shared_off_phase.py \
  --output-root outputs/r22_s1c_shared_off_phase_period5_20261029
python3 scripts/phase_r/analyze_r22_s1c_period5_shared_off_phase.py \
  --output-root outputs/r22_s1c_shared_off_phase_period5_20261029 \
  --evidence-dir docs/research/phase_r/R22/support_failure_diagnostic/evidence/s1c/v2
```

runner 默认使用本机已构建 binary `/tmp/pgrcc_r22_s1b_cuda_20261029/GMTI_pipe_core`；Stage2 simulator 只作存在性检查，本轮没有启动 Stage2。执行 binary 仍要求 SHA 与 runner 内冻结值一致。

**停止点：** S1c 已完成。下一步先做 ChatGPT 独立科研审查，再决定是否针对 ON 自估 CSI range-phase 的差异做更窄的目标/背景机制实验；本报告不自动启动该后续实验，也不恢复 R22 大矩阵。

## 2026-10-31 补记：目标距离坐标核对

对 S1c manifest 所绑定的 SNR30 `period_0004.bin`（SHA-256 `d324584380c67b37ea19f3e7efb7ecfdc33ed7287b2e4c97fca5aa98e9d2f831`）重新核对其 Stage2 真值后，确认 `R22_T010` 在 period 4、beam 50 的 `range_bin/expected_bin` 为 **1076/1077**，距离约 85,490.16 m。S1c 诊断组覆盖 range 1065–1197；同组还覆盖 T004（1124–1125）、T012（1162–1164）、T017（1130–1131）和 T019（1102–1103）。因此，先前审查文字将 T010 标为 range bin 1175 的说法不符合这份场景真值；1175 是组内普通距离单元，不是 T010 的真值距离单元。

相邻 S1b `A1_TO` target-only 分支的 `production_calibration_selected_cells.csv` 在 GMTI05、beam 50、`az_index=52`、range 1076 记录到 `|F1|=29.5312`、`|F2|=127.0090`。该单元与 T010 真值距离一致，支持目标响应进入本次参考组/行的判断。S1c 相位逐 bin 表中，T010 的 1076、1077 两格在 SNR20 baseline 均被保留，在 SNR30 baseline 均被剔除。

逐 bin 支持集合比较显示，SNR20 与 SNR30 baseline 的保留集合交集只有 **1 格**：SNR20-only 有24格、SNR30-only 有6格。SNR20-only 的24格中，3格位于组内目标真值距离单元，另外21格不在目标真值距离单元；目标距离单元本身共11格，SNR30 baseline 中没有一格通过 frozen phase gate。因此，不能把18格的 support 数差简单解释成直接剔除了目标距离单元。目标加入后改变 CSI phase vector、再使非目标距离单元跨越相位 gate，是与当前干预结果相符的机制解释，但其具体贡献仍未唯一分解。

这些证据修正了目标距离坐标，并确认目标响应与失效参考组存在空间重合；但 target-only 分支不是 S1c 的 ON 输入，且该重合本身不能证明目标响应造成了全部 25→7 的支持数下降。S1c 已证明支持数对 CSI range-phase 向量替换敏感；ON 相位差异的来源及其对支持下降的具体贡献仍未唯一归因。未降低门槛、未更改方法、未启动新 CUDA 实验。独立审查完成前继续保持停止状态。

审计来源：S1c `s1c_manifest.json` 与 `s1c_period5_phase_bin_diagnostics.csv`；Stage2 `A1_ON/truth/moving_target_truth.csv`（SHA-256 `5bad1373ffcf3e7b4ccf7ffa231cae6aae567fb12f707473919b12c35ce12f1e`）；S1b target-only `production_calibration_selected_cells.csv`（SHA-256 `f873bfe687b81a92177c4cd1bb3e8a54d8d8d23e58621b4cec08db0536636c5f`）。[11个目标距离单元交叉审计表](evidence/s1c/v2/s1c_target_overlap_audit.csv)逐行列出真值范围、SNR20/30 phase 残差与保留状态、S1b target-only 响应幅度，以及相关 ON/TO/真值/处理表 SHA-256；交叉表 SHA-256 为 `4c1d69495b08b96d9ef8837358891ba79cb13b58a7ec63c6963cedfa91e56887`。
