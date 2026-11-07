# R18：传统 GMTI 方法与能力审查

状态：`IMPLEMENTED`（文献与当前代码审计）；Native CUDA 执行状态见 R18 运行报告。

## 审查边界

本审查只为 R18 的 C0–C3 Production 对照建立传统方法背景与信息边界，不以“方法名”代替实现检查，也不把离线处理、不同自由度或不同雷达体制的结果当作本项目 Production 排名。本阶段不新增处理算法。

## 一手研究证据

| 文献 | 真实系统与数据 | 方法/输入信息 | 结果可支持的判断 | 与 PGRCC 的限制 |
|---|---|---|---|---|
| Banahan et al., “GMTI clutter cancellation using real non-ideal data,” IET Radar, Sonar & Navigation, 2010, [DOI: 10.1049/iet-rsn.2009.0057](https://doi.org/10.1049/iet-rsn.2009.0057)，[ANU 原文库](https://openresearch-repository.anu.edu.au/items/ab9f6fe7-c5ca-4632-ad2b-dc98356e3a62) | 实测 airborne radar 数据；采集时并非专为 GMTI；少量空间通道 | DPCA、ADPCA、JDL-STAP，并比较跨通道脉冲配准预处理 | 预处理的脉冲移位与相位斜坡会改变 DPCA 表现；ADPCA 对跨距离单元信号泄漏更稳健；算法能否压制静态峰取决于实际非理想条件 | 实际数据与 PGRCC 的扫描、波形、通道融合和处理链不同；只支持“预处理与泄漏必须审查”，不直接迁移量化收益 |
| Brennan & Reed, “Theory of Adaptive Radar,” IEEE Transactions on Aerospace and Electronic Systems, 1973, [DOI: 10.1109/TAES.1973.309792](https://doi.org/10.1109/TAES.1973.309792)；Reed, Mallett & Brennan, “Rapid Convergence Rate in Adaptive Arrays,” 1974, [DOI: 10.1109/TAES.1974.307893](https://doi.org/10.1109/TAES.1974.307893) | 经典自适应雷达/阵列理论与样本矩阵逆（SMI）分析 | 以训练样本估计干扰/杂波统计并形成自适应权值 | 训练样本数、样本代表性与协方差估计误差是经典自适应处理的性能条件；有限样本影响必须与“算法本身无效”区分 | 这些理论结果不替代本项目 Production 测试，也未在 R18 中实现 STAP |
| Barros Cardoso da Silva et al., “In-Flight Multichannel Calibration for Along-Track Interferometric Airborne Radar,” IEEE TGRS, 2020, [DOI: 10.1109/TGRS.2020.3011287](https://doi.org/10.1109/TGRS.2020.3011287)，[DLR 原文库](https://elib.dlr.de/135728/) | DLR F-SAR 实测 airborne X-band 多通道数据 | 从距离压缩训练数据估计接收通道幅相偏差，并补偿随距离和方位时间变化的 Doppler centroid；与 digital channel balancing 比较 | 确定性多通道物理校准在实测 airborne ATI/PD-STAP 系统中具有现实基础；校准也关系到后续杂波协方差估计 | F-SAR ATI 与 PGRCC 的扫描双融合通道 CSI 不同；不能据此断言 PGRCC 的固定时延或某种残余复权需要新算法 |
| “Airborne GMTI experiment based on multi-channel SAR using STAP,” Aerospace Science and Technology, 2012, [DOI: 10.1016/j.ast.2011.07.007](https://doi.org/10.1016/j.ast.2011.07.007) | 三孔径 airborne SAR 实测数据 | 阵列流形自适应校准、Wiener 意义上的杂波抑制、单快拍 DOA 参数估计；用功率与相位条件降低弱杂波/强目标对协方差估计的污染 | 实测系统中的通道失配、几何误差、杂波异质性和目标污染会破坏理想 STAP 假设；训练数据选择是明确机制 | 三孔径 SAR/STAP 的空间自由度、成像前后域和算法链与 PGRCC CSI 不同，只作为机制证据 |
| Kang, “Robust Covariance Matrix Estimation for Radar Space-Time Adaptive Processing (STAP),” 2016, [arXiv:1602.05069](https://arxiv.org/abs/1602.05069) | airborne STAP 的理论/仿真研究与受限训练样本问题 | 结构、低秩与约束协方差估计 | 训练不足、杂波非平稳、目标污染和物理约束误差都是成熟传统方法已研究的问题 | 本项目尚未建立四通道 STAP 的公平 Production 基线；不可把该工作视作 R18 的性能证据 |
| “Passive Radar DPCA Schemes With Adaptive Channel Calibration,” IEEE TAES, 2020, [DOI: 10.1109/TAES.2020.2987478](https://doi.org/10.1109/TAES.2020.2987478) | 移动平台 DVB-T 被动雷达实测与合成目标 | 对参考信号/杂波方向失配、幅相误差和双通道 DPCA 校准进行比较 | 校准参考的角度/距离代表性会影响可迁移性；“有参考数据”不自动等于“同质参考数据” | 被动雷达直达波与主动 airborne GMTI 的观测和传播模型不同，只用于独立参考的公平性提醒 |

## 仓库方法能力矩阵

统一输入为四通道协议 IQ，经 `(1,3)`、`(2,4)` 融合为 F1/F2，再进入生产两通道 CSI。R18 冻结的信号与任务结果将以实际 runtime XML、F1/F2 tap 和 Production 二进制为准。

| 方法 | 当前入口与估计自由度 | 估计数据/外部信息 | 输出及 R18 角色 | 已有证据与边界 |
|---|---|---|---|---|
| C0 Current | Production 常规 CSI 路径；不估计研究复权 | 当前 F1/F2，不读额外 OFF | 生产锚点 | R13 的 A0/C0 闭合；R18 重新以同一个 Native CUDA 二进制核验 |
| C1 ordinary subtraction | `src/production_calibration_adapter.cpp` 的 `ordinary_subtraction`；固定 `Gamma=1` | 不拟合、不读参考 | 直接输出 `F2−F1` | 实现及 C++ selftest 已存在；R13 未执行，R16 曾在单场景暴露目标保持风险；本轮纳入任务级比较 |
| C2 SCC | `scc`；每个 period、CSI 有效支持域一个复 Gamma，`Σ(F2·conj(F1))/Σ|F1|²` | Mode-B：当前 ON 的可观测 F1/F2；Mode-A：配对目标自由 OFF；独立参考子试验：单独 seed 的 target-free C+N | Gamma 固定应用到 F1/F2 残差 | adapter 和 fixed-reference loader 已有实现；Mode-B 与 Mode-A 信息类别必须分表 |
| C3 Robust DDC | `robust_ddc`；每个 Doppler 行分别估计复 Gamma，并按配置相位门做稳健排除 | 与 C2 相同的三种预注册数据条件 | 逐行复权后形成残差 | R13 只覆盖 paired-OFF Mode-A；R18 首次用同输入 Mode-B 多条件任务矩阵验证 |
| C4 Robust DDC-RB | `robust_ddc_rb`；逐 Doppler 行、range-band 的局部稳健 Gamma | 依赖局部支持域；无单独标定数据并不代表信息边界与 Mode-B 相同 | 局部复权残差 | 生产 adapter 和边界 selftest 存在；R18 核心矩阵未预注册 C4，故不临时扩成第五个竞争方法 |
| 离线等效 CSI / LS / Wiener / DPCA / JDL / MNEC | `scripts/run_baseline_benchmark.py`、`scripts/run_baseline_v2.py` 等 Python/NumPy 路径；不同方法分别为两通道等效复权、4 通道空间向量或 4×3 Doppler 邻域特征 | 部分使用配对 C+N；部分保留目标观测 P38；训练样本与自由度依方法而异 | 离线残余图/局部检测或协方差权值 | 不在本轮 Native CUDA Production 链，且四通道方法自由度更高；不作为 C0–C3 排名结果 |

## R18 的强传统基线判断

在当前仓库可直接复现且信息量匹配的 Production 两通道方法中，C0、C1、C2、C3 是可审计的最小比较组：C0 给出现役处理锚点，C1 给出不拟合的固定系数下界，C2 与 C3 在同一数据与自由度层级比较共享/逐 Doppler 行复权。Mode-B 是实际可部署信息条件；paired-OFF Mode-A 是额外参考上界；独立 target-free secondary 是单独标识的信息增益条件。

现有文献表明更高自由度的经典 STAP、DPCA/ADPCA 以及物理通道标定是有价值的传统方法族，但它们的通道数、训练数据和模型与当前 CSI 处理链不同。当前阶段不通过离线代码或不同自由度方法声称已完成“最强 GMTI 算法”比较。R18 的决策范围是判断当前 Production CSI 附近的传统两通道复权是否带来可重复的任务级收益，以及瓶颈是否可由已有物理/统计方法充分解释。

## 来源

- 仓库实现：[`production_calibration_adapter.cpp`](../../../../src/production_calibration_adapter.cpp)、[`gpu_kernels.cu`](../../../../src/gpu/gpu_kernels.cu)、[`run_production_hierarchical_calibration_pilot.py`](../../../../scripts/run_production_hierarchical_calibration_pilot.py)。
- 历史代码与证据边界：[`R13_baseline_capability_matrix.md`](../R13_baseline_capability_matrix.md)、[`R13_minimal_comparison_results.md`](../R13_minimal_comparison_results.md)、[`R16_experiment_design.md`](../R16_experiment_design.md)、[`R16_minimal_c1_results.md`](../R16_minimal_c1_results.md)；R16 的结论只作为历史材料。
- 方法定义与 Production 执行是否相同，以 R18 固定源码 commit 及本阶段执行清单为准。
