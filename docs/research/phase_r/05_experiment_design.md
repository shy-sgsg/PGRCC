# Phase R3：最小四条件 CUDA Production 因果试验设计

**状态：设计冻结；seed 101 的 A/B/C/D 与 seed 202、303 的 A/B/C 已执行。** 本文保留预注册设计；执行记录和结果见 [06 最小 CUDA 因果 pilot](06_minimal_cuda_causal_pilot.md)，方向判断见 [07 初步方向评估](07_preliminary_direction_assessment.md)。

## 研究问题与边界

检验在有效连续面积地杂波下，通道 2 的 `+4 ns` 时延是否使当前 Production GMTI 链性能退化；评估仅使用注入真值的物理校正上限，以及仅从目标关闭数据估计的传统确定性盲校正。

本阶段不训练 AI、不开发 Router、不扩展六参数联合估计、不重跑传统 Baseline 矩阵。唯一误差为通道 2 `+4 ns`；`fs=60 MHz`，对应 `+0.24` fast-time sample。通道误差注入和校正符号、输入来源均写入 manifest。

## 冻结的四条件

| 条件 | 输入 | 校正 | 处理链 |
| --- | --- | --- | --- |
| A — Production Current / 0 ns | seed 配对的无误差输入 | 无 | 四通道协议 IQ → `(1,3)/(2,4)` 融合为 F1/F2 → Production Current |
| B — Production Current / +4 ns | 相同场景、相同噪声/杂波 seed；通道 2 注入 +4 ns | 无 | 与 A 完全相同 |
| C — Known Physical Correction | B 的同一原始输入 | 使用 +4 ns 真值；仅用于 evaluator 物理校正，不进入任何估计器 | 与 A/B 完全相同 |
| D — Blind Deterministic Correction | B 的同一原始输入 | 仅从该 seed 的 `A1_OFF`（目标关闭、C+N）F1/F2 估计并应用 | 与 A/B/C 完全相同 |

D 预注册使用现有传统估计套件优先级中的 `D3_Huber_weighted_LS`。估计器输入只允许 A1_OFF 的融合 F1/F2；禁止 A1_ON、A0 参考数据、目标 truth、延迟真值及任何 evaluator 结果进入估计。只有 D3 结果为有限数值、状态有效且支持样本数不少于 8，D 才标记为 `EVALUABLE` 并运行校正；否则标记 `NOT_AVAILABLE`，不得回退到真值、Current 或其他 oracle。D 仅在 seed 101 执行；seed 202、303 的独立扩展只执行 A/B/C，不把单 seed 的盲校正结果包装成重复验证。

每个条件均保留 H0/OFF（C+N）和 H1/ON（S+C+N）配对输入；另保留 TO（S-only）作为目标保持/自对消旁路，不作为额外处理条件。配对背景由 A 的零误差 A0_ON 生成，其他 Stage2 角色复用同一背景身份。生产 Current 是唯一生产处理链，不开 Mode-A/B 校正分支。

## 固定场景、几何与运行

- seed：101；目标：beam 50、斜距 85,000 m、径向速度 6.7 m/s、SNR 30 dB；每条件 3 个 CPI/period。
- 平台高度 6,000 m、地面高程 0 m。地面散射的斜距不得小于垂直高度差；采用模板中目标附近的斜距窗 `[84,500, 85,500) m`，而不是低于平台高度的 `[2,500, 5,000) m`。在运行报告和配置中同时核验，不以 `area_enabled` 代替注入证据。
- 杂波 gate：Stage2 A0_ON 报告必须满足 `continuous_area_packets > 0`、`continuous_area_samples > 0`、`area_mean_power_measured > 0` 且有限；场景几何须有正的可达地面距离。其余角色必须证明复用相同背景目录/身份。记录 H0/H1、F1/F2 及 A/B/C/D 的输入功率和差异，作为可比性诊断；通道时延带来的实际功率变化不被人为归一化。
- 首个 CUDA Production smoke 固定为 A_ON，必须保存实际 runtime XML、日志、逐 beam stage、CSI tap、CFAR geometry 和源码/可执行身份。质量门槛使用当前配置，不得为通过烟测而下调。
- 若 A_ON smoke、Stage2 注入、输入闭合或 CUDA/CFAR 有效 CUT gate 失败，停止后续算法比较并报告失败现场。

## 预注册评价与阶段 gate

1. **输入真实性与配对**：Stage2 注入计数/样本数/功率；背景身份；四通道输入布局；A/B/C/D 的 H0/H1 输入 SHA-256、F1/F2 功率和目标/杂波可比性。
2. **物理闭合**：逐 sample 对比 A 与 C 的 ON/OFF/TO 融合 F1/F2；分别记录平均功率、归一化复 RMS、峰值误差和圆周相位 RMS。`F1/F2 relative complex RMS ≤ 1e-3` 才视为 float32 数值闭合；若不闭合，先审计 Stage2 时延实现与校正算子，不得把 C 直接当作已闭合的 Known-error correction 上限或继续做算法性能结论。
3. **Production 正确性**：A_ON 必须通过真实 CUDA production 路径；逐 beam stage 无失败；CSI 输出/指标完整；CFAR geometry 有非零有效 CUT；追踪输出能与同周期检测对应；Mode-B ON/OFF 源码契约及现有针对性测试不得显示 ON/OFF 算子不一致。此处本试验不执行 Mode-B 算法矩阵。
4. **检测/保持指标**：比较 CSI 支持区杂波残余功率、目标复幅度/相位保持（用配对 ON/OFF 与 TO 诊断 tap）、Output SCNR、检测和参数估计。虚警仅在 H0 存在有效 CUT 分母时报告 `false hits / valid H0 CUTs`；不得把 `hit/valid CUT` 写成 Pfa。径向速度、方位误差按 simulator truth 的坐标/符号和单位核对。
5. **统计口径**：seed101 只报告可行性与正确性，不声称统计显著的 Pd/Pfa 改善。A/B/C 的生产、杂波、闭合和评价 gate 通过后，已增加独立 seed 202、303 的 A/B/C；三个 seed 的方向一致性只作为描述性重复证据，不是显著性检验。D 的单 seed 结果仍为传统盲校正的探索性对照。三个 seed 的具体结果见 06。

比较定义：

- `unknown-error loss = B − A`（每项注明好坏方向）；
- `known-correction recovery = A − B` 与 `C − B` 按指标方向归一化；
- `recovery ratio = (C − B)/(A − B)` 仅在分母有效且方向定义合理时计算；
- `blind gap = D − C`，仅 D 有效时计算。

## 状态标签

- `IMPLEMENTED`：当前源码/配置具备该功能；不代表运行过。
- `EXECUTED`：有本阶段实际命令、日志、退出码和输入身份。
- `EVALUABLE`：数据/处理/分母/真值配对满足该指标定义。
- `PERFORMANCE_VERIFIED`：A/B/C 的 gate 通过，并且至少三个独立 seed 在目标角度、跟踪地速、位置 RMSE 上均呈现 B 比 A 差、C 比 B 好的同向结果。该标签仅表示这些任务指标的方向重复，不代表 Pd/Pfa、整体系统、统计显著性或径向速度已验证。seed101 单次结果不得单独标成该状态。

每条结论分别标注上述状态；失败、缺失分母和不可用估计器保留为明确失败/`NOT_AVAILABLE`，不得填补或静默跳过。

## 预期交付

- `05_experiment_design.md`（本设计）
- `06_minimal_cuda_causal_pilot.md`（实际执行、输入/输出、数值、失败与限制）
- `07_preliminary_direction_assessment.md`（按 gate 回答研究方向，不自动启动下一阶段）
- 完整 manifest、逐条件运行配置/XML、命令日志、源码和二进制身份、Stage2 地杂波证据、CSI/CFAR/beam-stage 证据、指标表、图表及失败案例。

---

## Phase R4：E1/E4/E5 最小 Production 预注册（2026-10-22）

本节在任何新 CUDA 运行前冻结。R3 的四条件时延实验是 E2 的当前证据，不重复运行；2026-09 的 128 场景 E3 属于 R3 CFAR 修复前历史结果，只用于背景审查，不作为当前生产性能结论。E1、E4、E5 使用同一组配对观测和生产分支，避免为每项评价重复生成数据。

本预注册已执行 seed 101 的 8 个冻结 case。实际执行、证据可评估边界和停止原因追加记录于[06 执行报告](06_minimal_cuda_causal_pilot.md)及[紧凑证据目录](evidence/r4_e1_e4_e5_seed101_20261022/)。由于 valid-CUT 汇总、残余闭合和低速 P5 背景 ROI gate 未全部通过，未扩展 seed 202/303；冻结阈值未降低。

### 决策假设与最小条件

1. **E1 低速边界**：在无通道误差、有效连续面积地杂波条件下，目标相对杂波的径向速度和目标输入 SNR 共同影响 Production Current 的检测、目标保护与参数估计；普通减法、SCC 或 Robust DDC 可能改变该边界。
2. **E4 杂波抑制与目标保护**：更低 CSI 杂波残余不必然代表更高目标输出 SCNR 或更好的检测；必须从同一处理参数下的 `OFF=C+N`、`ON=S+C+N`、`TO=S` 配对结果同时评价。
3. **E5 参数边界**：只在生产检测正确关联到 truth 的记录上评价径向速度、方位角和位置误差；另行报告全部可见目标机会中的检出数，不用条件误差替代检出率。

| 设计项 | 冻结值 |
| --- | --- |
| 误差 | `0 ns`；只运行无误差基线，不把 R3 的 `+4 ns` 数据混入 E1/E4/E5 |
| 场景 | seed 101 pilot；beam 50、85 km、expected bin 2048、3 个 period；平台高度 6,000 m、地面高程 0 m |
| 地杂波 | `continuous_texture`，`texture_sigma=0.25`、`rho=0.98`、配置均值功率 0.02，噪声功率 0.001；斜距窗由 85 km 场景位置生成，Stage2 报告须证明实际注入 |
| 目标速度水平 | 配置地速标量 `{0.5, 1, 2, 4} m/s`；场景速度向量沿模板固定 ENU 方向。统计横轴和 truth 一律使用 Stage2 每期 `target_vr_self_mps`，不得把输入地速标量直接标成径向速度 |
| 目标输入 SNR | `{10, 30} dB`，与四个速度全配对，共 8 个显式 case；Stage2 将此值相对目标位置处的 target-free local-background RMS 标定到脉压域，报告同时保留 truth 中的 SNR 和实测背景功率 |
| 方法 | `C0 Production Current`、`C1 Ordinary subtraction`、`C2 SCC`、`C3 Robust DDC`；不执行 C4、物理 delay estimator、Known-error 或 Router 方法 |
| 校准模式 | C1/C2/C3 只用 Mode-A：reference 仅从 OFF=C+N 估计并固定应用到 ON 与 TO；C0 不启用校准。Mode-B 不进入本次最小 pilot，避免把目标参与估计与目标传递实验混为一谈 |
| 配对角色 | OFF、ON、TO 使用相同 seed、背景身份、目标身份、几何和下游处理配置。三者须分别保留；同一分支固定的 reference 必须可审计 |
| 运行规模 | correctness pilot 为 seed 101 的 8 case、每 case 3 periods。全部 gate 通过后，以完全相同的 8 case 配置运行 seed 202、303；不再扩大速度/SNR 网格 |

目标信号按模板现有固定 ENU 朝向生成。由于该朝向与实际视线并非严格同向，逐期 truth 的 `target_vr_self_mps` 才是 E1 横轴。配置的 `target_snr_db` 是 Stage2 的脉压域标定量；报告中不把它误称为独立测得的 SCNR。E4 的输出 SCNR 与目标保持以生产 CSI tap 和严格配对的 ON−OFF 结果计算。

### 控制、观测和估计边界

- 每个 case 的 A0 零误差 ON 负责生成一份背景；所有 OFF/ON/TO 复用该背景身份。`area_clutter_gate` 在第一份 A0_ON Stage2 报告后立即要求物理可达几何、`continuous_area_packets > 0`、`continuous_area_samples > 0`、有限正的 `area_mean_power_measured`。任何一项失败即停止该 case，不运行余下 Stage2，也不调用 Production。
- 完成 Stage2 后，保留每期通道 IQ、场景 runtime XML、背景身份、F1/F2 输入功率与因果 triplet 审计。非零输入或输出差异必须记录，不做事后功率归一化；A0 与零误差 Current 的闭合失败时，不比较算法收益。
- C1/C2/C3 的估计只使用其 Mode-A 生产接口；评估侧读取 truth 仅用于关联、定位 ROI、计算指标，不反馈估计器或 Production。方法/Modes 子集只减少运行数量，不得跳过任何被选择方法的 OFF、ON、TO 角色。
- 目标参数采用生产 `detection_results_GMTI*.csv` 中径向速度/方位/位置字段和生产 TrackManager 输出；以固定位置/距离门控确认 truth association。对 unmatched/漏检目标只计入 detection opportunity，不填入估计误差。

### 主指标和统计单位

- **E1**：每个速度/SNR case 的目标机会数、正确关联检出数、`Pd=正确检出的可见目标机会/全部可见目标机会`；按实际径向速度展示 Pd、output SCNR、target gain。只有生产 CSI tap 中正式 clutter ROI 的输入/残余功率可称 CSI clutter residual；不把它代称目标输出 SCNR。
- **E4**：由生产 CSI tap / `scripts/run_p5_csi_eval.py` 计算配对 `ON−OFF` 的输入/输出目标功率、`SCNR_before_dB`、`SCNR_after_dB`、`target_gain_dB` 和 clutter attenuation；再与 OFF 有效 CUT 命中、ON 检出、TO 目标保持及下游 TrackManager 结果并列判读。若 P5 target ROI/配对轴/背景单元不满足 evaluator gate，该项记 `NOT_EVALUABLE`，不替换为 CSI 残余功率。
- **E5**：全体可见目标的检出机会/命中数单独报告；径向速度、方位角、位置误差只在正确关联的目标检测上报告，另列 unmatched、错配、无有效估计的数量。TrackManager ground-speed RMSE 是补充指标，不冒充径向速度 RMSE。
- **虚警**：从同方法 H0/OFF 的生产 CFAR geometry 读取 false hit 数及真实 valid CUT 分母。报告观测比率、分子/分母、按 seed-period 分块的区间和相关性限制；若有效 CUT 或 false-hit 身份不完整则 `NOT_EVALUABLE`。名义 `Pfa=1e-6` 只作为 configured value；本 pilot 不因 cell 总数大就声称已统计证明 1e-6。
- pilot seed 101 只判断路径/配对/指标是否可评估，不作性能增益结论。若其所有 correctness gates 通过，seed 202、303 按同一冻结配置执行。最终统计单位是独立 seed；同 seed 内的 period/CUT/像素不作为独立重复样本。对 seed 级配对差报告原始值、均值/中位数及 95% 配对 t 区间（df=2），并给出逐 seed 方向；三个 seed 的区间只作小样本描述，不称为稳健总体保证。Pfa 的 CUT-level Wilson 区间只作二项近似描述，主要限制由 seed/period 分块结果揭示。

### Gate、停止与解释

1. **数据 gate**：Stage2 area injection、可达几何、背景复用、四通道协议输入和 F1/F2 非零有效功率均可追溯；否则该 case 停止，禁止报告杂波收益。
2. **处理 gate**：所有选中方法/角色的真实 CUDA Production 分支、runtime XML、CSI tap、CFAR geometry、检测和 track 记录可审计；Mode-A reference 一致，ON/OFF/TO operator 对照可比；未降低任何质量阈值。失败/缺文件/不完整输出保留为失败，相关指标 `NOT_EVALUABLE`。
3. **关联 gate**：逐期 truth、检测记录和正确关联指标可闭合；全体目标检测率与条件参数误差必须分别完整输出。误差字段缺失不通过其他字段推算补齐。
4. **扩展 gate**：seed 101 只有在全部 8 case 数据、Production、CFAR valid CUT、目标关联和 P5 指标 gate 可评估后，才扩展相同 8 case 到 seed 202/303。否则定位根因并停止，不降低门槛、不加新参数。
5. **方向 gate**：若零失配下低速检出/目标保持已退化而 C1–C3 不能改善，优先讨论低速目标保护/观测信息边界；若目标保护与检测可接受而杂波/估计指标对方法或速度有稳定差异，再把结果与 R3 E2 合并判断。E3 当前没有 R3 修复后的合格正式数据；只有 E1/E4 明确显示结论取决于杂波统计假设时，下一步才注册最小 H0/H1 杂波单因素验证，不复跑 128 场景矩阵。

### E2 与 E3 证据边界

- **E2 复用**：[R3 CUDA pilot](06_minimal_cuda_causal_pilot.md) 的 +4 ns / Known Physical Correction 对比已覆盖 seed 101、202、303 的 A/B/C；D 仅 seed 101。其角度、跟踪地速和位置 RMSE 方向在三个 seed 重复，属于任务指标方向的描述性证据；径向速度有 ambiguity 限制，Pd/Pfa 显著性未验证。本阶段不重跑 E2。
- **E3 历史**：128-scene `Physics-Adaptive Selective Calibration V2` 来自源码 `669df38…`，脚本当时要求真实可见 CUDA；产物有 seed/LHS holdout 和 scene-block 汇总。但其 production CFAR 的 observed cell hit fraction 中位数约 `8.4e-3`，远高于配置 `1e-6`，且 J5/J6 的 target-preservation no-regression gate 均失败；数据也早于本次 R3 CFAR 修复。因此它是已执行的历史 CUDA 方法研究、但不是当前 R3 生产口径可评估的 E3 性能证据。保留结果，不重标或覆盖。

执行状态在后续报告分开记录 `IMPLEMENTED`、`EXECUTED`、`EVALUABLE`、`PERFORMANCE_VERIFIED`；单纯脚本/测试通过不代表相应实验通过。
