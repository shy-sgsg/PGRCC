# R22-S1：C4 Mode-B 参考支持失败的 CUDA 因果诊断

> **2026-10-31 更新：** S1d 在 period-correct GMTI05 上完成同 binary CSI phase-vector crossover。SNR30 的选定组 support 在自身 ON 向量下为7，交叉应用 SNR20 ON / paired OFF 向量后为11/22；独立重算与 Production 一致。结果只证明该组对相位向量来源敏感，不证明物理根因或任务收益。详见 [S1d 结果](S1D_PHASE_VECTOR_CROSSOVER_RESULT.md)、[紧凑证据](evidence/s1d/)和[固定快照 ChatGPT 审查提示词](../CHATGPT_REVIEW_PROMPT_S1D_20261031.md)。S1c 独立审查仍是后续研究方向的必需审查；R22 screening 仍停在 `GATE_FAILED`，没有恢复矩阵。

> **最新补充（2026-10-29）：** 用户批准后完成 [S1c GMTI05 共同 target-free OFF CSI 相位反事实](S1C_SHARED_OFF_PHASE_RESULT.md)。在同一输入与冻结门槛下，SNR20/SNR30 support 从25/7变为21/22；这支持局部 CSI phase 敏感性，不证明目标污染或任务性能收益。S1c 首轮十期 attempt 因 phase sidecar 周期身份不明已隔离并作废，见 [失效说明](evidence/s1c/ATTEMPT1_INVALIDATION.md)。

## 阶段裁决

**`INCONCLUSIVE / STOP_AT_GATE`。** 本次真实 CUDA Production 重放精确复现了冻结门控结果，并确认 SNR20/SNR30 配对背景和诊断 tap 的非侵入性。证据使“目标污染导致参考支持崩溃”更可信，但 ON/OFF/TO 在非线性处理后不闭合，因此仍不能唯一归因。没有性能验证，也没有统计显著性结论。

本报告是对早先离线审计的补充和更正。没有修改冻结门限、R22 Screening 矩阵或研究总纲，也没有启动 AI 或新的宽矩阵实验。

## 研究定位与固定身份

- 总体目标：刻画传统 GMTI 方法的能力边界，并识别可证伪的任务瓶颈。
- 本阶段问题：C4 Robust DDC-RB 在 `GMTI05 / beam 50 / az 52 / range 1065–1197` 的支持数由25降至7，能否定位导致变化的观测与物理条件？
- 审查输入基线：`bbc2476282f9245f516b41eab1754f69af7ee75d`；原实验报告快照：`95a8854b01b9af26e6e955d0871e130db81e2f6e`。
- 实验执行时 Git HEAD：`dc39afcbfea060777a4a7b247aeb3d4e16fe6d0c`。诊断 tap 当时是该工作树上的未提交改动；最终提交会包含源码、测试和本报告。
- 总纲 blob：`e637e3769bde7814298ff012d0f42301884e2985`，保持不变。
- Stage2 binary SHA-256：`47fa5ec34ca15cdc4edeb35226eefa941a78ee344aa2311e5e7984c811dcb40f`。
- 原 Production binary SHA-256：`d9e4d2e0b8429c5415e94be1af0230f3c0148455c394dc6d661e7e6c13a4ad1e`。
- 诊断 Production binary SHA-256：`480db5484a705634d7b467dcdec4b0c77cf8f212489071f8350cb2e0a195b83a`；本地 Native CUDA，CUDA 12.4，Release，架构 75，CoreX 关闭。运行前 manifest 记录 GPU 为 NVIDIA GeForce RTX 3050 Laptop GPU，P0，51°C，13.69 W，5% 利用率，SM 1500 MHz。
- 方法配置 SHA-256：`588c36904f62febad8476ddccb789ba67de2e9c23dc2ff12b52418eb06706bbc`。

运行参数固定为 seed1932001、2.5 m/s、+4 ns、20 targets、10 periods、beam 50；只重放 SNR20 和 SNR30 两个配对场景。C4 最低支持数仍为8，相位残差门限仍为±0.35 rad。Stage2 实际运行6个角色，Production 运行12个分支；SNR30 按既有门控在 C4 Mode-B reference 失败后停止相依分支。

## 杂波与配对条件

SNR20/30 的 R22 汇总 manifest 记录的面积杂波数据来自 `A0_ON` 写入的共同 `A0_background`：1,300 packets、6,508,782 samples、测得平均功率 `0.0221596696662`，距离窗 `[84500,85500] m`，模型 `continuous_texture`。A1 ON/OFF/TO 单角色报告的 `continuous_area_packets/samples=0` 表示该次没有重新生成，因为它们通过 `background_input_dir` 复用 A0 背景；不能将该字段解释为 A1 输入没有地杂波。S1b 对 SNR30 的 A0 生成报告、三个 A1 场景配置及 10/10 `background_reuse_mapping` 做了直接核验，证据见 [`evidence/s1b/stage2/`](evidence/s1b/stage2/)。

两组 `A1_OFF` 的十个 Stage2 周期文件逐字节相同；对失败时段所对应的 Production F1/F2，OFF20 与 OFF30 的266个复通道值也完全相同。Stage2 target generator manifest 除目标 SNR 外相同。

诊断 trace 显示 Mode-A 与 Mode-B 的 F1/F2 在 SNR20 OFF、ON、TO 以及 SNR30 ON 上逐值一致，每项比较覆盖133个距离单元的F1/F2复值。SNR30 Mode-B OFF/TO 分支因上游 ON reference 失败没有运行，故无对应对照。

## 支持门控的重放结果

按 Production 输入的 float32 精度还原 CSV 数值，并使用冻结的 `F2·conj(F1)` 相位中心与±0.35 rad筛选，独立离线计算与 CUDA 输出精确一致：

| 条件 | 输入角色 / 算子 | 当前输入有效互谱单元 | 支持数 | 相位一致性 | 生产状态 |
|---|---|---:|---:|---:|---|
| SNR20 ON | Mode-B，当前观测直接拟合 | 133 | 25 | 0.2028995670 | `OK` |
| SNR30 ON | Mode-B，当前观测直接拟合 | 133 | 7 | 0.1172000323 | `NOT_EVALUABLE`，低于8 |
| SNR20 OFF | Mode-A，OFF输入直接拟合 | 133 | 55 | 0.7418131251 | `OK` |
| SNR30 OFF | Mode-A，OFF输入直接拟合 | 133 | 55 | 0.7418131251 | `OK` |
| SNR20 TO | Mode-A 固定 OFF 参考应用 | 133 | 14（离线对 TO 输入重拟合） | 0.0219272154（离线） | 生产行显示继承的 OFF 参考55，不是 TO 自身拟合结果 |
| SNR30 TO | Mode-A 固定 OFF 参考应用 | 133 | 14（离线对 TO 输入重拟合） | 0.0219269821（离线） | 生产行显示继承的 OFF 参考55，不是 TO 自身拟合结果 |

ON20 的25个保留距离单元与 ON30 的7个仅有1个重合：ON20独有24个、ON30独有6个；相位中心由 `1.7583232 rad` 移到 `0.9807498 rad`，变化 `−0.7775734 rad`。这说明退化不是由名义分组长度变化造成，而是这一配对观测下相位分布和筛选成员发生了明显变化。

**周期索引更正：** Production `GMTI05` 的一基序号5映射到 Stage2 零基 `period_0004.bin`，不能映射到 `period_0005.bin`。失败组所在的 Stage2 period 4、beam 50 中，T004/T010/T012/T017/T019 五个可见目标的 range bin 分别为1124/1076/1162/1130/1102，均落入失败距离组。T010 的bin1076是 GMTI05 对应时刻；此前把下一时刻的bin1175归入 GMTI05 属于周期错位。range-bin 重合不能证明目标处理后二维响应覆盖 az row 52，也不能把全部 TO 响应归给单个 T010。

## 因果解释边界

配对的 OFF 背景相同、OFF 处理输入完全相同，而 ON 从 SNR20 到 SNR30 出现支持数25→7和保留集合变化；失败组距离窗内同时存在多个可见目标。这些证据支持“目标幅度变化或目标与处理链交互影响参考相位”的解释。

但在 Production 的 P38/range-phase 后，`ON = OFF + TO` 不成立：

| SNR | F1 相对 L2 残差 | F2 相对 L2 残差 | 互谱相对 L2 残差 |
|---:|---:|---:|---:|
| 20 dB | 0.01015 | 0.76541 | 0.67784 |
| 30 dB | 0.00948 | 0.74374 | 0.64460 |

因此，F2 与互谱层面的差异很大，当前三条件对照不能作为线性目标分量分解。目标污染仍是有证据支持的假设，不是已证实的唯一根因。未证明目标在方位维扩展到 row 52，也未测量目标保持、Detection/Pd、独立H0/Pfa或径向速度与方位误差。支持数恢复不能替代任务性能改善。

## 诊断 tap 非侵入性

新增 tap 由 `PGRCC_C4_TRACE_GROUP=5,50,52,1065,1197` 显式启用，只记录指定组的 Production F1/F2 复数输入及来源元数据；缺失或写入失败时显式失败。未设置环境变量时不改变原输出路径。质量门、估计算法和运行参数未变。

将同一 SNR30 A1_ON 的 `period_0004.bin` 分别送入原 Production binary 和诊断 binary（后者运行完整10周期，基线只运行目标周期），对可严格对齐的 `GMTI05` 检查：

- `GMTI05.bin` SHA-256 完全一致：`15c9a5d2fdde237335ac6313b16da7457748b810264e0dae2819a8bd6f6e1866`；
- 156条Gamma分组的键与所有结果字段完全一致，失败组均为 support 7、`insufficient_robust_support`；
- adapter summary 对齐字段完全一致；
- period 4 / beam 50 的两条 CFAR geometry 记录完全一致。

Detection CSV 和 track 文件没有作字节相等声明：基线从新 TrackManager 历史只处理一个周期，诊断运行处理十个周期，历史上下文不同。tap 的非侵入性结论只覆盖上列对齐产物。

## 交付状态与研究判断

- `IMPLEMENTED`：选定单组 F1/F2 tap、失败关闭行为和配置自检。
- `EXECUTED`：本地 Native CUDA 下完成 SNR20/SNR30 配对重放和原 binary 单周期对照。
- `EVALUABLE`：冻结支持门重现、选定组逐单元相位成员、配对 OFF 输入及 tap 对齐产物可评估；目标污染的唯一因果归属不可评估。
- `PERFORMANCE_VERIFIED=false`：单 seed、单 beam 的可行性/正确性诊断，不作 Pd/Pfa 或统计结论。
- 研究方向：`INCONCLUSIVE / STOP_AT_GATE`。保留 C4 失败现场，不降低支持阈值，不回退为 Current，不恢复大规模 Screening，不进入 AI。

### 后续建议（待独立审查后裁定，不自动启动）

S1b 已执行。若 ChatGPT 认为还需要解释 SNR20→SNR30 的支持数变化，唯一建议的后续是 **R22-S1c：同一选定组的共同距离相位反事实**：复用两种 SNR 下相同 seed1932001、+4 ns、beam 50 的 `OFF/ON/TO` Stage2 输入；用 target-free OFF 估计一个共同 CSI 距离相位向量，再将该固定向量应用到 ON20 与 ON30 的选定处理组，对比原生逐角色拟合与共同相位条件下的 C4 support、相位残差和保留单元。真值不进入估计，`min_support=8` 与 ±0.35 rad 门限不变。该反事实只区分角色自适应相位拟合与输入 SNR 变化对 support 的影响，不是部署算法比较，也不证明检测收益。若共同向量无法与四个输入条件身份闭合，或结果仍不能区分两种来源，则保留失败并停止归因；是否执行须在本次独立审查后由用户决定。

## 证据入口与复现

紧凑审查证据位于 [`evidence/`](evidence/)（约4.6 MB），包括实际运行 XML、逐分支 manifest/运行配置/日志、Stage2 报告、目标真值切片、十个 Mode-A/Mode-B 选定组输入、逐单元相位审计 CSV、图表、对照摘要及 SHA-256 manifest。GitHub 审查副本中的运行日志只规范化换行并去除行尾空格；`evidence_manifest.json` 同时记录副本和原始本地日志的 SHA-256。执行机上的原始日志和 Stage2 大型二进制均未修改，保存在忽略目录 `outputs/r22_s1_c4_support_replay_20261029/`，没有上传 GitHub；manifest 记录所用输入身份及所选处理输出。用户确认 ChatGPT 审查完成前保留该本地重放目录。

在仓库根目录用已构建的 CUDA 诊断 binary 重放单个配置：

```bash
PGRCC_C4_TRACE_GROUP=5,50,52,1065,1197 python3 scripts/phase_r/run_r22_cuda_pilot.py \
  --phase screening --seed 1932001 --speed-mps 2.5 --snr-db 30 --delay-ns 4 \
  --target-count 20 --period-count 10 --first-beam 50 --beam-count 1 \
  --stage2-binary build/simulate_stage2_statistical \
  --production-binary build_r22s1/GMTI_pipe_core \
  --template simulator/scenarios/beam50_one_targets_continuous.run.json \
  --method-config docs/research/phase_r/R22/config/r22_method_config_v2_range_roi.json \
  --method-ids C4 --calibration-modes Mode-A,Mode-B --keep-raw
```

离线复核命令：

```bash
python3 -m py_compile docs/research/phase_r/R22/support_failure_diagnostic/analyze_selected_cells.py
python3 docs/research/phase_r/R22/support_failure_diagnostic/analyze_selected_cells.py
```

两种SNR的实际命令、退出码、运行时XML、Stage2报告和分支日志均收录在紧凑证据包；完整命令也保存在执行机 runner log。SNR30 因既有质量门报告 `completed_with_gaps` / `EVALUABLE=false`，这是预期失败门控，不是成功检测结果。

## S1b 处理阶段审计

用户批准的 S1b 已完成；结论、实验身份、局限、单一后续建议和复核命令见 [`S1B_STAGE_BOUNDARY_AUDIT.md`](S1B_STAGE_BOUNDARY_AUDIT.md)。简述：三角色 CUDA Production 运行均正常；原 Mode-B 失败组输入逐值复现；最大的 F2 闭合恶化发生于 CSI 距离相位校正之后，C4 adapter 按固定 γ 正常传递该差异。距离相位前已存在小残差，且尚无 SNR20 同边界反事实，因此不能唯一归因 support 25→7，也不构成任务性能证据。

审查所需的小型运行快照、runtime XML、分支 manifest、原始日志、133格 trace、相位与 γ 记录及面积杂波背景复用链均在 [`evidence/s1b/`](evidence/s1b/)。A1 角色报告的“新生成杂波计数为0”对应复用 A0 背景；背景生成和10/10复用映射有单独证据，不能将 A1 报告中的0解释成无杂波输入。

提交给 ChatGPT 的固定快照审查提示词见 [`CHATGPT_REVIEW_REQUEST.md`](CHATGPT_REVIEW_REQUEST.md)。
