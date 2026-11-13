# R7：GO-CFAR 修复实现与验证

日期：2026-10-22

## 修复范围

修复仅针对 Production GO-CFAR 的数值累加；CA-CFAR 路径、训练窗几何、guard/background 宽度、Doppler circular 语义、CUT/exclusion band、运行时 alpha、输出 ABI 和严格 `CUT > threshold` 均保持不变。

- 新增共享 CUDA GO-CFAR helper：行前缀和积分图用 `float64`；矩形查询计算保守累积误差界。
- 当误差界大于噪声均值/最终 float32 threshold 的容差时，逐方向直接从原始 float32 power 用 float64 重算，不裁剪 sum 或 threshold，不加 epsilon floor。
- Threshold 仍按既有 map ABI 输出 float32；可选数值诊断记录直接重算方向数。
- 固定 synthetic 类别和已保存的 R6 生产输入由同一 CUDA helper 测试，不以 CPU smoke 代替设备证据。

## 实际验证

| 验证 | 状态与结果 |
|---|---|
| Native CUDA Release build | `EXECUTED`；`build_r7/GMTI_pipe_core` SHA-256 `3f58a55330b4afec75e595ce3c6238d5471bbb4f7cda119605c53b4fc141c040` |
| CUDA numerical selftest | `EXECUTED`，真实 NVIDIA GeForce RTX 3050 Laptop GPU；覆盖 8 类合成输入及保存的 R6 period-0 branch 输入，所有已检查 CUT 与 float64 参考的 float32 输出一致，最大输出误差 0 float32 ULP；真实零背景保持为零。当前完整 CTest 中此项再次通过 |
| Full CTest | 本次更新后 `ctest --test-dir build_r7 --output-on-failure`：25/25 passed，含 CUDA 数值自检与缺失 fixture 拒绝测试 |
| Production branch maps | 九组修复后配对地图，每组 527,280 个有效 CUT；threshold 对 float64 参考转 float32 的最大距离小于 0.5 ULP，超过 2 ULP 的 CUT 为 0；branch hit flips 为 0 |
| A0 repeat control | 首次 A0_ON 与重复运行的 GMTI01、union power/threshold/hits、branch maps 和 CSI tap bitwise 一致；但首次旧 `run_manifest.json` 未记录 executable SHA，因此只能确认输出相同，不能确认 same-binary repeatability。配对数值分析使用有 SHA 的 `A0_ON_repeat`。 |
| Python numeric/reference tests | `python3 -m pytest -q tests/test_phase_r7_cfar_numerics.py tests/test_phase_r7_cfar_reference.py`：9 passed |
| 诊断 metadata follow-up smoke | 矩阵之后以更新二进制在 RTX 3050 上重跑 A0_ON 单周期；exit 0。metadata 的 GO alpha `13.447822570800781` 与 geometry CSV 逐位文本相同，精度标记为 `float64_accumulation_float32_output`。此 smoke 只验证诊断元数据，不属于九组数值矩阵或性能测试。 |

Follow-up smoke 摘要与轻量产物在 [`evidence/r7_cfar_correctness_20261022/metadata_smoke/`](evidence/r7_cfar_correctness_20261022/metadata_smoke/)。该 smoke 二进制 SHA-256 为 `8bd9a4a1ec98992a082c70b1bf81d5a6c007cfaf367c0f01e332121250239e6b`；它晚于九组矩阵构建，因此不替代或改写矩阵 manifest 中的 binary SHA。运行时 XML SHA-256 为 `352a69c22edc3f073d1686ad2684a1da3a5c18dbac3af3d22c23219479fef558`，输入 SHA-256 为 `ac124e51df49292a7dcd1b00b43f8d7e701a2a50c461ab10ddf7bf6223d609fc`。

CUDA 自检的保存输入和实际几何已逐字节保存在 [`evidence/r7_cfar_correctness_20261022/fixtures/`](evidence/r7_cfar_correctness_20261022/fixtures/)；生产矩阵复现命令见 [最小 Production 复验](R7_minimal_production_revalidation.md)。

## 最终三周期 Production 复验补充

之后为冻结的一对一目标评价执行了 3-period 最终矩阵：A0/B/C × OFF/ON/TO 共 9 个 case，另有同二进制 A0_ON repeat 与 diagnostics-off 控制。11 个 case 均真实执行于 CUDA，退出码为 0，period 0/1/2 顺序完整；加上早期 focused run，最终 manifest 共 12 条记录。最终三周期二进制 SHA-256 为 `de3026ed4f1263be4143dcbcd10ccbb5e53b4be357e21906e1bf19ea0ec29d00`，与本节前述单周期 numerical matrix 的 `3f58a553...` 是不同构建，不能混用身份。

A0_ON 与 repeat 的该 binary SHA 相同。两次运行的 CFAR/CSI float32 地图、数值/几何与 lineage CSV 逐字节相同；检测 CSV 忽略动态 `run_id` 和输出路径后内容相同；TrackManager frames/detections/payload 忽略运行 ID 后内容相同。故三周期 A0_ON 重复性 `EVALUABLE`，仅限这组固定输入与输出字段。Diagnostics-off 控制保留检测和航迹产物，且未生成诊断 lineage 侧车，确认诊断开关隔离。

矩阵运行前 GPU 记录为 RTX 3050 Laptop、P0、1500/6001 MHz、20.04 W、55 °C、86% 利用率、1391/4096 MiB 显存。设备忙碌，运行 wall time 不是基准；未量测 kernel/传输分离或干净重复吞吐，故 CUDA 性能 `NOT_EVALUABLE` / `PERFORMANCE_VERIFIED = NO`。额外约 4.6 MiB/map 的 double accumulation 与 diagnostics storage 是按 map 几何计算的理论增量，不是峰值显存实测。

Earlier closeout verification (before the per-CUT logging assertion) had a sandbox CTest run with 24 passed and the CUDA test skipped, followed by an authorized 1/1 CUDA selftest pass. The authoritative `build_r7/GMTI_pipe_core` SHA remained `de3026ed4f1263be4143dcbcd10ccbb5e53b4be357e21906e1bf19ea0ec29d00`. These are build/correctness checks, not a performance claim; the newer complete verification is recorded below.

### 近门限 CUT 逐项记录补充

审计发现旧自检虽汇总 `near_threshold_cuts`，却未逐 CUT 输出；且 `near_threshold_hit_flips` 曾误用全部 CUT 的 hit-flip 计数。自检现逐项记录 CUT 功率、float32 reference/CUDA threshold、threshold ULP 误差、两侧 hit 判决及 hit-flip；汇总也分别给出近门限与总 hit-flip。CTest 另要求严格相等用例 `(row=0,col=3)` 的近门限记录必须出现，缺少明细即失败。

本次真实 CUDA 完整 CTest（命令 `ctest --test-dir build_r7 --output-on-failure`）的逐项明细保存在 [`evidence/r7_cfar_correctness_20261022/analysis/cuda_near_threshold_cuts_20261022.log`](evidence/r7_cfar_correctness_20261022/analysis/cuda_near_threshold_cuts_20261022.log)：共 3,671 个 CUT（稀疏零背景 1,815；严格相等 1,856），记录中 reference/CUDA threshold 均为 0 ULP，hit flips 为 0。设备为 NVIDIA GeForce RTX 3050 Laptop GPU，driver 580.178.04 / CUDA 13.0，测试时 GPU 使用率约 98%；该运行仅作正确性验收，不用于性能比较。运行时源码基线为提交 `07f75e0375c90b43c59149561d1726fa44ac2838` 加本次自检/CMake 测试改动。日志 SHA-256 为 `a83c1a454143c1c1dbbb80d7312fb8fcfea2cedf2867259c569c1757d0f1cd1e`，自检二进制 SHA-256 为 `011189d196b82cecb849ddc7af9989e63afd400fc6861d1d39106d95908eb412`。

### 构建后端范围

本项目 R7 使用原生 CUDA/nvcc 路径：`CMakeLists.txt` 中 `USE_COREX` 默认关闭，实际 `build_r7/CMakeCache.txt` 记录 `USE_COREX=OFF`、`CMAKE_CUDA_COMPILER=/usr/bin/nvcc`。Native CUDA Release 构建、真实 RTX 3050 数值自检和 Production 矩阵均已执行。CoreX 是仓库提供的可选替代后端，不属于本次 CUDA R7 验收范围；本报告不声称验证了该可选后端。

## 资源开销与性能边界

对 130×4096 map，仅把 row-prefix 和 (H+1)×(W+1) integral 从 float 改 double，额外约 4,276,748 bytes；启用数值诊断的 fallback byte map 再需约 532,480 bytes，总增量约 4.6 MiB/被诊断分支，另计既有检测与 map buffer。具体尺寸由运行配置决定。

2026-10-23 对同一 period-0 A0_ON 输入、runtime XML 和两版 Release 二进制补做了一次 warm-up + 3+3 交错 A/B。修复前/后外部 process wall median 分别为 1.41/1.33 s，日志内 `algorithm_process_ms` median 分别为 1095.104/1030.419 ms；但过滤 detection 数从 140 变为 19，后续工作量不同，因此表观约 6% 的耗时下降不能归因于修复。设备采样也从运行前 P8/210 MHz、8% 利用率变化到运行后 P0/1500 MHz、5%，且没有 per-run 遥测、kernel/传输分离或 VRAM high-water 记录。完整命令身份、样本和日志见[性能 A/B pilot manifest](evidence/r7_cfar_correctness_20261022/performance_ab_20261023/manifest.json)。因此本次只得到受限的端到端描述性测量，性能开销仍 `NOT_EVALUABLE`，`PERFORMANCE_VERIFIED = NO`。

上述运行中两版都记录 CUDA trig-LUT device upload、CUDA worker-pool prewarm `cuda_free=3304MB / total=3759MB`；该值是启动期、取整后的空闲量，不是峰值显存。进程最大 RSS median 为 253,568/253,272 KB，属于主机内存，不代表 VRAM 增量。双精度积分图约 4,276,748 bytes/map、诊断 fallback byte map 约 532,480 bytes/map 仍为按几何计算的理论量；本次没有测得峰值显存差。

## 限制

数值阈值/命中决策正确性已在本轮输入与测试覆盖范围内验证；任务级目标保护、统计 Pd/Pfa 和复杂地杂波泛化并非该修复测试的证明范围。修复不调整检测质量门槛，也不把减少错误 hit 直接解释成真实目标性能改善。
