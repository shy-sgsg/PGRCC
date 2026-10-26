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

## 检测链路数量与 H0 描述

下表为 6.7 m/s 三 seed 的中位数，依次是 ON full-band CFAR hit count / 最终 Detection record count。不同层的数量单位不同，不能相互替代。

| SNR | A0/C0 | C1 | C2-A | C2-B | C3-A | C3-B |
|---|---:|---:|---:|---:|---:|---:|
| 30 dB | 1884 / 315 | 1057 / 213 | 7565 / 792 | 1049 / 211 | 9966 / 583 | 1176 / 226 |
| 20 dB | 2263 / 301 | 1106 / 151 | 3018 / 553 | 1362 / 167 | 6099 / 704 | 1364 / 179 |

R18 每个完整场景 full-band geometry 有 1,581,840 个 valid CUT，CFAR alpha 为 13.44782257。OFF H0 observed false-hit fraction 可按测试 CUT 比例描述；例如 6.7 m/s、30 dB 三 seed 中位数：A0 0.0002187、C1 0.0001998、C2-A 0.0001934、C2-B 0.0001998、C3-A 0.0001928、C3-B 0.0001947。CUT 相关、只有三个背景 seed且 Mode-A 在同一 OFF 样本拟合，故这些是描述统计，`empirical Pfa=NOT_EVALUABLE`。

## TrackManager 与参数估计

Production TrackManager 记录支持 confirmed track、truth matched payload、false-confirmed unique ID、目标匹配周期和确认后匹配率的描述性重算。高速度 30 dB 下，Mode-A 的 false-confirmed track ID 数按三个 seed 分别为 C2 `[267, 296, 275]`、C3 `[207, 196, 130]`；Mode-B 分别为 C2 `[11, 12, 77]`、C3 `[12, 14, 161]`。这些记录反映 branch 的候选/跟踪负担，不能单独解释为 Pfa 或 ID-switch 根因。目标确认时延可读，约 6.10 s；周期级 matched 统计受首次 track confirmation 前周期影响。`track_association_audit_v2` 不在 snapshot，ID-switch 根因标为 `NOT_IDENTIFIABLE_FROM_CURRENT_DEBUG`。

方位、平面位置、range、folded Doppler 和 TrackManager ground-speed RMSE 在有效的唯一匹配样本上产生 own-success/common-success 汇总。不同方法 own-success 数不同，且每层仅三个 seed；应连同成功样本数查看，不能挑一个 RMSE 排名。Radial velocity 因模糊状态未闭合，所有场景为 `NOT_EVALUABLE`。

## 关键解释

高速度条件下，Mode-A 相比 Mode-B 有更多目标命中机会，同时也有更多 CFAR hits、Detection records 和 false-confirmed TrackManager IDs。它是使用 paired OFF 的 privileged reference，不是盲估计器，也不能据此声称 Mode-A 总体更优。低速数据中各条件的严格命中数大多相同，未建立低速保护主线的证据。

逐条件完整指标见 [`baseline_task_metrics.csv`](evidence/results/formal_matrix/baseline_task_metrics.csv)、[`detection_waterfall.csv`](evidence/results/formal_matrix/detection_waterfall.csv)、[`track_metrics.csv`](evidence/results/formal_matrix/track_metrics.csv)、[`parameter_estimation_metrics.csv`](evidence/results/formal_matrix/parameter_estimation_metrics.csv) 及 [`metric_evaluability.csv`](evidence/results/formal_matrix/metric_evaluability.csv)。

## 状态

- `IMPLEMENTED`：严格任务 matcher 与跨层记录读取。
- `EXECUTED`：12 场景全部离线重算。
- `EVALUABLE`：truth opportunity 命中、saved CUT counts、Production TrackManager 可读字段与有效参数字段。
- `PERFORMANCE_VERIFIED`：没有统计性 Pd/Pfa 或总体方法胜负验证；零时延设计不测试未知误差因果效应。
