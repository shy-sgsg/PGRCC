# R20 冻结离线评价契约

## 契约身份与范围

- evaluator：`r20-task-eval-v2`；R19 `r19-task-eval-v1` 只读作为回归对照。
- 输入：R18/R8/R12 已保存的 standalone truth、Production Detection、CFAR Doppler 轴、TrackManager、runtime/config 与 manifests。
- 不在范围内：CUDA/Production 重放、新 IQ、Stage 2 重跑、配置或算法调整、AI 训练、R21。

## 目标机会与匹配

primary unit 是 standalone visible truth 的 `(period, beam, target)` opportunity。truth 的 required fields 均需有限有效；非法项单独审计，不计入 valid miss denominator。Production Detection 只用本周期 Detection fields，不使用检测输出里嵌入的 truth。

严格一对一门保持 R19 冻结定义：同 period/beam；range bin 差 ≤2；range 差 ≤150 m；位置差 ≤1 km；依据实际 Production Doppler axis、PRF folding 和方向，circular Doppler row 差 ≤2。分配用最大匹配数，再最小化冻结归一化代价。门限不会因审计结果调整。

缺失 `range_bin` 时只允许有合法 `col` 的回退；present-invalid 不准 fallback。truth hits、misses、unmatched detection rows、candidate edges 与参数可评估率分栏报告。

## 历史身份和可比性

R8 seed × condition 是独立描述单元，不把三个 period 当成三个独立 seed；R12 seed404 单独呈现。跨条件固定 identity 包括场景 seed、truth/target、背景来源、Production binary 和语义配置；延迟条件、条件标签、输入 IQ digest 等是预期可变身份。受控输入变化不要求 IQ SHA 相同。identity schema 不完整或固定字段不符时拒绝该对比。

raw IQ 当前缺失意味着：保存的最终 Detection/track/truth task 可被离线重评，但 input chain 不能由 R20 重放；Known closure 仅能引用原始 manifest 结果，不能称本次重算。

## H0、参数与 TrackManager

- `OFF` 记录若只有 valid CUT hit fraction，标为 descriptive observed cut fraction；没有独立 H0 holdout 和 uncertainty 时 empirical Pfa `NOT_EVALUABLE`。
- 参数 RMSE 报 own-success 样本和分母/缺失原因；另报同一两条件都匹配的 common-success RMSE，避免把命中差异混进参数精度差。
- TrackManager period 用 `result_id → Production Detection period` 精确映射。Confirmed observations 与 unique periods 分开；只按 Production 当前帧 matched/payload lineage 定义目标命中。parallel target tracks 与跨时间 disjoint-ID handoff 分开，不从计数自动推断根因。

## 解释规则

所有 R8/R12 指标是逐场景的可行性/正确性描述证据。一个 seed 内三个目标机会、或三个期，均不构成独立 seed 数。不得声明具有统计显著性的 Pd/Pfa 改善；未记录量标 `NOT_EVALUABLE`，原始证据缺失时标 `REPLAY_REQUIRED` 或相应不验证状态。

状态字段分别使用：`IMPLEMENTED`（离线代码/契约）、`EXECUTED`（本次实际离线复评）、`EVALUABLE`（特定指标满足数据与身份条件）、`PERFORMANCE_VERIFIED`（跨独立重复与统计/工程门闭合）。后三者不能由代码测试或脚本退出码替代。
