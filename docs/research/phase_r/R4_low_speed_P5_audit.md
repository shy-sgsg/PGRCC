# R4 低速 P5 支持域审计

日期：2026-10-22。只复核 seed-101 E1/E4/E5 已执行 CUDA Production 与 period-specific P5 文件；不改 evaluator、不补跑。

## 结论

0.5/1 m/s 的 **P5 局部背景 CA ROI** 为 0/30，确由“目标邻域 Doppler 窗”与 CSI 活动支持带 `[46,84]` 不相交造成；不是数组索引错位，也不是 guard 单独剔空。该指标应保持 `NOT_EVALUABLE`，不能解读成没有杂波或算法性能失败。

这不等于低速的所有 P5 指标都失效：ON/H1 target ROI 使用 full-range valid support，并以配对目标关闭 OFF tap 估计 C+N，因此低速 target SCNR 指标可单独评价。汇总中的 `incomplete` 是整套背景/目标行的合并状态，不能覆盖其有效 ON target-SCNR 子集。

## 可复核的几何例

`v=0.5 m/s, SNR=10 dB, period=0, beam=50, C1_modea/ON`：

- truth `range_bin=2048`，总 Doppler `−3985.9566 Hz`；映射到本期 FFT 轴为 `−85.9566 Hz`、wrap order 3、truth row 114。
- `csi_roi_manifest.csv` 的活动支持是 rows 46–84、range cols 1–4095。target ROI 半宽为 2 Doppler × 2 range bins（最多 25 cells）；guard 半宽 4×4；背景局部窗半宽 16 Doppler × 20 range bins；最小背景门为 100 cells。
- 背景 Doppler 行为 `[98,130]`，与活动行 `[46,84]` 交集为空。故 `background_mask = local & active & ~guard_union` 在 guard 扣除前已是空集。`valid_background_cells=0` 是正确 fail-closed 结果。
- target mask 则使用 `full_range_support_mask`，保留 25 cells；匹配 OFF 计算 `S_before=7,457,668.80`、`S_after=7,651,510.32`、`C_before=255.809`、`C_after=313.218`，`SCNR_before=44.6469 dB`、`SCNR_after=43.8790 dB`、变化 `−0.7679 dB`。这是一个期/方法的例子，不是全场景性能结论。

跨方法/角色/三 period 的汇总：

| 地速 | 有效背景 ROI | 有效 target metric | 判读 |
|---:|---:|---:|---|
| 0.5 m/s | 0/30 | 15/30 | ON 配对 target SCNR 有效；背景 CA ROI 无支持 |
| 1 m/s | 0/30 | 15/30 | 同上 |
| 2 m/s | 20/30 | 30/30 | 背景指标部分可评估，不能只报有效子集为全覆盖 |
| 4 m/s | 30/30 | 30/30 | 全部 period-specific ROI 通过数量门；仍是一个 seed |

`TO` 是独立 target-only 角色，不能拿它替代 ON 的背景估计。0.5/1 m/s 整体 30 行 `incomplete`，不表示 15 条 ON 配对 target-SCNR 行全部无效。

## 是否需要修 evaluator

没有证据表明 P5 的 period truth 或 FFT wrap 映射错误；脚本明确将 target mask 放在 full-range support，而 background CA 使用活动支持带与真值局部窗交集。这个交集为空时报告 insufficient cells 正确。故本轮**不改评价器、不补单测**。

低速目标任务应报告已存在的配对 H0 目标 ROI C+N 与 H1−H0 target signal/SCNR（逐 period、保留 S/C/target gain），将局部活动带 CA 标作 `NOT_EVALUABLE`；不要用 band 外的噪声单元冒充地杂波。若未来改变该估计定义，再先加入已知 H0/H1 线性功率与空交集的独立合成测试。

证据：[`p5_method_role_summary.csv`](evidence/r4_e1_e4_e5_seed101_20261022/p5_method_role_summary.csv)、[`p5_period_summary.csv`](evidence/r4_e1_e4_e5_seed101_20261022/p5_period_summary.csv)；production tap/manifests 位于本地 `outputs/phase_r_e1_e4_e5_cuda_pilot_seed101_20261022_maps/`。
