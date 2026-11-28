# R18：Native CUDA 执行与质量审计

日期：2026-10-25。研究总纲保持不变，Git blob 为 e637e3769bde7814298ff012d0f42301884e2985。

## 阶段状态

| 状态 | 结论 |
|---|---|
| IMPLEMENTED | R18 冻结配置、生产二进制选择与 SHA fail-closed runner、C0/C1/C2/C3 方法条件及评价路径已实现。 |
| EXECUTED | 12 个冻结场景均在本机 Native CUDA 上执行；每场景 21 个 Production branches，共 252 个分支。另执行 seed909 target-free 独立参考的 Stage2 与 C2/C3 Gamma 生产检查。 |
| EVALUABLE | 12/12 场景的有效面积杂波、输入身份、causal triplet、valid-CUT geometry/分母和 final-union hit 审计可评估并通过。目标周期命中可作为本矩阵的描述观察。 |
| PERFORMANCE_VERIFIED | 否。经验 Pfa、PK/PKR residual closure、target-only 幅相保持/SCNR 与独立参考任务差值均未闭合；不作方法性能胜负或统计显著性结论。 |

## 源码、配置和二进制身份

| 项目 | 身份 |
|---|---|
| 分支 | codex/unknown-system-error-next-stage |
| 正式矩阵 runner 源码提交 | 014352ace1c4ad0bade0d759a099a47fe9ecfaa9，运行 manifest 记录 tracked worktree clean |
| Release build 源码提交 | 4c6d4d303621324f5e174db0ba1811c5691df2e2，build identity 记录 tracked worktree clean |
| R18 配置 | configs/research/phase_r18_native_cuda.json；SHA-256 3af60966332877095e5447247ab08cb39a54ef7a15681f37adf33cc251082ae1 |
| Production runner | SHA-256 2f59997f92ffd5f0b79dae688731c5fac3f54e7c510caafa68de2a6176c6fab8 |
| Analyzer | SHA-256 3ffc7de0cf442c33626d91ee665e2cd769a86489a4379ec0c4698ca263480d5b |
| GMTI_pipe_core | SHA-256 3328d7c61bc36b6d3b906692e355c46c328150c0399fa457e6f871bcb79abaf0 |
| simulate_stage2_statistical | SHA-256 5dd0ac4e2536b8bcf2508cc53db8a30780eba75f40c025e13ffaa930f8ba7b9b |
| CUDA 构建 | Release，USE_COREX=OFF，CUDA architecture 86，CUDA 12.4，CMake 4.2.3；详细 CMakeCache 和二进制身份见 build/native_release_identity.json |

build commit 与运行源码 commit 之间只有文档、Python runner/evaluator 和测试文件变化，没有 C++/CUDA 源码变化；全部 Production 分支也都通过相同的 GMTI_pipe_core SHA 门。分开记录 runner 与二进制身份，避免把脚本提交误称为二进制构建来源。

## 设备与正式执行

设备为 NVIDIA GeForce RTX 3050 Laptop GPU，驱动 580.178.04。正式矩阵前的 nvidia-smi 记录为 P0、63°C、瞬时功率 53.28 W；该记录用于设备审计，不用于性能排名。本阶段没有将 kernel、分支或完整流程计时作为性能结论。

| 执行项 | 实际结果 |
|---|---|
| 冻结矩阵 | seed 606/707/808 × 6.7/0.5 m/s × 30/20 dB，共 12 cases；每 case 3 periods，delay error 固定 0 ns。 |
| Production | 每 case 21/21 branches 返回 passed；共 252/252。分支运行时 XML、日志、CSI tap、CFAR geometry、检测/跟踪审计及输入身份保留在各场景快照。 |
| 连续面积杂波 | 每场景由 A0_ON 负责注入：390 packets、1,952,655 samples。OFF 等角色复用背景时本地新注入计数为 0，不能将其误读为整场景没有杂波。 |
| 因果 triplet | ideal 与 none 条件 12/12 通过；每 case 18,439,200 samples，最大绝对误差约 1.20e-6 至 1.35e-6，低于冻结容差 5e-5。 |
| CFAR geometry | 每 case 各分支 valid-CUT denominator 与最终 union-hit 门通过；full-band union 每 period 527,280 个 valid CUT，3 period 合计 1,581,840。 |
| 真实 Pfa | 12/12 为 NOT_EVALUABLE。配置阈值和 hit fraction 不是实测 Pfa。 |
| 算法性能 | decision matrix 均未声明 algorithmic results；未验证跨 seed 统计显著性。 |

## 证据入口

- [Release build identity](evidence/build/native_release_identity.json)、[CUDA CFAR 数值 selftest 日志](evidence/build/cfar_cuda_numerical_selftest.log)及完整日志。
- [正式矩阵设备状态摘要](evidence/formal_matrix/nvidia_smi_before_summary.md)和[启动前失败记录](evidence/formal_matrix/preflight_failure.md)。本机完整 nvidia-smi 输出保留在本地，未纳入仓库版本。
- 12 个 seed/speed/SNR 目录位于 [formal_matrix](evidence/formal_matrix/)；每个目录含完整 manifest、snapshot index、逐 case manifest、运行时 XML/log、CSI/CFAR/Detection/TrackManager 审计和 compact evidence。
- [12 场景 gate 汇总](evidence/formal_matrix/summary/r18_cuda_case_gate_summary.csv)、[ON 条件描述性任务指标](evidence/formal_matrix/summary/r18_cuda_on_method_descriptive_summary.csv)、[杂波可比性图](evidence/formal_matrix/summary/r18_cuda_clutter_comparability.png)。
- [独立 seed909 次级验证](evidence/independent_seed909_secondary_attempt3/report.md)；状态为 NOT_EVALUABLE，细节见 R18 失效机制报告。
- [磁盘清理台账](evidence/cleanup_ledger.md)记录保留证据与删除的可重建数组。

## 质量结论

R18 的 Native CUDA 路径身份、真实面积杂波、因果输入闭合和 CFAR valid-CUT geometry 已有本次运行证据。检测记录和 TrackManager 审计文件存在且生产一致性检查通过，但这不等同于独立 H0 false-CUT 统计、ID-switch 可归因性或目标专属幅相/SCNR 验证。R18 固定零时延，故该矩阵不能判断未知通道误差是否降低 GMTI，也不能量化 Known/Blind correction 的恢复量。
