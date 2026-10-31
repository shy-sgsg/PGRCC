# R22-S1b：C4 处理阶段闭合审计

日期：2026-10-29

研究范围：R22 C4 Robust DDC-RB 在 `GMTI05 / beam 50 / azimuth 52 / range 1065–1197` 的支持数失效。

审查状态：`PASS_WITH_LIMITATIONS`（本阶段 Production trace 正常执行）；R22 Screening 仍为 `INCOMPLETE / INCONCLUSIVE`。

## 1. 任务与执行身份

本次只复用 seed1932001、2.5 m/s、SNR30、+4 ns、20 targets、10 periods、beam 50 的既有 `A1_ON/A1_OFF/A1_TO` Stage2 输入。未重新运行 Stage2、未扩展 Screening 矩阵、未改变算法门槛。Stage2 输入每个 role 各10个 period 文件、每个文件49,204,480 bytes；完整逐文件 SHA-256、输入来源 manifest SHA 和实际运行身份见 [`evidence/s1b/s1b_manifest.json`](evidence/s1b/s1b_manifest.json)。

Production 使用本地 NVIDIA GeForce RTX 3050 Laptop GPU，CUDA 12.4、Release、架构75。运行前设备快照为 P0、87°C、84% GPU 利用率、62.85 W、2237/4096 MiB、SM 1830 MHz。设备当时繁忙且温度较高，因此本次只作正确性和机制诊断，不报告运行速度或性能结论。

| 运行角色 | Production 状态 | 返回码 | 周期数 | truth 用于估计 |
|---|---|---:|---:|---|
| ON | `passed` | 0 | 10 | 否 |
| OFF | `passed` | 0 | 10 | 否 |
| TO | `passed` | 0 | 10 | 否 |

CSI tap 选择 `5,50,52,1065,1197`，每个角色输出5个阶段 × 133个 range cells，共665行。门槛保持 `min_support=8`、相位残差 ±0.35 rad、range group 133 bins。Mode-A 的固定参考仅用于配对机制诊断，不是盲部署评估。SNR30 原失败的 Mode-B 输入由独立证据文件作逐值对照。

代码执行基线为 `1ed2a2e67a88a918858784be81794238ef112c7d`。tap 依赖的源文件 SHA-256、隔离 CUDA binary SHA-256、原输入 case manifest SHA-256、各分支 runtime XML、branch/run manifest 和实际日志均收录在审查证据包。诊断 Production binary SHA-256：`16cb0f2d35596ff98ab8bdcbc9571b972b99e217053cf0b1b7f6b49168c8c998`。

## 2. 面积杂波与输入条件核验

A1 单角色 Stage2 报告的 `continuous_area_packets=0`、`continuous_area_samples=0` 是“本次角色没有重新生成连续面积杂波”的计数。A1_ON/OFF/TO 都通过 `background_input_dir` 复用共同的 `A0_background`；A1 的背景复用映射均为每角色10/10周期 `matched`。

生成共同背景的 A0_ON Stage2 报告记录：

- 模型：`continuous_texture`；距离窗：`[84500,85500] m`；
- 连续面积杂波：1300 packets、6,508,782 samples；
- 实测平均功率：`0.0221596696662`；lag-1 相关：`0.98001127856`；
- A1_ON/OFF/TO 场景配置都指向该共同背景；TO 使用 `signal_only=true`，在注入目标之前移除复用背景负载。

生成报告、A1 场景配置及复用映射表位于 [`evidence/s1b/stage2/`](evidence/s1b/stage2/)。Stage2 causal triplet 在61,464,000个样本上通过 `5e-5` 容差，最大绝对误差 `1.7172759e-5`、RMS误差约 `9.36e-7`。因此该次 S1b 输入具有已记录的连续面积杂波背景；A1报告的零值不能单独用于断言没有杂波。

## 3. 直接观测

### 3.1 与 Mode-B 失败输入对齐

在 C4 前的 `F1/F2` 输入上，S1b Mode-A ON 与原 SNR30 Mode-B 失败组的133个 range cells 逐值相同。原失败仍是 support=7、`insufficient_robust_support`，低于冻结的8单元门限。该匹配说明 S1b trace 覆盖了原失败所用的数据，但 Mode-A 分支不等于 Mode-B 在线估计器，也不证明失败原因。

### 3.2 `ON−OFF−TO` 处理边界闭合

闭合量只针对复数 F1/F2 定义为 `ON−OFF−TO`，相对 L2 分母是 ON 的 L2。`F2·conj(F1)` 是双线性量，其差值不作为加性闭合判据。

| 处理路径 | 处理边界 | F1 相对 L2 残差 | F2 相对 L2 残差 |
|---|---|---:|---:|
| raw | DBS，距离相位校正前 | 0.9367% | 1.8056% |
| raw | 距离相位校正后 | 0.9367% | 22.4942% |
| CSI | DBS，距离相位校正前 | 1.0316% | 1.8056% |
| CSI | 距离相位校正后、C4前 | 1.0316% | 78.7703% |

raw 与 CSI 从原始通道数据重新分支，不能把 raw 校正后阶段当作 CSI DBS 的输入。CSI 路径中最大已观测闭合跳变位于 `csi_dbs_pre_range_phase` 到 `csi_post_range_phase_pre_c4_adapter` 之间。三个角色估得的 CSI range phase 均值为 ON `0.05733 rad`、OFF `−1.76689 rad`、TO `0.66254 rad`。对应 P38 拟合值、每 cell 输入和各阶段复数值见 [`evidence/s1b/analysis/stage_boundary_closure.json`](evidence/s1b/analysis/stage_boundary_closure.json) 及分支 trace CSV。

源码显示 `rg_correct_CUDA` 从当前角色的 GPU F1/F2 中估计相位，再以 RANSAC 二次拟合生成距离相位校正曲线。三角色独立校正向量差异很大；这与 CSI 距离相位校正后的 F2 非闭合相符。首个已捕获 raw/CSI DBS 边界在校正前已经有约1–2%残差；本次没有采集脉压、数据驱动多普勒中心及更早阶段的完整边界，不能将这部分残差归到某个具体步骤。

### 3.3 C4 adapter 不是大幅残差来源

三角色 Mode-A 固定 γ 相同：`0.143796772317323 + 0.83936050987282j`。Production 输出符合 `F2−γF1`，各角色公式相对 L2 误差最高 `1.0861e-7`；预测与实测输出闭合的差为 `5.0290e-8`。输出的 F2 残差仍为78.7703%，与 adapter 前一致。因此大幅差异在 C4 adapter 输入之前已经形成。

## 4. 因果解释与科研判断

**直接观测：** 原失败 Mode-B 输入可重现；生产链中最大的 F2 闭合恶化发生在各角色独立估计并应用 CSI 距离相位校正之后；C4 固定线性 adapter 按公式传递已有输入差异。

**支持的推断：** 角色自适应距离相位拟合是本次 ON/OFF/TO F2 闭合大幅变差的主要已观测来源。它可能改变 C4 相位一致性支持集合。

**仍未验证：** SNR20→SNR30 的 support 25→7，究竟主要来自目标 SNR/干涉输入变化、角色独立相位拟合，还是更早的处理差异。S1b 未对 SNR20 采集同边界 trace，也未执行共同冻结相位的反事实。目标落在同一距离分组内不能证明目标二维响应覆盖 azimuth row 52；也没有本阶段 Pd/Pfa、目标保持或参数估计误差结果。

阶段判断为 `PASS_WITH_LIMITATIONS`（trace 实现和三角色 CUDA Production 重放按身份完成）与 `INCONCLUSIVE`（25→7 唯一原因未确认）。保留 C4 原失败，未降低 `min_support`、未删除失败组、未回退 Current、未恢复大规模 Screening。AI 进入门仍为 `FAIL`；这些结果不证明 AI 必要。

## 5. 状态分层

| 状态 | 判断 |
|---|---|
| `IMPLEMENTED` | 选择性处理边界 tap、配置校验、失败关闭、自检与闭合分析器已实现 |
| `EXECUTED` | 三角色本机 CUDA Production 重放已运行；未重跑 Stage2 |
| `EVALUABLE` | 选定组五阶段复数 trace、Mode-B 输入逐值匹配、固定 γ adapter 线性度及 Stage2 背景复用链可核验 |
| `PERFORMANCE_VERIFIED` | `false`；未开展任务性能或统计检验 |

## 6. 唯一建议的后续工作

若独立审查仍要求解释25→7，建议仅讨论 **R22-S1c：SNR20/30 共同 OFF 距离相位反事实**，暂不自动执行：

1. 复用相同 seed1932001、+4 ns、beam50、GMTI05 的 SNR20/SNR30 OFF/ON/TO 输入；核验背景及10个输入文件身份。
2. 从 target-free OFF 得到一个冻结 CSI 距离相位向量，将它相同地应用于 ON20 与 ON30；对照生产各自拟合向量下 support、相位中心和保留单元。
3. 不读取目标真值进入估计，保持 `min_support=8` 和 ±0.35 rad；该反事实只做 selected-group 机制判断，不做全扫描任务性能排名。

若共同相位不能在两种 SNR 的配对身份下成立，或固定相位后仍无法区分 SNR 与自适应校正影响，则保留原失败、报告 `NOT_VERIFIED` 并停止归因。是否进入此任务由用户在 ChatGPT 独立审查后决定。

## 7. 证据与复核

公开审查材料位于 [`evidence/s1b/`](evidence/s1b/)；其中保留 manifest、实际 XML、逐角色运行日志和 manifest、逐 cell trace、所用 γ 表、背景生成报告与复用映射。完整 Stage2 binary 未上传；输入字节数和 SHA-256 列于 `s1b_manifest.json`。S1b 完整本机运行仍保存在忽略目录 `outputs/r22_s1b_phase_boundary_20261029/snr30/`；更大的 Stage2 输入保存在 `outputs/r22_s1_c4_support_replay_20261029/snr30/`，本次不清理，以待 ChatGPT 审查完成后按用户保留规则处理。

复核复数闭合分析：

```bash
python3 docs/research/phase_r/R22/support_failure_diagnostic/analyze_stage_boundary_trace.py \
  --trace-root docs/research/phase_r/R22/support_failure_diagnostic/evidence/s1b \
  --output-dir /tmp/r22_s1b_recheck \
  --modeb-failure-cells docs/research/phase_r/R22/support_failure_diagnostic/evidence/mode_comparison/snr30_modeb_on_selected_cells.csv
```
