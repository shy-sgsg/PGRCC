# R19 总实施计划

状态：`WP0–WP5 COMPLETE`。R19 仅审计和重新评价 R18 已保存证据；没有运行 CUDA、生成新 IQ 或训练 AI。

## 科研定位

- **研究总纲：** `docs/research/RESEARCH_MASTER_PLAN.md`，审查基线 commit `095d40996bd2a85c5d04b3c8db1b1e9528737b9d`，正文 blob `e637e3769bde7814298ff012d0f42301884e2985`。
- **总体目标：** 判断真实 airborne GMTI 的检测、参数估计和航迹链路中，哪些物理/处理因素形成了可验证的任务瓶颈；传统方法与任何后续方法都按同一任务证据评价。
- **本阶段科学问题：** 在 R18 的零通道时延条件下，C0/C1/C2/C3 的任务输出是否存在严格评价下的差异；Mode-A 与 Mode-B 的中间差异首次出现在哪一级、是否传播到最终 GMTI 任务。
- **必要性：** R18 的旧报告将多个关键指标列为不可评估，且报告中的目标周期命中仅为描述统计。重新审计留存 Production CSV、真值和 TrackManager 记录，可在不重跑 CUDA 的情况下收紧任务评价，并确定哪些结论仍缺原始数组。
- **科研价值类型：** 评价证据与机制定位；不实现新的信号处理算法。
- **停止条件：** 某项结论若依赖已清理且不可替代的 IQ、功率图或阈值图，则将该指标隔离为 `REPLAY_REQUIRED`/`NOT_EVALUABLE`；不以近似量替代，也不扩大到新 CUDA 实验。

## 固定边界

1. R18 正式矩阵为 seed 606/707/808 × 速度 6.7/0.5 m/s × SNR 30/20 dB，共 12 场景；通道时延均为 0 ns。
2. 比较条件保留 A0、C0、C1、C2 Mode-A/Mode-B、C3 Mode-A/Mode-B；Mode-A 和 Mode-B 分层。
3. R18 执行源码 commit `014352ace1c4ad0bade0d759a099a47fe9ecfaa9`；Release build 源码 commit `4c6d4d303621324f5e174db0ba1811c5691df2e2`；Production binary SHA-256 `3328d7c61bc36b6d3b906692e355c46c328150c0399fa457e6f871bcb79abaf0`。
4. 不覆盖 R18 报告、manifest、配置或结果；新评价输出标记 `r19-task-eval-v1`。
5. `RESEARCH_MASTER_PLAN.md` 不得修改；新阶段状态写入动态状态和决策日志。

## 执行步骤与阶段门

| WP | 内容 | 门控/状态 |
|---|---|---|
| WP0 | 盘点 12 个快照、源数据、身份及可重算范围 | `COMPLETE`；见 `01_evidence_inventory.md` 和 `evidence/manifests/` |
| WP1 | 冻结检测、H0、错误输出、跟踪、参数及统计契约 | `COMPLETE`；契约先于 R19 指标生成冻结 |
| WP2 | 新增版本化离线评价器与边界测试 | `COMPLETE`；28 项 evaluator 测试通过，未触发 Production |
| WP3 | 先评代表场景 seed606 / 6.7 m/s / 30 dB，再处理 12 个现存场景 | `COMPLETE`；pilot 与 12 场景离线重算完成 |
| WP4 | 离线分析 Mode-A/Mode-B 的 Gamma、CSI、CFAR、Detection、TrackManager 传播 | `COMPLETE`；可识别层级已分析，底层数组缺失处标为不可归因/需重放 |
| WP5 | 依据总纲评估方向，给出唯一下一优先任务 | `COMPLETE`；建议先 harmonize 复评 R8/R12 既有 +4 ns 证据，等待用户授权 |

## R19 交付入口

主报告、冻结契约、评价器、统一 CSV、身份清单、测试日志和 ChatGPT 审查请求均置于 `docs/research/phase_r/R19/`。对 R18 历史事实的复述只作定位；新计算与旧报告结果并列并注明差异，不回写旧文件。

## 阶段结论

R19 的严格一对一 Detection 复评已对 12 个场景执行。三个 seed、两种速度和两档 SNR 的 delay error 均为 0 ns，因此本阶段不能检验未知时延误差的性能因果效应。高速度下 Mode-A 与 Mode-B 的目标命中、Gamma 和下游输出存在描述性差异；Mode-A 使用 paired OFF 信息，是 privileged reference，不代表在线可部署估计器。缺少完整 CSI/CFAR maps 限制了空间归因；经验 Pfa、radial velocity 和 ID-switch 根因保持不可评估。完整结论见 `04`–`09` 报告。
