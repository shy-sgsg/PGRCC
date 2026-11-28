# R22-V4 确认性冻结候选（待审查与需求阈值）

日期：2026-11-23
状态：`DRAFT_CANDIDATE_NOT_FROZEN`。本文不修改机器设计 JSON 中的阈值，也不授权执行。

## 1. 候选实验问题

在物理可解释的 +4 ns 通道时延条件下，周期前缀 M1 能否相对 Fixed D3 改善三种目标速度层的定位，同时保持正确检测覆盖；其表现相对相同输入信息的 Huber D3、Known correction 上界如何？独立 H0 下 false event/cluster/track 是否可接受？

## 2. 冻结候选矩阵

| 设计项 | 候选值 |
|---|---|
| target-present seeds | 30 个独立 seed；三个速度 0.5、2.5、6.7 m/s |
| 目标条件 | 20 dB；0 ns / +4 ns 配对；同 seed/几何/目标/杂波噪声背景 |
| target-present identities | 180（30×3速度×2时延） |
| 独立 H0 identities | 60（30 seed×2时延；不得复用 V3.2 H0 seeds） |
| 方法 | 0 ns：A0；+4 ns：A0、C0、PK Known correction、Fixed D3、M1 causal prefix、Huber D3 causal prefix |
| Primary | +4 ns、20 dB：每 seed 三个速度层等权的 `M1 − Fixed D3` common-success position RMSE 差；负值有利于 M1 |
| Secondary | `M1 − Huber D3`、Known upper bound、覆盖和固定 common ROI 目标复响应；M1 Batch-10 仅离线诊断，不进入在线排名 |
| 统计 | seed-cluster bootstrap 95% CI，10,000 次，PCG64 seed 22029；速度层显著性分析用 Holm |
| 机器计数 | 1,470 注册科学槽位；2,520 次 Production 调用；1,200–1,440 次 Stage2 调用 |

seed 范围仍须在 freeze 前完成全局 registry 审计。以上设计计数来自当前源码展开，不是执行证据。

## 3. 候选判定语句

建议经用户/工程需求方批准后采用：

1. Primary 位置改善需达到用户指定的 `MDE_position_m`；可操作候选为 M1−Fixed 的 seed-level 95% CI 上界 `< −MDE_position_m`。
2. M1 正确检测覆盖需非劣于 Fixed D3；候选判据为覆盖差 95% CI 下界 `> −margin_detection_pp`。
3. 若要求航迹保护，另设目标 confirmed/matched coverage 与 false-confirmed track 的工程边界。
4. H0 可接受 false event/cluster/track 边界须预先写明；不得把配置 Pfa、target-present 未匹配输出或大量相关 CUT 当成经验 Pfa。
5. 任务收益不能只由检测率或共同成功定位误差单独判定；输出缺失、有效覆盖和目标保持必须同时报告。

`MDE_position_m`、`margin_detection_pp`、航迹保护界值和 H0 边界当前均未获批。历史精度敏感度约 2.539 m MDE / 0.262 pp coverage NI margin 仅供讨论，不填入冻结值。

## 4. 必须先闭合的冻结门

- ChatGPT 读取固定审查快照中的研究总纲，完成阶段正确性与总体科研价值双层审查。
- 用户/工程需求方明确上述工程阈值和 H0 判定语义。
- 对全部 target/H0 seeds 完成最终碰撞审计。
- 对 common-success 分层为空/不完整的预先处理规则、评估覆盖率和缺失语义作机器可读冻结。
- 完成检测/真值映射、逐周期 TrackManager payload、fixed target ROI、CFAR valid CUT 和 compact archive schema 审计。
- P0 的 422-member compact allowlist 在 H0 和 0 ns 代表性 case 上做 schema dry-run；实际峰值空间按卷剩余量门控。运行中可超过最终 20 GB 保留上限，但每个原子 case 必须安全完成、核验和可恢复。
- 只有用户另行明确授权，才启动尺寸 pilot 或 CUDA 确认矩阵。

## 5. 当前裁决

冻结候选的总体因果结构和统计单位可进入独立审查；确认性阈值和 H0 误警接受标准未定。当前保持 `DRAFT_NOT_FROZEN`、`execution_authorized=false`、`cuda_authorized=false`、`ai_training=false`。本报告不自动启动任何后续实验。
