# R22-V4 Phase G：统计契约闭合与冻结缺口审计

日期：2026-11-24

## 1. 范围与身份

本阶段依据 Phase F 固定审查提交 `ddbf057d8d944c54f94af54e239cc00644988f1a`，读取研究总纲、动态状态、决策日志、R22-V4 统计修正/冻结选项/方向报告、统计实现、执行器及对应测试。研究总纲 blob 为 `e637e3769bde7814298ff012d0f42301884e2985`；本阶段未修改总纲。

仅完成统计规则和执行器绑定的 CPU 侧闭合、候选 seed 与空间审计、阶段报告。没有运行 Stage2、Native CUDA Production、确认性矩阵或 AI。

## 2. Phase F 审查问题及处理

### 2.1 bootstrap-t 上端点

实现中的 bootstrap-t 区间反演公式原本正确，但 `position_test_contract()` 的说明把上端点关联的学生化统计量分位数写成了 97.5th percentile，容易让复核者把“端点置信度”和“统计量分位数”混为一谈。

定义 (T^*=(\bar{x}^*-\bar{x})/SE^*)。95% 双侧区间反演为：

\[
L=\bar{x}-q_{0.975}(T^*)SE,\qquad U=\bar{x}-q_{0.025}(T^*)SE.
\]

因此，上端点置信度是 97.5%，但用于上端点的学生化分布分位数是下尾 2.5%。代码说明和机器返回值现明确记录该关系，并有直接公式回归测试。

### 2.2 零方差与非有限统计量

此前零标准误重抽样会映射成 (+/-\infty) 或 0，再进入 `np.quantile()`；全相同样本也可能形成退化区间。这不能被解释成“估计无不确定性”。

现行规则是：

- 观测 seed 差值方差为零：保留点估计，区间端点置空，状态 `NOT_EVALUABLE_ZERO_OBSERVED_VARIANCE`。
- 任一 bootstrap-t 重抽样标准误为零：保留点估计和退化/非有限统计量计数，区间置空，状态 `NOT_EVALUABLE_DEGENERATE_BOOTSTRAP_VARIANCE`。
- 统计量或区间端点非有限：状态为 `NOT_EVALUABLE`，不生成有限性假象。
- 合成 Monte Carlo 中只要有一个数据集遇到上述退化，整项方法校验标为 `INCONCLUSIVE_INVALID_BOOTSTRAP_DATASET`，不把异常数据集悄悄丢出分母继续报通过率。

这些规则只修正统计实现与审计语义，不提供 GMTI 性能证据。

### 2.3 位置共同成功下限

原聚合函数在某速度仅有一个共同成功机会时仍把该 seed 位置差纳入主推断。现在 `minimum_common_support_per_speed` 必须显式提供正整数；未提供时仍保留描述性差值，但 seed 状态为 `NOT_EVALUABLE_MINIMUM_COMMON_SUPPORT_UNAPPROVED`，不会进入正式推断。低于已批准下限时标为 `NOT_EVALUABLE_BELOW_MINIMUM_COMMON_SUPPORT`。

对三个候选下限做了不使用 P0 单例的历史敏感性检查：V3.3 的 SNR 20 dB、+4 ns、速度 0.5/2.5/6.7 m/s、12 个独立 seed、M1 batch 与 Fixed D3 配对数据共有36个 seed×速度单元，每单元计划200个机会。共同成功数为117–161；下限1、5、10均保留36/36单元。V3.3 使用全周期 batch estimator，不是 V4 因果前缀，因此该切片不能区分候选门槛，也不能据此替用户选值。

| 候选下限/速度 | 历史 V3.3 通过单元 | 解释 |
|---:|---:|---|
| 1 | 36/36 | 最宽松；单个机会可决定该速度 RMSE，稳定性保护最弱 |
| 5 | 36/36 | 要求至少2.5%的200个机会成功；仍是工程约定，非数据推导值 |
| 10 | 36/36 | 要求至少5%的200个机会成功；当前历史切片未显示比1或5更有区分力 |

**当前正式下限仍为空。** 选值应来自工程最低可解释样本量要求；V4 结果应同时报告共同成功数、覆盖率、不可评价比例和同一预注册场景上的配对结果。不得由 P0 单 seed 倒推门槛。

## 3. 需绑定的完整统计规则

统计设计草案和 `_frozen_thresholds()` 现在共同记录并哈希绑定：

- 位置方案 A/B、MDE、每速度共同支持下限；
- bootstrap-t 方法、置信水平、重抽样次数、PCG64 和 seed；
- 位置方案具体通过式、严格边界相等时不通过；
- 检测覆盖非劣式及 M1−Fixed D3 的百分点方向；
- 共同成功机会定义、漏失/缺失机会和缺失 seed 处理；
- M1/Huber 失败的当周期零校正策略，禁止隐式替代；
- 是否把正确航迹覆盖作为硬门及其非劣 margin；
- H0 seed-any-event、false-confirmed-track-event、周期事件比例和唯一轨迹数的统计单位与区间；
- 位置、检测覆盖及可选航迹保护的联合判定，以及 H0 两个单侧上界门。

草案中的未决项仍为 `null`；执行器只在这些值明确、设计状态冻结、阈值通过验证且单独 CUDA 授权存在时接受授权记录。当前设计仍 `DRAFT_NOT_FROZEN`，不会被默认值补全。

## 4. H0 统计解释

H0 主任务事件定义为每个独立 seed 的十个有效 beam-50 周期中是否至少出现一次冻结 postfilter 后的 false Detection。false-confirmed-track guard 定义为每个 seed 是否至少出现一条唯一 false-confirmed TrackManager track ID。两者都按 seed 计数，并各自报告 exact Clopper–Pearson 两侧区间与单侧95%上界；周期事件比例和唯一假轨数/seed是辅助量。

30个完整 H0 seed 零事件时，单侧95% seed-any-event 上界约9.503%，所以可以检验低于10%的目标，但不能支持低于5%的目标；零事件时达到10%、5%、1%上界分别至少需要29、59、299个独立 seed。该范围只适用于 beam-50 十周期任务口径，不等同于多波位系统 Pfa。seed-any-event 和 false-track-event 的上界都必须严格低于各自批准的限制；任何一个限制未定，H0 不能宣告通过。

## 5. 验证与状态

定向 CPU 回归：统计文件34项通过、执行器15项通过。JSON 解析检查通过。包含 bootstrap-t 上端点公式、全相同 seed、零标准误重抽样、未批准共同支持下限、1/5/10敏感性门、授权阈值绑定和 H0 seed 事件单位。

状态区分：

- 统计契约处理：`IMPLEMENTED`。
- CPU 统计与授权回归：`EXECUTED`。
- 新 GMTI 实验：`NOT_EXECUTED`。
- 新性能结果：`NOT_EVALUABLE`。
- `PERFORMANCE_VERIFIED=false`。
- V4：`DRAFT_NOT_FROZEN`；`execution_authorized=false`；`cuda_authorized=false`；`AI_ENTRY_GATE=FAIL`。

细节见 [机器统计契约](evidence/v4_phase_g/statistical_contract_audit.json)、[候选 seed 审计](evidence/v4_phase_g/seed_registry_audit.json)、[资源审计](evidence/v4_phase_g/resource_constraint_audit.json)及[验证摘要](evidence/v4_phase_g/validation_summary.json)。
