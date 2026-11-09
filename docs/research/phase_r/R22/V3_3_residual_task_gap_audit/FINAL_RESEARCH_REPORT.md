# R22-V3.3 确定性校正后的 GMTI 任务残差审计

**阶段状态：** `OFFLINE_AUDIT_COMPLETE / PERFORMANCE_NOT_VERIFIED`
**研究裁决：** `CONTINUE` 有界确定性物理状态估计的确认性评价；`AI_ENTRY_GATE=FAIL`。
**研究总纲 blob：** `e637e3769bde7814298ff012d0f42301884e2985`，本阶段未修改。

## 审计范围与结论

离线复核 R22 V3.2 中 12 个独立 seed、4 种速度、3 档 SNR 的 144 个 `+4 ns` case。144/144 个使用中的 compact ZIP SHA 与冻结索引一致；每个 case 均有连续面积杂波 1,300 packets、6,508,782 samples。未重跑 Stage2、Production 或 CUDA，也未开展新实验或 AI 训练。

**本轮一项重要补充：** 之前把“全十周期共同参数无法评价”概括得过宽。虽然压缩 ZIP 仅保留 period 8 Detection CSV，但 `target_associations.csv` 保存了冻结 evaluator 的全周期 `position_error_m`。据此可复算全十周期位置成功集合；四核心方法 576/576 个 case×method 的 RMSE/样本数与归档 `parameter_metrics.csv` 完全一致。因此，全周期位置 own-success、pairwise common-success 和四方法 common-success 均可评价。方位、斜距、折叠 Doppler 的全周期共同成功仍无法评价。

结果仍属于筛选审计：没有统计显著性主张，也不能外推到一般真实平台误差分布。

## 研究问题回答

| 问题 | 本轮回答 |
|---|---|
| 固定 +4 ns 是否降低任务表现？ | 在本仿真、当前 Production 与冻结检测机会分母下，Current 命中率明显低于 A0/PK；不能外推为一般未知误差或真实机载结论。 |
| Known correction 恢复多少？ | `Known − Current` 的 seed 等权检测恢复空间为 58.035 个百分点；PK Known 检测率与 A0 汇总相同。 |
| target-present 确定性估计距 Known 多远？ | 检测命中接近 Known。全周期位置四方法共同集上 M1 比 Known 高 18.387 m、Huber 高 24.940 m；M1 比 Fixed D3 低 13.979 m（12/12 seed 同向）。这仍是筛选描述。 |
| 是否继续未知物理状态估计？ | 可继续一次有界、确定性的确认性评价。位置残差方向一致但未作确认性推断；Huber/M1 使用全十周期 batch 输入，实时可用性尚未验证。 |
| 是否改为低速保护或复杂杂波？ | 暂无证据支持变更；本轮未操纵复杂非平稳杂波，也不是低速保持专项实验。 |
| 是否需要 AI？ | 否。`AI_ENTRY_GATE=FAIL`；还未证明稳定任务缺口不能由更简单的确定性方法解决。 |

## 检测与恢复

12 个 seed 各自汇总 12 个速度×SNR case，每 seed 2,400 个目标周期机会。以下为 seed 等权均值 ± seed 间样本 SD：

| 方法 | 正确目标命中率 | 总命中 / 28,800 |
|---|---:|---:|
| A0 no-error | 69.729% ± 5.139% | 20,082 |
| Current +4 ns | 11.694% ± 2.264% | 3,368 |
| PK Known correction | 69.729% ± 5.139% | 20,082 |
| Fixed D3 | 69.705% ± 5.160% | 20,075 |
| Huber D3 ON | 69.767% ± 5.146% | 20,093 |
| M1 Physics Matched | 69.767% ± 5.108% | 20,093 |

Known−Current 可恢复空间为 58.035 个百分点。Fixed D3、Huber、M1 的实际恢复分别为 58.010、58.073、58.073 个百分点；平均 recovery ratio 分别为 99.970%、100.071%、100.074%。略高于 100% 是有限筛选比值，不表示超过物理上界。A0/PK 总命中相同，但在 144 个 case 中只有 142 个 case 命中数逐 case 相同，故只称任务级近似闭合。

## 参数误差

### 全十周期位置共同成功

位置误差读取自 `target_associations.csv` 的冻结 evaluator 字段。四方法共同成功为 **19,682/28,800（68.340%）**。表中先按 seed 计算 RMSE，再给 12 个 seed 的均值 ± 样本 SD：

| 方法 | Own-success 位置 RMSE（m） | 四方法共同成功位置 RMSE（m） | Own-success 覆盖率 |
|---|---:|---:|---:|
| PK Known | 31.765 ± 2.952 | 31.571 ± 2.695 | 69.729% |
| Fixed D3 | 64.128 ± 1.365 | 63.995 ± 1.368 | 69.705% |
| Huber D3 ON | 56.637 ± 4.070 | 56.494 ± 4.018 | 69.767% |
| M1 Physics Matched | 50.125 ± 3.990 | 49.997 ± 3.966 | 69.767% |

同一方法对的 common-success 差值定义为 `RMSE(B) − RMSE(A)`，负数有利于 B：

| 配对 A → B | B−A 位置 RMSE（m，均值 ± seed SD） | B 较低 seed 数 |
|---|---:|---:|
| PK → Fixed D3 | +32.447 ± 2.191 | 0/12 |
| PK → Huber D3 ON | +24.940 ± 4.507 | 0/12 |
| PK → M1 | +18.387 ± 4.503 | 0/12 |
| Fixed D3 → Huber D3 ON | −7.493 ± 4.126 | 12/12 |
| Fixed D3 → M1 | −13.979 ± 4.133 | 12/12 |
| Huber D3 ON → M1 | −6.524 ± 0.771 | 12/12 |

这表明本筛选中 Huber/M1 的位置误差低于 Fixed D3，但二者均高于 PK。方向一致不等于预注册显著性、真实部署收益或论文创新结论。

### period 8 多参数共同成功

只有 period 8 保留可复算的完整 Detection→truth lineage。四方法共同成功机会为 **1,982/2,880（68.82%）**，以下为 seed 均值 ± seed 间 SD：

| 参数 | PK Known | Fixed D3 | Huber D3 ON | M1 |
|---|---:|---:|---:|---:|
| 方位 RMSE（deg） | 0.01782 ± 0.00184 | 0.04214 ± 0.00288 | 0.03678 ± 0.00366 | 0.03197 ± 0.00346 |
| 位置 RMSE（m） | 27.08 ± 2.78 | 63.87 ± 4.37 | 55.75 ± 5.55 | 48.48 ± 5.23 |
| 斜距 RMSE（m） | 0.7162 ± 0.0526 | 0.7197 ± 0.0578 | 0.7234 ± 0.0540 | 0.7224 ± 0.0536 |
| 折叠 Doppler RMSE（Hz） | 3.173 ± 0.504 | 3.226 ± 0.555 | 3.269 ± 0.543 | 3.267 ± 0.564 |

全十周期共同方位、斜距和折叠 Doppler、共同地速、有效径向速度仍为 `NOT_EVALUABLE`。归档 TrackManager 仅保留 case 级摘要；目标专属复数幅相与完整逐周期 track debug 未保留。

## 航迹与时延残差

归档 TrackManager 汇总显示，确认后匹配率为 A0/PK 99.383%、Current 79.996%、Fixed 99.614%、Huber 99.614%、M1 99.460%。每个目标场景 case 的 false-confirmed tracks 分别为 155.95、156.13、189.06、158.05、156.72、156.45（按 A0、PK、Current、Fixed、Huber、M1 顺序）。这些是 target-present 汇总，**不是独立 H0 或系统 Pfa**。

平均有符号时延残差（估计减注入值）为 Fixed D3 −0.5900 ns、Huber D3 ON −0.4555 ns、M1 −0.3342 ns。Huber/M1 来源于 ON-only、truth-free 的 C1/C2，但估计拟合使用完整十周期、1,300 pulses 的 batch/offline 数据；后续周期参与较早周期结果的估计。实时因果可用性和独立 estimator runtime 未验证。

## 状态、证据和磁盘

| 状态 | 本轮判定 |
|---|---|
| `IMPLEMENTED` | 离线审计器、parity 校验、位置共同成功表、图表与报告已更新 |
| `EXECUTED` | 144 个 compact ZIP 直接读取并完成 SHA、面积杂波、任务和参数审计；未运行 CUDA |
| `EVALUABLE` | 检测/航迹筛选汇总、全十周期位置共同成功、period 8 多参数共同成功 |
| `PERFORMANCE_VERIFIED` | `false`：仍为筛选数据，且多个任务维度与实时估计边界未闭合 |

- V3.2 实验源码 commit：`74bccce61f3ec2f74746512d67271c815ce6a3a8`。
- V3.2 design SHA-256：`9fcc56e27c3a78a96ccfbecdf6ef1b3098fb1298d4bac9886058ea9af6081063`。
- Archive index SHA-256：`a5bd66302acc12ac410ad4f8560ecd7e137ba40d4e54a09abd7eaadcf4d8bdd1`。
- 144/144 使用中归档 SHA 与索引一致；面积杂波 144/144 case 均有效。
- 本地 1.5 GiB V3.2 ZIP 保留在 `outputs/r22_v3_2_screening_20261106/archives/screening/`，未上传 GitHub；无 ZIP 复制、解压、删除或改写。Git 审查包包含紧凑派生证据，不包含原始 IQ 与完整 CUDA 日志。
- 本轮分析输出约 6.1 MB；项目工作树约 4.8 GiB，低于 20 GB 上限，文件系统约 11 GiB 可用。没有删除既有输入、实验归档或用户数据。
- 研究总纲 blob `e637e3769bde7814298ff012d0f42301884e2985` 保持不变。

## 唯一下一步建议

若用户继续，先冻结一份输出完整的 R22-V4 确认性实验契约，不自动运行 CUDA。重点是确认全十周期位置共同成功差距，保存全十周期 Detection/truth lineage 以计算角度、距离和 Doppler共同误差，保存 TrackManager逐帧关联和目标专属复数幅相，并定义独立 H0/Pfa 统计单位。Huber/M1 应另行记录逐周期可用数据与 estimator runtime，以检验 batch screening 结果能否转为在线可用方法。

新契约须比较 A0、Current、Known 与同一部署信息条件下的确定性估计，报告覆盖率和预定义共同机会；再根据独立确认结果与简单确定性替代方法的对照决定是否调整方向。只有证明稳定、任务相关且确定性方法不能解决的残差后，才重新评估 AI。该建议不授权新实验。本阶段完成后停止。

复现入口、逐项证据与限制见本目录 [`README.md`](README.md) 和 [`evidence/manifest.json`](evidence/manifest.json)。
