# R12 seed404 Native CUDA evidence package

这是 R12 seed404 的可审查小包。完整本地运行根目录：
`outputs/phase_r_r12_physics_matched_delay_20261024/`。

## 阅读顺序

1. [Pilot summary](pilot_summary.json)：状态标签、运行身份、GPU、面积杂波门、Known closure、M1 输入和估计、处理链 fingerprint、分支 gate 汇总。
2. [Condition metrics](condition_metrics.csv)：五条件的检测、参数误差、H0 描述计数、CSI active-support 残余、目标传递、SCNR proxy 和 F2 输入闭合。
3. [Blind estimates](blind_estimates.csv)：D1/D2/D3/M1 同输入盲估计与 R9 固定校正的 evaluator-only 误差。
4. [Task metrics](task_metrics.csv)：ON/OFF/TO 的冻结 R7/R8 task matcher 汇总。
5. [Summary figure](r12_seed404_summary.png)：单 seed 描述图；生成脚本为 `scripts/phase_r/plot_r12_results.py`。
6. [Artifact index](artifact_index.json)：每个归档文件的原始输出路径、字节数和 SHA-256。
7. [Run manifest](source_manifests/run_manifest.json) 与 [case manifest](source_manifests/case_manifest.json)：完整机器记录和配对/输入/生产 gate 来源。
8. `production/<condition>/<role>/`：每个 15 个 CUDA branch 的 runtime XML、configure log、生产 log、runtime config dump、branch manifest、CSI tap summaries。
9. `evaluation/<condition>/<role>/`：task/association/lineage、CFAR geometry、TO response、detection CSV、TrackManager detections/frames/payload。
10. `stage2_scene_evidence/`：仿真配置、场景和杂波报告、truth/impairment CSV/JSON；不含 Stage2 IQ。

## 证据身份与界限

- 基线提交：`3e23235180f59fbb09b890af000544ae7a7c9fd0`。
- 生产二进制：`build_r7/GMTI_pipe_core`，SHA-256 `de3026ed4f1263be4143dcbcd10ccbb5e53b4be357e21906e1bf19ea0ec29d00`，CMake `USE_COREX=OFF`，Native CUDA。
- 15/15 Production condition-role 分支 gate 通过，处理链配置 fingerprint 一致；面积杂波注入 gate 通过。
- `IMPLEMENTED=true`、`EXECUTED=true`、`EVALUABLE=true`；`PERFORMANCE_VERIFIED=false`。唯一 union CFAR hit-cell 和统计 Pd/Pfa 不可评估。
- 所有原始 `.bin` IQ 和全图 CSI `.npy` 留在忽略目录 `outputs/phase_r_r12_physics_matched_delay_20261024/`，没有复制到这个紧凑包。原始文件路径、大小、SHA 记在 source manifests 中；它们仍在当前工作机，供本地复算。

## 复现

```bash
python3 scripts/phase_r/run_r12_physics_matched_delay.py \
  --output-root outputs/phase_r_r12_physics_matched_delay_20261024_rerun \
  --seed 404 --production-binary build_r7/GMTI_pipe_core
```

绘图复现：

```bash
MPLCONFIGDIR=/tmp/mplconfig_r12 python3 scripts/phase_r/plot_r12_results.py \
  --manifest outputs/phase_r_r12_physics_matched_delay_20261024/manifest.json \
  --output /tmp/r12_seed404_summary.png
```
