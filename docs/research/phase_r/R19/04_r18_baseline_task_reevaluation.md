# R19：R18 Production 任务指标严格复评

## 范围

复评 R18 seed 606/707/808 × 目标速度 6.7/0.5 m/s × SNR 30/20 dB 的 12 个场景。每个场景 delay error 均为 0 ns。比较 A0、C0、C1、C2 Mode-A/Mode-B、C3 Mode-A/Mode-B。ON 检测以 standalone visible truth 为机会，以冻结的一对一关联规则评价。Mode-A 和 Mode-B 保持分层。

## 目标检测机会

每个 speed/SNR 分层有 3 个 seed × 3 个 period = 9 个描述性 target-period opportunities。下表是严格一对一命中数/机会数，不是具有统计不确定性估计的 Pd。

| 速度/SNR | A0/C0 | C1 | C2-A | C2-B | C3-A | C3-B |
|---|---:|---:|---:|---:|---:|---:|
| 6.7 m/s, 30 dB | 5/9 | 3/9 | 9/9 | 6/9 | 9/9 | 3/9 |
| 6.7 m/s, 20 dB | 8/9 | 5/9 | 9/9 | 9/9 | 9/9 | 4/9 |
| 0.5 m/s, 30 dB | 9/9 | 9/9 | 9/9 | 9/9 | 9/9 | 9/9 |
| 0.5 m/s, 20 dB | 9/9 | 9/9 | 9/9 | 9/9 | 9/9 | 9/9 |

R18 旧报告中的 `3/3` 是旧 target counter 口径，不等于 R19 在所有 period、最终 Detection CSV 上重新构造的唯一 truth opportunity。R19 的结果不回写 R18，也不将旧计数与新计数合并。

### 旧门控候选数量与严格一对一门控

为定位旧 `3/3` 与 R19 命中差异，额外按每个可见 truth opportunity 统计通过旧 range/position 两门的 Detection–truth 候选边：`|Δrange| <= 150 m` 且 EN 位置误差 `<= 1000 m`。该审计只用独立 truth 与最终 Production Detection；随后在同一候选上观察 R19 冻结规则新增的 range-bin 与 circular Doppler 门。旧候选不是命中数，也不是虚警数。拒绝原因按冻结评价器的首个失败门互斥归类。

| seed | 条件 | Detection records | 旧 range/position 候选边 | 旧候选重复数 | range-bin 首拒 | Doppler 首拒 | R19 合格边 | R19 命中 / 3 |
|---:|---|---:|---:|---:|---:|---:|---:|---:|
| 606 | C0 | 315 | 24 | 21 | 0 | 22 | 2 | 2/3 |
| 606 | C3 Mode-B | 226 | 23 | 20 | 5 | 17 | 1 | 1/3 |
| 707 | C0 | 128 | 25 | 22 | 0 | 23 | 2 | 2/3 |
| 707 | C3 Mode-B | 136 | 25 | 22 | 0 | 24 | 1 | 1/3 |
| 808 | C0 | 372 | 20 | 17 | 0 | 19 | 1 | 1/3 |
| 808 | C3 Mode-B | 412 | 21 | 18 | 1 | 19 | 1 | 1/3 |

seed606 的逐机会记录进一步显示：C0 的 period 0/1/2 分别有 `6/9/9` 条旧候选，但严格门后为 `0/1/1`；C3 Mode-B 分别有 `5/7/11` 条旧候选，严格门后为 `0/0/1`。因此两条件旧计数都可以是 `3/3`，但严格的一对一 Detection 命中分别是 `2/3` 和 `1/3`。差异主要来自旧门控未约束 Doppler 一致性；C3 seed606 另有 5 条候选先被 range-bin 门排除。此处是关联定义差异，不是本阶段新观察到的物理性能变化。

逐 opportunity 的候选数、首拒门和 R19 匹配结果见 [`candidate_gate_audit.csv`](evidence/results/formal_matrix/candidate_gate_audit.csv)；分支汇总及同口径门控计数见 [`baseline_task_metrics.csv`](evidence/results/formal_matrix/baseline_task_metrics.csv)。

### 配对 seed 的任务变化与候选量

每个固定速度/SNR 层内，以下为三 seed（依次 606/707/808）的 `strict_hit_fraction` 相对 C0 逐 seed差值，单位是 3 个 target-period opportunities 的比例差。高速度 30 dB：C1 `[-1/3,-1/3,0]`，C2 Mode-B `[-1/3,+1/3,+1/3]`，C3 Mode-B `[-1/3,-1/3,0]`；高速度 20 dB：C1 `[-1/3,-1/3,-1/3]`，C2 Mode-B `[+1/3,0,0]`，C3 Mode-B `[-1/3,-1/3,-2/3]`。两档低速中所有这些差值均为 0。C2/C3 Mode-A 的同 seed 目标命中差在全部四个速度/SNR 层均为 0。

高速度下多个在线条件的 CFAR hit 与 Detection 数量相对 C0 下降，但 seed 方向不总与目标命中方向一致；例如 30 dB 的 C2 Mode-B 目标命中在三个 seed 中一低两高，而 CFAR full-band hit 三个 seed 都下降。因而本数据支持“候选/处理链数量发生变化”，不能把数量下降直接解释成任务收益或虚警改善。每个差值只以 seed 为配对独立单位；每条件只有 3 个 seed，不作显著性检验。

## 检测链路数量与 H0 描述

下表为 6.7 m/s 三 seed 的中位数，依次是 ON full-band CFAR hit count / 最终 Detection record count。不同层的数量单位不同，不能相互替代。

| SNR | A0/C0 | C1 | C2-A | C2-B | C3-A | C3-B |
|---|---:|---:|---:|---:|---:|---:|
| 30 dB | 1884 / 315 | 1057 / 213 | 7565 / 792 | 1049 / 211 | 9966 / 583 | 1176 / 226 |
| 20 dB | 2263 / 301 | 1106 / 151 | 3018 / 553 | 1362 / 167 | 6099 / 704 | 1364 / 179 |

R18 每个完整场景 full-band geometry 有 1,581,840 个 valid CUT，CFAR alpha 为 13.44782257。OFF H0 observed false-hit fraction 可按测试 CUT 比例描述；例如 6.7 m/s、30 dB 三 seed 中位数：A0 0.0002187、C1 0.0001998、C2-A 0.0001934、C2-B 0.0001998、C3-A 0.0001928、C3-B 0.0001947。CUT 相关、只有三个背景 seed且 Mode-A 在同一 OFF 样本拟合，故这些是描述统计，`empirical Pfa=NOT_EVALUABLE`。

## TrackManager 与参数估计

Production TrackManager 记录支持 confirmed track、truth matched payload、false-confirmed unique ID、目标匹配周期和确认后匹配率的描述性重算。高速度 30 dB 下，Mode-A 的 false-confirmed track ID 数按三个 seed 分别为 C2 `[267, 296, 275]`、C3 `[207, 196, 130]`；Mode-B 分别为 C2 `[11, 12, 77]`、C3 `[12, 14, 161]`。这些记录反映 branch 的候选/跟踪负担，不能单独解释为 Pfa 或 ID-switch 根因。目标确认时延可读，约 6.10 s；周期级 matched 统计受首次 track confirmation 前周期影响。`track_association_audit_v2` 不在 snapshot，ID-switch 根因标为 `NOT_IDENTIFIABLE_FROM_CURRENT_DEBUG`。

R19 保留的启动 latency 原始值为 `−1.90735e−7 s`（84 个 ON 分支相同）。代表性记录中 standalone truth `utc_mid=4.95`，Production create event `utc=4.949999809265`，后者等于前者的 float32 舍入值；因此该负差是时间戳精度下的零差，不表示检测发生在真值之前。CSV 保留原始差值以便审计。Track state 的 period 通过 `result_id → GMTI period file` 映射，避免把 TrackManager event CSV 中的一基 `result_id` 当作独立真值周期。

方位、平面位置、range、folded Doppler 和 TrackManager ground-speed RMSE 在有效的唯一匹配样本上产生 own-success/common-success 汇总。不同方法 own-success 数不同，且每层仅三个 seed；应连同成功样本数查看，不能挑一个 RMSE 排名。Radial velocity 因模糊状态未闭合，所有场景为 `NOT_EVALUABLE`。

## 关键解释

高速度条件下，Mode-A 相比 Mode-B 有更多目标命中机会，同时也有更多 CFAR hits、Detection records 和 false-confirmed TrackManager IDs。它是使用 paired OFF 的 privileged reference，不是盲估计器，也不能据此声称 Mode-A 总体更优。低速数据中各条件的严格命中数大多相同，未建立低速保护主线的证据。

逐条件完整指标见 [`baseline_task_metrics.csv`](evidence/results/formal_matrix/baseline_task_metrics.csv)、[`detection_waterfall.csv`](evidence/results/formal_matrix/detection_waterfall.csv)、[`track_metrics.csv`](evidence/results/formal_matrix/track_metrics.csv)、[`parameter_estimation_metrics.csv`](evidence/results/formal_matrix/parameter_estimation_metrics.csv) 及 [`metric_evaluability.csv`](evidence/results/formal_matrix/metric_evaluability.csv)。

## 状态

- `IMPLEMENTED`：严格任务 matcher 与跨层记录读取。
- `EXECUTED`：12 场景全部离线重算。
- `EVALUABLE`：truth opportunity 命中、saved CUT counts、Production TrackManager 可读字段与有效参数字段。
- `PERFORMANCE_VERIFIED`：没有统计性 Pd/Pfa 或总体方法胜负验证；零时延设计不测试未知误差因果效应。
