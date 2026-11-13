# R22 V1A 工程门复测记录（2026-10-28）

## 结论状态

本记录只覆盖 R22 V1A 工程矩阵的第一个 0 ns case。矩阵在该 case 后按 fail-closed 门控停止，**不是 594-case 扩展实验的完成报告**。

| 状态 | 本次结果 | 适用范围 |
|---|---|---|
| `IMPLEMENTED` | true | V1A runner、场景、生产分支和评价流程已实现 |
| `EXECUTED` | true | Stage2 与本 case 中 28 个 Production 分支实际运行；部分依赖分支未运行 |
| `EVALUABLE` | false | 必需的 C4/PKR 参考分组未通过 support 验证；本 case 整体不可评价 |
| `PERFORMANCE_VERIFIED` | false | 未形成可用于性能结论的完整条件集合或多 seed 统计 |

不得将本次写成完整生产矩阵通过、Pd/Pfa 改善、Known correction 恢复收益或 AI 必要性证据。

## 运行身份与范围

- 仓库分支：`codex/unknown-system-error-next-stage`
- 实验执行源码 commit：`91cb5b1de8c2831073465e9192c1a385f0e0e25a`
- 设计：`R22_EXTENSION_20261028_V1A`；SHA-256 `b7613779c4031036e34b8929bfd9525e1f846d8d87d616e00050ffe34b4b5c29`
- 实际条件：seed `1930101`，0 ns，2.5 m/s，20 dB，20 targets，10 periods
- 命令：`python3 scripts/phase_r/r22_experiment_matrix.py --design docs/research/phase_r/R22/config/r22_extension_experiment_design.json --phase engineering --output-root outputs/r22_extension_20261028_v1a/engineering --stage2-binary build/simulate_stage2_statistical --production-binary build/GMTI_pipe_core --max-cases 2`
- Stage2 binary SHA-256：`ca8aa9eb8b7d97e5743a27e67c312e66a4efecbf2c76880db581192afd08ce8e`
- Production binary SHA-256：`3c4c6bef08291e7d02cefa1b23387d442dfb00cf0fbed0604e9a0fbecaa86760`
- CUDA：NVIDIA GeForce RTX 3050 Laptop 4 GB，driver 580.178.04，CUDA 13.0。运行期间另有用户训练进程使用 GPU；本次 wall time 仅作资源规划观察，不作为性能基准。
- 运行时间：2026-10-27 18:37:57–18:50:06 UTC（约 12 分 10 秒）；matrix manifest 为 `GATE_FAILED`，12 个工程 case 计划中完成 1 个。

精简可审查证据位于 [`evidence/extension_engineering_v1a/seed1930101/`](evidence/extension_engineering_v1a/seed1930101/)。完整本机运行树仍保存在 `outputs/r22_extension_20261028_v1a/engineering/`，不应把忽略目录路径当作 GitHub 已托管证据。

## 已通过的输入与物理检查

Stage2 报告确认连续面积地杂波确实进入场景：

- 连续面积包：`1300`
- 连续面积样本：`6,508,782`
- 实测面积杂波平均功率：`0.0225128995081`
- 平台高度：`6000 m`；地面可达斜距窗：`84,286.713–85,289.214 m`
- Stage2 报告 SHA-256：`dfffa3c55d7479814187daaa76ddc0da56711ec11c559e01eb6b6d0963b4e264`
- area clutter gate：`passed`
- Causal triplets（ideal / known / none）：均 `passed`

OFF/C+N、ON/S+C+N、TO/S 具有同一 seed、背景、目标与 Production 身份；估计器未读取 truth。零误差条件下的时延估计只作诊断，未对无误差参考应用非零物理校正。

## Production 执行与 gate 根因

Case manifest 登记 41 个方法/角色记录：

- 24 个 Production 分支满足分支状态和 Production path gate；
- C4 Mode-A/OFF、C4 Mode-B/ON、PKR Mode-A/OFF、PKR Mode-B/ON 共 4 个分支实际启动，Production return code 为 0，但其 robust gamma reference 被质量验证拒绝；
- 因这些 reference 无效，依赖的 8 个后续角色没有运行；
- 5 个零误差物理校正记录标为 `NOT_APPLICABLE_ZERO_DELAY_REFERENCE`。

拒绝原因是候选 `range_band_bins=133` 分组仍存在少数组未达到冻结的 `min_support=8`：

| 输入角色 | CSI tap 行数 | `OK` | `NOT_EVALUABLE` | 失败支持数 | 原因 |
|---|---:|---:|---:|---:|---|
| Mode-A OFF | 12,090 | 12,075 | 15 | 3–7 | `insufficient_robust_support` |
| Mode-B ON | 12,090 | 12,079 | 11 | 5–7 | `insufficient_robust_support` |

验证器要求每个必需 gamma group 均为 `status=OK`，故整份 reference 被标记 `reference_status_not_ok`，后续 ON/OFF/TO 应用角色不执行。Production 进程退出码为 0 不代表参考估计通过。

源码 `src/production_calibration_adapter.cpp` 的 `fitRobust` 先统计非零交叉项；若少于 8，返回 `insufficient_phase_support`。本次失败行为 `insufficient_robust_support`，代码在 ±0.35 rad 相位残差筛选后将 `support_count` 设为保留项数，再以 8 为门槛。因此这些失败行筛选前已有至少 8 个有效交叉项，筛选后留下 3–7 个。可确认的直接原因是相位一致性筛选导致支持不足；这不能证明几何稀疏，也不能识别低相位一致性的底层物理原因。该 case 未保留完整 F1/F2 CSI arrays/maps，不能从现有产物进一步归因。

不能降低 `min_support`、默许缺组、回退 Current 或把未运行角色记为成功。

有效 CUT 分母在已完整运行分支中有逐周期记录；但跨全部方法的 valid-CUT / unique-hit union 门为 `NOT_EVALUABLE`。case 汇总有 41 条 task rows、3200 个 target-period opportunity rows，其中 8 条 ON 记录可评价、独立 H0 seed 为 0；case 整体 `EVALUABLE=false`。经验 Pfa、独立 H0 虚警以及跨分支检测 union 均不能由本次数据给出。

## 未执行事项与解释边界

+4 ns 配对工程 case、其余 5 个工程 seeds、screening 288 cases、confirmatory 180 cases、40-target stress 48 cases、3-target bridge 6 cases 和独立 H0 60 cases 均未启动。停止原因是 V1A 第一个 0 ns case 的必需 C4/PKR reference gate 失败。

当前证据说明：面积杂波有效，CUDA Production 路径实际运行，且少数 133-bin group 经 robust phase consistency 筛选后无法产生完整合格 reference；现有产物不能判断这是何种底层物理效应造成。它不证明未知时延降低 GMTI 任务性能，也不证明已知校正有收益，不足以判断盲估计的任务差距、总体研究方向或 AI 必要性。

### 后续门控建议

先审查支持不足 group 的空间/周期分布、输入资格条件和 133-bin 分组覆盖；保持 `min_support=8`、扫描 beam quality gate、CFAR 门及现有 Production gate 不变。若提出新的分组或有效区域规则，应先写清空间分辨率代价、缺组处理和评价分母，作为新版本预注册后再作工程验证。当前不继续算法性能比较，不自动训练 AI，也不修改研究总纲。

## 空间清理

本 case 在 case manifest、运行日志、XML、CSI summary、CFAR geometry 与清理台账保存后，删除了 921 个可再生 `.bin` 中间文件，共 `4,429,702,312` bytes；完整 SHA/路径记录仍在本机 `outputs/.../raw_cleanup.json`。运行期间 worktree 峰值约 12 GB；清理后本次工程输出约 296 MB、worktree 总占用约 6.9 GB，低于 20 GB 上限。原始大 binary 不提交；精简审查包保留关键 CSV、XML、日志、阶段报告和指标摘要。
