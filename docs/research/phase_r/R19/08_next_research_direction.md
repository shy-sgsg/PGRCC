# R19 下一步研究方向

## 唯一建议：先离线统一复评 R8/R12 的 +4 ns 证据

建议下一阶段首先开展**既有 R8 三 seed 和 R12 seed404 +4 ns 四条件证据的离线可重算性审计与 harmonized task re-evaluation**，不启动 CUDA，也不训练 AI。目标是按 R19 `r19-task-eval-v1` 口径重算 A0 / Current+unknown-error / Known-error correction / available blind deterministic estimator 的唯一 truth opportunity、最终 Detection 命中、参数与 TrackManager lineage，并逐条件核验 binary/config/input/runner identity。R8/R12 的历史方向结果可作为输入，但不能预先当作重评结论。

### 进入门

开始指标重算前先盘点各包是否保留：standalone truth、最终逐周期 Detection、Production Doppler axis/PRF、TrackManager current-frame association、条件身份与运行 manifest。逐条件确认输入 realization 配对、Production binary/config 以及 estimator 是否确实未读取 truth/OFF/TO。凡缺少必要字段的指标逐项标记 `NOT_EVALUABLE`，不得从 Detection 嵌入 truth 字段补齐。

### 停止门

- 如果 R8/R12 记录无法按冻结的一对一规则唯一关联，停止对应任务指标重算，列出最小缺失证据。
- 如果运行身份、paired input 或 blind-estimator 信息隔离不成立，不比较该条件。
- 如果 strict task comparison 仍显示失配劣化而 Known 恢复，且 blind estimator 明显落后并在独立 seed 可重复，下一阶段才值得研究传统盲估计性能限制；这仍不自动证明 AI 必要。
- 如果当前保存证据已无法回答，整理一个最小、预注册的 +4 ns A/B/C/D Production replay 设计供用户决定；不得在本建议下自动运行 CUDA。
- 若统一复评不能复现方向、Known 与失配无任务差异，或已有确定性方法足以闭合任务指标，则暂停未知状态估计主线并重新评估研究瓶颈。

## 为什么先做这项工作

这是当前证据链中成本最低且最能改变研究决策的一步：R19 已证明仅靠 R18 零时延数据无法回答未知误差问题；R8/R12 已有 +4 ns 生产记录且给出不同条件的历史信号。先统一评价定义、保留身份并审查信息隔离，能判断是否真的需要新 CUDA。只有该离线门不能回答任务问题时，才提出最小重放设计。

## 授权边界

该工作是下一项研究建议，**尚未由 R19 自动启动**。此报告不申请或预设总纲修改；若重评证据与长期目标发生冲突，先记录于动态状态/决策日志并与用户讨论。
