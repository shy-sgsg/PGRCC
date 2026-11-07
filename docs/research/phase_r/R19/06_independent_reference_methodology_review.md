# R19：seed909 独立参考 gate 方法审查

## 既有判定

R18 seed909 secondary attempt3 在 Stage2 area/clutter 与统计可比性门通过后审查 Gamma support。C2 的三组支持可精确配对；C3 的 117 个 group key 均存在且状态为 `OK`，但 116/117 组的 support count 与对应 Mode-A/Mode-B 不同，总 support 从 95,803 变为 92,859。R18 当时按事先冻结的 exact-support gate 将 C3 标为 `NOT_EVALUABLE` 并停止任务评价。

## R19 方法复核

R18 的结果是按照当时规则作出的正式结论，应保留为 `NOT_EVALUABLE`。R19 不改变该历史判定。R19 的方法学复核发现，**跨独立随机 realization 要求 robust、data-adaptive estimator 的 support count 逐点完全相等，通常不是合理的统计同质性条件**：有效样本数可随输入估计结果变化，而组 key 和诊断状态仍可能完整。反过来，容许 support 自适应变化也不意味着它对估计或任务无影响；它需要被记录、设定下界并检验结果稳定性。

因此应分开问：

- **数据/计算完整性：** 组键、几何、valid 状态、无 truth leakage、权重/残差记录是否齐全？
- **估计器统计质量：** support 是否足以支撑估计，跨 realization 的 Gamma 是否稳定，质量分布变化是否在预设范围？
- **任务可比性：** 输出是否能在冻结的一对一任务门、目标保持和检测/航迹指标上评价？

exact equality 是 R18 的冻结停止门，不足以成为后续所有随机样本的通用质量阈值。R19 的这一审查不改写 R18、不补算 seed909 的任务表现，也不证明该 estimator 的 Gamma 或任务输出可靠。

## 后续参考验证的设计要求

下一次独立参考验证若获批准，应在运行前预注册：

1. 冻结 group geometry、键与 valid/invalid 状态规则；
2. 按估计任务定义最低有效 support 与权重/残差诊断，不要求不同独立 realization support count 完全一致；
3. 设定 Gamma 幅值/相位稳定性和支持域质量分布的容差，并给出物理或开发数据依据；
4. 使用独立重复评估估计量稳定性与方向一致性；
5. 明确该参考信息在在线/实机上的可获得性；Mode-A 有配对 OFF 不能默认视为可部署；
6. gate 通过后仍须通过独立任务指标、目标保持与 false-alarm 评价门。

数值阈值必须在新数据评价前冻结，不能根据 seed909 结果倒推。若达不到 support 或稳定性门，局部标为不可评价并保留原因。
