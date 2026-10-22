# R6 阶段门与下一步方向

## 阶段裁决：`INCONCLUSIVE`

1. **A0/C 历史 seed101 差异能否复现？** 能。A0/A0 控制确定，A0 ON/C ON 的角度、地速和位置 RMSE 精确复现 R3。
2. **第一处连续/离散差异在哪里？** 连续数值先在 F2/channel2；离散 hit flip 首现于 CFAR。生产 detector 的完整 map 差异仍很小，但 hit mask 与最终 candidate selection 已改变。
3. **目标漏检或主要目标参数发生变化了吗？** 冻结 evaluator 下 A0/C 的目标 detection opportunity 都为 3/3；共同邻近候选的估计近似。RMSE差主要由 A0 一条在宽 range gate 边缘的额外 track payload 主导。
4. **Known correction 已被证实能恢复任务性能吗？** 只观察到 seed101 ON 冻结 metric 的数值改善；同一评估下 OFF 的位置/角度反而变差，TO 基本相同；且 CFAR 门限存在明确数值异常。因此稳健的任务收益尚未验证。
5. **未知误差估计应继续吗？** 可保留为候选科学问题，但尚不能称为主要瓶颈。此轮没有盲估计器比较，也没有训练 AI；不允许把 known-correction 上限等同于可实现收益。
6. **现在是否切到低速目标保护或复杂杂波建模？** 不能仅凭 R6 选定。先修复/验证当前 CFAR 数值正确性和目标关联评价口径，再看可信的候选与航迹差异是否仍在。

## 建议的下一阶段（尚未启动）

1. 对 CUDA GO-CFAR 积分图精度作定向修复设计：用直接窗口/float64 reference 建立测试，覆盖大动态范围、循环 Doppler、边缘训练窗、负门限禁止条件；不改变 Pfa、门限或质量门槛来掩盖异常。
2. 仍只用 seed101 原配对 A0_ON 输入做 CFAR 正确性与 A0/A0 回归；确认 thresholds 全有限且非负、CUT 分母一致、hit map 符合 double reference，并再次检查插桩 parity。
3. 在相同修复后程序下重跑最小 A0/C ON/OFF/TO；保存候选/cluster 可追溯关系，明确 target track 唯一关联规则，报告所有关联输出和边界 payload。
4. 若 C 相对 B/A0 的任务收益仍存在，且不是一条边界关联驱动，再增加独立 seed 评估误差损失/已知校正可恢复收益。若收益不再现，暂停未知时延作为主线，转而按证据评估低速目标保护或复杂杂波建模。
5. 只有在确定性盲估计不足、跨 seed 退化与可恢复收益成立后，才评估 AI 是否必要；本轮不授权训练。

本建议只定义下一步方向，不自动启动代码修复、GPU 实验或 AI 训练。
