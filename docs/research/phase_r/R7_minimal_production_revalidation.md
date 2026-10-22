# R7：最小 CUDA Production 复验

日期：2026-10-22
范围：seed101、A0/B/C × OFF/ON/TO；只修复并复验 GO-CFAR，不训练 AI、不跑 seed202/303。

## 最终三周期任务复验（当前权威）

在早期单周期地图数值比较之后，最终 CUDA Production 矩阵对每个 A0/B/C × OFF/ON/TO 条件使用匹配的 period 0/1/2；另执行同 binary 的 A0_ON 重复和诊断关闭控制。9 个配对 case、A0_ON repeat 和 diagnostics-off 共 11 个三周期记录全部为真实 CUDA Production、exit code 0、period 顺序可评估。另有一条较早 focused run，因此最终 manifest 共 12 条记录。B pre-fix 三周期输入由保存的旧 Production 程序回放；A0/C pre-fix 来自 R6 同输入运行。九个 pre/post 配对的三个输入 SHA 与 runtime XML SHA 均一致。

最终矩阵位置和身份：

- 修复后：`outputs/phase_r7_cfar_correctness_20261022/post_fix_matrix_3period_final/experiment_manifest.json`
- 修复前 B：`outputs/phase_r7_cfar_correctness_20261022/pre_fix_matrix_3period_id_only/experiment_manifest.json`
- 逐机会评价：`outputs/phase_r7_cfar_correctness_20261022/task_metrics_final_v4/evaluation_manifest.json`
- 修复前 Production binary SHA-256：`f65a8aa446754cc79b6417f9275b3a01e73eb85098c6a4f9eaea0cb999c38962`
- 最终三周期 Production binary SHA-256：`de3026ed4f1263be4143dcbcd10ccbb5e53b4be357e21906e1bf19ea0ec29d00`；manifest 记录 Git revision `04958a996f6a1449fa3d9f158a4765493c85038d`，构建时工作树有 R7 未提交代码，因此以 binary SHA 确认实际程序。

本地完整 case 目录保留全量 maps、CSI arrays 与日志；Git 内的 [`production_runs_3period/`](evidence/r7_cfar_correctness_20261022/production_runs_3period/) 紧凑审查包另保存 20 个 pre/post/repeat/control case 的真实 runtime XML/log、run manifest、运行时配置快照、per-period CFAR geometry/numerical/axis diagnostics、lineage、CSI summaries/taps、检测表和 TrackManager CSV。该包约 14 MB；大块 IQ、全 CSI NPY 与 CFAR map 不上传，完整路径/身份仍由 manifest 关联。

最终任务结果：A0/C 各 3/3 期有唯一门内 detection，B 为 0/3；A0/C 各 2/3 期进一步关联到 confirmed matched-this-frame track，B 为 0/3。pre-fix 已有同样的 detection 候选格局，说明 CFAR 数值修复减少了大量 H0/非目标候选，但没有改变这组场景的目标门内 detection 数。完整三周期逐期参数和 lineage 解释见 [H0/目标关联审计](R7_H0_detection_association_audit.md)。

A0_ON 与 A0_ON_repeat 使用相同 Release binary、三份输入和 runtime XML。两次 CFAR/CSI `.f32`、geometry/numerical 和 cluster lineage 结果一致；检测 CSV 忽略运行 ID/输出路径后逐行一致；TrackManager frames/detections/payload 忽略运行 ID 后逐行一致。三周期 repeatability `EVALUABLE`，不外推到其他 seed。Diagnostics-off 运行仍保留 detections 与 TrackManager 输出，且没有生成 diagnostics lineage sidecar。

### 三周期 CFAR 地图数值闭合

地图文件按 Production result ID 保存为 `GMTI01/02/03`；与 manifest 中 period ID `0/1/2` 的对应关系已逐 case 核实，并用输入 SHA、runtime XML/配置身份及每 period geometry 的 hit count 交叉校验。先前将文件视为每 case 仅一张 map 的判断有误，现已用全部三周期真实保存的 power/threshold/hit `.f32` 地图重算，不需重新运行 CUDA。

完整 27 条 case-period 记录和聚合结果见[三周期 CFAR 数值 JSON/CSV](evidence/r7_cfar_correctness_20261022/analysis/r7_three_period/)。每个模式 9 张 union map、每张 `527,280` valid CUT，共 `4,745,520` CUT；三种模式合计 `14,236,560` union CUT。post branch threshold 在 `28,473,120` 个 branch-CUT、post union threshold 在 `14,236,560` 个 union-CUT 上均精确等于 float64 GO 参考转 float32；所有 post branch/union hit flips 均为 0。旧 union map 相对 reference 共多 `695,232` 个 hit、少 `1,431` 个 hit；按 OFF/ON/TO 的分项数值、旧门限正负/零计数和 branch-map 覆盖范围见[数值根因报告](R7_CUDA_CFAR_numerical_root_cause.md#最终三周期-production-数值复验)。pre-fix B 没有独立分支地图，所以只做 union 级比较；A0/C branch-specific pre maps 可逐 branch 核对。以上是数值与 CUT 决策正确性，不是统计 Pfa 或跨 seed 性能结论。

当前工作区复现入口（不覆盖已提交审查证据）：

```bash
python3 scripts/phase_r/analyze_r7_three_period_numerics.py \
  --output-dir /tmp/r7_three_period_recheck
```

矩阵前 GPU：RTX 3050 Laptop，P0、1500/6001 MHz、20.04 W、55 °C、86% 利用率、1391/4096 MiB 显存。运行时间仅是受设备负载影响的 wall-time 记录；性能 `NOT_EVALUABLE`。double accumulation 与 diagnostics 的约 4.6 MiB/map 是按尺寸估算的显存增量，不是峰值实测。R7 五份报告、compact evidence 与本地完整输出目录互相区分；本阶段到此停止。

## 早期单周期数值复验（保留为历史范围）

以下配对设计、binary `3f58a553...`、单周期任务观察及 wall-time 记录属于早期单周期数值验证，不是上方最终三周期任务评价的替代。其数值结论只覆盖所保存的单周期地图。

## 配对设计与身份

| 条件 | 处理 | OFF 输入 SHA-256 | ON 输入 SHA-256 | TO 输入 SHA-256 |
|---|---|---|---|---|
| A0 | 0 ns，Production Current | `353901c637fd2b03c3f41ebbaab4992a9bd5a7f6c29d2ee2e684cce17d988f2a` | `ac124e51df49292a7dcd1b00b43f8d7e701a2a50c461ab10ddf7bf6223d609fc` | `3f8603aa4423d73129afab34f34913c812eee213db4476f98a212e435f30ac1f` |
| B | +4 ns，Production Current | `64b39889e237ccb5f70538392f573da6a673d878e93b9863aeb30b2b84665be6` | `a0781e0cb2fef4fe30edf5d3af1dd5862a3a5291f3e57b16a1d493362c66abd7` | `db16a9ea63dcb50c982640cd4c36a1f98b478cfe5e300e3d1e76e9d8aaa7ddba` |
| C | +4 ns，Known Physical Correction | `0ba7c2cd03f13bfc7854b2ab327e630350258890a5ba19cd5d03429940c37aa5` | `77052787dc401d6cbe156bd0aadf0ea44603dded3d2124ae2a693a3c1659d32d` | `fcc69c9baa188cb2eeea3ac4e433ace6ed2d3e258f038f4cc18f1e243f20609a9` |

每个 pre/post 配对的 Stage2 input SHA 和 runtime XML SHA 均一致。Pre-fix Production binary SHA-256：`f65a8aa446754cc79b6417f9275b3a01e73eb85098c6a4f9eaea0cb999c38962`；post-fix Release binary SHA-256：`3f58a55330b4afec75e595ce3c6238d5471bbb4f7cda119605c53b4fc141c040`。两个 manifest 记录的 Git revision 都是 `f50ce8a1ceb5ecbe15e903fd4eb946d4b1c1af86`；修复源在当时为未提交工作树内容，因此以 binary SHA 区分程序身份。

该早期 Production manifest 包含 A0_ON 首次输出、重复运行和 diagnostics-off 控制；总计 11 个修复后记录。首次 A0_ON 的旧 `run_manifest.json` 没有记录 executable SHA，故早期矩阵将其标记为 `NOT_RECORDED_IN_RUN_MANIFEST`；该次不能作为同 binary repeatability 证据。最终三周期重复性已在上文由同 binary hash 的两次执行另行验证。

## 处理链与采集

记录实际 runtime XML、命令日志、逐 beam stage、CSI tap、CFAR branch power/threshold/hits、CFAR geometry/numerical fallback、TrackManager debug 和 detections。输入来自现存 R6 seed101 Stage2 数据或 Known Correction 产物；回放不重新模拟、不改输入，不复用覆盖旧输出。核心目录：

- `outputs/phase_r7_cfar_correctness_20261022/pre_fix_matrix/experiment_manifest.json`
- `outputs/phase_r7_cfar_correctness_20261022/post_fix_matrix/experiment_manifest.json`
- `outputs/phase_r7_cfar_correctness_20261022/numeric_analysis/numeric_comparison.json`
- `outputs/phase_r7_cfar_correctness_20261022/numeric_analysis_r6/historical_r6_comparison.json`

可提交的精简审查包位于 [`evidence/r7_cfar_correctness_20261022/`](evidence/r7_cfar_correctness_20261022/)，保存两份原始 run manifest、六份 runtime XML、九份 CSI summary 及 R7/R6 数值 JSON/CSV；raw IQ、全量 map 与 log 仍只留在本地输出树。

生产命令模板由 manifest 原样记录，形如：

```text
build_r7/GMTI_pipe_core --config <runtime.xml> --result-dir <case>/result \
  --track-debug-dir <case>/track_debug --track-debug-dump=on \
  --runtime-mode=debug --runtime-diagnostics=<on|off> \
  --local-test <period_0000.bin>
```

配对矩阵入口：`python3 scripts/phase_r/run_r7_minimal_production_matrix.py [binary_path]`；历史旧 binary 补跑分别由 `run_r7_prefix_b_replays.py` 与 `run_r7_pre_fix_a0c_replays.py` 驱动。脚本遇到已存在目标输出目录会 fail closed，不覆盖；正式复现需先选新的输出根并在脚本配置中明确指定，不能删除现有审计现场来复跑。

## 主要结果

- 修复前九组共 4,745,520 个 valid CUT 中，176,994 个门限为错误非正值；相对 branch-specific float64 reference 有 312,779 个额外 hit 与 215 个漏 hit。
- 修复后同样九组的阈值输出与 float64 reference 转 float32 完全一致；branch hit-map flips 为 0。
- 修复后 H0/OFF 仍有 11、12、11 条 raw detection；一周期运行均只有 tentative tracks、无 confirmed track。它们不是 CFAR 数值修复失败的证据，也不能用作统计 Pfa。
- A0/C ON 各有一个 target-gated raw detection；B ON 没有 target-gated detection，但有 15 个 hit cells 落在目标局部窗口。缺 cluster lineage，无法定位 B 的 loss stage；无 confirmed track，目标任务性能尚未完成闭环。
- A0 ON 首次/重复运行的关键输出 bitwise 一致；首次 run 的 binary identity 未记录，因此只报告输出相同，不声称同一 executable 的 repeatability。这不替代不同 seed 的统计验证。

设备矩阵前状态：NVIDIA GeForce RTX 3050 Laptop GPU，P0，1440 MHz core、6001 MHz memory、24.55 W、58 °C、100% 利用率、1813/4096 MiB 显存。该设备明显忙碌，wall time 只作运行记录，不作性能结论。

## 复现分析

```bash
python3 -m pytest -q tests/test_phase_r7_cfar_numerics.py tests/test_phase_r7_cfar_reference.py
python3 scripts/phase_r/analyze_r7_cfar_numerics.py
python3 scripts/phase_r/analyze_r7_cfar_numerics.py --legacy-r6-only
```

两份 analysis 命令拒绝覆盖已存在目标目录。原始 CUDA run trees 当前保留在本机 `outputs/phase_r7_cfar_correctness_20261022/`，体积较大的 IQ/map/log 不纳入 Git；应通过 manifest 中 input/config/binary SHA 与本地证据核对。
