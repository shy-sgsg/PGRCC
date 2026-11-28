# R16 seed505 C1 Gamma=1 紧凑证据包

本证据包支持[R16 结果报告](../../R16_minimal_c1_results.md)与[方向评估](../../R16_research_direction_assessment.md)。本次执行身份与输入 SHA 保存在 `runner/`，分析器原始表格在 `analysis/`，九个 branch 的 runtime XML、Production 日志和诊断 CSV 在 `production/`，Stage2 配置、报告、truth、simulate 日志在 `stage2/`。

## 审查入口

1. 先读 `../../R16_experiment_design.md`、`../../R16_minimal_c1_results.md` 和 `../../R16_research_direction_assessment.md`。
2. 核对 `runner/runner_manifest.json`、`runner/case_manifest.json`、`analysis/manifest.json` 与 `analysis/summary.csv`。
3. 检查 `analysis/r14_r16_input_identity.csv`、`analysis/r14_r16_c0_production_comparability.csv`、`analysis/c0_c1_runtime_xml_semantic_differences.csv` 和按确认航迹/当前帧条件派生的 `analysis/track_association_summary.csv`。
4. 追溯 `production/<METHOD>_not_applicable_<ROLE>/production.xml`、`gmt_pipe_core.log`、`result/production_calibration_adapter.csv`、CSI tap/ROI manifest、CFAR geometry/meta、Detection 结果与 `track_debug_runs/`。
5. `cfar_union_maps/` 含 27 张 final-union `.f32` map 的无损 gzip 版本。`compression_manifest.csv` 同时记录原 map SHA-256、压缩文件 SHA-256、大小和对应 evaluator hit/CUT 数；解压后原 SHA 必须匹配。
6. `source_snapshot/` 保存运行时 runner/evaluator/test 源文件快照以及相对执行前 HEAD 的 patch。只看最终已提交脚本不能代替该次 dirty-worktree 源码身份。
7. [`failures/`](failures/) 记录实际暴露并修复的 Production adapter schema 问题，以及未通过通用 runner gate 的范围限制。

## 执行身份与复现

本次运行分支 `codex/unknown-system-error-next-stage`，执行前 HEAD `2d2992b8b1fe54d42789c7250917ebac3c658de3`。Production/evaluator Python 源在该 HEAD 上为 dirty 状态；实际脚本哈希与差异见 `provenance.json` 和 `source_snapshot/`。`GMTI_pipe_core` SHA-256 为 `ae41c79ee48b530fc71e07b4ade7dcf7095d2ae9e9ee388f0e5ed9943a8fca4f`；Stage2 binary SHA-256 为 `5dd0ac4e2536b8bcf2508cc53db8a30780eba75f40c025e13ffaa930f8ba7b9b`。Production 使用 `USE_COREX=OFF` 的本机 Native CUDA 路径。

复现前需在仓库根目录完成原生 Release 构建，并确保本机 CUDA 设备可用。运行命令：

```bash
env USE_COREX=OFF GMTI_CFAR_DUMP_BEAM=50 python3 scripts/run_production_hierarchical_calibration_pilot.py \
  --config docs/research/phase_r/evidence/r16_c1_gamma1_seed505/inputs/r16_runner_config.json \
  --mode pilot \
  --output-root .scratch/r16_c1_gamma1_seed505_20261025 \
  --input-mode local \
  --delay-errors-ns 0 \
  --seeds 505 \
  --target-velocities-mps 6.7 \
  --target-snr-db 30 \
  --method-ids C0,C1 \
  --calibration-modes Mode-A \
  --max-cases 1
```

```bash
python3 scripts/phase_r/evaluate_r13_minimal_baseline_pilot.py \
  --normal-case-manifest .scratch/r16_c1_gamma1_seed505_20261025/cases/base__seed_505__v_6d7__snr_30__delay_0ns/case_manifest.json \
  --normal-scene-label normal_seed505_v6p7 \
  --output-dir .scratch/r16_c1_gamma1_seed505_20261025/r16_analysis
```

`.scratch/r16_c1_gamma1_seed505_20261025/` 是本任务生成并在证据封存后清理的临时 raw output root。Stage2/Production 原始 IQ binaries 与完整 CSI `.npy` maps 已清除；输入文件 bytes/SHA、配置、truth、报告和数值审计仍保留。按上述命令重跑会重新生成输入，当前保留包不伪称含有原始 IQ。跨 R14 比较另受 Production binary 不同的限制，见结果报告。

## 设备与限制

正式执行前以提权方式取得的本机 GPU 记录为 NVIDIA GeForce RTX 3050 Laptop、Driver 580.178.04、CUDA driver 13.0、P8、45°C、6 W/80 W、558/4096 MiB、7% GPU 利用率。之后普通沙箱中的 `nvidia-smi` 无法与 driver 通信；该失败不是 CUDA 运行日志，也不覆盖执行前设备记录。Toolkit 为 nvcc 12.4.131，g++ 15.2.0，CMake 4.2.3。该运行不是性能基准。

`verification_pytest.log` 记录本次两组定向测试 66 passed；`cleanup_ledger.md` 记录临时文件清理范围与保留/删除项。所有运行时路径在临时 root 清理后均作为审计来源路径保留，需复现时用新输出 root。
