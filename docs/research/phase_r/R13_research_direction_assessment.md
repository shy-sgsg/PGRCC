# R13 研究方向评估与阶段裁决

日期：2026-10-25。审查基于 seed101 的两个零时延 Native CUDA 场景、24 个 Production branches、map-backed CFAR/F1/F2/CSI 审计和 [R13 结果表](R13_minimal_comparison_results.md)。这是本阶段裁决，不回写 R3–R12 历史报告。

## 结论

**裁决：`CONTINUE` 一项有边界的独立 seed 复核；R13 完成后停止，不自动启动。** 正常速度 6.7 m/s 的 TO 数据出现 target-only CSI retention 从 C0 `−18.18 dB` 到 C2 `−4.11 dB`、C3 `+3.25 dB`，且正确 target detections 从 2/3 到 3/3 的候选差异。C2 与 C3 的输入和 OFF-reference 信息类别相同，C0 不消费该参考；所以不能把 C0→C2/C3 解释成纯算法因果收益。ON 三者都是 3/3，输出错误 detection 很多。一个 seed 的三机会不足以决定瓶颈或论文主线。

建议后续唯一任务：冻结一个新独立 seed，只复核正常速度场景的 C0/C2/C3 配对 OFF/ON/TO；把 C2 与 C3 作为同信息类别的主要比较、C0 作为 Production operational anchor；复用同一 F1/F2/GO-CFAR/TrackManager 与 target gate，重点检查 TO target preservation 是否复现，并同时约束 ON 正确检测、错误输出、H0 描述计数、定位/角度、折叠 Doppler 和地速。若 target retention 差异不复现或只改善中间 CSI、没有可接受的任务收益，则 STOP 该候选，不扩成低速/复杂杂波矩阵。这个后续任务尚未执行。

## 对研究问题的回答

1. **未知通道误差是否降低 GMTI 性能？** R13 的 delay=0 ns 设计没有检验未知通道误差。作为历史窄证据，R12 seed404 的指定 `+4 ns` case 出现 B 0/3、A0/Known 2/3 正确检测；它只说明该注入场景值得保留在局部问题中，不能推广成实系统普遍规律。
2. **Known physical correction 恢复多少？** R13 未运行 Known correction；不提供新估计。R12 的 A0/Known 2/3 与 B 0/3 属历史单 seed结果，不能与本次 R13 零误差 Baseline 混算。
3. **传统盲估计离 Known 多远？** R13 没有 blind estimator；C2/C3 用了配对 OFF 的 Mode-A 固定参考，不能冒称盲估计。此问由 R12 报告限定回答，本轮不更新差距数字。
4. **是否需要继续未知物理状态估计？** 尚无 R13 证据证明必要。先验证有实际任务意义的非 AI 基线差距；不训练 AI，不开发 Router，不扩展六参数估计。
5. **是否转向低速保护或复杂杂波？** 尚不应。0.5 m/s 单 seed 没有稳定方法差距；R13 也没有把复杂杂波作为变量。两者保留为候选，不升为主线。

## 状态与转向条件

- `IMPLEMENTED`：R7 period/beam CFAR evaluator、R13 final-map audit、严格当前周期 TrackManager join、F1/F2 tap/hash audit。
- `EXECUTED`：两场景 Native CUDA、24 branches、真实 Stage2/Production 输入与日志。
- `EVALUABLE`：本报告明示的 H0 observed hit fraction、目标检测/CSI/参数指标、H0 false detection 与 strict track-period count；Pfa 和 radial RMSE 不可评估。
- `PERFORMANCE_VERIFIED`：`false`。需要独立 seed 才能检验候选重复性。

若独立 seed 的 C2/C3 同信息比较稳定改善任务级 TO 目标保持，且没有不可接受的 ON/H0 输出代价，可再决定是否针对低速或复杂杂波作机制实验；若改善消失、只剩 SCNR/CSI 中间收益或任务代价恶化，则停止这项候选。无论哪种结果，AI 均需另外满足强非 AI 基线、稳定残差、推理可用输入、无泄漏和独立场景验证门。
