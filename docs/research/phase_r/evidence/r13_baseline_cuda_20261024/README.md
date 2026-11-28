# R13 seed101 Native CUDA Production evidence

本包保存 R13 两个配对场景的关键运行记录和审查所需证据。结果只用于单 seed 可行性与方法闭环检查，不能作为统计性 Pd/Pfa 或通用性能结论。

## 范围与身份

- 仓库：<https://github.com/shy-sgsg/PGRCC>
- 分支：`codex/unknown-system-error-next-stage`
- CUDA 执行源码提交：`3289c13bab6457bd1b190e393ddb01d51e9448e5`，执行 manifest 记录 tracked worktree clean。
- 本机 Native CUDA：`USE_COREX=OFF`，RTX 3050 Laptop，driver 580.178.04、CUDA 13.0。manifest 保存完整 GPU probe、Python、build 与命令。
- 两个 case 均为 seed 101、delay error 0 ns、SNR 30 dB、三 period、beam 50，Production Current / SCC / Robust DDC；正常目标速度 6.7 m/s，挑战速度 0.5 m/s。每个场景 12 条 Production branches，共 24；A0 重复运行生产 Current 用于闭合，另有 C0/C2/C3，每种均含 OFF/ON/TO。
- 真实输入链为 4-channel protocol IQ → `(1,3)/(2,4)` 融合 F1/F2 → Production 两通道 CSI → GO-CFAR → detection/localization → TrackManager。

## 文件导航

- `manifests/`：两个 runner manifest、完整 case manifest、Stage2 scene manifest 和 compact-evidence manifest/report/CSV。原始 manifest 中保留执行时输入路径、SHA、配置、命令、二进制和状态字段。
- `records/stage2/`：scenario JSON、resolved config/XML、逐 period XML、Stage2 日志、面积杂波报告/时序诊断、真值和 `period_files.csv`。IQ 数据本体不在包中。
- `records/production/`：逐 branch 实际 `production.xml`、配置与 core 日志、运行时配置快照、CFAR geometry/meta、逐期 CSI tap/ROI manifest、校准 adapter/reference、detection 与 TrackManager CSV。全图 CSI `.npy` 和 final union `.f32` 不在包中。
- `analysis/`：最终离线 evaluator 的完整 CSV/JSON，包括逐图 CFAR audit、F1/F2 输入 hash audit、paired Stage2 input audit、目标 ROI samples、关联、summary 和 manifest；`historical_cfar_impact.csv` 记录 R8/R9/R12 的修复前后逐角色重算。
- `figures/r13_seed101_target_metrics.png`：TO 目标响应及正确目标检测机会的描述图；错误检测行数不是 Pfa。
- `reproduce.sh`：从仓库根目录重建两 case 并执行本包 evaluator 的命令。完整原始结果会重新生成在一个新 outputs 目录。
- `CHATGPT_REVIEW_PROMPT.md`：固定到本包审查 commit 的可复制提示词，规定 ChatGPT 核验哪些代码/证据并按仓库审查协议作出方向判断。
- `cleanup_ledger.md`：本次仅对这两个新 R13 case root 清除的派生大数组和保留材料记录。

Production/Stage2 CSV、日志与运行时文本保留生成时的 CRLF 和列对齐空格；证据包 `.gitattributes` 允许这些输出格式，未改写其记录内容。

`A0_OFF` 的 Stage2 报告写入的“本次新注入面积杂波为 0”是因为它重用 `A0_background`；面积杂波有效性由配对 `A0_ON` writer 报告以及逐期 OFF/background 输入 SHA 对应关系共同确认。`A0_ON` 报告 390 continuous-area packets、1,952,655 samples、实测均功率 0.0223353、lag-1 rho 0.980007。完整配对表在 `analysis/paired_input_audit.csv`。

选定 24 个 branch 的 Production gate 均通过，36 个 C2/C3 adapter 行均为 OK、truth-used=false 且无 Current fallback，72 张 final union map 的几何/日志/map 审计通过，18 组 F1/F2 输入审计闭合。完整旧 runner manifest 仍显示 `completed_with_gaps`，其旧 residual-closure 总结要求 R13 子集中没有运行的 A0/PK/PKR 组合；该汇总状态保留在 manifest 中。R13 evaluator 对所选任务结果的状态为 `evaluated`。两种状态不可混为一谈。

## 主要判读边界

- C2 和 C3 都用目标自由 OFF 拟合固定参考，再应用到 ON/TO；C0 不读取 paired OFF。C2/C3 彼此属于同一 reference 信息类，C0 对比是 operational capability 对比，不是单纯算法因果比较。
- 正常速度 TO retention 与正确检测呈现一个值得复核的候选差异；低速挑战样例不支持稳定方法差异。仅一个独立 seed、每场景三个目标 period，不做统计显著性推断。
- OFF final-union hit fraction 是本次 H0 的观测命中比例，不是 Pfa；truth-filtered false-CUT estimator 不存在，经验 Pfa=`NOT_EVALUABLE`。
- Doppler 仍未解模糊，真实 LOS 径向速度 RMSE=`NOT_EVALUABLE`。TrackManager 地速是另一物理量。
- 本次 delay error=0 ns，没有检验未知通道误差损失、Known correction 恢复量或盲估计与 Known 的差距。Mode-A 不是盲估计。
- `PERFORMANCE_VERIFIED=false`。不据此训练 AI，也不自动启动后续 seed。

查看方式：先读 `../../R13_minimal_experiment_design.md`、`../../R13_minimal_comparison_results.md`、`../../R13_research_direction_assessment.md`，再沿本包 manifests 与逐 branch records 核对决定性结果。

历史 CFAR 重算入口：从仓库根目录运行 `python3 scripts/phase_r/audit_r7_historical_cfar_impact.py`。该命令使用 R8/R12 紧凑评价清单和本机 `outputs/phase_r_r9_deterministic_delay_20261023/evaluation/`；若原始 R9 outputs 不在当前机器，需用 `--r9-evaluation-root` 指向相同 schema 的保留目录。它只写此包中的 `analysis/historical_cfar_impact.csv`，不会改写历史 evaluation 文件。历史 raw maps 未纳入此紧凑包，CSV 中的当前值是基于已保存 map-audit sidecars 由当前 R7 evaluator 重算的结果。
