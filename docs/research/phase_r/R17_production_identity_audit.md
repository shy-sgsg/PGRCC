# R17：R14/R16 Production executable 身份审计

日期：2026-10-25。审计基线为分支 `codex/unknown-system-error-next-stage`、commit `5d6d237732871ea98d27398754d02f4454198bf9`。本阶段只读检查仓库记录、源码版本、runtime XML 和紧凑证据；未运行 CUDA、未运行新的 Production 分支，也未修改算法或参数。

## 结论

R14/R16 的巨大 CFAR 命中差异，主要原因已定位：**R14 实际运行了 R7 修复前的 Production binary。** R14 provenance 记录的 `GMTI_pipe_core` SHA-256 为 `f65a8aa446754cc79b6417f9275b3a01e73eb85098c6a4f9eaea0cb999c38962`，与 R7 `pre_fix_experiment_manifest.json` 中的旧 executable SHA 完全相同。R7 已在配对输入上复现该旧 GO-CFAR 的数值缺陷：float32 前缀和/积分图的包含—排除会产生错误负/零阈值并增加 hit。

R14 的声明源码提交 `095221bbfa458fd5f65bc1d095401cd04445aa9f` 晚于 R7 修复提交 `04958a996f6a1449fa3d9f158a4765493c85038d`；修复提交也是 R16 声明源码提交 `2d2992b8b1fe54d42789c7250917ebac3c658de3` 的祖先。因此 R14 的异常不是“源代码提交尚未包含修复”，而是**实际运行二进制与声明源码版本不一致**。R14 旧 binary 的具体复用/构建环节无法由留存记录还原。

这解释了 R14 与 R16 为什么不能作为同一 Production 实现的跨阶段方法比较。它不证明 R14 每一个 hit 都是错误 hit；R14 的旧版 power/threshold map 已清理，无法逐 CUT 计量。

## 证据核对

| 项目 | R14 / R16 留存证据 | 判断 |
|---|---|---|
| 声明源码版本 | R14 `095221…`；R16 `2d2992…` | 两者均位于 R7 修复提交 `04958a…` 之后；仅看源码 commit 不能代表实际 binary 身份。 |
| C++/CUDA 源码差异 | `git diff 095221… 2d2992… -- src include CMakeLists.txt gmti.xml` 无差异 | 两次声明源码之间未发现可解释 hit 差异的生产 C++/CUDA 改动。 |
| R14 Production binary | SHA `f65a8aa…`，见 R14 `provenance.json` | 与 R7 pre-fix manifest 的 binary SHA 完全一致；R7 manifest 记录的路径同为 `build/GMTI_pipe_core`。 |
| R16 Production binary | SHA `ae41c79…`；R16 manifest 记录 `cmake --build build -j4`、Release Native CUDA、nvcc 12.4.131、g++ 15.2.0、CMake 4.2.3 | 与 R14 不同。R16 CFAR 数值诊断记录 GO 方向 fallback 直接重算，约 1.55M direction recalculations/period，表明修复路径在运行。 |
| Stage2 inputs | `r14_r16_input_identity.csv` 中 9/9 role-period 文件 bytes 与 SHA-256 相同 | 配对输入身份一致；原始 `.bin` 已清理，当前为封存时比较记录。 |
| F1/F2 与 CSI tap | `r14_r16_c0_production_comparability.csv` 中 F1、F2、CSI after real/imag hashes 均 9/9 相同 | 封存记录显示这些检查点相同；原数组不在 compact evidence 中，无法当前重新散列。CSI 输出 hash 不等于保存了 CFAR 内核实际消费的全部中间量。 |
| Runtime XML | 两份 C0 XML 共 238 个标量叶子；差异仅为 `GMTI_data_new`、`pc_peak_scene_truth`、`result_add`、`track_debug_dir` 路径 | 未发现处理参数语义差异。 |
| CFAR geometry | 两边均 130×4096、guard=4、background=16、Doppler circular=true、GO、configured Pfa≈1e−6、alpha=13.44782257、每图 527,280 valid CUT | 几何与运行参数相同。 |
| CFAR 输出 | R14/R16 的 C0 final-union map hash 9/9 不同；每期 branch hit 数见 [CFAR 分析](R17_CFAR_divergence_analysis.md) | 分歧最迟在生产 CFAR 输出端已确定；旧 binary 身份解释了处理实现不等价。 |

## 复核到的历史数值根因

[R7 数值根因报告](R7_CUDA_CFAR_numerical_root_cause.md)指出，旧 GO-CFAR 将行前缀和、积分图存为 float32；局部矩形背景和由大前缀值作包含—排除得到，动态范围较大时舍入误差可能大于真实局部和，生成负/零门限。严格 `CUT > threshold` 会使正功率 CUT 被错误判为 hit。R7 同输入 pre/post 对照中，修复前出现 312,779 个额外 hit 和 215 个漏 hit；修复后 branch map 无 hit flip。该组数字来自 R7 的专用配对输入，不能移植为 R14 的错误 hit 计数。

R7 修复使用 double 前缀/积分累加、保守误差界，并在需要时从原始 float32 power 逐方向以 double 直接重算；训练窗几何、alpha 和 hit 判据保持不变。R14 的 Production binary SHA 与 R7 pre-fix binary 一致，R16 运行诊断则记录直接重算路径。因此 R14/R16 命中差异有已知、可定位的实现原因：比较的一端运行了数值修复前的 executable。

## 可得与不可得的结论

- **已确认：** R14 实际 binary 是 R7 pre-fix executable；R14 与 R16 不能用于跨版本方法排名。该结论不依赖用检测行数反推 CFAR 根因。
- **有历史根因支撑：** R7 已证明该旧 executable 对 GO-CFAR 存在负/零门限数值缺陷；R14 hit 数级别与旧实现行为一致。
- **不可逐 CUT 复算：** R14 power、threshold、complex detector-input `.f32` maps 已清理，因此不能核算 R14 本场景的非正门限数、具体错误 hit indices 或 R14 专属的错误额外 hit 数。
- **不可还原：** R14 未保存当时完整 CMake cache、编译器/toolchain、构建命令和完整 build log；可以确认 stale binary 身份，不能确定是哪一步漏了重建或复用了哪个缓存。
- **仍有效的局部比较：** R16 C0/C1 使用同一 Production binary、同一 seed 场景与配对输入；R17 没有发现本次跨 binary 问题会改变该同运行比较的证据。其适用范围仍仅限 R16 单 seed 条件。

## 证据入口

- [R14 结果与 compact evidence](R14_minimal_comparison_results.md)、[R14 evidence README](evidence/r14_scc_robust_ddc_seed505/README.md)、`evidence/r14_scc_robust_ddc_seed505/provenance.json`。
- [R16 结果与 compact evidence](R16_minimal_c1_results.md)、[R16 evidence README](evidence/r16_c1_gamma1_seed505/README.md)、`evidence/r16_c1_gamma1_seed505/provenance.json`。
- `evidence/r16_c1_gamma1_seed505/analysis/r14_r16_input_identity.csv`、`r14_r16_c0_production_comparability.csv`、`c0_c1_runtime_xml_semantic_differences.csv`。
- [R7 根因报告](R7_CUDA_CFAR_numerical_root_cause.md)、[R7 pre-fix manifest](evidence/r7_cfar_correctness_20261022/manifests/pre_fix_experiment_manifest.json)、[R7 post-fix manifest](evidence/r7_cfar_correctness_20261022/manifests/post_fix_experiment_manifest.json)。
