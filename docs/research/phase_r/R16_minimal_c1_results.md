# R16 最小 C1 Gamma=1 Native CUDA Production 结果

日期：2026-10-25。状态标签依次区分实现、执行、可评价性和性能验证；单 seed 结果不作统计显著性或普遍性能声明。

## 1. 研究问题与范围

本阶段在 seed505、目标地速 6.7 m/s、SNR 30 dB、零通道时延、beam 50、3 periods 下，评估无需额外 OFF 参考的传统 C1 ordinary subtraction（固定 `Gamma=1+j0`），并与同一次运行的 Production Current C0 配对比较。A0 提供同链路参考。实验复用 R14 的 Stage2 场景输入；不研究未知系统误差效应，不扩 seed，不扫描 Gamma，不训练 AI。

## 2. 阶段门和实际执行

- **IMPLEMENTED：** runner 将“写入 adapter tap”与“拟合/应用跨角色参考”分开；C1 被限定为 `not_applicable` 模式，不读取 OFF reference、不调用 estimator、无真值与 Current fallback。评估器支持 C0/C1 子集并校验 Production adapter 真实 schema。相应源代码、测试和精确差异见[证据包 source snapshot](evidence/r16_c1_gamma1_seed505/source_snapshot/)。
- **EXECUTED：** 原生 Release CUDA 构建成功；使用本机 NVIDIA GeForce RTX 3050 Laptop 实际运行 9 个 A0/C0/C1 × OFF/ON/TO Production branches，9/9 返回通过。CUDA 执行时采用提权设备访问；执行前记录 Driver 580.178.04、CUDA driver 13.0、P8、45°C、6 W/80 W、558/4096 MiB、7% utilization。该任务不是性能基准。
- **EVALUABLE：** Stage2 面积杂波、Stage2 输入、F1/F2 tap、C1 adapter、CSI、CFAR final-union geometry/maps、Detection 和 TrackManager 诊断已按本报告范围审计。经验 Pfa 与径向速度仍不可评估。
- **PERFORMANCE_VERIFIED：** `false`。一个 seed、一个速度和一个 SNR 只支持该冻结场景的可行性观察。

Stage2 报告确认连续面积地杂波有效：平台高度 6000 m，斜距窗 84.5–85.5 km（地距约 84.287–85.289 km），注入 390 packets、1,952,655 samples；测量平均杂波功率 0.0234967，lag-1 相关系数 0.9800183，噪声平均功率约 0.000999854。未降低任何质量门槛。逐期输入、配置和报告身份见[机器证据](evidence/r16_c1_gamma1_seed505/)。

9 个分支的 Stage2 输入身份与 C0/C1 配对闭合，F1/F2 tap 均完整且跨方法相同。C1 每角色三期均报告 `ordinary_subtraction`、固定 `Gamma=1+j0`、Production 最终 F1/F2 来源、有效支持量 159,705、无 truth、无参考拟合和无 fallback。9 个 C1 分支逐一通过。

## 3. C0 与 C1 的任务结果

### Detection 与航迹

| 方法 | OFF 检测行 / H0 confirmed-track period associations | ON 正确目标 / 3 | ON 检测行 / 非目标行 | TO 正确目标 / 3 | TO 检测行 / 非目标行 |
|---|---:|---:|---:|---:|---:|
| C0 Production Current | 32 / 8 | 3/3 | 174 / 171 | 3/3 | 454 / 451 |
| C1 ordinary subtraction | 32 / 8 | 1/3 | 153 / 152 | 1/3 | 169 / 168 |

C1 在 ON 和 TO 各漏掉 2/3 个目标机会。它同时减少了非目标检测行，但这不是 Pfa 或虚警率改善证据；R16 没有可用的经验 false-CUT 估计器。C0 的 3 个 ON/TO 目标都被检测，但评估器确认的当前周期目标航迹关联分别为 2 个；检测与确认航迹关联按不同指标报告。

TrackManager 的当前帧确认关联审计为：ON 中 C0 有 2 个正确目标 track-period associations、23 个非目标关联；C1 有 1 个正确目标关联、22 个非目标关联。TO 中 C0 为 2/192，C1 为 1/11。OFF 下两者均有 8 个 false confirmed track-period associations。这里的非目标 Detection/track 数是任务链候选统计，不是 Pfa。C0 的 ON 目标在 period 1–2 关联到 track 7，TO 关联到 track 5；C1 仅 period 2 有正确目标确认关联（track 5），没有完整跨期目标 track continuity。

### CSI 与目标保持

| 指标 | C0 | C1 | C1 相对 C0 |
|---|---:|---:|---:|
| H0 active-support CSI cancellation | +5.582 dB | −2.453 dB | −8.035 dB |
| TO target-only CSI retention | −0.671 dB | −18.223 dB | −17.552 dB |
| evaluator output-SCNR proxy | 86.962 dB | 65.013 dB | −21.950 dB |

负的 H0 cancellation 表示该定义下残余功率高于输入功率。SCNR 是 evaluator 指定的配对 ROI proxy，不等同于外场操作性 SCNR。目标复增益传递诊断（C0 `−22.849 dB / +1.828 rad`；C1 `+0.075 dB / −0.058 rad`）与 TO target-only retention 并非同一个统计量，不能单独用其一替代目标保持结论。

### CFAR 与参数估计

全部 27 张 A0/C0/C1 × OFF/ON/TO × 3-period final-union map 的 geometry/map/log 审计通过；每张 map 有 527,280 个有效 CUT，单分支三期合计 1,581,840。C0/C1 合并 observed hit fraction 分别为 0.002531 / 0.000728，仅是所观察到的 hit-cell 占有效 CUT 比例，**不是 Pfa**。统计性 Pd/Pfa 未验证，radial velocity 因当前解模糊输出不可评估。

C1 只有 period 2 留下正确目标机会；C0/C1 的 ON/TO 可共同比较参数机会各仅 `n=1`。ON 共同机会中位置/角度与 folded Doppler 数值相同，ground-speed 绝对误差为 C0 0.312、C1 0.073 m/s；TO 共同机会的指标方向相互冲突。该样本量不支持 C1 参数估计更优的主张。

单次 Production wall-time（秒；只作本次执行描述，不是稳定性能基准）为：C0 OFF/ON/TO `2.890/3.496/5.059`，C1 OFF/ON/TO `2.796/3.397/3.395`。

## 4. 跨阶段可比性审计

R16 与 R14 的 OFF/ON/TO 九个 Stage2 period 文件大小与 SHA-256 全部相同；C0 对应的 F1/F2 输入及 CSI after real/imag hashes 也在 9/9 组相同。然而，R14 `GMTI_pipe_core` SHA-256 为 `f65a8aa446754cc79b6417f9275b3a01e73eb85098c6a4f9eaea0cb999c38962`，R16 为 `ae41c79ee48b530fc71e07b4ade7dcf7095d2ae9e9ee388f0e5ed9943a8fca4f`。R14 与 R16 的 C0 final-union CFAR map 在 9/9 period 均不同，C0 检测总行也不同（R14 OFF/ON/TO：358/539/592；R16：32/174/454）。因此，R14 C2/C3 与 R16 C1 不能构成严格跨运行因果比较；本报告不据历史数值对 C1、C2、C3 排名，也未重跑 C2/C3。

本次同运行 C0/C1 比较使用相同 Native CUDA Production executable、同一组 Stage2 输入以及相同的 F1/F2。Runtime XML 的语义差异只有预期的校正开关/方法字段；其余 processing 字段一致，输出和 track-debug 路径除外。逐项审计见证据包中的 XML 与输入比较 CSV。

信息条件也不同于 R14 的 C2/C3：C0/C1 不使用额外 paired OFF 标定参考；R14 的 C2/C3 Mode-A 会用 paired OFF 拟合 reference，且 OFF/H0 是 reference-in-sample。这个信息量差异必须纳入方法解释。由于 executable 还不同，本报告不将 R14 的 C2/C3 当成公平任务排名。

## 5. 结论与限制

在这个正常速度、零时延单 seed 场景，reference-free C1 Gamma=1 虽减少检测候选行，但目标漏检增多、H0 杂波残余增加且 TO 目标-only CSI retention 明显下降。因此它在该场景下不是可接受的整体替代方案。结果不证明 paired OFF 是必要条件；C1 是固定系数极简基线，不代表合理设计的盲估计器。结果也不回答未知通道误差是否损害 GMTI，不证明低速目标或复杂杂波应成为新主线，更不构成 AI 必要性证据。

通用 runner manifest 的 residual-closure gate 因本次明确裁剪的方法子集未包含它要求的 PK/PKR 而标记 `NOT_EVALUABLE`；此状态不是九个已选分支失败。runner 的通用 valid-CUT denominator 标记也因多分支 union 规则标记 `NOT_EVALUABLE`；R16 专项 evaluator 已独立验证 final-union map 与 geometry。经验 Pfa 仍为 `NOT_EVALUABLE_NO_TRUTH_FILTERED_FALSE_CUT_ESTIMATOR`。不得把这些通用门状态写成已通过。

复现命令、完整分支结果、限定保留的 CFAR map、源码快照、输入身份、runtime XML、CSI/CFAR/Detection/TrackManager 证据及清理账本见[R16 证据包](evidence/r16_c1_gamma1_seed505/README.md)。
