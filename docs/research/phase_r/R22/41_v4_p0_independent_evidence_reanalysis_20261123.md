# R22-V4 P0 独立证据重分析

日期：2026-11-23
类型：归档数据离线重算；不代表本次重新运行 CUDA。

## 1. 身份与范围

本报告读取唯一 P0 完整归档，并逐项重算其任务、TrackManager、CFAR、目标响应和指标 parity。没有解压 13 GB 归档到磁盘，没有运行 Stage2、Production、CUDA 矩阵或 AI。

| 项目 | 身份 |
|---|---|
| P0 执行源码 commit | `715639c8e9aad7359eb1a5058f9238c0d51f99a1` |
| 本次分析基线 | `f84a3607a5b10c4f0a79dacb9d389f60e87abd06` |
| 研究总纲 blob | `e637e3769bde7814298ff012d0f42301884e2985` |
| P0 ZIP SHA-256 | `0974e1dfd1467cdd2bdced856e8c673d40c8966a53ab05261d0c5db2d99c4b02` |
| ZIP 大小 | 13,037,254,578 bytes（13.037 GB） |
| 内嵌 manifest SHA-256 | `4f96e4f1fb1cf7db1ebe2d1d07c79a8537b47fccac1efa3f4fbd7c7a0e4036a0` |

P0 归档已有独立 `unzip -tq` CRC 检查记录；本次重新计算完整 ZIP SHA-256，并逐一确认 V4 必要成员清单。完整结果见 [archive_validation.json](evidence/v4_confirmatory_freeze_readiness/archive_validation.json) 和 [source_identity.json](evidence/v4_confirmatory_freeze_readiness/source_identity.json)。

P0 条件为 seed `1937003`、径向速度 `2.5 m/s`、SNR `20 dB`、通道时延 `+4 ns`、20 个目标、10 个周期、beam 50。Stage2 报告记录连续面积杂波 1,300 packets、6,508,782 samples，平均功率 `0.0227655060`、lag-1 相关 `0.979992769`。面积杂波有效存在，不只是配置开关为 true。

## 2. Production 与数值 parity

归档包含 18 个方法/角色 Production 分支。全部分支均有完整 10 周期检测记录和 20 条 CFAR geometry 记录；Production 返回码为 0。这里的“进程通过”不等价于确认性性能通过。

| 方法 | 正确关联命中 / 200 | 命中覆盖率 | own-success 定位 RMSE |
|---|---:|---:|---:|
| A0 / 0 ns | 140 | 70% | 26.663 m |
| C0 / Current +4 ns | 26 | 13% | 135.917 m |
| PK / Known correction | 140 | 70% | 26.663 m |
| Fixed D3 | 142 | 71% | 57.668 m |
| M1 causal prefix | 140 | 70% | 44.963 m |
| Huber D3 causal prefix | 142 | 71% | 51.710 m |

own-success RMSE 使用各自不同的命中集合，不能直接作为方法排名。共同成功机会上的配对结果：

- Fixed D3 与 M1：135 个共同成功机会，RMSE 分别为 56.204 m、43.704 m；M1 低 12.500 m。M1 自身覆盖率 70%，Fixed D3 为 71%。
- M1 与 Huber：136 个共同成功机会，RMSE 分别为 43.583 m、50.828 m；M1 低 7.245 m。M1 覆盖率仍低 1 个百分点。
- A0 与 PK：140 个共同机会，位置 RMSE 差约 `0.000063 m`，支持本场景 Known correction 与无误差参考闭合。
- C0 与 PK 仅有 23 个共同成功机会；这组强选择子集上的误差不代表全部 200 个目标机会。

离线复算与保存 evaluator 的参数结果通过 parity。TrackManager 复核将 `track_output_payloads.csv`、`track_states.csv`、`track_detections.csv` 和逐周期 Production Detection CSV 按周期、track ID、det index、beam、真值关联交叉核对：18/18 分支均无 payload  lineage、字段来源或测量位置/时间/斜距 provenance 违规；6/6 个 ON 方法的 track summary、全局 handoff 和逐目标 lineage 均与归档评价汇总一致。见 [航迹审计表](evidence/v4_confirmatory_freeze_readiness/p0_track_payload_lineage_audit.csv) 和 [逐目标 lineage](evidence/v4_confirmatory_freeze_readiness/p0_target_track_lineage.csv)。

审计按 P0 执行版本的 `summarize_track_lineage_v3` 定义区分：A0 有 145 条从未关联目标的 confirmed track ID；169 条 track ID 曾出现过任意未匹配 payload。两数口径不同，归档同时保存这两个字段。全局 handoff 比较相邻的已观测目标周期；逐目标 handoff 比较该目标相邻的已匹配周期，这两种数也不能互换。

## 3. CFAR、目标保持与未评估指标

- 每个分支每周期 full-band 有效 CUT 和 threshold test 都是 527,280；10 周期每分支 5,272,800。18 分支求和为 94,910,400 次门限测试，但 CUT、周期和分支不是独立统计重复。
- 配置 Pfa 约 `1e-6`。本次没有独立 H0 truth join；经验 Pfa 状态为 `NOT_EVALUABLE_WITHOUT_INDEPENDENT_H0_TRUTH_JOIN`。
- TO-only、CSI_AFTER、固定真值 ROI 覆盖 200 个目标周期、5,000 个 ROI cells/方法。M1 相对 A0 的 power-ratio 中位数为 1.000，P10/P90 为 0.983/1.186；Fixed D3 为 1.000，P10/P90 为 0.975/1.275。它们是固定 ROI 的描述性目标响应，不是 Pd、总体能量守恒或统计证明。
- 所有方法在该归档的正确关联径向速度机会为 0；径向速度性能 `NOT_EVALUABLE`，不能填 0 误差。

## 4. 证据状态

| 内容 | 状态 |
|---|---|
| 归档身份、必要成员、ZIP CRC/SHA | `EXECUTED / EVALUABLE`（CRC 使用既有独立检查记录；SHA 和成员清单本次重算） |
| P0 任务与位置配对表 | `EXECUTED / EVALUABLE`，限单 seed/单场景 |
| 18 分支 Detection/TrackManager lineage | `EXECUTED / EVALUABLE`，与归档摘要 parity 通过 |
| CFAR 几何与 valid CUT 数 | `EXECUTED / EVALUABLE`；经验 Pfa 未评估 |
| Known correction 与 A0 物理/任务闭合 | `EVALUABLE`，限此配对场景 |
| 跨 seed 性能、统计显著性、系统 Pd/Pfa | `NOT_VERIFIED` |
| 原始 IQ 到 Production 的本次独立重放 | `NOT_EXECUTED` |

Native CUDA 执行证据来自 P0 既有运行包；本次只做归档离线重算。GitHub 不包含完整本地 ZIP；读取 GitHub 快照的审查者不能声称已独立检查其原始 IQ 或所有归档成员，具体边界见审查请求。

## 5. 可复现入口

在仓库根目录、且本机保留上述 P0 ZIP 时运行：

```bash
python3 scripts/phase_r/analyze_r22_v4_p0_freeze_readiness.py
```

脚本只读取 P0 ZIP 和历史 V3.3 机会表，重写本报告引用的机器可读离线分析产物；不会启动 GPU 或提取大型输入。
