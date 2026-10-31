# R22 V2 首个配对工程 case 记录

日期：2026-10-28
设计：`R22_EXTENSION_20261028_V2_RANGE_ROI`
执行范围：seed1930101、2.5 m/s、20 dB、20 targets、10 periods、beam 50，0 ns 与 +4 ns 配对。
状态：两个 case 均 `IMPLEMENTED / EXECUTED / EVALUABLE`；`PERFORMANCE_VERIFIED=false`。

## 本次回答的问题

在 V2 共同距离支持域和不变的质量门下，真实 CUDA Production 是否能够完成 0 ns 与 +4 ns 配对链路？单 seed 是否显示未知 4 ns 时延造成任务损失，以及 evaluator-only 已知校正或确定性盲校正能否恢复？

## 执行身份

- 仓库工作分支：`codex/unknown-system-error-next-stage`
- 执行时 HEAD：`c1215354b4354fb598bc747eb799ff9995eeee58`
- 执行时工作区有 5 个 tracked 文件修改；完整脚本身份见两份 `r22_run_manifest.json` 的 `source_identity.files`。本次运行不是由干净提交工作树产生，必须将该 commit 与 manifest 记录的源码文件 SHA 一起使用。
- Production 二进制 SHA-256：`d9e4d2e0b8429c5415e94be1af0230f3c0148455c394dc6d661e7e6c13a4ad1e`
- Stage2 二进制 SHA-256：`ca8aa9eb8b7d97e5743a27e67c312e66a4efecbf2c76880db581192afd08ce8e`
- V2 方法配置 SHA-256：`588c36904f62febad8476ddccb789ba67de2e9c23dc2ff12b52418eb06706bbc`
- 研究总纲 Git blob：`e637e3769bde7814298ff012d0f42301884e2985`，本阶段未修改。
- GPU：本机 NVIDIA GeForce RTX 3050 Laptop GPU，4 GB；每个 case 的 `native_cuda_preflight` 留存了设备状态。`use_corex=false`，执行的是本机 Native CUDA。

每个 case 的完整运行入口：

```bash
python3 scripts/phase_r/run_r22_cuda_pilot.py \
  --output-root outputs/r22_extension_20261028_v2_range_roi/engineering/seed1930101/reference_0ns \
  --phase engineering --seed 1930101 --speed-mps 2.5 --snr-db 20 --delay-ns 0 \
  --target-count 20 --period-count 10 --first-beam 50 --beam-count 1 \
  --method-config docs/research/phase_r/R22/config/r22_method_config_v2_range_roi.json
```

配对 +4 ns case 使用同一命令，将输出目录改成 `.../error_plus4ns`、`--delay-ns 4`。

## 距离域和面积杂波

V2 对所有 Production 方法、角色、误差条件和 evaluator 使用相同的 0-based 闭区间 `rg_st=666, rg_ed=1197`。两个 case 的全部实际 Production XML 均读回该范围：0 ns 为 36/36，+4 ns 为 54/54。每个 Production 分支的 runtime XML、配置审计、逐 beam stage、CSI tap、CFAR diagnostics、检测/航迹记录和 branch manifest 均保留。

距离轴 `origin=82,802.6768996 m`、`spacing=2.498270483333333 m/bin`；连续面积杂波 ROI `84,500–85,500 m` 映射到 bins `680–1079`，目标也由该 ROI 生成。两个 A0_ON Stage2 报告均确认真实注入：1,300 packets、6,508,782 samples、面积均值功率 `0.0225128995081`、lag-1 相关 `0.979999807132`、热噪声功率 `0.00100014187201`。

+4 ns case 中 A0_ON 写出一份配对 C+N 背景，其余 A0/A1 的 OFF、ON、TO 输出复用该背景。A1/OFF/TO 报告里的 `continuous_area_packets=0` 是“没有重新生成背景”的计数，不表示没有杂波；五份 `background_reuse_mapping.csv` 每份均有 10/10 个 period 映射为 `matched`，场景 manifest 记录相同 `A0_background` 身份。所有四类输入因果 triplet 检查通过，最大误差不超过冻结的 `5e-5`。

## 生产门与评价覆盖

| Case | 生产分支 | 参考校准文件 | ON 评价行 | ON 目标-周期机会 | 无效 truth | 结果 |
|---|---:|---:|---:|---:|---:|---|
| 0 ns | 36/36 passed；5 个零时延物理校正角色按规则标为 `NOT_APPLICABLE_ZERO_DELAY_REFERENCE` | 8/8 passed | 12/12 EVALUABLE | 2,400 | 0 | `EVALUABLE=true` |
| +4 ns | 54/54 passed | 10/10 passed | 18/18 EVALUABLE | 3,600 | 0 | `EVALUABLE=true` |

每个方法/角色的 CFAR `union_dynamic_in_band` 与 `union_full_band` valid-CUT 计数均为 `5,272,800`；各分支 Production path gate 通过，beam stage、CSI cancellation、CFAR summary 均有实际运行日志。两 case 的 `PERFORMANCE_VERIFIED=false`：这里仅有一个独立 seed。

## seed1930101 的描述性任务结果

主要端点为 200 个 target-period opportunities 上的正确目标命中比例。只列出决策相关条件：

| 条件 | 命中 | 命中率 |
|---|---:|---:|
| 0 ns A0 Ideal/No-error | 167/200 | 0.835 |
| +4 ns C0 Production Current | 25/200 | 0.125 |
| +4 ns PK Known physical correction upper bound | 167/200 | 0.835 |
| +4 ns P1 target-free OFF blind correction | 167/200 | 0.835 |
| +4 ns P2 robust DDC-RB Mode-A / Mode-B | 173/200、171/200 | 0.865、0.855 |
| +4 ns PKR known correction + robust DDC-RB Mode-A / Mode-B | 175/200、173/200 | 0.875、0.865 |

在该单 seed 上，`recoverable space = PK − C0 = 0.710`；P1 的描述性 `actual recovered = 0.710`、恢复比例 1.0。不得把这组比例当统计估计或可推广的 Pd 增益。A0 与 PK 的杂波残余闭合 spread 为 `1.5643801773990162e-6 dB`。本 case 的 PKR 相对 PK 额外残余抑制为 `-0.02248363873422221 dB`（负值表示本次没有额外残余抑制），残余指标按 CSI `clutter_band_strong_power_top10pct` period-beam 行计算；它与融合噪声的 scalar tap `active_support_unmasked` 不应混为一谈。

盲信息审计：

- P1 使用同 seed A1_OFF 的目标自由 C+N 原始输入，估计 `3.215904742478316 ns`，真实注入为 4 ns；truth 未用于估计或应用校正。
- R22 M1 使用 A1 ON 的 C1/C2 原始 F1/F2 估计 `3.7521942288603465 ns`，support=2364，truth 未用于估计。
- Known PK 使用 4 ns 只作为 evaluator-only 物理校正上限；`truth_used_in_estimator=false`，`truth_used_to_apply_correction=true`。
- 全部 correction 输入的 10 periods、OFF/ON/TO 应用审计状态均为 passed。

结果中 angle、position、range、folded Doppler 和 ground-speed 参数记录可评价。但 `radial_velocity_mps` 的 0 ns 与 +4 ns 全部条件均为 0/200 valid，missing reason 为 `ambiguity_unresolved`（其余为未匹配检测）；因此本次没有可报告的径向速度 RMSE。独立 target-free H0 seeds 未运行，不能报告经验 Pfa。

## 规模、清理和阶段门

V2 计划总量仍为 594 cases；截至本记录仅启动 2 个 case，它们属于同一个 seed 的配对，不是两个独立 seed。当前工程门覆盖 `1/6` engineering seeds；screening、confirmatory、40-target、3-target bridge 和独立 H0 均未启动。

Runner 清理后：

- 0 ns：移除 1,161 个可再生成文件，共 `4,430,213,108` bytes；`remaining_bin_count=0`。
- +4 ns：移除 1,841 个可再生成文件，共 `11,319,692,956` bytes；`remaining_bin_count=0`。
- 两 case 仍保留 compact case manifest、输入/输出哈希、XML、日志、CSI/CFAR CSV、evaluation CSV、reference gamma 与 cleanup ledger。
- 整个 worktree 当前约 8.4 GiB，低于 20 GiB 项目上限。

**阶段判断：** `V2 first paired engineering seed = PASS_WITH_LIMITATIONS`。这一个 seed 显示 +4 ns Current 命中退化，Known correction 闭合恢复，P1 target-free blind correction 的命中数与 Known 相同；同时 PKR 没有显示额外残余抑制，径向速度不可评估。现有结果支持继续完成余下五个 engineering seeds 来检验可重复性，不支持统计显著性、经验 Pfa、普适盲估计能力或 AI 必要性结论。任一后续 seed 的生产/配对/有效 CUT/真值 lineage/reference gate 失败时，停止后续矩阵并定位根因。

ChatGPT 审查应从 [V2 配对工程审查提示词](CHATGPT_REVIEW_PROMPT_V2.md) 开始，并使用提示词指定的合并证据 ZIP。
