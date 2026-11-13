# R22 V2 六个工程 seed 结果与审查边界

日期：2026-10-28
设计：`R22_EXTENSION_20261028_V2_RANGE_ROI`
本地执行分支：`codex/unknown-system-error-next-stage`
执行时 Git HEAD：`c1215354b4354fb598bc747eb799ff9995eeee58`
研究总纲 blob：`e637e3769bde7814298ff012d0f42301884e2985`（本阶段保持不变）

## 阶段结论

六个 seed 的 0 ns 与 +4 ns 配对工程矩阵已完成。最终纳入描述性汇总的 12 个 case 均为 `IMPLEMENTED=true`、`EXECUTED=true`、`EVALUABLE=true`；全部 case 的 `PERFORMANCE_VERIFIED=false`。最终矩阵包含 216 个 0 ns 与 324 个 +4 ns Production 分支，以及 72 个 Stage2 variant。每个有效 case 的生产和评价门均通过。

**跨 seed 汇总有一个必须先审查的版本边界：** seed1930101 的两种误差条件使用 Stage2 binary SHA-256 `ca8aa9eb8b7d97e5743a27e67c312e66a4efecbf2c76880db581192afd08ce8e`；seed1930102 的有效精度修复重跑及 seed1930103–1930106 使用 `47fa5ec34ca15cdc4edeb35226eefa941a78ee344aa2311e5e7984c811dcb40f`。差异对应 Stage2 XML double 序列化改为 `max_digits10`，修复 Rmin 从 `82802.6768996 m` 被截短成 `82802.7 m` 的问题。seed1930102 的旧 0 ns 失败目录仍保留，但不纳入结果表。请独立审查这一变更是否足以影响配对和跨 seed 可比性；在此判断完成前，不把六 seed 汇总描述为已完成确认性性能验证。

## 执行身份和复现入口

- Production：本机 NVIDIA RTX 3050 Laptop GPU，Native CUDA，`USE_COREX=false`；Production binary SHA-256：`d9e4d2e0b8429c5415e94be1af0230f3c0148455c394dc6d661e7e6c13a4ad1e`。
- Stage2：前述两个 SHA-256 版本；各 case 的代码身份、工作区状态、设备预检和源文件 SHA 位于对应 `r22_run_manifest.json` 的 `source_identity` 与 binary 字段。
- 执行时 HEAD 为 `c1215354b4354fb598bc747eb799ff9995eeee58`，运行时工作区含 tracked 改动；不得仅用 HEAD SHA 代替源码身份。证据包附上源码快照、diff 和全部有效运行 manifest。
- 方法配置：`config/r22_method_config_v2_range_roi.json`；机器可读设计：`config/r22_extension_experiment_design_v2_range_roi.json`。
- 单 case 命令模板：

```bash
python3 scripts/phase_r/run_r22_cuda_pilot.py \
  --output-root outputs/r22_extension_20261028_v2_range_roi/engineering/seed1930101/reference_0ns \
  --phase engineering --seed 1930101 --speed-mps 2.5 --snr-db 20 --delay-ns 0 \
  --target-count 20 --period-count 10 --first-beam 50 --beam-count 1 \
  --method-config docs/research/phase_r/R22/config/r22_method_config_v2_range_roi.json
```

每个 seed 的 +4 ns 条件使用相同配置和 seed，将 `--delay-ns 4` 并写入对应 `error_plus4ns` 输出目录。复现必须遵循 manifest 中的实际源码、binary、输入身份及运行配置，不能把上面的模板当作本次完整历史命令。

## 生产门、面积杂波和配对

- 最终 12 case：`36/36` 个 0 ns 分支与 `54/54` 个 +4 ns 分支通过；有效 ON 行无 invalid truth。seed1930102 旧版 0 ns 失败尝试的 `EVALUABLE=false`，只作为错误案例审查，不计入 12 个最终 case。
- 共同处理区间为 0-based 闭区间 `[666,1197]`；Production XML、运行时配置、beam stage、CSI tap、CFAR geometry、日志、branch manifest 与 evaluator CSV 均纳入审查包。各分支的 union CFAR valid CUT 为 `5,272,800`，应从逐分支 geometry 独立核实。
- 每个 seed 的有效面积杂波报告均有 `continuous_area_packets=1300`、`continuous_area_samples=6,508,782`；六 seed 面积平均功率均值为 `0.0226353293`，范围 `[0.0225109350,0.0227848916]`，lag-1 相关约 `0.98`，热噪声约 `0.001`。+4 ns 条件配对复用同 seed 的 C+N 背景；应核对 background reuse map，而不能把复用场景的新增注入计数 0 解释为无杂波。
- 每 seed 有 20 个目标、10 个周期；每个方法的主要 ON 任务分母为 200 个 target-period opportunities。统计独立单位是 6 个 seed，不是合计 1,200 个目标周期。

## 六 seed 任务命中结果

下面计数均来自有效 case 的 `evaluation/task_metrics.csv`，每 seed 每方法为 200 个可评价机会。`PK` 的真实 4 ns 只用于 evaluator-side Known physical correction；`P1` 是目标自由 OFF 估计后作物理 delay correction；其估计输出在运行目录的 `case_manifest.json` / 证据包的 `case_manifest_review_extract.json` 中。

| Seed | A0 0 ns | C0 +4 ns Current | PK +4 ns Known | P1 +4 ns blind | P2 Mode-A | PKR Mode-A |
|---:|---:|---:|---:|---:|---:|---:|
| 1930101 | 167 | 25 | 167 | 167 | 173 | 175 |
| 1930102 | 151 | 24 | 151 | 145 | 150 | 151 |
| 1930103 | 140 | 22 | 140 | 137 | 140 | 144 |
| 1930104 | 148 | 25 | 148 | 148 | 154 | 152 |
| 1930105 | 153 | 36 | 153 | 156 | 162 | 162 |
| 1930106 | 163 | 34 | 163 | 163 | 169 | 168 |
| 合计 / 比例 | 922/1200 (0.7683) | 166/1200 (0.1383) | 922/1200 (0.7683) | 916/1200 (0.7633) | 948/1200 (0.7900) | 952/1200 (0.7933) |

六 seed 的等权描述性差值：

- `A0 − C0 = 0.6300`（63.00 个百分点）；每 seed 的差为 `[142,127,118,123,117,129]/200`。
- `PK − C0 = 0.6300`，Known correction 的命中计数与 A0 在每个 seed 完全相同。
- `P1 − C0 = 0.6250`；`P1 − PK = -0.0050`。按命中比例定义，P1 的描述性 recoverable-space 比例约为 `0.625/0.630 = 99.2%`。
- P2 Mode-A 平均 `0.7900`、PKR Mode-A `0.7933`；相对 PK 分别高约 2.17 和 2.50 个百分点。P2/PKR Mode-B、R9 固定 D3、Huber D3、M1 Physics Matched、C1–C4 及逐 seed 结果见证据包评价 CSV。小样本下这些差值只供工程比较，不是显著性结论。
- target-free OFF 的 D3 Huber delay estimate（作为 P1 估计）六 seed 为 `[3.2159,3.1097,2.9136,3.0999,3.2378,3.1621] ns`；均值 `3.1232 ns`，样本标准差 `0.1165 ns`，相对真实 4 ns 的平均偏差 `−0.8768 ns`。需检查所用估计字段、盲信息边界与实际校正输入是否闭合；不能只依据命中比例推断物理估计正确。

这些是一个速度（2.5 m/s）、一个 SNR（20 dB）、一个目标密度（20 个）和六个工程 seed 的描述性结果。六 seed 既不是确认性矩阵，也不能支持跨场景的一般性 `Pd` 结论。

## 未闭合指标

- 六 seed 有 360 条 paired OFF H0 诊断记录；记录标记为 `DESCRIPTIVE_ONLY_NOT_STATISTICAL_PFA`、`paired_OFF_role_not_an_independent_H0_seed`、`NOT_EVALUABLE_NO_INDEPENDENT_H0_CUT_UNIT`。没有独立 H0 seeds，不能报告经验 Pfa。
- `radial_velocity_mps` 在全部方法的有效参数记录中为 0 个 valid opportunity，原因包括 `ambiguity_unresolved` 和无匹配检测；不得给出径向速度 RMSE。
- 证据包附有 CSI tap、CFAR geometry、检测、航迹、目标关联和参数评价记录。需检查 clutter residual power、目标幅相保持、Output SCNR、跨周期 track 质量以及 range/angle/ground-speed 等可评价参数；不能把标量 CSI 的混合噪声指标当作纯杂波残余。
- 运行时没有独立 H0 场景，也没有径向速度可评价数据；这两项不是“零虚警”或“零速度误差”。

## Rmin 精度失败与修复

seed1930102 初次 0 ns 运行使用旧 Stage2 binary，XML double 默认精度将 `Rmin=82802.6768996 m` 输出成 `82802.7 m`，造成目标真值 range-bin 对齐失败，最终 `EVALUABLE=false`。修复为 `std::setprecision(std::numeric_limits<double>::max_digits10)` 后重新编译，运行 `tests/test_stage2_rmin_precision.py` 并重跑该 case；修复后 36/36 分支通过、12/12 ON 行 EVALUABLE、200 个机会均有效。旧失败目录保留并放入审查包，不能混进本报告的六 seed 结果。

## 资源门和下一步边界

- 六 seed 最终 12 个 case 的保留输出约 `4,832,844,979 bytes`；当时整个项目工作区约 `8.1 GB`，低于用户要求的 `20 GB` 上限。实验临时 raw 文件已按各 case cleanup ledger 清理，必要 manifest 和审计输出保留。
- 按当前完整 runner 留存大小估算 594-case 计划约需 `240 GB`；按实测 case wall time 粗略估算串行约 `141.7 h`。这些是工程外推，不是已执行结果，当前磁盘保留策略下 `resource gate = NOT_CLEARED`。在建立并验证不会丢失审计证据的紧凑归档/逐 case 清理策略前，不启动 screening。
- 本阶段未训练 AI、未开发 Router、未启动 screening 或 confirmatory matrix。六 seed 结果显示 P1 等确定性盲校正已接近 Known 上限；目前没有 AI 必要性的证据。下一项科研任务须先根据独立审查决定，并解决 Stage2 binary 跨版本可比性和资源门。

## 状态标签

| 标签 | 本阶段状态 | 范围 |
|---|---|---|
| `IMPLEMENTED` | true | V2 runner、共同 range override、Rmin 精度修复和评价链 |
| `EXECUTED` | true | 本机 Native CUDA 的六个 seed、两种 delay 条件及保留的一次失败尝试 |
| `EVALUABLE` | true | 最终 12 个 case；seed1930102 旧 0 ns 失败尝试除外 |
| `PERFORMANCE_VERIFIED` | false | 尚无确认性统计、独立 H0 Pfa、跨速度/SNR/密度结论；另需审查 Stage2 版本差异 |

## 独立审查入口

使用 [六 seed ChatGPT 审查提示词](CHATGPT_REVIEW_PROMPT_V3.md) 和本机精简证据包 `outputs/r22_chatgpt_review_evidence_v3_six_seed_20261028.zip`。证据包使用精简 case manifest 摘录，省略完整 case manifest、Doppler opportunity 明细、可再生成 `.bin`/`.npy` 大数组及重复长表；不能声称可以从原始 IQ 完整重放。
