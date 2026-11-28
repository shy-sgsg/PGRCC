# R19 冻结 GMTI 任务评价契约

契约版本：`r19-task-eval-v1`
冻结基线：R18 review commit `095d40996bd2a85c5d04b3c8db1b1e9528737b9d`；Master Plan blob `e637e3769bde7814298ff012d0f42301884e2985`。
本文件先于任何 R19 新评价表生成而冻结；仅用于 R18 已保存数据，不触发 Production 重放。

## 1. 评价对象与输入隔离

- 正式 ON 评价对象：A0、C0、C1、C2 Mode-A、C2 Mode-B、C3 Mode-A、C3 Mode-B；Mode-A 和 Mode-B 永不合并。C0/C1/A0 标为 `not_applicable` mode。
- OFF 只作目标自由 H0 描述或反事实结果；TO 是目标-only 处理控制，不作有杂波任务性能条件。
- 目标周期机会来自 standalone `truth_targets_by_beam.csv`，以 `(case, seed, period_id, beam_id, target_id)` 唯一标识；只纳入 `visible=true` 的 truth。Production Detection CSV 中嵌入的 `truth_*` 字段不得用来创建或筛选真值机会。
- 同一方法条件必须具备一致的 scene/seed/speed/SNR/delay、Production binary SHA、config SHA 和输入角色身份。不同二进制、配置、种子、条件或 realization 不得拼接为同一行或配对差值。
- 评价器读取 truth 是离线用途；truth、目标标签、估计误差标签、TO 与配对 OFF 不得进入历史估计器或其 Gamma 输入。

## 2. 唯一目标-Detection 关联

### 机会与字段有效性

1. 匹配组必须相同 `period_id` 和 `beam_id`；period/beam、target ID、range bin、目标 Doppler 真值、truth EN、Detection ID、range bin、Doppler row、estimated EN、range meters、Detection Doppler/PRF/轴字段必须存在且为有限数。
2. 缺失、NaN、Inf、非法索引和重复 `(period, beam, det_id)` 不可静默通过；记录无效行，重复主键使该 branch 评价失败。
3. standalone truth 的 `range_bin`, `range_m`, `af_total_truth_hz`, `e_mid`, `n_mid` 为评价真值；Detection 侧用实际输出 `range_bin`（缺失时才允许使用同一行 `col`）、`range_m`, `row`, `new_e`, `new_n`。嵌入 Detection 的真值列一律忽略。

### 冻结门与代价

候选必须同时满足：

- `abs(detection.range_bin - truth.range_bin) <= 2` bins；
- `abs(detection.range_m - truth.range_m) <= 150 m`；
- EN 平面位置误差 `hypot(new_e-e_mid, new_n-n_mid) <= 1000 m`；
- circular Doppler row distance `<= 2` rows，在真实 Production Doppler row 数上取模。

range-bin 与 Doppler gate 继承 Phase R7 已冻结关联规则；150 m range 和 1 km EN gate 继承 Phase R3 已有 target matcher。所有门限对所有方法相同。无完整 Doppler axis 或 center/PRF 时该候选无效，不补用嵌入 truth row。

满足门限的边代价为四个归一化残差的 RMS：`sqrt(((dr_bin/2)^2 + (dr_m/150)^2 + (dpos_m/1000)^2 + (dd_row/2)^2)/4)`。按 `(period, beam, target_id)` 和 `(period, beam, det_id, row, col, source_row)` 排序后，使用确定性 Hungarian 一对一匹配；先最大化匹配基数，再最小化总代价。非法边不可分配。相同输入顺序与数值必须得到相同关联。

每 branch 报告 `N_opp`, 有效候选数、总原始 Detection 行数、门内候选边数、唯一命中数、漏检数、未匹配 Detection 行数、一个 truth 的重复候选数和无效字段数。`N_hit/N_opp` 命名为 `truth_opportunity_hit_fraction`，仅描述本冻结数据，不自动叫统计 Pd。未匹配 Detection 是 truth-unmatched Detection record，不称为 false CUT 或独立 false alarm。

## 3. CFAR 与 H0

- Tested-CUT hit fraction：对经几何审计有效的 CFAR 行计算 `hit_cut_count / valid_cut_count`，称为 CUT hit fraction。
- OFF H0 描述值：仅当 standalone truth / scenario 确认该 OFF realization 无目标，且 `0 <= hits <= valid_cuts`、`valid_cuts > 0` 时，报告 `h0_observed_false_hit_fraction`。附加报告方法角色、标定信息来源、是否同样本拟合、seed 数和 CUT 相关限制。
- Mode-A OFF 是 Gamma 拟合同一样本上的样本内描述；Mode-B OFF 使用 ON 拟合权重，是 paired counterfactual；二者都不是独立部署 H0。C0/C1/A0 OFF 也只有三个 seed，且 CUT 相关。
- 统计意义 empirical Pfa 需要独立 H0 holdout、合法 false-CUT 与 H0 分母、独立重复单位和不确定性评估。本 R18 数据不满足，R19 保持 `NOT_EVALUABLE`。
- 不以 configured Pfa、非目标 Detection 行、cluster 数或 `hit_index_hash` 反推 false-CUT 分子；不把 Mode-A/B 混合排名。

## 4. Detection、跟踪与输出错误

- 分开报告 CFAR hit count、postfilter cluster、Detection record、Production TrackManager association、confirmed track 和 protocol payload；每个数量附单位与来源。
- Detection false-output 指标只可命名为 truth-unmatched Detection rows / per-period candidate count；OFF 的 Detection 行是该 H0/counterfactual branch 的描述输出，不等于 CUT Pfa。
- 仅使用 Production TrackManager debug；正确关联以同周期、同 beam、当前实际关联 Detection 的已确认 TrackManager 输出为准。不得实现替代 tracker。
- initiation/confirmation latency、确认后匹配率、连续性、track fragmentation 和 payload 一致性须能从保留的帧、事件、检测关联及 payload 逐项追溯；缺失字段逐项 `NOT_EVALUABLE`。
- false confirmed track 按 branch 内 unique confirmed `track_id` 统计；若同一 track ID 有任一合法 truth matched payload，则不将该 ID 计作 false confirmed track，另外计其 unmatched payload。不同分支/不同 case 不共享 track-ID 命名空间。
- ID switch 根因必须有足够关联竞争、拒绝、生命周期和 gate 证据；缺 `track_association_audit_v2` 时标记 `NOT_IDENTIFIABLE_FROM_CURRENT_DEBUG`，不凭 ID 变化猜根因。

## 5. 参数误差与 RMSE

- 先使用上节唯一 truth-Detection match；每个 `(case, seed, period, beam, target_id, metric)` 最多一个样本。重复 key 导致错误，不能重复加权。
- bearing/angle：Detection `new_e/new_n` 相对 `platform_e/platform_n` 与 truth `e_mid/n_mid` 相对同平台位置计算环形角差，单位 degree。
- 平面位置：`hypot(new_e-e_mid,new_n-n_mid)`，单位 m；range：Detection `range_m` 减 truth `range_m`，单位 m。
- folded Doppler：在 Production axis/PRF 上把 truth `af_total_truth_hz` 与输出 Doppler 转成同一折叠频率后求最小环形差，单位 Hz。
- radial velocity：只在 `ambiguity_status=resolved` 且 Detection `radial_velocity_mps` 与 truth `vr_self_mps` 均有限时评价，单位 m/s；否则记录为模糊/无效机会，不以 ground speed 代替。
- ground speed：仅由已关联的 Production TrackManager output speed 与 `hypot(ve_mps,vn_mps)` 比较，单位 m/s；不从 radial velocity 推算。EN 分量仅在输出与真值分量都实际存在时分别评价。
- 每指标报告 own-success RMSE、成功样本数、失败/无效机会；common-success RMSE 仅在同一预定义比较组中所有方法成功的同一 truth opportunity 交集计算。
- 统计汇总按固定 speed/SNR 分层中的三个 independent seed 做逐 seed 配对差值、方向一致性、中位数和范围。period/CUT/Detection 不增加独立 seed 数；不做伪重复显著性声明。

## 6. 身份、输出 schema 与状态

- R19 输出 schema 固定为 `r19-task-eval-v1`，携带 case/seed/speed/SNR/method/mode/role、R18 runner commit、R18 binary SHA、config SHA、input SHA（只注明 manifest 记录、源数组缺失）、真值来源、evaluator 版本和契约版本。
- 同一个 paired difference 的双方必须使用同一 scene truth、同一 binary/config 口径及配对背景身份；否则拒绝配对并记录 `IDENTITY_MISMATCH`。
- 每项结果分别标 `IMPLEMENTED / EXECUTED / EVALUABLE / PERFORMANCE_VERIFIED / NOT_EVALUABLE / NOT_VERIFIED`。新离线 evaluator 的测试通过仅验证软件语义，不构成 GMTI 性能证据。
