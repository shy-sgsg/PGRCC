# R22-V4 周期因果、输出完整的确认性实验契约（草案）

> **状态：** `DRAFT_NOT_FROZEN`。本文件只定义建议的实验契约，不代表实现完成、实验已执行或 CUDA 已获授权。<br>
> **研究总纲：** `docs/research/RESEARCH_MASTER_PLAN.md`，本次基线 blob `e637e3769bde7814298ff012d0f42301884e2985`（正文不修改）。<br>
> **拟稿基线：** `codex/unknown-system-error-next-stage`，authoring base `4a2f51aef8ecea2ff1eee5278d6cc93b36131560`。<br>
> **启动门：** MDE、检测覆盖率非劣界值、同信息 Huber 前缀实现审计、输出/估计器实现审计、资源 pilot 和用户 CUDA 授权均未闭合。`CUDA_AUTHORIZED=false`，`AI_TRAINING=false`。

## 1. 目的与研究位置

V3.3 对 V3.2 Screening 的离线复算显示：在当前仿真与任务评价口径下，target-present 的 Fixed D3、Huber 和 M1 检测命中接近 Known；在四方法全周期位置共同成功机会中，M1 相对 Fixed D3 的位置 RMSE 低 `13.979 ± 4.133 m`（12/12 个筛选 seed 同向），相对 Huber D3 低 `6.524 ± 0.771 m`（12/12 个筛选 seed 同向），但仍高于 PK Known `18.387 ± 4.503 m`。这些是每 seed 汇总 12 个混合速度/SNR case 的筛选描述，不能作确认结论或用作 MDE。Huber/M1 使用了整段十周期、1,300 pulses 的 batch 输入，因此不能由该结果宣称在线因果可用或确认性收益。

V4 只检验一个有界问题：**在 `+4 ns` 通道时延、target-present 且非零时刻前缀信息受限时，周期因果的确定性 M1 是否相对冻结 Fixed D3 改善 GMTI 定位误差；该改善是否在检测覆盖率、目标保持和航迹质量不退化的条件下成立，并距离 Known-error correction upper bound 多远。** A0、C0 与 PK 用于闭合误差/校正对照。批量 M1 仅作非部署诊断。

这属于研究总纲中的物理模型驱动未知状态估计、通道校正与任务评价，不改变长期研究主题，也不建立 AI 必要性结论。

## 2. 因子、样本与配对

### 2.1 Target-present 确认矩阵

| 因子 | 冻结提案 |
|---|---|
| 独立 seed | 1933001–1933030，共30个；沿用 V3.2 预留确认集，执行前重新做全仓库/归档碰撞审计 |
| 径向速度 | 0.5、2.5、6.7 m/s |
| 目标 SNR | 20 dB |
| 通道时延 | 0 ns、+4 ns |
| 每 case 目标数 | 20 |
| 处理周期 | 10 |
| Production beam | 50（单 beam 范围，不外推完整扫描系统） |
| Case 数 | 30 × 3 × 2 = 180 |
| 地杂波 | 沿用 V3.2 物理面积与功率配置；每条件实测注入 packet/sample、功率、相关性与配对背景身份 |

每个 seed×速度的 0/+4 ns 条件必须使用相同目标轨迹、目标幅度、杂波/噪声随机背景及场景几何；只有预注册通道时延干预不同。保存背景复用映射并验证输入 SHA。area enabled 标志不构成有效注入证据；packet 与 sample 计数必须大于零，功率/相关性必须通过冻结的 Stage2 质量门。

Target-present 至少保存 ON（目标+杂波+噪声）与 TO（目标专属分量）输入/运行身份。若生产接口不能从相同目标状态生成并配对 TO，应在执行前修订并审查复数响应取样方法；不得把 CSI ROI 代替 target-only complex response。

### 2.2 独立 H0 矩阵

| 因子 | 冻结提案 |
|---|---|
| 新 H0 seed | 候选 1936001–1936030，共30个；不得重用已查看过的旧 H0 seed |
| 时延 | 0 ns、+4 ns，同 seed 配对背景 |
| 目标数 | 0 |
| 周期数 / beam | 10 / 50 |
| Case 数 | 30 × 2 = 60 |

候选 H0 区间目前在文档、运行路径和 ZIP 文件名的初筛中未发现碰撞；正式冻结前必须核对所有配置、manifest、归档成员与历史 seed 注册表。若发现碰撞，停止冻结并另选未使用区间。V3.2 已执行且分析过的 1935001–1935030 只作历史描述，不进入 V4 确认性 H0 分母。

H0 的 0 ns branch 为 A0 no-error；+4 ns branch 包括 C0、Fixed D3、Huber D3 Causal Prefix、M1 Causal Prefix、M1 Batch-10 diagnostic 与 PK Known。H0 下 M1 和 Huber 均只能读取该 seed 的当前/过去 target-free C1/C2 输入；batch 只作诊断，Known 真值仍仅用于上限校正。H0 无 TO branch。每一 branch 对应关系和适用状态须在 runner 生成的预注册 case manifest 中展开，不能事后挑选方法或 seed。

总计为 **240 个唯一 case identity**（180 target-present + 60 H0）；这些身份按 seed 配对，不是240个统计独立样本，也不是单条 Production branch 数。每个 target-present case 的方法、ON/TO 角色和每个 H0 case 的方法分支均在正式实现清单中展开，并通过静态 case-count 核对。

## 3. 处理条件与信息边界

| 条件 | 用途与可读取信息 | 比较限制 |
|---|---|---|
| A0 no-error | 0 ns 目标/杂波配对参考 | 不施加非零校正 |
| C0 Current | +4 ns 输入，不校正 | 当前失配基线 |
| Fixed D3 | +4 ns；固定校正 `3.4099650318669545 ns` | R9 静态标定值，来自 R3 seed101 的 target-free `A1_OFF` C+N；来源为 `docs/research/phase_r/R9_static_calibration_baseline.md`，原 IQ SHA-256 为 `d8bd8673c30605f3817e25e54e5b5659f2d7617f9bba1f27b769449a7e458bd6`。原 IQ 已清理、该值不能从原始 IQ 独立重算；V4 只沿用记录的固定值，不重拟合、不按 V4 seed/检测结果调参；它不是本 case 盲估计或 `+4 ns` 真值输入。 |
| Huber D3 Causal Prefix | +4 ns；将审计确认的几何校正、幅度加权 Huber phase-slope D3 应用于每周期当前及历史 ON raw C1/C2 前缀 | 与 M1 使用相同观测、周期前缀、几何和校正算子；不读 OFF/TO、真值或未来周期。现有代码只有 full-case batch 版本；V4 前缀 runner 尚未实现。 |
| M1 Causal Prefix | +4 ns；仅使用当前及历史周期的 target-present ON 原始 C1/C2 | V4 主要待确认条件；禁止读取 OFF、TO、真值或未来周期 |
| M1 Batch-10 diagnostic | +4 ns；使用同一 ON case 的完整十周期 | 使用未来周期的信息；只能标记为离线诊断，排除在线主排名和部署收益主张 |
| PK Known correction | +4 ns；真值仅用于 evaluator-only 物理校正 | 已知误差校正上限，不是可部署盲估计器 |

所有 Production 对照共用相同输入、源码身份、Native CUDA backend、运行 XML 主参数、range/CFAR/关联/航迹评价门与输出解释。只允许指定的物理校正输入变化。配置使用 `USE_COREX=OFF` / `use_corex=false`；不得以 CPU 仿真或历史 ZIP 替代 CUDA 证据。

### 3.1 现有实现源码审计

只读审计基线为 `1063d67c96af7dbbe7fe871b3a56be0ca453b9b2`；本次仅检查源码，没有执行估计器、Production 或 CUDA。

- `scripts/phase_r/run_r22_cuda_pilot.py:_raw_role_periods` 为每个角色取完整 `period_count` 个周期；`run_case` 随后将全部 A1/ON 路径一次性传给 `r12._blind_m1_from_b_on`。该调用没有当前周期 `k` 或截止周期参数。
- `scripts/phase_r/run_r12_physics_matched_delay.py:_blind_m1_from_b_on` 逐个读取传入路径、把 C1/C2 pulse 矩阵拼接后只估计一次；几何相位也覆盖全部传入 packet。当前函数没有逐周期结果或前缀输入身份校验。
- 现有同输入 Huber 候选来自 `scripts/phase_r/physics_matched_delay.py:_slope_baselines`：对 `observed_cross / geometry_cross` 的展开相位做幅度加权斜率拟合。它复用 M1 选出的同一频率 mask（有限非零的 observed/geometry cross、非零频率，且 observed-cross 幅度不低于有效频点第80百分位），因此这是**共享 M1 支持选择与几何的估计规则对照**，不是独立的端到端预处理/选频基线。`scripts/estimate_channel_delay.py:fit_weighted_line` 的 `robust=True` 使用12次迭代、`1.4826 × MAD` 尺度（下限 `1e-6`）及标准化 Huber 截点 `1.345`。R21 qualification 要求至少8个支持频点、频率跨度至少3 MHz、有限 D3 值且 D3/M1 支持数相同；但它不检查 D3 结果是否落在 M1 的 `(-0.49, 0.49)` sample delay bounds 内。V4 必须冻结该共享 mask 是否为目标对照的设计选择，以及 Huber 自己的有效范围和失败/fallback 规则。这一候选不得与固定 R9 D3 校正值混称。
- `scripts/phase_r/run_r22_cuda_pilot.py:_run_extra_delay_method` 接收一个 case 级 `estimate_ns`，并将同一标量用于全部周期、全部 ON/OFF/TO role 的输入改写。现有 R21/R22 pilot 因此均是整 case batch 估计与整 case 标量校正，不是 V4 所定义的逐周期因果处理。

**审计状态：** full-case batch M1 与上述 Huber D3 已有源码实现；V4 的 period-prefix 调度、逐周期估计、当前周期校正和未来周期隔离尚未实现或执行。V3.3 的 batch 结果不能作为 V4 因果实现或性能证据。V4 冻结前必须新增并审查：带明确 `period_id` 的有序前缀 `{0,…,k}`；M1/Huber 对同一前缀输入和共享 mask 的配对身份；冻结 Huber delay-domain/有效性/fallback 规则；每个前缀只将估计校正应用到当前周期的 ON/OFF/TO；失败时当周期显式 C0 零校正并进入全策略分母；逐周期 correction、输入和产物身份。代码必须证明 `k+1…9` 的样本未进入估计。

### 3.2 M1 周期因果定义

对周期 `k=0..9`，M1 与 Huber D3 前缀估计器可读信息严格限定为当前 case 的 ON 原始 C1/C2 pulses，其周期集合为 `{0,…,k}`，以及运行前已知的采样率、频率轴、通道几何和名义电子扫描延迟等部署配置。两个估计器必须使用同一个前缀输入和对应周期几何。它们不得读：

- `k` 之后任一周期的脉冲或估计结果；
- 配对 0 ns case、OFF/TO 输入、其他 seed 的数据；
- 目标位置/速度/SNR 真值、注入的 +4 ns 真值或 evaluator association；
- 已知误差校正结果、真值驱动的参数或批量 M1 估计。

在周期 `k` 的输入完整到达后运行前缀拟合，再将本周期估计应用于周期 `k` 的 Production 处理；记录前缀周期、pulse 数、输入身份、估计值、置信/拟合诊断、所施加校正、估计器耗时、Production 耗时及失败状态。该定义证明的是**周期边界因果性**，不是 pulse-by-pulse 流式因果性，也不自动证明满足实时延迟预算。

如果任一前缀估计无效或失败，该估计器的对应周期显式执行 C0（不校正），状态记为 `ESTIMATOR_UNAVAILABLE_ZERO_CORRECTION`；不得静默沿用上一周期估计、切换 Fixed D3、切换另一估计器、读取 batch 结果或回退 PK。估计覆盖率和该策略的所有任务结果均纳入各自 policy 分母，不得剔除失败周期。若工程实现不能遵守此行为，停止执行并先修订契约。

`HUBER_D3_CAUSAL_PREFIX` 是与 M1 的信息匹配次要方法对照：两者使用相同 ON 前缀、几何、校正接口和 Production 质量门，仅估计规则不同。报告 M1−Huber 的 seed-level 位置 RMSE 差、95% 区间、估计可用率与检测覆盖率；在没有用户另行批准该对比的工程 MDE 前，只作预注册的次要对比，不宣称 M1 对简单估计器具有工程显著优势。若无法按相同信息和周期时序实现 Huber 前缀，则该次要对比为 `NOT_AVAILABLE`，并且不能据 V4 宣称 M1 特有模型优势或 AI 必要性。

M1 Batch-10 diagnostic 每个 case 只生成一次十周期估计；其可使用全部十周期。该支路用于量化 V3.3 batch 与周期因果差异，不得混入 M1 Causal Prefix 的结果或替代其失败周期。

## 4. 主要终点、保护门与统计单位

### 4.1 唯一主要比较

主要比较为 +4 ns、20 dB 下 `M1 Causal Prefix − Fixed D3` 的定位误差。对每个独立 seed：

1. 依冻结 task evaluator 对相同 target×period×beam 真值机会做 one-to-one Detection/Truth 关联；
2. 在两方法均成功且位置有效的共同机会集计算各速度的二维位置 RMSE；
3. 先计算三个速度各自的配对 RMSE 差，再对三速度等权平均，得到一个 seed-level 主统计量 `Δ_position_m`。负值有利于 M1；统计 `n=30 seeds`，不把目标、周期、beam、CUT 或检测数当独立样本。

共同成功 RMSE 是**条件定位精度**，可能受方法检出覆盖率影响；因此必须同时报告两方法各自的 correct-detection / valid-target-period coverage、估计可用率、不可评价原因、漏检和匹配数。不得对 miss 或 estimator failure 填入 0 m。只有覆盖率保护门通过，才允许把共同成功位置 RMSE 的改善解释为完整任务的有利结果。

`M1 Causal Prefix − Huber D3 Causal Prefix` 是预注册的信息匹配次要比较，使用同样的 seed-level 汇总、配对共同机会与检测覆盖率报告。该对比不替代主要比较；若要宣称 M1 比同信息传统估计器有工程意义的增益，必须另行预先批准该对比的 MDE。Fixed D3 的主要对照回答相对历史静态校正的任务收益，Huber 对照回答 M1 相对同信息确定性估计器的增量，二者不得互相替代。

### 4.2 执行冻结前必须补齐的工程阈值

现有 `POSITION_GATE_METERS` 是任务关联门，不是最小工程有意义改善量。不得将它用作 MDE，也不得把 V3.3 的 `13.979 m` 筛选差值事后用作 MDE。

用户/工程需求方须在确认性数据解盲前明确：

1. 最小有意义定位改善 `MDE_position_m > 0`；
2. M1 相对 Fixed D3 可接受的正确检测覆盖率非劣界值 `margin_detection_pp`；
3. 如要求航迹保护门，确认确认后目标匹配覆盖率/false-confirmed track 的可接受界值。

未定义这些阈值前，设计只能保持 `DRAFT_NOT_FROZEN`，不得声称具备确认性功效或作“有意义改善/非劣”结论。阈值须来自工程需求或用户明确决定，而非本实验观察值。

### 4.3 统计预注册

- 独立推断单位为 seed。每个 seed 保留全部三个速度的配对结构；不把 3 个速度拆成独立 seed。
- 主效应报告 30 个 seed-level `Δ_position_m` 的均值、中位数、标准差和 95% seed-cluster bootstrap 区间；bootstrap 以 seed 为簇并保留该 seed 内全部速度/目标/周期，10,000 次重抽样，固定 PCG64 随机种子 `22029`。
- 同时报逐 seed 配对值、各速度效应和每速度的共同机会数。速度分层检验为次要分析；若进行显著性检验，对三个速度的 p 值用 Holm 校正。
- 30 seed 数沿用预留确认设计，不代表已证明对待定 MDE 有足够功效。冻结前需以盲化/历史 seed-level 方差作精度/功效审查；不得用确认集结果反向调样本数。
- **仅供精度规划的筛选切片（非功效分析）：** 从已归档的 V3.3 `parameter_opportunity_audit.csv.gz` 取 `+4 ns`、20 dB、速度 `{0.5,2.5,6.7}`，按每个 seed、每速度的 pairwise-common 位置成功机会计算 RMSE 差，再对三个速度等权平均；own detection coverage 由同一 opportunity 表中的 `matched` 和 truth-opportunity 行数计算。12 个 batch screening seed 的 `M1−Fixed D3` 为 `−11.146 ± 4.797 m`（均值±seed SD，12/12同向）；`M1−Huber D3` 为 `−5.603 ± 0.985 m`（12/12同向）；coverage 差分别为 `+0.083 ± 0.495 pp` 和 `0.000 ± 0.333 pp`。机会表的 own detection 计数与本机 V3.2 case summary 在108个 seed×speed×method行上相符。若历史 seed SD 原样转移到30 seed，普通 t(29) 均值区间半宽约为1.79 m、0.37 m、0.185 pp、0.124 pp；这只是条件性精度量级，不是功效保证。筛选估计器使用全十周期 batch，V4 改为周期前缀；这些数不得作为 MDE、非劣界值、确认性效应或样本量充分性的依据。
- 只有主要 95% 区间与用户批准 MDE 的方向/幅度相符且检测覆盖率非劣门通过，才可判定具有实际定位收益。仅排除零不等于工程意义；仅有方向一致不等于显著。

### 4.4 次要任务指标

对 A0、C0、Fixed D3、Huber D3 Causal Prefix、M1 Causal、M1 Batch-10 diagnostic、PK 报告：

- 逐 seed 检测正确率/漏检数与共同、各自可评价覆盖率；`Known−Current` 可恢复空间、各确定性方法实际恢复量及 recovery ratio，明确符号方向；
- 在 own-success、pairwise-common、全核心方法 common-success 三种机会集分别给出参数估计数；方位、位置、斜距、折叠 Doppler 的共同机会误差；失败数与缺失原因；
- 目标保持：使用 TO signal-only Production branch 在同一 evaluator truth-aligned ROI 比较固定复数输出的幅度与 wrapped phase。逐目标、逐周期保留复数值和索引映射；同一 ROI 规则对所有方法固定，不允许各方法分别选峰。可报告相对 A0/PK 的复传递幅相。若没有可审计且共享的 ROI 提取器，该指标 `NOT_EVALUABLE`；不得用 CSI ROI 代替；
- TrackManager 实际逐帧状态、关联 detection、PIPE payload 与每字段来源；仅使用生产 TrackManager 和仓库规定的同周期 `Confirmed + matched_this_frame` 语义。地速及航迹指标需能回溯到该检测，禁止另写简化 tracker；
- 每个方法/条件的有效 CFAR CUT 分母、unique CUT hit/cluster/detection、阈值与几何状态。配置 Pfa 不写成经验 Pfa。

### 4.5 独立 H0 虚警指标

对 30 个新 H0 seed 分别计算每个 seed/时延/方法的：

- `P_FA_beam50_period`：10 个有效处理周期中，至少出现一个符合冻结 postfilter Detection 定义的周期比例，再按 30 个 seed 报告 seed-clustered 区间；
- false unique CFAR CUT / valid CUT、false cluster 和 false-confirmed TrackManager track 的 seed-level 汇总。

这些是单 beam 50、十周期的 H0 指标，不称完整 61-beam 系统扫描 Pfa。CUT 和周期不能当作独立 seed。target-present 未匹配 Detection 或 false-confirmed 摘要不能替代 H0 虚警率。虚警 Detection/cluster/track 的真值归属规则必须在实现前冻结，缺少真值映射的对象标记 `NOT_EVALUABLE`。

## 5. 必须归档的完整证据

每个 target-present 方法×case×周期至少保留：

1. 完整 Detection CSV（周期0–9），同一冻结 evaluator 的 target association、one-to-one truth lineage、参数有效性；
2. 生产输出的 candidates、clusters、cluster/detection lineage、CFAR geometry、valid CUT 分母与 unique-hit 统计；
3. 原生 TrackManager `track_debug` 的帧、状态、关联 detection，以及实际 PIPE payload 审计和逐字段来源；验证每个 payload 可反查到同周期确认且实际命中的 track/detection；
4. target-only complex response 的复数值、固定 ROI 坐标、信号注入/truth 映射、处理 tap 名称和提取程序身份；
5. Huber D3 与 M1 每周期前缀输入 period/pulse 集合、估计结果、校正值、诊断、耗时、状态与显式 fallback；
6. 实际 runtime XML、各角色 Stage2 报告、输入 SHA、background reuse mapping、源码 commit/dirty 状态、Production/Stage2 binary SHA、Native CUDA 设备/驱动状态、命令/日志/退出码和运行 manifest；
7. 每 case 的压缩归档、成员清单/大小、CRC/SHA 校验、evaluability 与清理 ledger。

原始 CSI 全图和重复中间图不属于默认保留项；只有当它们是上述结论不可替代的输入时才保存。阶段报告需逐项标注 `IMPLEMENTED / EXECUTED / EVALUABLE / PERFORMANCE_VERIFIED`，禁止以退出码0替代算法/任务通过。

## 6. 失败、续跑与磁盘治理

### 6.1 失败状态与续跑

设计拟采用不可变 case ID 和追加式 matrix manifest；case 状态至少为 `PLANNED → RUNNING → ARCHIVED → VERIFIED → EVALUATED`，失败状态记录为 `FAILED` / `NOT_EVALUABLE`，不得覆盖旧 attempt。

- 只允许跳过 SHA/CRC 和 manifest 验证均通过的完整 compact archive；从该 case 的下一个未完成 case 继续。
- 中断时保留当前 case checkpoint 与失败日志；恢复前核对输入、源码、配置和运行身份相同。身份变化必须使用新的 design/output root。
- 新未知共享故障停止矩阵；已确认的单方法不可评价状态可按冻结矩阵继续其他方法，但必须完整报告该方法缺口和覆盖率，不能静默 fallback 或放宽门槛。
- 一项 case 只有所有必需输出齐全并通过结构、身份、lineage、CRC/SHA 验证后才算完成。

### 6.2 空间预算与清理

用户的 **20 GB 是最终项目占用上限**，不限制中间试验峰值。V4 不在本阶段产生大数组。当前项目目录约 5.3 GiB；其中约 1.5 GiB 的 V3.2 compact archives 和 V3.3 汇总证据仍用于本契约与审计，均保留。2026-11-14 清理仅删除 `.scratch/task5_targeted_gpu_zero_retry/manifest.json`（13,597 bytes；状态 `not_started`、0 case），并移除其空目录；其余含真实运行与审计内容的 scratch、R22 归档和 S1e Production binary 均保留。未删除唯一输入或失败证据。

V3.2 archive 只保留部分时期 Detection，且不含逐帧 TrackManager debug/PIPE payload 与目标专属复数响应，不能据此线性估算 V4 完整输出空间。V4 每个 target-present seed×speed 配对含 14 个 ON/TO method branches（A0 两支、+4 ns 六种方法各两支），共 1,260 支；H0 每个 seed 含 7 支（0 ns A0 一支、+4 ns 六种方法各一支），共 210 支；合计 1,470 支 Production branches。它们不等于 240 个场景 case 身份。正式矩阵前必须先获用户授权，运行一个不计入确认集、涵盖全部 branch kinds 的代表性完整输出/尺寸 pilot，记录每角色/方法压缩前后大小、最大 case、临时峰值与处理时间；再冻结安全可用空间门、归档与恢复行为。pilot 未通过不得启动矩阵。

运行期间允许临时占用超过20 GB；每 case 成功归档并完成身份/内容/评价校验后，才可按 cleanup ledger 删除已被验证 archive 完整替代且可重建的临时 raw。未闭合失败现场、不可重建输入和核心复数数据在阶段审查结束前保留。完成 ChatGPT/用户审查后，再清理无复现、审计或高价值结果用途的中间产物，最终项目占用须低于20 GB；保留协议、配置、manifest、cleanup ledger、核心汇总表、图及必要代表性/失败证据。不得用覆盖旧归档或缩小质量门换空间。

## 7. 停止条件与解读边界

出现以下任一情况，停止受影响条件或整个矩阵，不进行性能排名：

- 有效面积地杂波、背景配对、目标真值、输入/源码身份或 A0/C0/PK 物理闭合不通过；
- Native CUDA Production、CFAR geometry/valid CUT、beam/scan quality gate 或 TrackManager lineage 失败；
- M1 读取了禁止信息，或无法证明周期 `k` 未读取 `k+1..9`；
- Huber D3 与 M1 的前缀输入身份/周期范围不一致，或 Huber 前缀被用于替换失败的 M1 结果；
- M1 估计失败被隐藏、采用未预注册校正、或 estimator fallback 未进入全策略分母；
- Detection/Truth association、target-only complex ROI、独立 H0 定义或关键输出缺失；
- 归档身份/CRC 不通过、续跑身份改变、磁盘资源不足以安全完成一个原子 case。

单 seed 或单 case 只可作 pilot/实现可行性；V4 的确认性推断必须使用完整、独立 seed 集。若 M1 相对 Fixed D3 无达到预设 MDE 的重复改善，停止将该估计器作为主要创新；若检测/航迹保护门失败，即使共同成功位置误差更低也不判为任务改进。若 M1 仅优于 Fixed D3、但没有相对 Huber D3 Causal Prefix 的可解释增量，不得将改善归因于 M1 物理模型本身。若确定性方法已充分闭合 Known 且任务缺口不重要，停止该创新论证。任何结论都不自动证明 AI 必要；AI 仍须另行满足研究总纲的独立入口门。

## 8. 冻结与启动前置门

从草案转为冻结设计、再启动实验前，必须按顺序完成：

1. ChatGPT 独立审查本文件及机器设计，确认总体科学价值和阶段方法学；
2. 用户/工程需求方批准 `MDE_position_m`、`margin_detection_pp` 及必要的航迹保护界值；
3. 完成目标 ROI、真值关联、H0 false object 与 per-period output schema 的源码审计；
4. 实现周期前缀 M1 与同信息几何校正 Huber D3 基线、逐周期 correction runner，并完成无 GPU 的未来周期隔离/边界检查；此项不得替代 CUDA 证据；
5. 对全部 target/H0 seed 和输入身份做最终碰撞审计；
6. 用户单独明确授权完整输出尺寸 pilot / CUDA Production；确认 pilot 结果与磁盘恢复机制后，再请求或批准完整矩阵。

本文件没有修改研究总纲、算法源码、实验参数或 V3.2/V3.3 历史结果。当前裁决为：**契约草案可供独立审查；冻结与 CUDA 执行均未授权。**
