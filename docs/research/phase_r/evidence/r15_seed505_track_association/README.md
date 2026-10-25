# R15 seed505 航迹关联离线反事实证据包

本包保存解释 R14 C2/C3 末期 track switch 所需的最小证据。R14 Native CUDA Production 记录是输入；R15 的 Greedy/Hungarian 反事实只通过现有 `TrackManager` 对固定 detections 离线运行，不是新的 CUDA Production 实验。

## 内容

| 路径 | 说明 |
|---|---|
| `replay_summary.csv` | Greedy/Hungarian 的 target D0 track lineage、末期地速、误差、confirmed output 数，以及与原生产关联逐条比较结果。 |
| `period2_candidate_costs.csv` | p2 D0/D3 附近各方法、关联模式的低代价有效候选边及所有实际分配边。 |
| `provenance.json` | 基线 commit、源码 blob/hash、输入 CSV hash/size、回放设置和执行分类。 |
| `replay/track_detection_eval_r15.cpp` | 本次离线 replay 使用的 evaluator wrapper；相对仓库原 CLI 只提高诊断级别并设置 `track_v_max=50 m/s` 以匹配 R14 XML。 |
| `replay/prepare_r15_inputs.py` | 从 R14 `track_detections.csv` / `track_frames.csv` 生成 replay 输入；不读取任何 truth 字段。 |

完整生产事件、状态、检测和接受边仍在 [R14 C2 TrackManager records](../r14_scc_robust_ddc_seed505/production/C2_modea_ON/tracks/) 与 [R14 C3 TrackManager records](../r14_scc_robust_ddc_seed505/production/C3_modea_ON/tracks/)；生产 XML 与日志也留在各自 branch 目录。

## 身份与结论

- R15 基线 commit：`2b1942453b002045aaba4c1e35a8b93e75d0c38b`。
- `src/TrackManager.cpp` 的 Git blob 与 R14 执行基线 `095221bbfa458fd5f65bc1d095401cd04445aa9f` 相同；R15 没有更改跟踪实现。
- 固定输入 Greedy 回放与生产 `track_detections.csv` 的检测到 track 映射完全相同：C2 `990/990`、C3 `701/701`。
- Hungarian 与 Production Greedy 的 detection-track edges 分别改变 C2 `582/990`、C3 `483/701`。它降低了 C3 p2 速度误差，但使 C2 误差增大；不能据此判定为通用修复。
- R15 实际执行状态：offline CPU replay 为 `EXECUTED`，本 seed 关联分析为 `EVALUABLE`，总体/跨场景 `PERFORMANCE_VERIFIED=false`。R15 未运行 GPU。

## 离线复现

从仓库根目录执行。先构建仓库已有的 CPU TrackManager evaluator target，再以证据包中的 wrapper 替换它的 CLI object；TrackManager 和其余链接对象来自当前基线 build。

```bash
cmake --build build --target track_detection_eval -j4
mkdir -p /tmp/pgrcc_r15_replay
c++ -DNDEBUG -Iinclude -Iinclude/xml -Iauth/shared/hardware -Iauth/shared/crypto -Iauth/hardware_bind_flow/core -Iauth/hardware_bind_flow/gate -O3 -std=gnu++11 -fPIE -Wall -Wextra -Wpedantic -c docs/research/phase_r/evidence/r15_seed505_track_association/replay/track_detection_eval_r15.cpp -o /tmp/pgrcc_r15_replay/track_detection_eval_r15.o
c++ -O3 -DNDEBUG /tmp/pgrcc_r15_replay/track_detection_eval_r15.o build/CMakeFiles/track_detection_eval.dir/src/TrackManager.cpp.o build/CMakeFiles/track_detection_eval.dir/src/runtime_diagnostics.cpp.o build/CMakeFiles/track_detection_eval.dir/src/trig_lut.cpp.o build/CMakeFiles/track_detection_eval.dir/src/trig_lut_host_only.cpp.o -o /tmp/pgrcc_r15_replay/track_detection_eval_r15 -lm -lrt -ldl /usr/lib/x86_64-linux-gnu/libfftw3.so
python3 docs/research/phase_r/evidence/r15_seed505_track_association/replay/prepare_r15_inputs.py --r14-root docs/research/phase_r/evidence/r14_scc_robust_ddc_seed505 --output-root /tmp/pgrcc_r15_replay/inputs
```

对 C2/C3 × nearest/hungarian 各执行一次；`--output-dir` 分别设置为 `/tmp/pgrcc_r15_replay/<method>_<assignment>`：

```bash
/tmp/pgrcc_r15_replay/track_detection_eval_r15 --input /tmp/pgrcc_r15_replay/inputs/C2_input.csv --output-dir /tmp/pgrcc_r15_replay/C2_nearest --distance-mode mahalanobis --assignment-mode nearest --output-source measurement --confirm-window 3 --confirm-hits 2 --max-missed 2 --tentative-max-missed 1 --gate-m 300 --chi2-gate 9.21 --dummy-cost 1.5 --measurement-noise-m 50 --process-noise-pos 25 --process-noise-vel 10 --use-speed-cost 1 --speed-weight 0.3 --use-heading-cost 1 --heading-weight 0.5 --min-linearity 0.65
/tmp/pgrcc_r15_replay/track_detection_eval_r15 --input /tmp/pgrcc_r15_replay/inputs/C2_input.csv --output-dir /tmp/pgrcc_r15_replay/C2_hungarian --distance-mode mahalanobis --assignment-mode hungarian --output-source measurement --confirm-window 3 --confirm-hits 2 --max-missed 2 --tentative-max-missed 1 --gate-m 300 --chi2-gate 9.21 --dummy-cost 1.5 --measurement-noise-m 50 --process-noise-pos 25 --process-noise-vel 10 --use-speed-cost 1 --speed-weight 0.3 --use-heading-cost 1 --heading-weight 0.5 --min-linearity 0.65
/tmp/pgrcc_r15_replay/track_detection_eval_r15 --input /tmp/pgrcc_r15_replay/inputs/C3_input.csv --output-dir /tmp/pgrcc_r15_replay/C3_nearest --distance-mode mahalanobis --assignment-mode nearest --output-source measurement --confirm-window 3 --confirm-hits 2 --max-missed 2 --tentative-max-missed 1 --gate-m 300 --chi2-gate 9.21 --dummy-cost 1.5 --measurement-noise-m 50 --process-noise-pos 25 --process-noise-vel 10 --use-speed-cost 1 --speed-weight 0.3 --use-heading-cost 1 --heading-weight 0.5 --min-linearity 0.65
/tmp/pgrcc_r15_replay/track_detection_eval_r15 --input /tmp/pgrcc_r15_replay/inputs/C3_input.csv --output-dir /tmp/pgrcc_r15_replay/C3_hungarian --distance-mode mahalanobis --assignment-mode hungarian --output-source measurement --confirm-window 3 --confirm-hits 2 --max-missed 2 --tentative-max-missed 1 --gate-m 300 --chi2-gate 9.21 --dummy-cost 1.5 --measurement-noise-m 50 --process-noise-pos 25 --process-noise-vel 10 --use-speed-cost 1 --speed-weight 0.3 --use-heading-cost 1 --heading-weight 0.5 --min-linearity 0.65
```

`track_v_max=50 m/s` 是 wrapper 中固定设置，用来匹配两个 R14 ON XML；不要依赖 evaluator 默认值 100。wrapper 的逐轨迹详细诊断会产生较大的 CSV。复现完候选摘录后应删除本次明确生成的临时目录：

```bash
rm -rf /tmp/pgrcc_r15_replay
```

本次执行的完整临时结果曾位于 `/tmp/pgrcc_r15_track_counterfactual`；交付前已在保留上述紧凑 CSV、回放源码和 provenance 后删除（约 2.4 GiB）。没有删除仓库内的 R14 证据或其他 outputs。
