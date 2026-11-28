# R14 seed505 紧凑证据包

本包对应 [R14 冻结设计](../../R14_experiment_design.md)、[最小比较结果](../../R14_minimal_comparison_results.md) 与[方向评估](../../R14_research_direction_assessment.md)。实验仅包含一个独立 normal-speed seed505 case；本机 Native CUDA 执行后，按用户已授权的输出清理要求删除了该 case 的大体量派生产物，保留本包用于审查与复现。

## 结论速览

- 真实 GPU：NVIDIA GeForce RTX 3050 Laptop，driver 580.178.04、CUDA 13.0。运行前探测为 P8、39°C、6.67 W、15% GPU utilization、731/4096 MiB；这是设备状态快照，不是性能基准。
- `USE_COREX=OFF`、runner `skip_cuda=false`。Production logs 记录 CUDA trig LUT device upload、`new_protocol_gpu_preprocess: enabled` 和 CUDA memory。XML 中 `dbs_mosaic_use_gpu=false` 只控制最终展示拼图，源码注释明确它不关闭信号处理 CUDA 路径。
- Stage2 面积杂波通过：390 continuous-area packets、1,952,655 samples、mean power 0.0234967，平台 6 km、地面斜距 84.5–85.5 km。
- A0/C0/C2/C3 × OFF/ON/TO 共 12 个 Production branches 全部通过；36 张 final-union CFAR map geometry/log 审计通过；9 个 role×period F1/F2 输入组 SHA 闭合；配对 OFF/ON 三期 hashes 闭合。
- 独立 seed 复现 C3 相对 C2 的 TO CSI retention 差约 +7.175 dB、ON 非目标候选减少 29.3%；但共同 ON 机会的 TrackManager ground-speed RMSE 为 C2 1.086 m/s、C3 9.259 m/s，C3 在最后一期把目标候选从 track 6 切换到 track 64。结论是 `CONTINUE` 有界传统方法任务评估，尚未证明总体性能优势。
- Empirical Pfa `NOT_EVALUABLE`；OFF reference 与 H0 同样本，属于 reference-in-sample。单 seed 不支持统计显著性或跨场景 claim；没有 AI 训练。

## 证据导航

| 路径 | 内容 |
|---|---|
| `runner_manifest.json` | 本轮 runner 完整 manifest、命令、环境探测、代码/二进制/配置身份与 case 索引 |
| `case_manifest.json` | 单 case 完整结构、Stage2 与 Production branch 状态、配对关系、输入 hash、gate 和失败/缺项 |
| `runner_compact_manifest.json` | runner 的 compact-evidence machine manifest |
| `r14_gate_summary.json`、`provenance.json` | R14 专用门控汇总与执行时身份摘要 |
| `inputs/r14_runner_config.json` | seed505 的 task-scoped runner config；仅收窄 pilot selection，保留基线 Production/method 配置 |
| `analysis/*.csv` | 冻结 evaluator 的 summary、period metrics、paired input/F1/F2 audit、CSI ROI/tap 摘要、adapter、CFAR union audit、target association、TrackManager strict association；`target_candidate_cell_audit.csv` 将真值匹配候选与 exact final-union map cell/hash 连接 |
| `stage2/` | Stage2 场景 JSON/XML、报告、运行日志与真值 CSV；不含 generated IQ 二进制 |
| `runtime_xml/` | 12 个方法/角色 configure XML 及其 configure log |
| `production/<branch>/` | 12 个 branch manifest、Production runtime XML/configure log/full pipe log、逐期 detection CSV、TrackManager CSV、CSI tap summary/ROI manifest、adapter/reference gamma 与 CFAR geometry/meta |
| `references/Mode-A/` | C2/C3 的 OFF 拟合 reference gamma artifacts |
| `figures/` | seed505 单次对比图 |
| `plot_r14_seed505_metrics.py` | 从 `analysis/*.csv` 重绘图表的脚本 |
| `cleanup_ledger.md` | 清理前后大小、保留与删除类别、理由 |

原始完整 CSI `.npy` 与 final-union CFAR `.f32` 未纳入 compact bundle。其运行时 file hashes、map hashes、CFAR geometry/log 审计、CSI target ROI 样本与 period summary 已保存；目标 cell map 值也在清理前用原 map hash 独立闭合。Stage2/Production 大型 `.bin` 是按固定 seed/config 可复现的派生产物，按 cleanup ledger 删除。机器 manifest 中指向 raw run root 的绝对路径保留为来源身份；清理后该路径不再存在，复现时需重新执行下方命令。

## 执行身份与状态

- 执行源码基线：`095221bbfa458fd5f65bc1d095401cd04445aa9f`；运行时唯一 tracked dirty 文件是 R14 所需的 `scripts/phase_r/evaluate_r13_minimal_baseline_pilot.py` 单场景 CLI 变更，最终纳入本阶段提交。
- R14 config SHA-256：`cffc1bdbae654f2edf85ef7a711b0c90d2da26ad132571a3306c5437292b6dba`。
- Stage2 binary SHA-256：`5dd0ac4e2536b8bcf2508cc53db8a30780eba75f40c025e13ffaa930f8ba7b9b`。
- Production binary SHA-256：`f65a8aa446754cc79b6417f9275b3a01e73eb85098c6a4f9eaea0cb999c38962`。
- 本地运行命令的退出码为 0。runner/case status 为 `completed_with_gaps`，因为通用 residual-closure gate 仍要求本轮未选的 PK/PKR；R14 所需 branches 全通过，R14 evaluator 状态为 `evaluated`。
- Runner 第一次调用因基线 pilot config 未显式包含 seed505 而在选择阶段返回 `selection overrides removed every explicit case`；当时没有创建 output root，也没有执行 Stage2/Production。使用本包中的 R14 专用 config 后才启动正式 pilot。
- R14 状态分类：`IMPLEMENTED`、`EXECUTED`、场景指标 `EVALUABLE`；`PERFORMANCE_VERIFIED=false`。

## 复现入口

从仓库根目录，在本机可用 CUDA 环境执行：

```bash
env USE_COREX=OFF GMTI_CFAR_DUMP_BEAM=50 python3 scripts/run_production_hierarchical_calibration_pilot.py \
  --config docs/research/phase_r/evidence/r14_scc_robust_ddc_seed505/inputs/r14_runner_config.json \
  --mode pilot \
  --output-root outputs/phase_r14_scc_robust_ddc_seed505_20261025 \
  --input-mode local \
  --delay-errors-ns 0 \
  --seeds 505 \
  --target-velocities-mps 6.7 \
  --target-snr-db 30 \
  --method-ids C0,C2,C3 \
  --calibration-modes Mode-A \
  --max-cases 1
```

再把新生成的唯一 `case_manifest.json` 传给单场景 evaluator：

```bash
python3 scripts/phase_r/evaluate_r13_minimal_baseline_pilot.py \
  --normal-case-manifest outputs/phase_r14_scc_robust_ddc_seed505_20261025/cases/base__seed_505__v_6d7__snr_30__delay_0ns/case_manifest.json \
  --normal-scene-label normal_seed505_v6p7 \
  --output-dir outputs/phase_r14_scc_robust_ddc_seed505_20261025/r14_analysis
```

运行完成后使用 `MPLCONFIGDIR=/tmp/mplconfig-pgrcc-r14 python3 docs/research/phase_r/evidence/r14_scc_robust_ddc_seed505/plot_r14_seed505_metrics.py` 重绘图表。该命令会重新生成大体量 raw outputs；请按本包 cleanup ledger 保留审计证据并清理可复现派生产物。
