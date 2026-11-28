# R22 V3.2 Native CUDA Screening 执行与结果报告

日期：2026-11-09<br>
阶段：`R22 V3.2 Identity-Closed Full Screening`<br>
裁决：`SCREENING_COMPLETE / PERFORMANCE_NOT_VERIFIED`

## 结论摘要

在冻结的仿真场景中，+4 ns 通道时延条件下的 Production Current（C0）目标命中率明显低于无误差参考（A0）；Known physical correction（PK）回到 A0 附近。P1 和 P2 Mode-A 的估计校正也在本次筛选中接近各自的 Known correction。

这组恢复结果有一项关键边界：P1/P2 的盲时延估计使用配对的 target-free OFF 输入。运行记录没有发现估计器读取误差真值或目标真值，但这只证明了受限信息条件下的诊断性恢复，尚未证明目标存在时可部署的在线估计能力。

本阶段是覆盖和可行性筛选，不是确认性性能试验。不得据此声称统计显著的 Pd/Pfa 改善。经验系统 Pfa、目标专属复数幅相保持、径向速度误差和外场泛化均未验证。

## 状态标签

| 状态 | 本阶段判定 | 依据 |
|---|---|---|
| `IMPLEMENTED` | 是 | 冻结 V3.2 设计、Native CUDA 矩阵 runner、方法级门控和归档分析器均存在。 |
| `EXECUTED` | 是 | 本机 Native CUDA 完成 288/288 个计划 case；完整 case 归档经 SHA/ZIP CRC 核验后删除可重建 raw。 |
| `EVALUABLE` | 是，带方法级缺口 | 288 个 case 均有最终归档，4,320 个适用 ON 方法单元均已分类；49 个方法单元因参考支持不足标为 `NOT_EVALUABLE`，未伪装为漏检。 |
| `PERFORMANCE_VERIFIED` | 否 | 本阶段是 Screening；没有确认性独立验证或统计显著性结论。 |

## 固定实验身份

| 项目 | 固定身份 |
|---|---|
| 仓库 / 分支 | `shy-sgsg/PGRCC` / `codex/unknown-system-error-next-stage` |
| 实验源码 commit | `74bccce61f3ec2f74746512d67271c815ce6a3a8`；manifest 记录 tracked worktree clean |
| 研究总纲 blob | `e637e3769bde7814298ff012d0f42301884e2985`，未修改 |
| V3.2 design | `R22_EXTENSION_20261106_V3_2_IDENTITY_CLOSED_FULL_SCREENING` |
| design 文件 SHA-256 | `9fcc56e27c3a78a96ccfbecdf6ef1b3098fb1298d4bac9886058ea9af6081063` |
| canonical design SHA-256 | `ecdb5a3306d27f3298f8c4fd3f199728733ec0f2bae0fd13590e277579b765f5` |
| 方法配置 SHA-256 | `588c36904f62febad8476ddccb789ba67de2e9c23dc2ff12b52418eb06706bbc` |
| Stage2 binary SHA-256 | `47fa5ec34ca15cdc4edeb35226eefa941a78ee344aa2311e5e7984c811dcb40f` |
| Production binary SHA-256 | `d9e4d2e0b8429c5415e94be1af0230f3c0148455c394dc6d661e7e6c13a4ad1e` |
| 设备 | NVIDIA GeForce RTX 3050 Laptop GPU；driver `580.178.04`；Native CUDA；CoreX=false |

以上源码、配置和二进制身份来自本次矩阵 manifest。运行时 XML、Production run manifest、日志、CSI tap、CFAR geometry、truth/task 记录保存在每个 compact ZIP 中；GitHub 精简包只包含汇总表和归档 SHA 索引，不包含 288 份完整 ZIP。

## 设计与执行范围

冻结条件为 12 个 seed（1932001–1932012）× 4 个径向速度（0.5、1.5、2.5、6.7 m/s）× 3 个 SNR（10、20、30 dB）× 2 个时延（0、+4 ns），共 288 个 case。每个场景 20 个目标、10 个处理周期、beam 50。

处理距离范围为 bin 666–1197（含端点），参考分组宽度 133 bins，`min_support=8`、相位门限 `0.35 rad`。本阶段没有降低支持门槛、没有静默 fallback、没有训练 AI 或启用 Router。

连续面积杂波使用 84.5–85.5 km 距离窗，配置均值功率 0.02、纹理 sigma 0.25、时间相关系数 0.98，热噪声功率 0.001。所有 288 个 Stage2 case 均记录 1,300 个连续面积 packet 和 6,508,782 个样本；ON/OFF/TO 因果三元组及背景映射均通过门控。

实测面积杂波功率跨 seed 均值为 0.022672，seed 间样本标准差为 0.000312（CV 1.38%）；同一 seed 的 24 个 case 实测功率相同。实测 lag-1 相关系数均值为 0.980003，范围 0.979986–0.980012。Stage2 `area_power_cv_measured` 按代码定义为样本功率标准差/均值，288 case 均值 2.045，范围 1.959–2.100；该量描述面积样本功率离散度，不是跨 seed 的功率 CV。

## 执行完整性与方法覆盖

矩阵终态为 `COMPLETED`：255 个 case 为 `PASSED`，33 个为 `PASSED_WITH_METHOD_GAPS`。288 个最新 case 归档通过验证；另保留 1 个已核验的历史中断 attempt 归档。所有最新 case 的可重建 raw 均在归档 SHA 和 ZIP CRC 验证后清理。

4,320/4,320 个适用 ON 方法单元均分类。49 个方法单元缺口全部为 `insufficient_robust_support`：

| 方法 | 可评价 / 适用 | 覆盖率 | 缺口 |
|---|---:|---:|---:|
| C4 Mode-B | 264 / 288 | 91.67% | 24 |
| P2 Mode-B | 137 / 144 | 95.14% | 7 |
| PKR Mode-B | 270 / 288 | 93.75% | 18 |
| 其他适用方法/模式 | 100% | 100% | 0 |

这 49 行保留为方法级不可评价，不作为漏检计数。33 个带方法缺口的 case 中，其余可评价方法记录仍保留。P2 Mode-B 的完整 C4/PKR/P2 配对 triplet 只覆盖 12 个 seed 中的 1 个；其余 seed 的部分配对 recovery 均值不用于方法性能结论。

## 目标检测与已知/估计校正

下表是 +4 ns 条件下按 seed 汇总后再跨 12 个独立 seed 求均值的正确目标命中率；括号为 seed 间标准差。每个 seed 聚合 12 个速度×SNR case、2,400 个目标周期机会。目标机会不是独立 seed，所有值均为描述性 Screening 结果。

| 方法 | 命中率均值 ± seed SD | 覆盖 |
|---|---:|---:|
| A0 无误差参考 | 69.73% ± 5.14% | 12/12 完整 |
| C0 Production Current（+4 ns） | 11.69% ± 2.26% | 12/12 完整 |
| PK Known physical correction | 69.73% ± 5.14% | 12/12 完整 |
| P1 盲物理时延校正后接 Current | 69.59% ± 5.17% | 12/12 完整 |
| P2 Mode-A 盲物理校正后接 Robust DDC-RB | 71.11% ± 5.36% | 12/12 完整 |

因此，本场景的 `Known − Current` 可恢复空间为 58.03 个百分点；P1 相对 Current 实际恢复 57.89 个百分点，跨 seed 平均 recovery ratio 为 99.77%（SD 0.89%，范围 97.88%–100.70%）。P2 Mode-A 使用其对应配对组（Current=C4 Mode-A、Known=PKR Mode-A）：可恢复空间 60.09 个百分点，实际恢复 59.89 个百分点，recovery ratio 99.69%（SD 0.99%，范围 97.52%–100.86%）。这些接近 100% 的恢复比受 target-free OFF 估计信息条件约束，不能当作在线部署结果。

速度分层结果也显示下降并非只出现在低速档。下表按 seed 聚合每个速度下的 3 个 SNR：

| 速度 m/s | A0 无误差 | C0 Current +4 ns | PK Known | P1 OFF 估计 |
|---:|---:|---:|---:|---:|
| 0.5 | 67.18% | 12.14% | 67.18% | 66.81% |
| 1.5 | 67.08% | 10.40% | 67.08% | 67.19% |
| 2.5 | 71.58% | 12.25% | 71.58% | 70.92% |
| 6.7 | 73.07% | 11.99% | 73.07% | 73.43% |

该结果支持“本冻结时延失配在多个速度档都造成任务退化”，不支持将低速目标保护单独升级为当前主线。它也不是对所有低速目标或真实平台的统计性结论。

## 参数、杂波功率与 CSI/目标保持边界

以下为 +4 ns 条件的 own-success RMSE：先按 seed 汇总，再对有有效估计的 seed 求均值。不同方法成功估计的机会集合可能不同，因此不是共同成功机会上的配对因果比较。

| 方法 | 方位角 RMSE | 位置 RMSE | 地速 RMSE | 折叠 Doppler RMSE | 径向速度有效 seed |
|---|---:|---:|---:|---:|---:|
| A0 | 0.0212° | 31.76 m | 12.20 m/s | 3.279 Hz | 0/12 |
| C0 | 0.1138° | 170.20 m | 22.65 m/s | 3.240 Hz | 0/12 |
| PK | 0.0212° | 31.77 m | 12.20 m/s | 3.279 Hz | 0/12 |
| P1 | 0.0464° | 69.59 m | 15.15 m/s | 3.279 Hz | 0/12 |
| P2 Mode-A | 0.0454° | 68.12 m | 14.93 m/s | 3.163 Hz | 0/12 |

A0 与 PK 的检测率、角度、位置、地速和折叠 Doppler 结果接近，支持本仿真中 Known correction 与无误差参考的物理闭合。C0 的 own-success 角度、位置和地速误差更大；P1/P2 Mode-A 有明显恢复但仍未达到 Known 的参数误差水平。径向速度估计没有有效 seed。

CSI tap 对全部已执行分支保存了摘要；本阶段没有保存完整中间 CSI maps，也没有 target-only 复数幅相 ROI。源码将 `CA_ROI_dB` 定义为 `10 log10(before_mean_power / after_mean_power)`，正值表示该 tap ROI 中功率下降。+4 ns 的 target-free OFF `clutter_band_strong_power_top10pct` 描述均值为：A0 −0.877 dB、C0 −0.731 dB、PK −0.877 dB、P1 −0.840 dB、C4 Mode-A −0.745 dB、PKR Mode-A −0.894 dB、P2 Mode-A −0.855 dB。负值按该定义意味着 tap 的输出均值功率高于输入。

这些数值来自输入功率 top-10% 选区，不是 CFAR 最终残余 SCNR，也不是目标保持量；`active_support_unmasked` 混合目标、杂波与噪声。当前证据没有闭合“目标复数幅相保持”和“杂波 tap 功率变化”与最终检测结果之间的因果联系。不得把 CSI tap 数值包装成目标幅相恢复结论。

CFAR geometry 每张 map 的有效 CUT 数为 527,280。OFF 分支 false-CUT 统计只作描述性记录；当前 target-present V3.2 设计没有匹配的独立 H0 case 单元，因此经验系统 Pfa 为 `NOT_EVALUABLE`。已有独立 H0 矩阵属于另一份设计/执行身份，本报告不与 V3.2 拼接排名。

TrackManager 记录已随归档和精简表保存，但本阶段单 beam、10 周期筛选不构成完整扫描系统的航迹稳定性或虚警确认。任何“confirmed track”计数均不能代替独立 H0 Pfa。

## 科研方向判断

1. **未知 +4 ns 时延是否降低 GMTI 性能？** 在本仿真、固定 Production 链和四个速度档中，配对筛选数据强烈支持目标检测任务下降；12 个 seed 的 C0 命中率均值比 A0 低约 58 个百分点。该方向有继续研究价值，但尚非真实系统或一般时延误差的确认性结论。
2. **Known correction 恢复多少？** PK 将检测命中率恢复到 A0 附近；A0/PK 参数 RMSE 也接近。P1/P2 Mode-A 的筛选命中表现接近 Known，但估计来自 target-free OFF，仍需部署可用信息条件验证。
3. **盲估计距 Known 多远？** 在本次诊断性 OFF 信息条件下，P1 平均落后约 0.14 个百分点，P2 Mode-A 约 0.20 个百分点。P2 Mode-B 覆盖不足，不能给出可比差距。
4. **是否继续未知物理状态估计？** `CONTINUE` 有界、物理参数化的确定性时延/通道状态估计与误差传播研究；下一步优先验证 target-present 时可用的观测信息，不能把 OFF 估计结果直接称为在线性能。
5. **是否改为低速目标保护或复杂杂波？** 暂不调整。低速 0.5 m/s 也有失配损失，但相近退化出现在全部已测速度；复杂非平稳杂波没有单独受控实验。本阶段不能把二者提升为更重要主线。
6. **AI 是否必要？** `AI_ENTRY_GATE=FAIL`。确定性估计在当前诊断条件下已接近 Known；尚未证明部署可用信息下存在传统方法无法弥补的稳定任务差距。不得启动 AI 训练或 Router。

建议的下一项工作是先冻结一份小规模确认性设计：估计器仅使用真实部署可得的 target-present 多通道观测；保留配对 Known 上限；补充独立 H0、target-only complex response 和共同任务机会参数比较。此为建议，不自动启动 CUDA 或新的矩阵。

## 复现、产物和空间清理

完整矩阵归档位于本机 `outputs/r22_v3_2_screening_20261106/archives/screening/`，约 1.5 GiB；包含 288 个最终 case ZIP 和 1 个已核验历史 attempt ZIP。GitHub 精简审查材料位于 [`evidence/v3_2_screening_20261106/`](evidence/v3_2_screening_20261106/)，约 3.1 MB；包括 seed-level 检测、配对恢复、参数、方法覆盖、CSI ROI 摘要、track 指标和 archive SHA 索引。

本地重算汇总命令：

```bash
python3 scripts/phase_r/summarize_r22_v3_2_screening.py \
  --design docs/research/phase_r/R22/config/r22_extension_experiment_design_v3_2_identity_closed_20261106.json \
  --matrix-manifest outputs/r22_v3_2_screening_20261106/matrix/matrix_manifest.json \
  --archive-root outputs/r22_v3_2_screening_20261106/archives \
  --output-dir outputs/r22_v3_2_screening_20261106/analysis \
  --require-complete
```

此前执行的该命令返回 0，`analysis_status=COMPLETE_SCREENING`，验证了 288 个最新 ZIP、4,320/4,320 方法单元分类。完整 ZIP 没有上传到 GitHub；仅凭远端精简包不能声称重新检查了全部逐 beam 日志或原始 IQ。

清理后项目目录约 5.3 GiB，小于 20 GiB 最终上限。完整高价值 case archive、失败/方法缺口摘要、配置、manifest、runtime XML 和 cleanup ledger 均保留；raw `.bin`/CSI `.npy` 已在归档核验后清理。另清除一组父 manifest 标记为 `not_started`、零 case 但遗留的旧 targeted-zero 重放文件，保留父 manifest；逐路径清理量和理由见[磁盘清理台账](29_v3_2_disk_cleanup_ledger_20261109.md)。共享文件系统本身使用率仍高，后续运行前继续检查可用空间。

## 审查入口

- [紧凑 GitHub 审查证据包](evidence/v3_2_screening_20261106/README.md)
- [V3.2 初步方向评估](28_v3_2_preliminary_direction_assessment_20261109.md)
- [ChatGPT 独立审查提示词](CHATGPT_REVIEW_PROMPT_V3_2_SCREENING_20261109.md)
- [冻结 V3.2 设计](config/r22_extension_experiment_design_v3_2_identity_closed_20261106.json)
- [V3.2 执行契约](25_v3_2_identity_closed_screening_contract_20261106.md)
