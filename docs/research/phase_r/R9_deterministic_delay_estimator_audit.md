# R9 确定性时延估计器审查

日期：2026-10-23
实验基线：`22cefb8e947a8a3565a88445fe2c1d3616408c47`
范围：R8 seed202/303 原始输入；Native CUDA R7 Production 回放；不重跑 Stage2、不训练 AI。

## 结论摘要

- D1/D2/D3 已实现，且 D3 曾在 R3 seed101 的 A1_OFF、目标关闭 C+N 数据上执行；不能将其描述为“尚无盲估计器”。
- 本轮冻结规则 `D3 → D2 → D1`（取第一个有限输出），两个 seed 都选中 D3。R9 在线候选只读取同 seed 的 A1_Current/ON 四通道协议 IQ 和采集布局；估计函数不接收真值、A0、OFF、TO 或检测结果。
- 两 seed 的 ON 数据均能产生有限的正时延斜率，但 D3 得到 `3.321230 ns`、`2.987994 ns`，相对注入值 `4 ns` 分别低估 `0.678770 ns`、`1.012006 ns`。这证明当前观测上有可用相位斜率，不证明无偏恢复了物理通道2时延。
- 生产回放验证的是“把该标量送入已知 Stage2 算子逆校正后，对这些 R8 输入的任务效果”。在线候选在本实验中是对完整三周期 ON 数据的批处理估计，随后回放同批数据；尚非实时流式或在线部署。

## 数据流与物理量

Stage2 协议输入为每包 256-byte header，后接 11,820 个 fast-time 样本、4 通道、float32 I/Q；采样率 `60 MHz`，每 seed 三期、共 390 pulse。估计器从四通道协议 IQ 构造生产融合量：

```text
F1 = (C1 + C3) / 2
F2 = (C2 + C4) / 2
C12(f) = Σpulse FFT(F1) · conj(FFT(F2))
```

输入在 range/Doppler 处理之前；保留 packet/pulse 维度，逐 pulse 对 11,820 个 fast-time 样本 FFT，再沿 pulse 累加交叉谱。频率轴是 `numpy.fft.fftfreq(N, d=1/Fs)`，间隔 `Fs/N = 5,076.142132 Hz`，而不是 Doppler 轴或由 range bin 间接换算的轴。D1/D2/D3 只拟合正频率上交叉谱幅度最高的 20% 支持点；本轮每次聚合支持 1,182 点。

对“通道2相对通道1是纯时延”的理想模型，若 `X2(f)=X1(f)e^{-j2πfτ}`，则 `arg(C12)=b+2πfτ+ε`，因此 `τ=slope/(2π)`，单位由 Hz 斜率换算成秒，再乘 `1e9` 得 ns。D1 为等权 LS，D2 按交叉谱幅度加权，D3 在幅度权重上迭代 Huber 重加权。线性拟合包含截距 `b`，可吸收频率无关的常相位；频率相关几何相位若含线性分量，则与时延不可由该模型单独区分。

### 对当前生产误差模型的限制

R8/R9 的失配注入和校正对象是零基物理通道索引 `[1]`，即协议通道2；它只占生产 `F2=(C2+C4)/2` 的一个融合支路。因而 `F1/F2` 的整体交叉谱不严格等价于“一个完整通道对另一个完整通道施加纯时延”。此外，Stage2 使用零扩展线性分数延迟，其频响为 `H(f)=(1-d)+d·exp(-j2πf/Fs)`，相位并非全带宽严格线性。R9 的斜率因此是当前观测支持域下的有效拟合量；它可作回放校正量，但不应直接称为无偏的物理通道2时延测量。

物理逆校正则与模拟算子相匹配：`d=τ·Fs`，`x[0]=0`，`x[n]=(y[n]-d·x[n-1])/(1-d)`。R9 使用该逆算子改写原始协议通道2，再按原生产链融合；没有把理想频域相位坡道误当成 Stage2 算子的精确逆。

## 方法状态和输入隔离

| 状态 | 本阶段判定 | 证据/边界 |
|---|---|---|
| `IMPLEMENTED` | 是 | `scripts/delay_stage1_core.py::estimate_delay_d1_d2_d3`；D1/D2/D3 已存在。 |
| `PREVIOUSLY_EXECUTED` | 是 | R3 seed101 A1_OFF 目标关闭 C+N 输入，D3=`3.409965 ns`；R9 又在 seed202/303 A1_ON 上运行三种方法。 |
| `QUALIFIED_FOR_CURRENT_PRODUCTION` | 限定通过 | 静态 R3 D3 值和本轮 ON 候选分别送入固定 R7 二进制，在这两组 R8 场景完成 12 个 ON/OFF/TO Production 分支；不代表一般化或实时部署资格。 |
| `ONLINE_DEPLOYABLE` | 否 | 估计读取完整三周期数据，再回放同一批数据；未验证流式时延、更新策略、现场失效保护、置信度校准或时变跟踪。 |
| `PERFORMANCE_VERIFIED` | 仅本报告列明的描述性指标 | 两个留出 seed、每 seed 三期；不构成统计显著性或总体 Pd/Pfa 结论。 |

R3 固定标定的观测为 seed101 `A1_OFF` 目标关闭 C+N，融合后 F1/F2；估计函数未用时延真值。其输入 SHA-256 `d8bd8673c30605f3817e25e54e5b5659f2d7617f9bba1f27b769449a7e458bd6` 被历史 manifest 记录，但原始文件已不在当前输出中，故不能重新读取该历史 IQ。R9 将冻结的标量和来源 SHA 作为已执行标定记录使用。

在线候选逐 seed 的估计调用只收到 `A1_Current_unknown_error/ON` raw period 路径及采集布局；选择规则只读取 D1/D2/D3 数值是否有限。A0 零注入估计是单独 evaluator 诊断，不参加选择或校正；其输入真值和所有检测真值仅在估计冻结后用于报告。Known Correction 只作 evaluator 上限。

## 估计器结果与公式检查

| 输入 | D1 (ns) | D2 (ns) | D3 (ns) | D3 phase RMS (rad) | D3 `R²` | 有效支持点 |
|---|---:|---:|---:|---:|---:|---:|
| R3 seed101 A1_OFF，固定标定 | 3.377754 | 3.380632 | **3.409965** | 0.042793 | 0.747817 | 1,182 |
| R9 seed202 A1_ON | 3.327219 | 3.321667 | **3.321230** | 0.043270 | 0.737980 | 1,182 |
| R9 seed303 A1_ON | 3.010953 | 3.002197 | **2.987994** | 0.044621 | 0.673303 | 1,182 |

令误差 `ετ=τ̂−τtrue`，负值代表低估。seed202、303 的 D3 误差为 `−0.678770 ns`、`−1.012006 ns`。D3 每期估计分别为 `[3.838701, 3.113564, 3.188824] ns` 和 `[2.853085, 3.174342, 3.163584] ns`；期内样本标准差为 `0.398712 ns`、`0.182452 ns`。两 seed 聚合估计均值 `3.154612 ns`，跨 seed 样本标准差 `0.235634 ns`。只有两个独立测试 seed；D1/D2/D3 没有校准标准误或置信区间，期内离散度不是置信区间。

零注入 A0_ON evaluator 诊断的 D3 输出为 seed202 `−0.091090 ns`、seed303 `−0.288199 ns`；D1/D2 数值见机器记录。零误差偏置说明场景谱形、融合和拟合选择也会给出非零有效斜率。

为验证符号、单位和常相位截距，额外执行了无 GPU 的合成公式检查：已知纯频域 `+4 ns` 斜坡在三种方法下均估为 `4.000000 ns`；零时延叠加 `0.37 rad` 常相位时均估为约 `0 ns`。对满足 `x[0]=0` 的 Stage2 线性插值样例，逆递推复 RMS 闭合为 `1.25e-16`。该公式检查是在 CUDA Production 回放之后补做的，记录在 `outputs/.../evaluation/estimator_formula_audit.json`；它不改变此前冻结的估计值和选择，也不能声称满足“先公式检查、后 CUDA”的时间顺序。它还显示同一 `+4 ns` Stage2 插值在均匀合成频谱上的线性相位拟合约 `3.906 ns`，直接说明纯斜坡模型存在算子模型误差。

真实 ON 输入的支持域诊断：seed202 支持频率约 `0.0406–14.2487 MHz`，最大相邻支持 bin 间隔 325，最大展开相位步进 `0.1468 rad`；seed303 约 `0.0711–13.3807 MHz`，最大间隔 134，最大相位步进 `0.1382 rad`。本轮数据没有观察到接近 π 的局部跳变，但稀疏支持和未校准置信度仍是方法边界。

## CUDA Production 证据

- 运行平台：本机 Ubuntu、NVIDIA GeForce RTX 3050 Laptop GPU、驱动 `580.178.04`、CUDA 13.0；`USE_COREX=OFF`，Native CUDA。不是 CoreX，也不是 CPU 模拟。
- 使用 R7 已验证的 `build_r7/GMTI_pipe_core`，SHA-256 `de3026ed4f1263be4143dcbcd10ccbb5e53b4be357e21906e1bf19ea0ec29d00`。
- 两种估计校正 × 两个 seed × ON/OFF/TO，共 **12/12** 个 Production 路径门通过；每个 seed/校正的三个角色 runtime XML 归一化处理算子指纹相同。
- 另执行 **8/8** 个真实 CUDA CFAR 数值诊断回放（ON/OFF × 校正 × seed）。每次检查 3 期、dynamic/full 两类分支，合计 3,163,680 valid CUT；hit flip 与超过 2 float32 ULP 的 threshold CUT 均为 0。普通 Production 结果本身不落 raw float32 CFAR maps，首次直接读取 map 的评价尝试因此停止；之后按 R8 已验证的独立 CUDA map-dump 方法补做了这 8 次审计并全部通过。
- GPU 状态快照：回放前 P8、48°C、6.88 W、36% utilization；结束 P3、50°C、13.12 W、100% utilization。该状态只作运行环境记录，不作为性能基准。

完整 runtime XML、逐 beam stage log、CSI tap、CFAR geometry、run log、branch manifest 与 map audit 文件均留在本机 `outputs/phase_r_r9_deterministic_delay_20261023/`；紧凑索引见 [R9 证据包](evidence/r9_deterministic_delay_estimator_20261023/)。R9 未重跑 Stage2，复用 R8 seed202/303 的原始场景输入和已确认的连续面积杂波。全部运行与评价完成后，按清理台账删除 36 个可重建的校正 IQ 副本，释放约 `1.65 GiB`；原始 R8 输入和 Production 结果保留。

## 复现入口

本轮执行基于干净 tracked HEAD `22cefb8e...`，R9 runner 当时作为新增工作树文件；R8 IQ 和 R7 binary 位于被忽略的本地目录。可在当前仓库保留这些数据后，将 R9 文件复制到该基线的隔离 worktree，并为其链接 R8 输出和 `build_r7`，再按顺序运行：

```bash
python3 scripts/phase_r/run_r9_deterministic_delay_estimator.py --phase preflight --output-root outputs/phase_r_r9_replay
python3 scripts/phase_r/run_r9_deterministic_delay_estimator.py --phase production --output-root outputs/phase_r_r9_replay
python3 scripts/phase_r/run_r9_deterministic_delay_estimator.py --phase evaluate --output-root outputs/phase_r_r9_replay
python3 scripts/phase_r/run_r9_deterministic_delay_estimator.py --phase summarize --output-root outputs/phase_r_r9_replay
```

`preflight` 会拒绝覆盖已有输出，并要求固定基线、R8 原始 IQ 和 R7 二进制身份一致。Production 命令使用本机 CUDA；系统提权应由运行环境自动授予，不切换到 CoreX。上述公式合成检查不在四阶段 runner 内，机器记录及结果随证据包保存。

## 复核限制

1. 合成公式测试是在 GPU 回放后补做，时间顺序偏离冻结计划；方法和数值未因该检查而更改。
2. 生产时刻的 runner SHA 在 preflight manifest 记录为 `a7595537...`；当前最终 runner SHA 为 `b236c95c...`。后续编辑增加 CFAR 独立审计和汇总逻辑。每个 branch manifest 精确记录了生产二进制、输入、runtime XML、log 和输出，但未逐分支记录 runner 哈希；因此不把生产中间态 runner 源码身份描述得比 manifest 实际记录更精确。
3. R9 的物理估计量是四通道融合谱上的有效线性斜率，而校正对象是单一物理通道2；当前证据支持受控场景任务回放，不能宣称解决一般通道可辨识问题。
4. 只有两个留出 seed、每 seed 三期；Observed H0 candidates/hits 不是经校准的 Pfa，径向速度估计不可评估。
