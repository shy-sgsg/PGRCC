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
