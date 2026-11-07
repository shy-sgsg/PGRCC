# R15 阶段研究方向裁决

日期：2026-10-25。代码基线：`2b1942453b002045aaba4c1e35a8b93e75d0c38b`。审查范围：R14 seed505 C2/C3 生产记录、TrackManager 源码、固定检测输入上的 Greedy/Hungarian CPU 反事实，以及 paired OFF 的输入身份。

## 裁决

**`STOP`：停止把 R14 这次 C3 目标 track 切换和地速 RMSE 退化作为 Robust DDC 本身的独立算法创新依据。** 现有证据把本次输出直接追到方法相关 Detection 候选分布、Greedy 一对一关联和历史 EN 速度估计；这是一个常规检测/跟踪处理链交互，尚未证明跨 seed 稳定。

此 `STOP` 只作用于该局部主张，不表示停止 GMTI 研究，也不否定未知系统误差可能在其他条件下造成损失。R14/R15 是 0 ns channel-delay 场景，对未知系统失配没有辨别力。研究总纲的长期目标应保持为广义、证据驱动的 GMTI 任务性能研究；未知系统误差是候选模块之一，不是已证的唯一长期主线。AI 必要性没有新证据。

## 关键判读

1. **第一处分歧：** 正确目标 D0 的坐标和 CFAR cell 在 C2/C3 三期一致。p0 的其他候选位置已经不同，导致 track 7/64 建立不同的空间历史；p1 的 target D0 两法仍都进入 track 6，但 track 7 和 track 64 接收的非目标候选不同。
2. **末期关联：** p2 目标 D0 与相邻 D3 同时存在。C2 的 track 7 先以更低代价拿到 D3，track 6 得到目标 D0；C3 的 track 7 不在 gate 内，track 6 拿到 D3，track 64 随后拿到目标 D0。接受边均通过 gate、总代价低于 dummy。
3. **速度根因：** C3 p2 正确目标输出沿用 track 64 的历史速度，历史中包含 p0/p1 的非目标候选；其 p2 speed 为 19.792 m/s，真值 6.7 m/s。该数值链条来自 `TrackManager::updateMotionHistoryFromEN`，不是 detector 的瞬时径向速度，也不是 CSI 参数直接换算结果。
4. **常规关联的反事实：** Hungarian 改变 C3 的末期 target speed 误差由 13.092 m/s 降至 5.389 m/s，但仍发生 ID 切换；C2 误差则由 1.523 m/s 增至 6.138 m/s。它证明常规关联策略能改变这一次结果，不证明 Hungarian 是通用修复。
5. **候选链：** C3 unique CFAR hits `151,087`，多于 C2 `97,155`；聚类后 detections `701`，少于 C2 `990`。Production 聚类对每个保留连通分量只选峰值，因此候选数量不跟 hit-cell 数单调变化。候选分布与轨迹连续性存在本 seed 的可追溯交互，但没有跨 seed 稳定性验证。
6. **OFF reference：** R14 的 Mode-A OFF SHA 与 H0 背景 SHA 逐期相同，H0 是 reference-in-sample。实际 airborne 系统是否能持续获取等效匹配的 paired OFF 未验证；本地 C0 和已实现但未执行的 C1 是不依赖 paired OFF 的确定性路径。

## 状态分类

| 类别 | R15 状态 | 证据边界 |
|---|---|---|
| `IMPLEMENTED` | 现有 TrackManager 已实现 GreedyNearestNeighbor 与 Hungarian；R15 没有改动处理行为。 | 源码存在不等于 R15 新实现。 |
| `EXECUTED` | R14 CUDA Production 结果被复用；R15 另执行 fixed-detection CPU TrackManager replay。 | R15 没有新跑 CUDA，也没有把 CPU replay 冒充 Production 证据。 |
| `EVALUABLE` | 三期 target association、候选代价、门限、track state/speed 和 OFF 输入身份可追溯。 | 仅对 seed505 场景成立。 |
| `PERFORMANCE_VERIFIED` | **否**。 | 单 seed + 一个离线 Hungarian 反事实不能证明统计显著、跨场景或通用性能收益。 |

## 最后问题的回答

这次事件首先是 **常规工程问题**：候选检测变化让 track state history 不同，既有贪心关联将目标分给了不同 track，TrackManager 的历史位置差分速度随之改变。Hungarian 是常规方案之一，但本例结果不统一，不能写成已经解决。

这不是已建立的 Robust DDC 论文级任务瓶颈。值得长期保留的只有更宽的可证伪问题：杂波抑制方法改变候选分布后，是否会跨独立场景稳定影响目标 track continuity 和速度精度。当前一例不足以启动该方向的独立算法开发；无需为解释这次记录再跑 CUDA，因为固定候选离线矩阵已经取得。

## 下一项建议，未启动

不再为这次 p2 track switch 扩 seed或调门限。若用户希望继续最小 Production 比较，建议先在同一 seed505 数据身份下执行 C1 `Gamma=1` reference-free ordinary subtraction 的 OFF/ON/TO 三角色 pilot，与 R14 已有 C0/C2/C3 结果并列评价 target preservation、非目标 Detection、当前周期错误关联及地速；这样能先判断无需 paired OFF 的确定性方法是否有任务价值。该 pilot 仍只支持可行性判断，不是多 seed性能验证。实际部署参考可得性不明时，先给出测量方案，不以 Mode-A 仿真 OFF 替代实机可用性证据。

研究主线维持广义 GMTI 物理处理链与任务瓶颈识别。下一项优先工作应先回答可比较信息条件和目标任务，而不预设未知误差、低速、复杂杂波或 AI 中任何一项已经胜出。没有自动开始 C1、新 seed、完整 Baseline 矩阵或 AI 训练。
