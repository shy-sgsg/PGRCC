# Model-Mismatch Challenge 与机制 Oracle 报告

日期：2026-09-10。全程未训练 AI。所有 challenge 的 zero/static 档都与独立 Current baseline 做 raw BIN SHA 回归。formal challenge 的实验代码提交为 `09bbd624c063126a97ea02196394ea62d3b4c4a6`；最终仓库 `0b6f7e7` 只包含测试路径修复，不改变 C++/CUDA 或实验 runner。

## 运行范围

base case 为 4-channel float32 raw LFM、128 pulses、4096 range crop、1 beam、1 period、continuous texture、target SNR 0 dB。每个 family 使用同一 seed，仅改变一个明确物理字段；首轮及两个新增独立 seed 共 3 个 seed、36 条 Current 记录，结果目录分别为：

- [`M1 / seed 2026091011`](../outputs/ai_csi_model_mismatch_formal_m1_seed2026091011)
- [`M2 / seed 2026091011`](../outputs/ai_csi_model_mismatch_formal_m2_seed2026091011)
- [`M3 / seed 2026091011`](../outputs/ai_csi_model_mismatch_formal_m3_seed2026091011)
- [`seed 2026091012`](../outputs/ai_csi_model_mismatch_formal_seed2026091012)
- [`seed 2026091013`](../outputs/ai_csi_model_mismatch_formal_seed2026091013)

formal failure map：[`failure_map.csv`](../outputs/ai_csi_model_mismatch_formal_clean/failure_map.csv)；3-seed 汇总：[`failure_map_multiseed.csv`](../outputs/ai_csi_model_mismatch_multiseed_formal_clean/failure_map_multiseed.csv)。Pd 已由生产 detection snapshot 与同周期 visible truth 按仓库现有一对一 matcher 离线计算；本轮 36 条正样本记录合计 `29/36=0.8056`。随后对 36 个正例变体以生产 debug diagnostics 模式补跑，实际导出 P38 raw/refit inlier ratio 和 candidate CFAR margin；诊断汇总见 [`production_diagnostic_metrics.csv`](../outputs/ai_csi_model_mismatch_p38_cfar_diagnostics_formal_clean/production_diagnostic_metrics.csv)，36/36 通过。三 seed 诊断覆盖的 P38 inlier ratio 为 `0.9744–1.0000`、CFAR margin 为 `0.168–11.834 dB`；seed 2026091011 的 failure map 子集 margin 为 `0.407–11.834 dB`。`CFAR_selected` 仍只是选中 cluster 数，不能冒充 Pfa。

本轮随后补跑了 36 个 variant 的成对生产控制，共 72 个 CUDA control：`target_only` 使用
`scene.signal_only=true` 保留同一目标注入振幅，用于目标 ROI 功率保持；
`negative_control` 禁用全部目标，用于 empirical CFAR-cell Pfa。控制汇总见
formal clean [`manifest.json`](../outputs/ai_csi_model_mismatch_metric_controls_formal_clean/manifest.json)，
failure map 的 Pfa/target loss 字段均已由这些控制回填。Pfa 定义为生产 CUDA
`hit_cells / valid_cfar_test_cells`，当前 dynamic 配置的分母为
`128 × (4096 - 2 × (4 + 16)) = 519168`；配置 `pf=1e-6` 单独保留，未替代实测值。

P38/CFAR 诊断字段的来源和覆盖数记录在 formal
[`failure_map_manifest.json`](../outputs/ai_csi_model_mismatch_formal_clean/failure_map_manifest.json)；
每个诊断变体还保留独立的 `diagnostic_summary.json`、生产 CSV 和 `gmticore.log`，不覆盖
原始 challenge/control 目录。

36 条控制的 empirical Pfa 范围为 `0.003704–0.004960`，target-only target loss
范围为 `-39.059–-9.542 dB`。zero/static 基线自身约为 `-35–-36 dB`，说明该
compact 目标的 Doppler 落在 dynamic CSI band 时存在共同的目标保护问题；因此
target loss 不能单独当作 M1/M2/M3 失配严重度，必须与正例 Pd、负控制 Pfa、CSI
对消和机制 Oracle 一起解读。

## M1：fractional channel delay

配置 `channel_time_delay_ns = 0/2.0833/4.1667/8.3333 ns`，对应 truth effective shift `0/0.125/0.25/0.5 samples`。zero raw SHA 与 Current baseline 一致；强档 measured raw cross-spectrum slope 为 `5.349e-8 rad/Hz`，理论 `5.236e-8 rad/Hz`。

| delay | Current rho | phase RMSE | CSI cancellation | CFAR selected |
|---:|---:|---:|---:|---:|
| 0 ns | 0.9873 | 0.1785 | 25.29 dB | 64 |
| 2.083 ns | 0.9831 | 0.2038 | 24.31 dB | 61 |
| 4.167 ns | 0.9685 | 0.2795 | 22.12 dB | 52 |
| 8.333 ns | 0.8676 | 0.7510 | 13.37 dB | 30 |

已知 delay 在 raw channel-2 上做 inverse fractional shift 后重跑生产 GMTI：强档 cancellation 恢复 `6.97 dB`，phase RMSE 从 `0.2412` 降到 `0.1030 rad`。这说明 M1 有明确可恢复的 calibration-model headroom。

## M2：pulse-varying differential phase

配置 `per_pulse_phase_drift_deg = 0/0.05/0.2/0.5 deg/pulse`。`channel_impairment_truth.csv` 的 truth slope 与配置一致；强档 slow-time residual 线性拟合 `R²=0.9998`，但当前 row coherence 仍高于 gate threshold，因此 gate 不会替代 drift model。

| drift | Current rho | phase RMSE | CSI cancellation | CFAR selected |
|---:|---:|---:|---:|---:|
| 0 | 0.9873 | 0.1785 | 25.29 dB | 64 |
| 0.05 | 0.9884 | 0.1783 | 25.27 dB | 59 |
| 0.2 | 0.9870 | 0.1915 | 24.50 dB | 55 |
| 0.5 | 0.9736 | 0.2789 | 21.41 dB | 45 |

已知 phase trajectory 在 raw channel-2 上逆旋转后重跑，强档 cancellation 恢复 `3.88 dB`，phase RMSE 从 `0.0904` 降到 `0.0561 rad`。M2 属于可观测、机制明确的 Type-I calibration-model failure。

## M3：internal clutter motion / temporal decorrelation

配置 `temporal_correlation_rho = 1/0.99/0.95/0.8`，且在 surface cell slow-time 层实现 AR(1)。rho=1 raw SHA 与 Current baseline 一致。

| rho | Current rho median | gate bypass | phase RMSE | CSI cancellation | CFAR selected |
|---:|---:|---:|---:|---:|---:|
| 1 | 0.9873 | 0 | 0.1785 | 25.29 dB | 64 |
| 0.99 | 0.9791 | 0 | 0.3426 | 17.52 dB | 10 |
| 0.95 | 0.9470 | 0 | 0.4681 | 11.82 dB | 3 |
| 0.8 | 0.8992 | 0 | 0.5996 | 8.30 dB | 5 |

逐 row complex-LS perfect-phase 上界在 rho=0.8 只增加 `0.27 dB` cancellation；它没有提高真实 coherence。这个有限 headroom 与 Type-II decorrelation failure 一致：知道相位不能恢复已经变化的 clutter realization。

## Pd / Pfa / target loss failure map

3-seed 汇总的强档结果如下；完整 12 行 × 3 seed 记录保存在
[`failure_map_multiseed.csv`](../outputs/ai_csi_model_mismatch_multiseed_formal_clean/failure_map_multiseed.csv)。

| family / level | Pd mean | empirical Pfa mean | target loss mean (dB) | 解释 |
|---|---:|---:|---:|---|
| M1 strong | 1.000 | 0.003890 | -9.623 | fractional delay 使残余增大，目标保持数值较高并不等于对消正确 |
| M2 strong | 1.000 | 0.004030 | -23.554 | pulse drift 仍可检测，但目标 ROI 已显著衰减 |
| M3 strong_motion | 0.000 | 0.004403 | -34.354 | temporal decorrelation 造成正例漏检，Oracle 仅有有限恢复空间 |
| zero/static baseline | 1.000 | 0.004584 | -36.231 | 共同 baseline target-protection limitation，不归因于 mismatch |

Pfa 是无目标、保留杂波/噪声的 production negative control 下的 cell-level
虚警率，不是 H0 纯热噪声 A/B 的 21/0 hit 计数；Pd 是正例 production
detection snapshot 与 visible truth 的一对一匹配；target loss 是 target-only
production map 的 `10log10(after / before)`，负值表示目标功率下降。

### 3-seed Oracle 稳定性

对三个 seed 的强档重新运行机制 Oracle 后，M1 cancellation 恢复为
`6.749±0.188 dB`，M2 为 `3.716±0.209 dB`；M3 row-wise 上界只有
`0.246±0.028 dB`，且 `delta_coherence=0`。这支持“前两类是可观测的
calibration-model headroom，M3 主要是 temporal decorrelation”的机制区分。
Oracle 明细见 [`mechanism_oracle_summary_multiseed.csv`](../outputs/ai_csi_model_mismatch_multiseed_formal_clean/mechanism_oracle_summary_multiseed.csv)。

## H0 与三类 failure

纯热噪声 A/B 是独立的 Type-III 统计 failure：gate=false 产生 21 个 split hits，gate=true 75/75 行旁路并得到 0 hits。它的正确处理是 bypass，而不是把噪声交给更强的 phase estimator。

- Type I：delay/drift/geometry 表示误差；存在 mechanism-aware correction headroom。
- Type II：真实 decorrelation；perfect phase 也只剩有限 headroom。
- Type III：H0/低相干统计问题；应采用 gate/fallback。

## 当前 AI Go / No-Go

当前仍 **No-Go**：

1. mismatch=0 regression 已通过；
2. M1/M2/M3 都出现可重复的受控退化；
3. M1/M2 已证明 deterministic mechanism correction 能恢复一部分，M3 的恢复空间很小；
4. 3 个 seed 已确认紧凑场景中的退化方向和 Oracle headroom 稳定；36 条正例与 72 条 paired control 已补齐 Pd/Pfa/target-loss，但它们仍是单目标、单波位、单周期 compact 统计，不是生产场景泛化或正式 release 验收。

因此下一步若继续，不应训练 generic `delta-alpha`。优先方向应分别是：M1 学 `delta_tau`、M2 学 temporal phase trajectory、M3 研究 coherence/statistical fallback；只有在多 seed/多场景仍存在稳定、可观测且 Oracle 有正 headroom 的机制上，才重新评估 Physics-AI。

## Q1–Q8 结论

**Q1：PGRCC 是否完整复现了生产工程已验证的 phase fix 和 H0 gate？**

是。physical-theta beam reuse、`F1*conj(F2)`/`F2*=exp(+j*phi)` 符号、CPU/CUDA
row-level coherence gate 和 persistent workspace 均已迁移；source-equivalent、
14/15/16 beam、pure-noise A/B、coherent A/B 与三周期 CUDA 证据通过。Current
冻结为 phase-corrected + H0 gate 版本。

**Q2：修复后 texture 由什么主导？**

不是单一机制。残余诊断显示 coherent rows 的 phase RMSE `0.3635 rad`、幅度残差
std `6.18 dB`，Doppler 线性拟合 `R²=0.0553`，range-frequency 拟合
`R²=0.2759`（估计 `-0.8926 ns`），slow-time 漂移拟合 `R²=0.00936`；因此
存在 phase/amplitude mixed texture，delay 有提示但不足以判定为唯一主因，未观察到
清晰的 pulse drift 主导证据，低相干/temporal decorrelation 需单独做机制 challenge。

**Q3：rho<0.5 gate 在纯噪声和真实 mismatch 中分别做什么？**

纯噪声中 gate=false 产生 21 个 hits，gate=true 对 75/75 行旁路并得到 0 hits，
它保护 H0 统计路径。M1/M2/M3 当前 tested levels 的 gate bypass fraction 为 0；
特别是 M3 的 rho<1 是共享 clutter realization 的真实时间变化，不应被误判成
`rho<0.5` H0 噪声问题，故 gate 不会替它恢复信息。

**Q4：哪些真实物理机制会破坏 F1≈alpha F2？**

本轮已实际注入并验证三类：channel fractional delay、pulse-varying differential
phase drift、internal clutter motion/temporal decorrelation。审计上还保留 CTDR/P38
模型误差、幅度关系变化、空间/通道 decorrelation 和 heterogeneous clutter 作为
后续边界；它们分别破坏宽带相位关系、CPI 内 stationary alpha、或跨脉冲共同
clutter realization。

**Q5：Current 的 failure boundary 在哪里？**

在当前 compact 1-beam/1-period/0 dB target 场景中，M1 测到 `0/2.083/4.167/8.333 ns`
（最大 `0.5 sample`）时 cancellation 由约 25 dB 降到约 13 dB，但 Pd 仍为 1；
M2 在 `0.5 deg/pulse` 时 cancellation 约 21 dB、Pd 为 1；M3 在
`rho=0.95/0.8` 的 moderate/strong motion 上 Pd 为 0，`rho=0.99` 的 weak motion
三 seed 平均 Pd 仅 `0.667`。paired negative-control Pfa 约 `0.00370–0.00496`，
target-only loss 约 `-39.06–-9.54 dB`；这些是本 compact sweep 的 tested boundary，
不是外推阈值。

**Q6：失效属于哪一类？**

M1/M2 是 Type-I phase/calibration-model error；M3 是 Type-II true temporal
decorrelation；纯噪声 gate A/B 是 Type-III H0 statistical issue。三者的处理策略
分别是补物理表示、识别不可恢复 decorrelation、以及 gate/fallback。

**Q7：机制感知 deterministic Oracle 是否有稳定恢复空间？**

有，但按机制不同：三 seed 强档 Oracle 的 cancellation headroom 为 M1
`+6.749±0.188 dB`、M2 `+3.716±0.209 dB`、M3 `+0.246±0.028 dB`，M3
`delta_coherence=0`。Oracle estimator 不使用 target truth；因此 M1/M2 有稳定的
物理修正空间，M3 没有足够空间支持 generic correction。

**Q8：下一阶段 Physics-AI 最合理学习什么？**

若后续仍获准进入 AI，优先学习 M1 的 fractional delay `delta_tau`/宽带相位斜率、
M2 的 pulse-wise phase trajectory 及其 confidence；输出只作为物理 CSI 的小残差
和门控信号。M3 更适合学习 coherence/decorrelation detector 与 fallback policy，
不适合直接学习一个通用 `delta-alpha`，也不进入端到端 RD 图生成。

本报告对应的 13 个 formal manifest 均记录 `source_commit=09bbd624c063126a97ea02196394ea62d3b4c4a6`、
`worktree_dirty=false`，并保留了临时外置结果 alias 清理前的原始 dirty 观测和清理说明。
最终仓库提交 `0b6f7e7` 只修复了测试中的 `gate_false/gate_true` 路径拼接，`4fcc139` 只更新了进展导航；
C++/CUDA 与实验 runner 未变，因此不改变这些 formal 数值。该结果可以称为 clean-source formal artifact，但不能当作全场景
release 验收。

## 复现入口

正式结果的只读入口是上文列出的 `*_formal_clean` manifest/CSV；下面保留标准目录布局的
fresh rerun 命令。任何长任务前先执行 `nvidia-smi`、`free -h`、`df -h .`，并串行运行
CUDA case，避免在 RTX 3050 上并发造成 OOM。

```bash
python3 scripts/build_model_mismatch_failure_map.py \
  --output-dir outputs/ai_csi_model_mismatch \
  --controls-root outputs/ai_csi_model_mismatch_metric_controls \
  --diagnostics-csv outputs/ai_csi_model_mismatch_p38_cfar_diagnostics/production_diagnostic_metrics.csv

python3 scripts/run_production_diagnostic_audit.py \
  --output-dir outputs/ai_csi_model_mismatch_p38_cfar_diagnostics \
  --build-dir build

python3 scripts/run_model_mismatch_audit.py \
  --family M1_fractional_channel_delay \
  --output-dir outputs/ai_csi_model_mismatch_m1_rerun

python3 scripts/run_mechanism_aware_oracle.py

python3 scripts/run_model_mismatch_metric_controls.py \
  --mode both \
  --output-dir outputs/ai_csi_model_mismatch_metric_controls

python3 scripts/evaluate_model_mismatch_metric_controls.py \
  --root outputs/ai_csi_model_mismatch_metric_controls

python3 scripts/build_model_mismatch_failure_map.py \
  --output-dir outputs/ai_csi_model_mismatch \
  --controls-root outputs/ai_csi_model_mismatch_metric_controls \
  --diagnostics-csv outputs/ai_csi_model_mismatch_p38_cfar_diagnostics/production_diagnostic_metrics.csv

python3 scripts/run_model_mismatch_audit.py \
  --family all --random-seed 2026091012 \
  --output-dir outputs/ai_csi_model_mismatch_seed2026091012

python3 scripts/aggregate_model_mismatch_multiseed.py \
  --controls-root outputs/ai_csi_model_mismatch_metric_controls \
  --diagnostics-csv outputs/ai_csi_model_mismatch_p38_cfar_diagnostics/production_diagnostic_metrics.csv
```

Oracle 汇总：[`mechanism_oracle_summary_multiseed.csv`](../outputs/ai_csi_model_mismatch_multiseed_formal_clean/mechanism_oracle_summary_multiseed.csv)。
