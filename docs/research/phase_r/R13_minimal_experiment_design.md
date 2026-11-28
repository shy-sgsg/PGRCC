# R13 最小 CUDA Production 比较设计

## 研究问题与范围

R12 将固定时延估计从主创新方向 pivot 到传统 GMTI Baseline 公平比较。R13 先用冻结的任务评价门比较 Production Current、SCC 与 Robust DDC，检查是否出现需要独立 seed 验证的目标保持差距。它不是未知时延因果实验，也不包含 Known-error correction 或 blind system-state estimator。

## 冻结场景和方法

| 要素 | 冻结值 |
|---|---|
| 常规场景 | seed=101、target speed=6.7 m/s、SNR=30 dB、delay error=0 ns、3 periods |
| 困难挑战 | seed=101、target speed=0.5 m/s、SNR=30 dB、delay error=0 ns、3 periods；只作为低速应力样例 |
| 几何 | beam 50，目标斜距 85 km，平台高度 6 km，地面高度 0 m，area clutter range 84.5–85.5 km |
| 数据角色 | OFF=C+N；ON=S+C+N；TO=S；同 seed 和场景身份配对 |
| 条件 | A0 evaluator control、C0 Production Current、C2 SCC、C3 Robust DDC |
| 校准模式 | C2/C3 使用 Mode-A：OFF 拟合，冻结参数用于 ON/TO；不读取目标 truth 或误差真值 |
| 输入/处理链 | 4-channel protocol IQ → `(1,3)/(2,4)` 融合 F1/F2 → Production 两通道 CSI → GO-CFAR → clustering/localization → TrackManager |
| 处理门 | runtime XML、日志、CSI taps、CFAR geometry/map、final detections、TrackManager debug；门限保持生产值，不降低质量门 |

每场景 12 个 Production branches；两场景共 24。zero-delay A1 OFF/TO 物理配置与 A0 重合时由 runner 复用 Stage2 输出；不把复用目录计为额外独立实验。

## 信息条件与比较解释

四条件未知误差校正结构只在误差校正问题适用时使用。本次 delay=0 的 Baseline 能力对照不需要强行加入 Known correction。A0 与 C0 检查生产 Current 控制闭合；C2 与 C3 共享同类 paired OFF 信息；C0 不消费该 OFF 标定信息。因此 C2/C3 可作同信息类算法比较，而 C0 对比描述生产能力与额外 OFF-reference 方法的差异。

## 阶段门与停止条件

1. 如果生产分支、输入 hash、F1/F2、CSI tap 或 CFAR map/log/geometry 不闭合，停止性能比较并先定位原因。
2. 如果 OFF 输入没有从含面积杂波的背景 writer 派生，停止 H0/杂波比较；不能因 `area_enabled=true` 判定注入有效。
3. 如果 A0 与 C0 不闭合，先检查输入/处理配置；不解释方法效果。
4. 若单 seed 某差异只落在 CSI 中间量、没有任务输出或失败/错误输出代价，则不得判为任务收益。
5. 单 seed pilot 只用于检查流程和候选机制；统计性 Pd/Pfa 与 `PERFORMANCE_VERIFIED` 需独立重复。
6. 本阶段完成后停止，不自动启动后续 seed，也不开始 AI 训练。

## 复现入口

真实运行命令、原始 manifest 与 evaluator 命令见 [紧凑证据包 README](evidence/r13_baseline_cuda_20261024/README.md)。
