# R13 传统方法能力与公平性矩阵

本矩阵只把实际代码入口、可运行路径和 R13 实际执行分开。方法定义与执行记录见证据包 `analysis/adapter_records.csv`、`branch_inventory.csv` 及各 branch runtime/config/log。

| 条件/方法 | 数学模型与实际入口 | 输入、通道与支持域 | 拟合/额外参考 | Production、本次执行与测试 | 公平比较边界 |
|---|---|---|---|---|---|
| A0 Production Current closure control | 不启用 research adapter，沿生产 Current CSI；用于控制闭合 | 四通道协议 IQ 经 `(1,3)/(2,4)` 融合为 F1/F2；CSI 有效面为 2 通道 | 不拟合、不读取额外标定 | R13 两场景共 6 个真实 Native CUDA branches；C0 对应地图、CSI taps、target ROI 均逐项闭合 | 是重复的生产 Current 控制，不是 CPU/offline 结果，也不是独立算法。 |
| C0 Production Current | 生产 Current CSI；`src/gpu/gpu_kernels.cu` 的常规生产路径 | 同一四通道协议 IQ → F1/F2；两通道 CSI | 无额外 OFF reference、无训练 | R13 两场景共 6 branches Native CUDA；C0 与 A0 闭合 | 生产 operational anchor。 |
| C1 ordinary subtraction | `Gamma=1`，直接计算 `F2−F1`；入口 `src/production_calibration_adapter.cpp:344,414` | Production adapter 的最终 F1/F2；4 路协议输入融合后为 2 通道；无需样本拟合 | 无估计样本、无 paired OFF reference、无训练 | Production adapter 实现存在；R13 未执行。`production_calibration_adapter_selftest.cpp` 覆盖相减和支撑域外保持 | 最接近 C0 的简单确定性方法；本轮没有数值结果，不参与排名。 |
| C2 SCC | 在支持域最小化 `||F2−Gamma·F1||²`，`Gamma=Σ(F2·conj(F1))/Σ|F1|²`；入口 `src/production_calibration_adapter.cpp:344,446` | Production 最终 F1/F2；每 period 的 active support 为 `39×4095=159,705` cells；输出仍为两通道 CSI | Mode-A 从目标自由 OFF 拟合一个复 Gamma，固定应用到 ON/TO；不读目标真值、无训练 | R13 两场景共 6 branches、18 个 period-role adapter rows 均 OK；C++ selftest 覆盖符号、固定 reference 与失败关闭 | C3 同属 paired-OFF-reference 信息类；与 C0 的比较同时多了 OFF 信息。 |
| C3 Robust DDC | 每 Doppler row 独立估计 Gamma；先按 circular phase center 和 `0.35 rad` 门剔除离群，再做复最小二乘；入口 `src/production_calibration_adapter.cpp:227,479` | 每 period active support 共 159,705 cells；通常每 Doppler row 在 4,095 个距离 cell 上拟合，输出仍为两通道 CSI | Mode-A 从目标自由 OFF 拟合，固定 reference 应用 ON/TO；配置最小支持数 8；无训练 | R13 两场景共 6 branches、18 个 period-role adapter rows 均 OK；C++ selftest 覆盖相位离群、低相干和支持边界 | 与 C2 的观测信息和 reference 角色相同，模型自由度不同；这是本轮最直接的算法对照。 |
| C4 Robust DDC-RB | 每 Doppler row、每 range band 估计 Gamma 并执行 robust phase rejection；入口 `src/production_calibration_adapter.cpp:479` | Production F1/F2；range-band 支持在配置中指定；输出为两通道 CSI | 使用拟合支持域；默认仍需校验 reference 角色；无训练 | Production adapter 实现存在，R13 未执行；C++ selftest 覆盖最终短 range band 边界 | 未运行，不推测能力或收益。 |
| Python/offline calibration scripts | `scripts/two_channel_complex_calibration.py` 提供 SCC、DDC、DDC-RB、Robust DDC NumPy estimator | 离线 F1/F2 数组与 mask/support；不是额外物理通道 | 由离线调用方指定 support/reference；无模型训练 | 仅作离线实现；`tests/test_two_channel_complex_calibration.py` 有公式、符号和边界测试，不等同于 R13 CUDA 执行 | 只能作方法定义/数值 sanity，不能充当生产结果。 |
| 4-channel STAP/JDL | 四个物理通道上的时空自适应处理 | 保留与 F1/F2 CSI 不同的空间自由度 | 由具体算法训练快拍/支持域决定 | R13 runner 标记 native 4ch STAP disabled，未执行 | 信息量不同，须匹配自由度或分层比较，不能直接混排。 |

## 源码执行边界

- Production adapter 入口位于 `src/gpu/gpu_kernels.cu` 的 final F1/F2、P38 phase correction 之后；调用 C++ `production_calibration` 方法，校验失败即返回失败，不静默回退 Current。结果上传到 CUDA CSI buffer，后续 CFAR/检测/TrackManager 仍走 Production。
- 本次是原生本机 CUDA，`USE_COREX=OFF`。Python evaluator 仅在真实运行产物上离线关联和计算指标，不是 CPU 信号仿真，也不冒充 CUDA 证据。
- C0 与 C2/C3 处理的物理输入相同，F1/F2 maps 在三种算法间逐期相同。C2/C3 在 OFF 上获得额外校准信息，故对 C0 的差异包含信息可用性与处理算法两项；不能归结为“算法自身改进”。
- 本次 active support 每 period 为 39 Doppler rows × 4,095 range cells。C2 在该 support 上拟合共享 Gamma；C3 按 row 拟合并做 robust phase rejection。方法没有训练样本或离线学习步骤；“support”指单次估计的生产 CSI cells。
- 相关实现和测试：`src/production_calibration_adapter.cpp` 与 `src/gpu/gpu_kernels.cu`；C++ `tests/production_calibration_adapter_selftest.cpp` 覆盖 ordinary subtraction、SCC、DDC、Robust DDC、DDC-RB 和固定 reference 失败关闭；Python `tests/test_two_channel_complex_calibration.py` 只覆盖离线 NumPy estimator。
- 本轮未训练 AI、未开发 Router、未扩展六参数联合估计，也未执行完整 Baseline 矩阵。

## 历史 Baseline runner 代码审计

下列入口用于厘清既有方法的数学族、信息量和证据边界；本次 R13 没有重跑它们，也没有把离线结果混入 Native CUDA 表。

| 入口及实际方法 | 输入与自由度 | 拟合数据 / 额外信息 | 执行、测试及公平边界 |
|---|---|---|---|
| [`scripts/run_baseline_benchmark.py`](../../../scripts/run_baseline_benchmark.py)：Current legacy CSI、Phase-only CSI (DPCA-class)、Row complex LS/Wiener；另含 JDL-3×4、MNEC、SA-MNEC | 前三种读取模拟器生成并经 Python 对齐的 F1/F2 等效两通道，输出一个复数检测面；`Current` 按每 Doppler row 的复幅度均衡后相减，Phase-only 只用 P38 相位相减，LS/Wiener 为 `F1−alpha·F2`。JDL-3×4 把 4 个通道与相邻 `−1,0,+1` Doppler rows 拼成 12 维特征；MNEC/SA-MNEC 保留 4 个空间通道。 | Current/Phase-only 使用 target-observation P38 phase；LS/Wiener 和三种协方差法从配对 C+N background 取权重/协方差。无模型训练。执行是 statistical Stage2 输入加 Python/NumPy 离线处理；manifest 明确 `gpu_production_run=false`。脚本包含局部 CFAR、检测、MDV proxy，不等价于 Production CUDA/TrackManager。R13 未执行；`tests/` 中没有该 runner 的端到端测试。4-channel 方法与 2-channel CSI 方法自由度不同，只能分层解释。 |
| [`scripts/run_baseline_v2.py`](../../../scripts/run_baseline_v2.py)：两种 production-replay strict 方法、四种 scientific-controlled strict 方法、DL-SMI/MVDR global/local、Shrinkage-SMI/MVDR global/local、Corrected JDL、MNEC、SA-MNEC | Strict 方法沿 F1/F2 两通道接口。普通 LS 为逐 Doppler row 复系数拟合；Robust LS 使用 Huber reweighting 和 P38 physics prior。四通道方法使用按 Doppler row/range block 的 4 维空间向量；Corrected JDL 是 4 channel × 3 row = 12 维。MVDR 是 `R⁻¹s/(sᴴR⁻¹s)`；MNEC 将 steering 投影到估计杂波子空间正交补，SA-MNEC 用 4 个子孔径平均协方差，代码标明是 approximate reference。 | Current/Phase-only production-replay 保留 target-observation P38；scientific-controlled strict 与四通道自适应方法用配对 C+N background。Covariance 有对角加载；Shrinkage-SMI 另有 shrinkage。共享 V1 的模拟 Stage2 数据和 Python/NumPy 离线链；manifest 将证据边界标为 CPU offline baseline。特征相关和 ridge light-predictor 是探索性离线 screening，不是 Production AI。R13 未执行，未发现该 runner 的端到端测试。Production-replay 命名不代表真实 CUDA Production execution。 |
| [`scripts/run_equivalent_vs_physical_calibration.py`](../../../scripts/run_equivalent_vs_physical_calibration.py)：M0–M6 equivalent residual methods 与 P1/P2/PK/PK+R physical-state methods | 确定性生成的内存 F1/F2 复数阵列和 clutter support；不是四通道 IQ。M0/M1 直接相减；M2 全 support SCC；M3 每 Doppler row DDC；M4 robust DDC；M5 row×range-band DDC-RB；M6 robust DDC-RB。该线只输出等效复系数/残差或物理状态估计，不包含真实检测自由度。 | Mode-A 从合成 OFF 估计并应用；Mode-B 从 ON 估计。P1 盲估计 fast-time delay 时仅用 F1/F2 与频率轴，其他已注册物理状态使用传感器观测值；P2 作物理校正后再 robust residual calibration。PK/PK+R 将已知状态仅用于 evaluator 上限。没有训练。 | 可测试方法定义与输入隔离，但属于 deterministic synthetic CPU pilot，不是 CUDA/Production evidence。存在 [`tests/test_equivalent_vs_physical_calibration_pilot.py`](../../../tests/test_equivalent_vs_physical_calibration_pilot.py) 与相邻状态测试；这些不覆盖生产链。不可把 M1/M2 等效残差最优直接解释为物理误差已校正、GMTI 任务收益或 AI 必要性。 |

上述 runner 揭示的是不同输入信息与自由度层级。V1/V2 的四通道自适应方法、offline production replay，以及 equivalent/physical synthetic pilot 都不是本次三方法 R13 Production 子集的可直接排名对象。R13 选定的 C0/C2/C3 才是在本次相同 Production F1/F2 接口中的实际处理结果；C2/C3 另有目标自由 paired OFF 参考，必须单独标注这一信息条件。
