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
| CUDA numerical selftest | `EXECUTED`，真实 NVIDIA GeForce RTX 3050 Laptop GPU；覆盖 8 类合成输入及保存的 R6 period-0 branch 输入，所有已检查 CUT 与 float64 参考的 float32 输出一致，最大输出误差 0 float32 ULP；真实零背景保持为零 |
| Full CTest | `ctest --test-dir build_r7 --output-on-failure`：24/24 passed；在受限沙箱中 CUDA 项曾因设备不可见 skip，随后通过授权设备环境重跑全套，CUDA selftest 通过 |
| Production branch maps | 九组修复后配对地图，每组 527,280 个有效 CUT；threshold 对 float64 参考转 float32 的最大距离小于 0.5 ULP，超过 2 ULP 的 CUT 为 0；branch hit flips 为 0 |
| A0 repeat control | 首次 A0_ON 与重复运行的 GMTI01、union power/threshold/hits、branch maps 和 CSI tap bitwise 一致；但首次旧 `run_manifest.json` 未记录 executable SHA，因此只能确认输出相同，不能确认 same-binary repeatability。配对数值分析使用有 SHA 的 `A0_ON_repeat`。 |
| Python numeric/reference tests | `python3 -m pytest -q tests/test_phase_r7_cfar_numerics.py tests/test_phase_r7_cfar_reference.py`：9 passed |
| 诊断 metadata follow-up smoke | 矩阵之后以更新二进制在 RTX 3050 上重跑 A0_ON 单周期；exit 0。metadata 的 GO alpha `13.447822570800781` 与 geometry CSV 逐位文本相同，精度标记为 `float64_accumulation_float32_output`。此 smoke 只验证诊断元数据，不属于九组数值矩阵或性能测试。 |

Follow-up smoke 摘要与轻量产物在 [`evidence/r7_cfar_correctness_20261022/metadata_smoke/`](evidence/r7_cfar_correctness_20261022/metadata_smoke/)。该 smoke 二进制 SHA-256 为 `8bd9a4a1ec98992a082c70b1bf81d5a6c007cfaf367c0f01e332121250239e6b`；它晚于九组矩阵构建，因此不替代或改写矩阵 manifest 中的 binary SHA。运行时 XML SHA-256 为 `352a69c22edc3f073d1686ad2684a1da3a5c18dbac3af3d22c23219479fef558`，输入 SHA-256 为 `ac124e51df49292a7dcd1b00b43f8d7e701a2a50c461ab10ddf7bf6223d609fc`。

CUDA 自检的保存输入和实际几何已逐字节保存在 [`evidence/r7_cfar_correctness_20261022/fixtures/`](evidence/r7_cfar_correctness_20261022/fixtures/)；生产矩阵复现命令见 [最小 Production 复验](R7_minimal_production_revalidation.md)。

## 资源开销与性能边界

对 130×4096 map，仅把 row-prefix 和 (H+1)×(W+1) integral 从 float 改 double，额外约 4,276,748 bytes；启用数值诊断的 fallback byte map 再需约 532,480 bytes，总增量约 4.6 MiB/被诊断分支，另计既有检测与 map buffer。具体尺寸由运行配置决定。

本轮设备在 Production 矩阵前报告 RTX 3050 Laptop、P0、GPU 100% 利用率、约 1,813/4,096 MiB 显存占用；因此 2–4 秒级 wall time 不是干净性能基准。未报告 kernel-only/传输分离，也未完成至少三次干净设备计时：性能开销 `NOT_EVALUABLE`，不可标为 `PERFORMANCE_VERIFIED`。

## 限制

数值阈值/命中决策正确性已在本轮输入与测试覆盖范围内验证；任务级目标保护、统计 Pd/Pfa 和复杂地杂波泛化并非该修复测试的证明范围。修复不调整检测质量门槛，也不把减少错误 hit 直接解释成真实目标性能改善。
