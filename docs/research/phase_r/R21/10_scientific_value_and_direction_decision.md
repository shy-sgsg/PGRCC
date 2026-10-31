# R21：科研价值与方向裁决

## 阶段裁决

- **阶段结果：** `PASS_WITH_LIMITATIONS`。评价器历史修正、杂波注入、Production 分支、Known 物理闭合与任务记录均可审查；seed1001 外层包装器失败如实保留并由离线 evaluator replay 闭合。
- **固定静态时延估计：** `CONTINUE`，仅保留为有界的确定性估计候选和 GMTI 参数/物理基线。此判定不构成论文创新确认，也不授权追加 seed 或 CUDA。
- **广义未知物理状态估计：** `NOT_VERIFIED`。当前只测试固定时延；不可推广到时变时延、幅相、INS/伺服/几何或联合状态。
- **AI entry gate：** `FAIL`。尚无证据说明确定性方法不够用或 AI 能带来额外任务收益；本阶段不训练模型。
- **总体 GMTI 方向：** 继续服从研究总纲；不因 R21 局部结果修改长期主题，也不把低速目标保护或复杂杂波建模升级为已证实的新主线。

## 支持继续作为候选的证据

1. 两个 seed 中 B(+4 ns) 命中均低于 A0（1/3 vs 2/3；0/3 vs 2/3），Known 均恢复到 A0 命中数。
2. M1 与两种 blind D3 的检测机会命中数相同；在共同成功机会中，M1 的 angle/position RMSE 与 F2 residual 在两个 seed 均较低。这符合冻结的“同检测表现时，主要参数和物理闭合方向一致改善”可行性支持形式。
3. M1 仍比 Known 差：两 seed 的方位/位置 RMSE 都更高，F2 RMS 为 0.0123/0.0402，远高于 Known 的约 0.000088。Ground-speed 样本仅 n=1/2 且排序不稳定；radial velocity 与 statistical Pfa 不可评价。

因此只支持把 M1 留作传统候选方法，后续研究先弄清其残差结构，不足以宣称真实系统状态已被可靠估计或 GMTI 总体性能已改善。两个 seed 的方向一致性是 feasibility evidence，不是显著性。

## 对五个阶段问题的回答

1. **未知通道误差是否降低 GMTI 性能？** 在这一个冻结场景中，B 相对 A0 的检测命中下降在两 seed 重现；可支持场景内因果损失信号，不能外推为普遍 Pd 损失。
2. **Known correction 恢复多少？** Known 回到每个 seed 的 A0 命中数；相对 B 的描述命中差为 +1 与 +2。F2 RMS 从约 0.328 降到约 `8.8e-5`，低于冻结闭合门。参数性能以 Known 为参考上限，M1 未达到。
3. **盲估计离 Known 多远？** M1 比 R9/Huber 的 angle/position 与 F2 residual 好，但仍显著落后 Known；Ground speed 证据不足，radial velocity 无法评价。
4. **是否应继续未知状态估计？** 固定 delay 的确定性候选可有界保留；广义未知状态可辨识性与跨工况任务价值仍未验证。
5. **是否转向低速保护或复杂杂波？** 本实验没有针对性对照，不能据此选择这两条方向。也未证明它们比延迟估计更有价值。

## 下一项唯一建议

先由 ChatGPT 按 [`12_ChatGPT_review_request.md`](12_ChatGPT_review_request.md) 独立审查 R21 与研究总纲。原建议的离线 M1–Known F2 复数残差分解、局部优化收敛检查和计算代价审计已完成，见 [`13_offline_m1_residual_and_cost_audit.md`](13_offline_m1_residual_and_cost_audit.md)。这项补充分析将差距定位到 M1 估计后的 C2 标量时延残差，并排除了当前目标函数局部优化未收敛这一解释；它没有提供更一般状态模型或跨场景证据。因此下一步先等待 ChatGPT 独立审查和用户决策，不自动启动新实验。若审查后仍建议继续固定时延候选，再由用户决定是否开展独立场景验证；若不支持稳定任务级可恢复收益，则回到共同 Production 条件下的强传统 GMTI baseline 瓶颈判别。

本报告不是对研究总纲的修改建议批准，也不自动启动任何下一阶段。
