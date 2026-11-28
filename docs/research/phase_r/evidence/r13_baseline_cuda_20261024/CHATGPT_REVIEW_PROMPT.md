# ChatGPT 独立审查提示词：PGRCC Phase R13

请复制以下内容进行独立审查。

```text
你是 PGRCC 项目的独立科研审查者。请基于仓库中的代码和 R13 运行证据，审查阶段任务是否做对、结果是否值得继续，并判断它对整体 GMTI 研究路线的意义。请主动寻找替代解释和会推翻结论的证据；不要因为代码测试通过或 CUDA 分支退出码为 0 就默认算法有效。

仓库：https://github.com/shy-sgsg/PGRCC
分支：codex/unknown-system-error-next-stage
审查 commit：bb0b5feff6852059a1075cfda614d578a73e1692
执行源码 commit：3289c13bab6457bd1b190e393ddb01d51e9448e5
阶段：Phase R13，传统 GMTI Baseline 公平比较与最小 Native CUDA Production pilot

先确认 GitHub 上实际读取到的 branch、commit 和文件。必须读取：README.md、AGENTS.md、docs/research/RESEARCH_MASTER_PLAN.md、docs/research/RESEARCH_DECISION_LOG.md、docs/research/CHATGPT_REVIEW_PROTOCOL.md、docs/research/phase_r/README.md、R13 六份报告，以及本提示词所列的实现、配置和证据。重点沿结果主张追溯到 manifests、runtime XML/config dump、日志、Stage2 几何/杂波报告、CFAR geometry/map audit、CSI taps、detection 与 TrackManager CSV。另核对 R8/R9/R12 历史 CFAR 重算 CSV 与脚本、R13 common-opportunity 参数比较和单次耗时表、三个既有 Baseline runner 的方法信息矩阵。无需无差别读取仓库全部文件。

主要入口：
- docs/research/phase_r/R13_evaluation_contract.md
- docs/research/phase_r/R13_CFAR_and_velocity_audit.md
- docs/research/phase_r/R13_baseline_capability_matrix.md
- docs/research/phase_r/R13_minimal_experiment_design.md
- docs/research/phase_r/R13_minimal_comparison_results.md
- docs/research/phase_r/R13_research_direction_assessment.md
- docs/research/phase_r/evidence/r13_baseline_cuda_20261024/
- docs/research/phase_r/evidence/r13_baseline_cuda_20261024/analysis/historical_cfar_impact.csv
- scripts/phase_r/audit_r7_historical_cfar_impact.py
- scripts/run_baseline_benchmark.py、scripts/run_baseline_v2.py、scripts/run_equivalent_vs_physical_calibration.py
- scripts/phase_r/evaluate_r13_minimal_baseline_pilot.py
- scripts/run_production_hierarchical_calibration_pilot.py
- scripts/analyze_production_hierarchical_calibration_pilot.py
- configs/research/production_hierarchical_calibration_delay_pilot.json
- src/production_calibration_adapter.cpp 与 src/gpu/gpu_kernels.cu
- tests/test_phase_r13_minimal_baseline_pilot.py、tests/test_phase_r7_task_metrics.py、Production calibration adapter 相关自测

本次证据范围与关键事实（请逐项核验，不要把以下摘要当作已证结论）：
1. 两个 seed101、delay=0 ns 场景：正常目标速度 6.7 m/s 和低速挑战 0.5 m/s；各 3 periods。比较 A0 Production Current 闭合控制、C0 Production Current、C2 SCC、C3 Robust DDC；每个场景有 OFF/ON/TO，合计 24 个真实 Native CUDA Production branches。执行时 USE_COREX=OFF。离线 Python evaluator 只读取本次 CUDA 产物；不能把它说成 CPU 信号仿真。
2. 通道处理链为四通道 protocol IQ → `(1,3)/(2,4)` 融合为 F1/F2 → 两通道 CSI → GO-CFAR → detection/localization → TrackManager。检查 runtime XML 的实际融合配置和同一 scene/role/period 的 F1/F2 输入证据。
3. 面积地杂波由 A0_ON writer 实际注入：390 continuous-area packets、1,952,655 samples，实测均功率约 0.0223353；OFF 重用配对背景，故需联合 A0_ON Stage2 报告及 OFF/background 逐期 SHA 才能判断杂波真实存在。不能把 A0_OFF 自己“新注入 0”误读为 OFF 没有杂波，也不能仅凭 area_enabled=true。
4. 报告称 72/72 final-union CFAR 图通过 geometry、日志和有效 CUT 审计；18 个 F1/F2 scene/role/period 输入组跨 A0/C0/C2/C3 一致；A0/C0 的 map、CSI taps、ROI 闭合。请检查 evaluator 是否使用 final union map 唯一 hit cells 和有效 CUT，而非把 branch counts 相加或用 max 代替跨期统计。
5. C2/C3 在目标自由 OFF 上拟合并把固定参考应用于 ON/TO，truth-used=false；C0 不读取 paired OFF。故 C2/C3 属相同参考信息类，C0 对比包含额外 OFF 信息，不能直接解释为纯算法因果收益。请判断任务设计是否足以支持各层比较，C1 ordinary subtraction 未运行是否显著限制结论。
6. 正常速度 TO：target-only CSI retention C0 −18.18 dB、C2 −4.11 dB、C3 +3.25 dB；正确目标 detections 分别 2/3、3/3、3/3。ON 三者均 3/3，但 false detection rows 较多。检查 common-success pairwise 参数 CSV：TO 上 C0→C2 的共同机会 n=2，位置 RMSE 8.613→28.523 m、方位 0.00555→0.01846°、折叠 Doppler 17.413→3.801 Hz、地速 0.339→3.934 m/s；C0→C3 的位置/方位及 Doppler 相同，地速 0.339→0.413 m/s。低速 TO 三者均 3/3、CSI retention 都约 +0.019 dB；C3 的 TrackManager 地速 RMSE 约 5.15 m/s，但只有 2 个严格关联机会。请判断 CSI/检测候选收益是否抵得上参数代价；不要用不同成功样本的 RMSE 直接作配对因果结论。
7. H0 observed cell hit fraction 不是 Pfa；缺少 truth-filtered false-CUT probability estimator，因此经验 Pfa=`NOT_EVALUABLE`。Doppler 未解模糊，因此 LOS radial velocity RMSE=`NOT_EVALUABLE`；TrackManager ground speed 不能替代它们。
8. 只有一个独立 seed，三个 periods 不是独立重复；报告必须保持 `PERFORMANCE_VERIFIED=false`，不能宣称统计显著 Pd/Pfa 或通用收益。
9. 旧 runner 的 aggregate manifest 为 `completed_with_gaps`，因为其 legacy residual-closure 要求未纳入 R13 子集的 A0/PK/PKR；R13 选中分支的 Production gate 均通过。请判断报告是否充分区分这两个状态，不能隐藏 gap，也不能把它错误当作所选分支失败。
10. 原始 `.bin`、`.npy`、`.f32` 在归档前已从本地两个新 R13 roots 清理；包内保留完整 manifest/config/XML/log/CSV/true input hash/核心图。若某个主张必须依赖已不在仓库的原始数组，应标 `NOT_VERIFIED`，不可假称独立重算。
11. 用 `analysis/historical_cfar_impact.csv` 审核 R7 CFAR 修复对历史派生结果的影响。R8/R9 的历史 per-role `unique_union_hit_cells.count` 是逐期最大值，不能当三期总数；R8 汇总表已使用正确总和，R9 旧表把最大值标为“三期总数”。R12 没有逐期 final-union map audit，修复后 15/15 仍须为 NOT_EVALUABLE。区分 branch hit sum 与 unique union；不要把观测比例改称 Pfa。该 CSV 是使用当前 evaluator 对存档审计 sidecar 重算的结果，历史 raw maps / 部分 audit manifests 未纳入 GitHub 包；超出 CSV 直接覆盖的判断应标 `NOT_VERIFIED`。
12. 审查方法矩阵对 `run_baseline_benchmark.py`、`run_baseline_v2.py`、`run_equivalent_vs_physical_calibration.py` 的实际输入通道、自由度、拟合/paired-C+N 信息、Known-truth evaluator 条件、Production 与测试边界是否准确。它们的 CPU/offline 或 synthetic 结果不得混作 R13 Native CUDA Production 排名。
13. 检查 Production 单次耗时汇总是否明确标成非 benchmark（每角色仅一次，未 warm-up 重复），且没有据此声称稳定性能排序。

研究方向审查边界：
- 本次 delay=0 ns，不回答未知通道误差是否降低 GMTI、Known correction 能恢复多少、盲估计离 Known 多远。
- C2/C3 的 Mode-A 使用 paired OFF，不是 blind estimator。
- `Known-error correction upper bound` 只适用于特定误差校正问题；不是检测、定位等所有 GMTI 指标的绝对上界。普通 Baseline 对照不强制加入四条件设计。
- 不预设低速目标、复杂杂波、系统失配或 AI 是下一篇论文主线。AI 可适用于系统状态、杂波统计或目标/任务模型，但需先证明稳定任务级退化、强非 AI 方法仍有可用输入下的任务相关残差、无 truth/future/paired-reference 泄漏、独立场景公平收益和部署可用性。
- 本阶段不训练 AI、不开发 Router、不扩完整 Baseline 矩阵。请勿自动运行 GPU 或实施代码改动。

严格按 docs/research/CHATGPT_REVIEW_PROTOCOL.md 的 12 节输出：
1. 审查范围和代码/数据身份；
2. 阶段结果是否可信；
3. IMPLEMENTED / EXECUTED / EVALUABLE / PERFORMANCE_VERIFIED 状态表；
4. 相对研究总纲的实际进展；
5. 当前结果不能支持什么；
6. 物理、算法、数据、指标和公平比较问题（按严重性排序）；
7. 当前工作的科研价值、新颖性与更简单的替代方案；
8. 是否要调整总体主题、目标、模块或技术路线；
9. 明确给出 CONTINUE / STOP / PIVOT，并说明证据和停止条件；
10. 下一步唯一优先任务（输入、交付、验收/停止门）；
11. 哪个关键实验结果会改变你的建议；
12. 需要追加到 RESEARCH_DECISION_LOG.md 或经用户审阅后修改 RESEARCH_MASTER_PLAN.md 的内容。

若 GitHub 内容或引用证据不可访问，请列出具体文件并将相关判断标为 NOT_VERIFIED。需要外部文献才能改变方向判断时，只使用可核验的一手论文或官方来源并给链接；不要把外部结果当成 PGRCC 实测收益。不要建议无边界地“多跑实验”，也不要自动启动 R13 之后的阶段。
```
