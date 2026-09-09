# Baseline V2 审计与实验报告：物理模型驱动的 GMTI 杂波对消

## 1. 结论先行

Baseline V2 已完成一轮可复现的 CPU 离线研究闭环，当前没有训练 AI，也没有把
未运行的 formal 矩阵写成已完成结果。

- **物理 sanity：通过。** 四通道 steering 的空间/时空 distortionless 误差约
  `5.6e-17`/`2.2e-16`；目标 angle/Doppler 扫描峰值为 `3°`/`335.182 Hz`；
  杂波脊陷波 `-59.79 dB`；单目标复增益误差 `2.4e-16`；4° steering 扰动增益
  `-9.89 dB`。
- **多 seed screen：完成。** 9 个因素、每个因素 2 个水平、每个水平 5 个 seed，
  共 90 cases、1080 条方法结果，0 case failure。production replay 与 scientific
  controlled 分开记录，未混合目标观测 P38 和 background-only P38。
- **velocity/MDV：完成。** 7 个真实速度点、每点 5 个 seed，共 35 cases、420 条
  方法结果，0 failure。每个速度都重新生成 Stage2 target/background，未用 RD
  `np.roll` 伪造速度。
- **检测统计：已改为 trial 聚合。** 单 case 只保存二值
  `target_detected`；Pd 只在独立 seed/trial 聚合层计算，并同时保存成功数、试验数和
  Wilson 95% 区间。screen 已生成 Pd–SCNR、Pd–velocity；另有 5-seed、7 阈值点的
  独立 ROC 运行，共 420 个 ROC point、0 failure。
- **Pareto/feature 诊断：完成。** 已保存 suppression–target preservation、Pfa–Pd、
  runtime–SCNR 三类 Pareto 图，以及输入特征相关性、尾部/协方差诊断和仅用于探索的
  leave-one-out ridge 轻量 predictor；没有训练最终 AI。
- **主要结果。** screen 全部 cases 等权平均的 SCNR improvement 为：Current
  scientific controlled `13.244 dB`、Phase-only `10.421 dB`、Row complex
  LS/Wiener `11.028 dB`。四通道 adaptive 方法的全 screen 平均仍为负：Corrected
  JDL `-0.510 dB`、local DL-SMI/MVDR `-0.868 dB`、local shrinkage
  SMI/MVDR `-1.017 dB`、SA-MNEC `-1.463 dB`。这是真实实验结果，不做正值截断。
- **下一步边界。** 这轮结果足以冻结 baseline 接口并继续 formal sweep；尚不足以
  证明四通道 academic 方法优于当前 CSI，也不足以授权端到端 AI/RD 网络训练。

## 2. 任务范围与证据边界

本报告只覆盖当前源码、当前配置和本次运行生成的证据。历史 V1 的单 seed、7 场景
结果仍保留在 [`AI_CSI_05_Baseline实验报告.md`](AI_CSI_05_Baseline实验报告.md)，
但不作为 V2 的验收标准。

V2 使用 `configs/research/ai_csi_stage2_typical_1beam.json` 作为 Stage2 基础配置，
输入为单波位、130 PRT、四通道物理几何和单个运动目标。当前环境的 `nvidia-smi`
返回码为 `9`，提示 NVIDIA 驱动不可通信；因此本报告的仿真后端和算法回放均是 CPU
离线证据，不是 CUDA 生产执行结果。

源码身份为 `1119e8b` 基线提交加当前 dirty worktree；完整命令、Python/NumPy/
Matplotlib 版本、仿真器身份、GPU 探测和 worktree 状态见
[`baseline_v2_manifest.json`](../outputs/ai_csi_baseline_v2/baseline_v2_manifest.json)
以及 velocity manifest。

## 3. V2 实现审计

### 3.1 对比协议

| 协议 | 方法 | 权重/参数来源 | 用途 |
|---|---|---|---|
| production replay | Current、Phase-only | target-observation P38，保留当前生产行为 | 描述现有链路 |
| scientific controlled | Current、Phase-only、Row complex LS/Wiener | paired C+N background-only；再固定应用到 S+C+N | 公平 baseline |
| scientific controlled | DL-SMI/MVDR、shrinkage SMI/MVDR、physical JDL、MNEC | paired C+N background-only | 物理四通道研究对照 |
| scientific controlled | SA-MNEC | paired C+N background-only，明确为 approximate academic reference | 近似学术参考，不声称论文复现 |

目标 truth 只用于 target ROI、单 case `target_detected`、target loss 和结果交叉检查；不参与 adaptive
权重训练。impairment case 不把带损伤数据静默当作 paired background，而是按配置
分开生成 target/background，并在结果中保留 `channel_valid_fraction`。

### 3.2 物理 steering 与 sanity

四通道 steering 使用配置中的四个 phase centre、common-transmit receive path
difference、载频、carrier phase sign、beam side 和由 Doppler ridge 反推的角度。
Corrected JDL 使用 12 维空间/慢时间 steering；4 通道 MVDR/MNEC 使用物理空间
steering。

实际检查结果见 [`steering_sanity.json`](../outputs/ai_csi_baseline_v2/steering_sanity.json)
和 [`baseline_v2_steering_sanity.png`](../outputs/ai_csi_baseline_v2/baseline_v2_steering_sanity.png)：

| 检查 | 实际值 | 判定 |
|---|---:|---|
| spatial distortionless error | `5.55e-17` | 通过 |
| space-time distortionless error | `2.22e-16` | 通过 |
| clutter ridge notch | `-59.790 dB` | 通过，阈值 `-3 dB` |
| single-target complex gain error | `2.43e-16` | 通过 |
| 4° perturbation gain | `-9.886 dB` | 通过，阈值 `-0.5 dB` |
| scan peak | `3°`, `335.182 Hz` | 与测试目标一致 |

### 3.3 协方差与训练策略

方法配置为 range block `256`、local half-width `256`、CUT guard `4` bins、diagonal
loading fraction `0.01`。每个结果记录 training sample count、finite-vector valid
fraction、channel-valid fraction、covariance condition number、condition p95、
estimated clutter rank、loading、shrinkage 和 steering projection error。

screen 中 adaptive 方法的平均审计量如下；condition number 是 Hermitian 协方差的
特征值比值估计，避免为同一矩阵重复做 SVD：

| 方法 | training samples | condition mean / p95 | rank | shrinkage | method runtime mean |
|---|---:|---:|---:|---:|---:|
| DL-SMI/MVDR global | 4086 | 15,138 / 75,735 | 1.00 | 0 | 1.66 s |
| DL-SMI/MVDR local | 488 | 708,014 / 3,905,602 | 1.00 | 0 | 0.89 s |
| Shrinkage global | 4086 | 1,126 / 1,417 | 1.00 | 0.0030 | 3.17 s |
| Shrinkage local | 488 | 246 / 375 | 1.00 | 0.0209 | 1.29 s |
| Corrected JDL physical | 4086 | 4.06e6 / 2.20e7 | 4.16 | 0 | 2.86 s |
| MNEC physical | 4086 | 15,138 / 75,735 | 1.00 | 0 | 1.81 s |
| SA-MNEC approximate | 16,344 | 5,989 / 24,616 | 1.00 | 0 | 6.31 s |

`training_valid_fraction` 是有限且非零训练向量比例；通道级完整样本比例单独在
`training_channel_valid_fraction`/`channel_valid_fraction` 中记录，避免把单通道丢失
误报为完整四通道样本。

## 4. 多 seed screen 结果

### 4.1 完整性与数据质量

主结果见 [`baseline_v2_summary.csv`](../outputs/ai_csi_baseline_v2/baseline_v2_summary.csv)、
[`baseline_v2_sweep_summary.csv`](../outputs/ai_csi_baseline_v2/baseline_v2_sweep_summary.csv)
和 [`baseline_v2_feature_diagnostics.csv`](../outputs/ai_csi_baseline_v2/baseline_v2_feature_diagnostics.csv)。

- 90 cases、12 methods/case、1080 rows；全部 `status=ok`。
- 18 个因素水平点均为 5 个独立 seed；每点 `n_cases=5`、`n_seeds=5`。
- production replay 180 rows，scientific controlled 900 rows。
- SCNR improvement、target loss、residual suppression、residual p99/CVaR、Pfa 和
  `target_detected` 均为有限值；无静默失败。
- 单 case 的 `target_detected` 不是概率；`baseline_v2_sweep_summary.csv` 中的
  `Pd__mean`/`Pd__ci95_*` 才是 5-seed 经验 Pd 及其 Wilson 区间，且显式记录
  `Pd__successes`/`Pd__trials`。
- sweep 汇总同时对 residual mean proxy、p95、p99、CVaR95、15 dB high-tail、Pfa、
  SCNR、target loss 和 runtime 输出 mean/std/95% CI/p05/p95，并保留 failure rate。
- V2 只输出一个 `residual_suppression_dB`，不再同时输出旧脚本中公式相同的
  `CA_dB`/`CSR_dB`。

### 4.2 方法总体表现

以下为 90 cases 等权平均；p05/p95 是 case-level 分布，不是置信区间。

| 方法 | SCNR improvement mean | p05 / p95 | target loss mean | mean Pfa | empirical Pd |
|---|---:|---:|---:|---:|---:|
| Current production replay | 13.244 dB | 0.000 / 20.872 | -6.775 dB | 0.00236 | 0.978 |
| Current scientific controlled | 13.244 dB | 0.000 / 20.906 | -6.761 dB | 0.00236 | 0.978 |
| Phase-only production replay | 10.415 dB | -2.634 / 19.271 | -6.180 dB | 0.00090 | 0.978 |
| Phase-only scientific controlled | 10.421 dB | -2.635 / 19.299 | -6.167 dB | 0.00090 | 0.978 |
| Row complex LS/Wiener | 11.028 dB | -2.738 / 19.544 | -6.542 dB | 0.00067 | 0.967 |
| DL-SMI/MVDR global | -2.270 dB | -7.228 / -0.189 | -3.366 dB | 0.00584 | 1.000 |
| DL-SMI/MVDR local | -0.868 dB | -3.361 / -0.030 | -1.727 dB | 0.00509 | 1.000 |
| Shrinkage global | -2.014 dB | -7.063 / -0.157 | -3.114 dB | 0.00579 | 1.000 |
| Shrinkage local | -1.017 dB | -3.431 / -0.077 | -1.911 dB | 0.00515 | 1.000 |
| Corrected JDL physical | -0.510 dB | -3.099 / 10.015 | -1.178 dB | 0.00453 | 1.000 |
| MNEC physical | -1.523 dB | -6.869 / -0.082 | -2.701 dB | 0.00580 | 1.000 |
| SA-MNEC approximate | -1.463 dB | -6.398 / -0.074 | -2.554 dB | 0.00581 | 1.000 |

这些结果说明当前 strict CSI 在本输入上的稳定收益仍高于本轮四通道 adaptive
reference；adaptive 方法没有因为加入物理 steering 就自动成为更强 baseline。尤其
local covariance 的条件数显著高于 global，shrinkage 降低了条件数，但没有在本轮
平均 SCNR 上转化为正收益。

### 4.3 因素诊断

scientific controlled Current 的 screen 均值示例：

| 因素 | 低/名义水平 | 高/压力水平 | 观察 |
|---|---:|---:|---|
| texture sigma | 16.584 dB (`0.4`) | 15.738 dB (`1.1`) | 纹理增强后略降 |
| azimuth subcells | 16.908 dB (`5`) | 17.307 dB (`17`) | 本范围内影响小且非单调 |
| amplitude mismatch | 16.584 dB (`0 dB`) | 16.455 dB (`2.5 dB`) | Current 较稳；Phase-only 明显降至 8.262 dB |
| fixed phase mismatch | 16.584 dB (`0°`) | 16.584 dB (`18°`) | 前端残差估计吸收了该固定误差，需更复杂失配继续验证 |
| phase jitter | 16.584 dB (`0°`) | 16.661 dB (`3°`) | 本屏幕范围内未表现为退化 |
| valid sample fraction | 16.584 dB (`1.0`) | 11.028 dB (`0.4`) | 样本缺失是明显压力因素 |
| target SNR | 16.584 dB (`10`) | 15.882 dB (`-6`) | 目标信号变弱时总体略降 |
| true radial speed | -0.591 dB (`0`) | 0 dB (`4`) | 目标是否离开 clutter ridge 主导表现 |
| support edge speed | 7.357 dB (`1.8`) | 5.549 dB (`2.2`) | 支撑边缘继续扩大时收益下降 |

这些是单因素 screen，不是全 factorial 因果估计；正式更密 levels 和交互仍在配置中。

### 4.4 Pd、ROC 与 Pareto 证据

screen 的 [`baseline_v2_pd_scnr.png`](../outputs/ai_csi_baseline_v2/baseline_v2_pd_scnr.png)
使用 measured input SCNR 作横轴、5 个独立 seed 作 trial 聚合；本屏幕的两个目标
SNR 点在当前 GO-CFAR ROI proxy 下均为 `5/5` 命中，因此曲线在该范围内饱和，不能
被解释为完整的低 SNR detection curve。

独立 ROC 运行使用 5 个新 seed、每个 seed 重新生成 target/background，并扫描
threshold scale `[0.5, 0.75, 1, 1.5, 2, 3, 5]`，结果见
[`baseline_v2_roc_summary.csv`](../outputs/ai_csi_baseline_v2_roc/baseline_v2_roc_summary.csv)、
[`baseline_v2_pfa_roc.png`](../outputs/ai_csi_baseline_v2_roc/baseline_v2_pfa_roc.png)
和 [`baseline_v2_roc_points.csv`](../outputs/ai_csi_baseline_v2_roc/baseline_v2_roc_points.csv)。
在 threshold scale `1.0`、目标 SNR `10 dB` 时，Current scientific 的平均 Pfa 为
`0.002104`、经验 Pd 为 `5/5`；DL-SMI/MVDR global 为 `0.005950`、`5/5`；
Corrected JDL 为 `0.004555`、`5/5`。该 ROC 运行证明阈值操作点和 Pfa 统计链路已经
可追溯，但 Pd 饱和仍是当前研究输入/检测 proxy 的限制。

三类 Pareto 图分别为：
[`baseline_v2_pareto_suppression_target.png`](../outputs/ai_csi_baseline_v2/baseline_v2_pareto_suppression_target.png)、
[`baseline_v2_pareto_pfa_pd.png`](../outputs/ai_csi_baseline_v2/baseline_v2_pareto_pfa_pd.png)
和已有的 [`baseline_v2_pareto_runtime_scnr.png`](../outputs/ai_csi_baseline_v2/baseline_v2_pareto_runtime_scnr.png)。
第一张以 target loss（0 dB 为无损）对 residual suppression，第二张以 Pfa 对经验
Pd，第三张以 CPU runtime 对 SCNR improvement；不将单 case 二值命中当作概率。

## 5. Feature diagnostics 与探索性关联

每个 case 保存 P38 slope/intercept/RMSE/inlier/count、coherence、F1/F2 energy ratio、
phase variance、valid fraction、ridge distance、support distance、Current residual
结果以及输入 BIN SHA-256。screen 的主要范围：

- `p38_rmse_rad`: `0.00341–0.00848`；inlier ratio 全部为 `1.0`；accepted count
  `30–38`。
- coherence mean `0.392–0.733`，coherence p10 `0.063–0.360`。
- valid pulse fraction `0.2846–1.0`。
- measured texture nonuniformity CV `0.874–1.183`；配置 `texture_sigma` 为
  `0.4–1.1`。
- global DL covariance condition number `2.45e3–2.23e4`，estimated rank
  `0.977–1.000`。
- Current residual p95/p99 相对输入 median 分别为 `6.681–10.285 dB`/
  `10.484–15.773 dB`；CVaR95 为 `9.560–14.652 dB`。
- target-to-clutter-ridge distance `0.083–425.892 Hz`；support-edge distance
  可为负，表示 truth 已越过当前动态 support。

[`baseline_v2_feature_correlations.csv`](../outputs/ai_csi_baseline_v2/baseline_v2_feature_correlations.csv)
只使用输入侧物理/诊断特征作为 predictor，避免把方法输出自身作为 feature 造成 r=1
的泄漏。screen 中较强的探索性关系包括：

- Current SCNR 与 support-edge distance 的 Pearson `+0.585`，与 clutter-ridge
  distance 为 `-0.582`；
- best adaptive SCNR 与 clutter-ridge distance 为 `+0.778`，与 support-edge
  distance 为 `-0.776`；
- valid fraction 与 Current failure proxy 为 `-0.448`。

这些关联只用于 feature feasibility 和后续实验设计，不是预测器、不是因果结论，
也没有据此训练 PGRCC AI。

[`baseline_v2_feature_diagnostics.png`](../outputs/ai_csi_baseline_v2/baseline_v2_feature_diagnostics.png)
给出 covariance condition/自适应结果和 ridge distance/Current residual tail 的
输入侧散点诊断；[`baseline_v2_feature_light_predictor.csv`](../outputs/ai_csi_baseline_v2/baseline_v2_feature_light_predictor.csv)
只做 input-feature 的 leave-one-out ridge screen。其 Current SCNR 与 best adaptive
SCNR 的 LOO RMSE 分别为 `5.060 dB`、`2.477 dB`，LOO R² 分别为 `0.354`、`0.517`；
这些是 feature feasibility 统计，不是最终模型、不是训练许可。

## 6. Velocity 与 MDV

完整 velocity 证据在 [`outputs/ai_csi_baseline_v2_velocity/`](../outputs/ai_csi_baseline_v2_velocity/)，
包括 [`baseline_v2_velocity_pd.png`](../outputs/ai_csi_baseline_v2_velocity/baseline_v2_velocity_pd.png)、
[`baseline_v2_sweep_summary.csv`](../outputs/ai_csi_baseline_v2_velocity/baseline_v2_sweep_summary.csv)
和 [`baseline_v2_mdv_summary.csv`](../outputs/ai_csi_baseline_v2_velocity/baseline_v2_mdv_summary.csv)。

MDV 判据在运行前固定为：**最小绝对 regenerated radial-speed grid value，使
5-seed mean empirical Pd（由 `target_detected` 聚合）达到 `0.5`；若阈值夹在
相邻 grid 点之间，附加线性插值，但原始 grid Pd 不被替换。** 同时保存每个 seed
的 crossing mean/std/95% CI/p05/p95。在本轮 grid `[-4,-2,-1,0,1,2,4] m/s` 上：

- Current production/scientific、Corrected JDL、DL-SMI/MVDR、MNEC、SA-MNEC 和
  shrinkage 方法的 seed-mean Pd 在 `0 m/s` 已达到阈值，报告为 operational MDV
  `0 m/s`；这表示本实验的 CFAR/目标 ROI 判据在零径向速度也检测到目标，不等价于
  已证明无物理 blind velocity。
- Phase-only 和 Row-LS/Wiener 的首个阈值 crossing 在 `0–1 m/s` 间，插值 MDV
  为 `0.375 m/s`；5-seed crossing mean 为 `0.400 m/s`，95% CI 为
  `[0.204, 0.596] m/s`。

Current 的 seed-mean operational MDV 仍为 `0 m/s`，但 per-seed crossing mean
为 `0.100 m/s`、95% CI 为 `[0, 0.296] m/s`，因此不能把 `0 m/s` 写成无不确定性
的物理 blind-velocity 结论。

因此当前 MDV 结果首先是可追溯的 operational screen，不是生产探测性能上限。要把
它提升为工程结论，还需要扩展目标角度、纹理、噪声、beam、真实 POS/姿态和多目标
场景，并完成 CUDA/生产链路交叉验证。

## 7. 可复现命令与产物

交付前验证已实际运行：Python 脚本语法检查和 suite JSON 解析通过；Release 全工程
构建通过；CTest `16/16` selftests 通过；`git diff --check` 通过。构建日志中的
unused variable、初始化顺序和 nvlink 兼容性提示属于当前工程已有 warning，不是
本轮 V2 脚本错误。GPU 探测仍为 `nvidia-smi` exit `9`，因此没有把 CUDA 运行写成
通过。

在仓库根目录：

```bash
cmake -S . -B build -DCMAKE_BUILD_TYPE=Release
cmake --build build --target simulate_stage2_statistical -j4
python3 -m py_compile scripts/run_baseline_v2.py
python3 scripts/run_baseline_v2.py --mode sanity --out outputs/ai_csi_baseline_v2
OPENBLAS_NUM_THREADS=1 OMP_NUM_THREADS=1 MKL_NUM_THREADS=1 \
  python3 scripts/run_baseline_v2.py --mode screen --workers 2 \
  --out outputs/ai_csi_baseline_v2
OPENBLAS_NUM_THREADS=1 OMP_NUM_THREADS=1 MKL_NUM_THREADS=1 \
  python3 scripts/run_baseline_v2.py --mode velocity --workers 2 \
  --out outputs/ai_csi_baseline_v2_velocity
OPENBLAS_NUM_THREADS=1 OMP_NUM_THREADS=1 MKL_NUM_THREADS=1 \
  python3 scripts/run_baseline_v2.py --mode roc --workers 2 \
  --out outputs/ai_csi_baseline_v2_roc
```

当前已保留的高价值产物：

- [`baseline_v2_summary.csv`](../outputs/ai_csi_baseline_v2/baseline_v2_summary.csv)
- [`baseline_v2_sweep_summary.csv`](../outputs/ai_csi_baseline_v2/baseline_v2_sweep_summary.csv)
- [`baseline_v2_feature_diagnostics.csv`](../outputs/ai_csi_baseline_v2/baseline_v2_feature_diagnostics.csv)
- [`baseline_v2_feature_correlations.csv`](../outputs/ai_csi_baseline_v2/baseline_v2_feature_correlations.csv)
- [`baseline_v2_mdv_summary.csv`](../outputs/ai_csi_baseline_v2/baseline_v2_mdv_summary.csv)
- [`baseline_v2_feature_light_predictor.csv`](../outputs/ai_csi_baseline_v2/baseline_v2_feature_light_predictor.csv)
- sanity、三类 Pareto、Pd–SCNR、SCNR heatmap、feature 和 full velocity PNG
- 独立 ROC 目录中的 [`baseline_v2_roc_summary.csv`](../outputs/ai_csi_baseline_v2_roc/baseline_v2_roc_summary.csv)、
  [`baseline_v2_roc_points.csv`](../outputs/ai_csi_baseline_v2_roc/baseline_v2_roc_points.csv)
  和 [`baseline_v2_pfa_roc.png`](../outputs/ai_csi_baseline_v2_roc/baseline_v2_pfa_roc.png)
- screen/velocity manifests 与 formal 配置

每个 case 的约 49 MB target/background BIN、生成 XML 和 truth 目录在处理后自动删除；
只保留 CSV/PNG/JSON、manifest 和本报告。V1 的 `outputs/ai_csi_baseline/` 未覆盖。

## 8. 限制、未完成与下一步

1. `nvidia-smi` 不可用，未完成真实 CUDA 生产运行；CPU 回放不能替代 GPU 验收。
2. formal 配置包含 470 个 case（更密 levels、10 seeds），本次只完成 screen 和
   velocity；formal 配置已写入 [`ai_csi_baseline_v2_suite.json`](../configs/research/ai_csi_baseline_v2_suite.json)，
   不把它写成已运行结果。
3. SA-MNEC 是明确标注的近似 academic reference，不是某篇论文的逐公式复现。
4. GO-CFAR/Pfa 为 CPU 研究 proxy；`target_detected` 是单 case 二值观察，经验 Pd
   只在 seed/trial 聚合层成立。当前 10 dB ROC 的 Pd 饱和为 `5/5`，尚未接入生产
   检测器、聚类、跟踪或真实 CUDA 输出协议。
5. Stage2 当前仍是单波位/单目标研究输入，平台 POS/姿态和更复杂多目标交互尚未纳入
   本轮结论。

建议下一步按顺序：先在可用 GPU 上复跑 sanity 和一小组代表性 screen case，随后
完成 formal 470-case 矩阵；只有当物理 baseline、训练样本政策、指标和生产检测链路
完成交叉验证后，再设计小型 residual/置信度门控模型。当前不进入端到端 clutter-free
RD 网络训练。
