# R12 Native CUDA 生产任务比较

日期：2026-10-24。场景：seed 404、3 个 period、6000 m 平台、通道 C2 `+4 ns`、单个 38° 波束、连续纹理面积地杂波。比较条件为 A0 理想、B 未知误差 Current、C Known correction、R9 固定 D3 和 R12 M1。每个条件有配对 ON/OFF/TO 输入。

## 运行身份与入口门

- 执行后端：本机 **Native CUDA**，`USE_COREX=OFF`；RTX 3050 Laptop GPU，驱动 580.178.04。生产二进制 SHA-256：`de3026ed4f1263be4143dcbcd10ccbb5e53b4be357e21906e1bf19ea0ec29d00`。
- 运行命令见下方；统一运行 manifest、case manifest、逐条件 branch manifest、运行 XML、日志、运行时配置、CSI tap、CFAR geometry、检测/lineage/TrackManager 文件均在本地 `outputs/phase_r_r12_physics_matched_delay_20261024/`，必要审查副本在[证据包](evidence/r12_physics_matched_delay_20261024/)。
- 所有 15 个 condition-role 分支通过 Production gate。15 份 normalized runtime XML 的处理配置 fingerprint 相同。每个 ON 分支的 `union_dynamic_in_band` 和 `union_full_band` 各有 1,581,840 个有效 CUT；无降低门限。
- Stage2 面积杂波 gate 通过：平台 6000 m、地面 0 m，斜距 84.5–85.5 km（地面投影约 84.287–85.289 km），390 个连续面积包、1,952,655 个面积样本，实测面积均功率 0.112371。
- `IMPLEMENTED=true`，`EXECUTED=true`，本报告逐项指标可评估；`PERFORMANCE_VERIFIED=false`，原因是只有一个独立 seed，统计性 Pd/Pfa 未建立。

## 输入物理闭合与残余杂波

Known correction 与 A0 四通道协议 IQ 融合后的 F1/F2 在三个角色均闭合。F2 相对 RMS 误差为 ON `8.7338e-5`、OFF `9.1865e-5`、TO `7.1439e-8`；预设闭合容差为 `1e-3`。Current B 的 ON F2 相对 RMS 为 32.8816%，固定 D3 为 6.3719%，M1 为 2.6840%。

| 条件 | H0 CSI 有效支撑输入功率 | CSI 后功率 | 抑制量 | H0 false detection 行 / false confirmed tracks |
|---|---:|---:|---:|---:|
| A0 | 654.205 | 179.553 | 5.615 dB | 27 / 3 |
| B | 598.327 | 183.183 | 5.141 dB | 33 / 6 |
| Known | 654.205 | 179.561 | 5.615 dB | 27 / 3 |
| 固定 D3 | 646.759 | 178.381 | 5.594 dB | 28 / 2 |
| M1 | 651.268 | 178.967 | 5.610 dB | 27 / 2 |

功率和抑制量来自配对 H0 OFF 的 active-support CSI map，包含杂波与噪声；不是纯杂波分量。R9 的 H0 false-alarm 输出只作描述计数。当前 CFAR evaluator 没有可用的 unique-union hit-cell 数值，故不能从 hit fraction 推导 Pfa。

## 目标保持与检测任务

`paired target transfer` 定义为 truth-centered 5×5 ROI 上，用最小二乘复增益映射 `CSI(TO)` 到 `CSI(ON)-CSI(OFF)`。CSI 会按角色自适应，因此这是配对响应诊断，不是固定线性系统增益。`Output SCNR proxy` 定义为 `10 log10(mean CSI(TO) target-ROI power / mean CSI(OFF) same-ROI C+N power)`。目标保持列为 TO target ROI CSI 输出相对输入功率。三者不要混为一个指标。

| 条件 | 正确检测 / 3 | 正确 TrackManager 关联 / 3 | 目标 transfer 幅度 | 相位误差 | Output SCNR proxy | TO target CSI 保持 |
|---|---:|---:|---:|---:|---:|---:|
| A0 | 2 | 1 | −13.953 dB | 152.631° | 76.623 dB | −6.445 dB |
| B | 0 | 0 | −26.238 dB | 107.809° | 76.497 dB | −6.151 dB |
| Known | 2 | 1 | −13.953 dB | 152.630° | 76.624 dB | −6.445 dB |
| 固定 D3 | 3 | 2 | −17.481 dB | 131.196° | 82.787 dB | −0.236 dB |
| M1 | 3 | 2 | −14.082 dB | 152.321° | 76.621 dB | −6.448 dB |

Known 的输入闭合与目标保持几乎重现 A0，但本 seed 的检测结果也只回到 A0 水平。固定 D3 的 TO target retention 与 SCNR proxy 较高，但 paired complex transfer 幅相仍不同于 Known/A0，不能只按一个标量 SCNR 排名。

## 目标参数误差

RMSE 使用冻结 R7/R8 task matcher 的正确检测/关联记录；不同条件可用样本数不同，括号中为参与该误差的机会数。B 没有正确目标检测，参数误差不可计算。径向速度因模糊未解，在所有条件均 `NOT_EVALUABLE`。

| 条件 | 方位 RMSE | 地速 RMSE | 2D 位置 RMSE | 径向速度 RMSE |
|---|---:|---:|---:|---|
| A0 | 0.02191° (n=2) | 0.86056 m/s (n=1) | 33.769 m (n=2) | 不可评估 |
| B | 不可评估 | 不可评估 | 不可评估 | 不可评估 |
| Known | 0.02191° (n=2) | 0.86056 m/s (n=1) | 33.769 m (n=2) | 不可评估 |
| 固定 D3 | 0.08765° (n=3) | 8.06162 m/s (n=2) | 134.525 m (n=3) | 不可评估 |
| M1 | 0.05544° (n=3) | 11.03158 m/s (n=2) | 85.098 m (n=3) | 不可评估 |

## 可复现证据

```bash
python3 scripts/phase_r/run_r12_physics_matched_delay.py \
  --output-root outputs/phase_r_r12_physics_matched_delay_20261024_rerun \
  --seed 404 --production-binary build_r7/GMTI_pipe_core
```

输出根目录必须为空或使用新的名称。当前运行的全部 runtime 产物保存在本机忽略目录；紧凑证据包收录了关键 XML、配置、日志、逐 period CSI tap 汇总、CFAR geometry、检测/lineage/TrackManager 记录及 SHA 索引，未复制原始 IQ 和全图 CSI maps。
