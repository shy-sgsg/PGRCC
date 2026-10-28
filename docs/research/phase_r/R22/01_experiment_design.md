# R22-01：实验设计与信息条件

> **2026-10-28 V2 状态更新：** 原始工程门修复后，V2 设计配置与方法配置已冻结；六 seed engineering gate 已完成，screening 矩阵已在本机 Native CUDA 启动。当前逐 case 状态以 `outputs/r22_full_v2_compact/matrices/screening_diskquota_retry/matrix_manifest.json` 为准。screening 尚未完成，confirmatory、40-target dense、bridge 与独立 H0 批次尚未完成；当前单 case 通过不构成性能验证。下文描述的“原始 R22 未冻结/未启动”是形成 V2 前的历史状态，保留作审计，不代表当前状态。冻结配置及阶段更新见 `config/r22_extension_experiment_design_v2_range_roi.json`、`config/r22_method_config_v2_range_roi.json` 和 [05 资源与执行记录](05_resource_and_execution_plan.md)。

## 科研定位

- **长期目标：** 在真实、可追溯的 GMTI Production 链路中识别会限制任务性能的物理误差和处理瓶颈，并公平评价可实施的传统方法。
- **具体问题：** 多目标条件下，未知 +4 ns 通道时延是否造成可重复的检测/参数/航迹损失，已知物理校正能恢复多少，盲确定性估计距离 Known correction 上限还有多远？
- **研究必要性：** R21 仅有两个 seed、三个目标周期；R22 先审计历史 baseline，再检查多目标生成、真实 CUDA 路径、杂波配对和任务评价是否可扩展。
- **方法选择：** 先以 A0 no-error、C0 Current、PK Known correction、C2/C3 两种信息模式、R9 固定 D3、Huber D3、M1 Physics Matched 建立传统处理对照。AI 未满足 deterministic estimator 失败与任务收益证据门，不进入训练。

## 条件与输入信息边界

| 条件 | 含义 | 允许的估计信息 |
|---|---|---|
| A0 | 无通道误差参考 | 理想场景输入 |
| C0 | +4 ns 未校正 Current | 当前 ON/OFF/TO 生产数据 |
| PK | Known physical correction upper bound | 真实 +4 ns 仅在 evaluator correction 使用 |
| C2/C3 Mode-A | 配对 OFF 标定后应用至 ON/TO | 允许使用配对 OFF；与 blind 方法分层报告 |
| C2/C3 Mode-B | ON 数据盲估计后作用于本分支角色 | 不读误差真值、目标真值、A0/Known、配对 OFF 或未来信息 |
| R9 / Huber / M1 | 确定性盲估计候选 | 只用冻结的 ON 测量输入，真值只供 evaluator |
| PKR | 参考型校正及 residual closure 检查 | Mode-A OFF 或 Mode-B ON 的参考来源由 contract 限定；不得降支持门槛 |

上述是拟比较的信息合同，不代表每项已达到可评估状态。seed900203 的 PKR 支持 gate 未通过；所有 Mode-A / Mode-B 结果须按额外参考信息分层，不应与 C0 作纯算法同信息排名。

## 设计范围与当前冻结状态

最初资源草案包含 24 个 screening 条件、至少 12 个 seed/条件；6 个 confirmatory 条件、至少 30 个全新 seed/条件；另有目标密集、桥接与独立 H0 条件。该草案没有完成资源与方法门审计，不能误称已冻结或已执行。

当前已执行的仅是工程 pilot：seed900101 零时延 smoke；seed900202/900203 的 2.5 m/s、20 dB、+4 ns、20 targets、单 beam、单周期方法观察。详细实际执行见 [02](02_screening_experiment.md)。PKR gate 未通过后，筛选矩阵、确认 seed 清单及 H0 方案保持未启动。

2026-10-28 扩展工程矩阵的首个 `0 ns` 条件在盲校正预处理阶段失败；故障原因和原始执行边界记录于 [10 工程门失败记录](10_engineering_gate_failure_20261028.md)。复核后明确：无误差参考条件保留 P1/P2 盲估计数值作为诊断，但所有仅为校正时延而设计的物理校正分支在该条件标记 `NOT_APPLICABLE_ZERO_DELAY_REFERENCE`，不向无误差输入施加非零校正。该规则不改变 +4 ns 条件的校正、盲估计隔离或质量门，也不构成任何 Production 成功证据；新的执行设计版本及适用条件见 [09 延续执行契约](09_extension_execution_contract.md)。

## 验收与停止条件

1. 先通过输入/源码/二进制身份、Stage2 物理可达杂波、配对背景、Native CUDA Production runtime、CSI taps 和 CFAR valid-CUT gate。
2. Known correction 与 A0 的物理闭合需按冻结 CSI 复数/功率定义直接验证，不能仅凭命中数相同。
3. 所有方法统一使用 Production 任务评价，并报告 target-level 机会、当前周期 Detection/航迹字段来源和失败样本。
4. H0 false-alarm 只使用独立 target-free seed；目标场景未匹配 detections 只能作描述性错误输出。
5. 任一所需 reference、beam quality、valid-CUT 或配对 gate 失败，停止受影响比较，不降低质量门槛。
6. 只有工程 gate 通过后才开始 seed 级筛选；只有筛选集合/分析/配置冻结后才进入确认实验。

## 研究结论的统计单位

seed 是主要独立推断单位。seed 内多个目标和扫描周期先聚合，再对配对 seed 计算差值、效应量和按 seed 聚类区间。当前 pilot 的 20 个 target opportunities 是一周期内相关机会，不是 `n=20` 独立实验。
