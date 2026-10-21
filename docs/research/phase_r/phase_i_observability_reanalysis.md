# Phase-I 局部敏感度复核：圆周差分、FFT 格点与 seed 依赖

复核日期：2026-10-21。本文补充 Phase R2/R3；使用已保存的派生观测，不重新生成 IQ，不运行新的研究仿真或 CUDA production。历史报告、manifest 和矩阵保持原样，以下修正适用于后续引用。

## 结论

旧 Phase-I 的圆周相位差分存在表示跨界错误。修复后，逐元素 seed 中位数矩阵仍为数值 rank 6，但条件数由 `37.32835` 改为 `3.99259`，delay–servo 灵敏度列余弦由 `−0.91995` 改为 `−0.06815`。旧的“delay–servo 近混淆”不能继续作为当前证据。三个 seed 的条件数仍相差明显，且矩阵未使用观测噪声协方差；这次修正不证明稳定估计能力、物理可辨识性或 AI 必要性。

## 输入、身份与复现

- 历史输入是 2026-09-16 的 6 状态 × 3 seed × 正/零/负扰动，共 54 个 case。原记录源码为 `f1454304ef514a43ce4e562a82dee0c83cee4e36`、dirty=true，不能视作该提交的 clean-source 实验。
- [source_manifest.json](evidence/phase_i_observability_reanalysis_20261021/source_manifest.json) 是原 manifest 的逐字节副本，包含全部 54 组观测、扰动、seed、配置/原始输入哈希和历史源码状态。SHA-256 为 `c400b68553398605bef34876f64ab0edc5587efb9ddc18c7e231af5ec5c06c1c`。本次逐一核对，其内嵌观测与本地 54 份 `observables.json` 完全相同。
- [summary.json](evidence/phase_i_observability_reanalysis_20261021/summary.json) 保存所有逐 seed/汇总矩阵、奇异值、缩放系数、列余弦、跨界记录和 FFT 特征变化；同时记录复算脚本 SHA-256、Python/NumPy 版本。
- 历史模板 [`unknown_system_error_true_geometry_pilot.json`](../../../configs/research/unknown_system_error_true_geometry_pilot.json) 的当前 SHA-256 与原 manifest 中的 `87674afe…` 相同。54 组原始 IQ 不在此证据快照内，因此本快照能复算“观测到矩阵”，不能独立重验“IQ 到观测”。

在仓库根目录运行，输出使用新临时目录：

```bash
phase_r_replay=$(mktemp -d /tmp/pgrcc-phase-r-replay.XXXXXX)
python3 scripts/phase_r/reanalyze_observability.py \
  --manifest docs/research/phase_r/evidence/phase_i_observability_reanalysis_20261021/source_manifest.json \
  --output-dir "$phase_r_replay/result"
```

脚本检查状态、seed、正/零/负条件、步长、有限值与重复/缺失 case；输入不完整则失败，不用零补矩阵。已有输出目录也拒绝覆盖。

本次分析从 `a219465` 基线加入修复，精确源码身份见结果内两份脚本的哈希。实际验证：先观察到 7 项角度回归失败，修复后定向测试 27/27 通过；完整 `python3 -m pytest -q` 为 **408 passed、18 skipped**，相关 Python 编译检查与 `git diff --check` 通过。用发布用 manifest 副本在独立临时目录重放，矩阵、跨界/FFT 记录和零对照数值完全一致。上述验证覆盖离线计算，不新增 CUDA production 证据。

## 圆周差分错误及修复

旧实现直接使用 `(y(+h)−y(−h))/(2h)`。对于显式包裹的角度，现在使用 `arg(exp(j·(y(+h)−y(−h))))/(2h)` 的等价模运算。适用字段只有 `cross_channel_phase_rad`、`p38_intercept_residual_rad`、`ctdr_residual_rad`；斜率、非负相位 RMS、Hz、coherence 和 dB 仍取实数差分。零误差对照也采用相同的角度语义。源码见 [`central_difference_sensitivity`](../../../scripts/audit_two_channel_error_observability.py) 及其回归测试。

实际 9 次跨界均来自 `cross_channel_phase_rad`：delay、inter-pulse phase、yaw 各跨 3 个 seed。以 delay/seed101 为例，`h=0.5 ns`，正负观测为 `−3.1390131152` 与 `+3.1232454151 rad`；旧差分为 `−6.2622585303 rad/ns`，短弧差分为 `+0.0209267769 rad/ns`。差异来自 ±π 的表示切口。短弧修正本身仍假设两点之间没有未观测到的多周绕转；仅有一个扰动尺度不能证明导数收敛。

## 矩阵比较

每个 seed 先计算 13×6 有限差分矩阵，再逐元素取 seed 中位数，最后做观测行 L2 平衡和状态列 L2 归一化。“汇总矩阵”未必对应任何一次实际杂波实现。去除 FFT 特征时，同时排除 ridge 频率与其随角度斜率。步长缩放变体在平衡前将每列乘以其注册的 `h`，表示相对于该扰动尺度的变化。

| 差分/特征条件 | 汇总条件数 | seed101 | seed202 | seed303 | 汇总 delay–servo 余弦 |
|---|---:|---:|---:|---:|---:|
| 旧实数差分，全部 13 特征 | 37.32835 | 82.60892 | 49.16095 | 15.38795 | −0.91995 |
| 圆周差分，全部 13 特征 | 3.99259 | 69.75059 | 10.10735 | 3.39770 | −0.06815 |
| 圆周差分，去除 2 个 FFT argmax 特征 | 3.99735 | 70.45089 | 10.62651 | 4.72900 | −0.06815 |
| 同上，先按注册步长缩放状态列 | 7.93910 | 68.04534 | 6.08701 | 9.40557 | −0.21317 |

表中 16 个矩阵数值 rank 均为 6；阈值为 NumPy 浮点/SVD 数值尺度。行/列平衡不是噪声白化，也不是 FIM，条件数依赖参数尺度和特征权重。去除 FFT 后仍有精确派生关系：`slow_time_phase_slope` 与 `phase_vs_pulse_slope` 相同，`p38_slope_residual_hz` 为该斜率乘 `PRF/(2π)`；三行不代表三份独立信息。原观测 coherence 仅为 `0.05135–0.12101`，相位有限差分的统计稳定性尚需独立验证。零误差条件的同 seed 重复对照仍为 delta=0。

## FFT ridge 跳变的实际规模

提取代码对所有 slow-time 行做 FFT，取最大幅度所在频率格点；按 beam 分组的 ridge-angle 特征也使用 argmax（[`compute_f1_f2_observables`](../../../scripts/audit_two_channel_error_observability.py)）。这类有限差分可能由峰值切换主导，不能直接作为平滑局部导数。

平台速度扰动为 `±0.05 m/s`。seed101/202 的正负 ridge 都为 `469.44444 Hz`；seed303 则由负扰动的 `469.44444 Hz` 变为正扰动的 `−72.22222 Hz`，有限差分为 `−5416.66667 Hz/(m/s)`。原 manifest 记录每 case 36 行，对应模板 PRF `1300 Hz`、9 beam × 4 pulses；格点间隔为 `1300/36=36.11111 Hz`，两峰相距 **15 个格点**。先前审查提示词中的“单个 bin”推测应撤回。该 FFT 还拼接了不同 beam 的数据，不能直接解释为固定指向、平稳杂波的 Doppler 估计。

## 对研究判断的影响

1. 原历史 `rank=6/condition≈37.33/delay–servo≈−0.920` 保留为旧算法计算记录；其中条件数和混淆结论不得继续当作当前物理可观测性证据。
2. 当前复算明确区分跨界、离散特征、seed 与尺度影响。修正后的低汇总条件数不能替代噪声下误差估计试验，也不能证明六种物理状态可同时辨识。
3. 这些观测来自脚本对原始协议 C1/C3、C2/C4 的简单均值与频域处理，不包含生产解码器的完整几何相位补偿或后续 CSI 预处理。其结论只适用于该代理观测域，不能直接继承到 Production Current。
4. 没有新增未知误差生产损失、已知误差恢复上限、盲估计误差或 Pd/Pfa/航迹结果。AI 必要性仍为 `NECESSITY_NOT_ESTABLISHED`。R4 注册设计与新研究运行继续等待用户约定的外部审查方向。
