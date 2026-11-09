# R22 V3.2 初步方向评估

日期：2026-11-09<br>
依据：R22 V3.2 全量 Native CUDA Screening（12 个独立 seed；单 beam、20 目标、10 周期）<br>
状态：`SCREENING_COMPLETE / PERFORMANCE_NOT_VERIFIED`

## 对研究问题的回答

### 1. 未知通道误差是否降低 GMTI 性能？

在本次固定仿真条件下，+4 ns 的 Current（C0）目标命中率均值为 11.69%，无误差参考 A0 为 69.73%，差 58.03 个百分点。四个速度档均呈相近下降，且每档都有 12 个 seed。此结果支持“本场景的时延失配会造成显著任务退化”的继续研究判断，但本轮是 screening，没有统计显著性声明，也不能外推到真实平台或其他失配类型。

### 2. Known physical correction 恢复多少？

PK 命中率为 69.73%，与 A0 近乎相同；own-success 方位、位置、地速与折叠 Doppler RMSE 也接近 A0。这支持当前仿真注入/物理校正链闭合。Known correction 恢复约 58 个百分点的检测差距。

### 3. 传统盲估计与 Known 还有多大差距？

P1 命中率 69.59%，平均低于 Known 约 0.14 个百分点；P2 Mode-A 的 paired recovery 比率约 99.69%，平均低于其 Known PKR Mode-A 约 0.20 个百分点。两者的延迟估计都从配对 target-free OFF 数据取得，因此只能称为当前筛选中的诊断性恢复。P2 Mode-B 只有 1/12 个 seed 有完整 Current/Known/Estimated triplet，不做差距排名。

### 4. 是否继续研究未知物理状态估计？

建议继续，但限定在可解释的物理时延/通道状态估计与误差传播。优先解决估计器输入的信息边界：下一阶段必须只用部署时可获得的 target-present 观测，或者明确说明可获得 OFF 参考的系统假设。当前 P1/P2 结果不能作为在线盲估计能力证明。

### 5. 是否改为低速目标保护或复杂杂波？

暂不调整。0.5 m/s 条件存在退化，但 1.5、2.5、6.7 m/s 也有相近的 C0 损失；没有证据证明低速目标是唯一或最主要瓶颈。全部 case 使用同一种连续面积杂波模型，没有复杂非平稳杂波对照，因此也不能将复杂杂波提升为当前主线。

### 6. 是否需要 AI？

不需要启动 AI。Known correction 与当前诊断性确定性估计的差距很小；但由于估计器使用 OFF 信息，部署可用输入下的确定性能力仍待确认。AI 进入门仍为 `FAIL`，需要先证明部署条件下存在可重复、任务相关且传统方法难以消除的残差。

## 建议的唯一后续方向

先设计一份范围受控的确认性实验契约，再由用户决定是否运行。契约应当：

1. 将估计器限制为部署可得的 target-present 多通道观测，不读取误差真值、目标真值或配对 OFF oracle；
2. 保留 A0、Current、Known physical correction、Estimated physical correction 的配对输入和同一 evaluator；
3. 为经验系统 Pfa 配备独立 H0 seed；
4. 补充目标专属复数响应/幅相指标、参数共同机会比较和明确的径向速度可评价性；
5. 保持 min-support=8、相位门限0.35 rad及现有 fail-closed 质量门；
6. 先做最小范围 pilot 和存储预算，确认后才考虑扩展 seed。

这只是建议，不构成新 CUDA 实验或 AI 训练授权。研究总纲保持不变；低速目标和复杂杂波仍是待证伪候选。

## 证据入口

- [完整执行与结果报告](27_v3_2_screening_execution_report_20261109.md)
- [精简汇总证据](evidence/v3_2_screening_20261106/README.md)
- [ChatGPT 独立审查提示词](CHATGPT_REVIEW_PROMPT_V3_2_SCREENING_20261109.md)
