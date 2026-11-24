# R22-V4 Phase G：研究方向与最终决策准备

日期：2026-11-24

## 1. 给整体 GMTI 目标的贡献

R22-V4 仍处于确认性验证准备阶段。本阶段把独立 seed、共同成功位置误差、检测覆盖、H0 虚警侧与航迹保护的统计语义接入设计哈希和授权门，避免之后从同一批数据中临时挑选判据。它是研究评价基础，不是新的物理发现或算法创新。

R22-V4 P0 单 seed 已显示固定仿真场景中 Current 与已校正条件有较大的检测差异：Current 26/200，Known 140/200，M1 140/200，Fixed/Huber 各142/200。该结果只说明单个仿真 case 值得进入独立重复验证。目标机会、处理周期和方法分支不能替代独立 seed；它不能证明总体 Pd/Pfa 改善，也不能确定未知误差估计器优于强传统方法。历史 V3.3 batch 结果仅作设计敏感度和支持下限敏感性参考，不是 V4 causal-prefix 表现。

## 2. 研究方向裁决

**长期研究方向：`CONTINUE`，仍服从研究总纲。** 当前有界任务是判断物理通道误差校正能否带来可重复的 GMTI 任务收益，以及因果盲估计器相对 Known 上限与强确定性基线的差距。Phase G 没有证据要求修改长期目标或改投其他瓶颈。

**研究准备状态：`READY_FOR_USER_DECISION`。** 统计定义已落到机器设计与默认拒绝授权路径，但科学/工程门槛仍需用户或需求方决定；确认矩阵尚未具备冻结和运行条件。

**AI：`AI_ENTRY_GATE=FAIL`。** 现有证据未建立跨场景、跨独立 seed 的稳定任务差距，也未证明固定标定、因果前缀估计或其他确定性传统方法无法解决该差距。没有启动 AI 或 Router。

**低速目标与复杂/非平稳杂波：暂不转为主线。** 当前 V4 单例与统计准备工作不评价这两类机制，没有证据表明它们比未知通道误差校正更重要或更稳定。

## 3. 冻结前仍需用户决策

请在独立审查后逐项确定，或说明需由哪个工程需求方给出：

1. 位置假设：方案 A（统计优于零且点估计达到 MDE）或方案 B（统计证明真实改善超过 MDE）。
2. 工程 MDE：候选3/6/12 m或提供有任务依据的新值。
3. 每速度共同成功支持下限：候选1/5/10或提供工程依据；历史 V3.3 敏感性未能区分三者。
4. 检测覆盖非劣 margin：候选0.25/0.5/1.0 pp或提供需求依据。
5. 正确航迹覆盖：`DESCRIPTIVE_ONLY` 或设为联合非劣硬门并给出 margin。
6. H0 两项独立限制：seed-any-event 与“每 seed 至少一条 false-confirmed track”的单侧95%上界分别接受到什么水平。
7. 确认 seed 数：30/40/60/100或其他值。历史方差只用于敏感性分析，不能承诺 V4 功效。

本阶段没有替用户选择以上数值。机器设计中对应字段仍为空，授权校验拒绝这些未决值。

## 4. Seed 与磁盘门

2026-11-24 只读查询 GitHub 分支，目标分支与本地 `origin` 引用同为 `ddbf057d8d944c54f94af54e239cc00644988f1a`，master 同为 `39d4165df96eea6c2c090d7d9bf054c637c7b6a1`。此前本地项目审计扫描125个直接身份 manifest、418个 ZIP 与421个嵌入身份 manifest，没有发现候选确认 seed 碰撞。候选范围为 target-present 1933001–1933030、H0 1936001–1936030。

这不是全球 seed 唯一性证明：本阶段没有访问 GitHub Actions artifacts、Releases、LFS 或外部/离线实验注册表。若用户掌握额外归档位置，正式冻结前仍需纳入审计；没有外部位置时，报告应保留此限制，不假称完成全球检查。

当前项目目录实测约18.71 GB allocated、18.38 GB apparent，低于最终20 GB限制；剩余约1.29 GB allocated headroom。唯一完整 P0 ZIP（13,037,254,578 bytes）和 compact（17,603,158 bytes）仍在本机，未移动、删除或上传。完整确认矩阵的四种 schema 只有 target-present +4 ns 有完整 Production case 资源测量；其余三种和峰值临时空间未测。已有2.464/4.225/5.281 GB只是矩阵归档规划代理值。用户允许运行中超过最终容量，但开始确认矩阵前仍须测全四种 schema、峰值临时空间与清理后回到20 GB以内的具体保留方案。本阶段没有启动 size pilot，也没有使用 Google Drive。

## 5. 下一步与停止条件

当前只交付 ChatGPT 独立审查材料和可讨论决策，不启动 CUDA。建议顺序：

1. ChatGPT 按固定 commit 独立审查研究总纲、Phase G 报告、统计代码、执行器和机器证据。
2. 用户确定统计判据、seed 数量；如有额外 seed registry，提供其位置。
3. 单独批准并完成四 schema 与峰值磁盘 size pilot；若容量测量不满足实际空闲空间/清理要求，停止确认实验。
4. 冻结新的设计/registry，并由用户另行授权确认性 Native CUDA 矩阵。

任何后续方案都必须保留 V4 的独立 seed、同条件配对、完整计划分母、未知方法失败显式记录、Known evaluator-only、H0 独立统计及最终20 GB清理门。若 M1 相对 Fixed/Huber 没有可重复任务收益，停止该算法主张；若 Known 未显著优于 Current，则重新检查误差注入和物理校正闭合；若强传统方法已充分解决问题，不转向 AI。

## 6. 最终阶段标签

| 项目 | 状态 |
|---|---|
| 统计实现与授权绑定 | `IMPLEMENTED` |
| 定向 CPU 回归 | `EXECUTED`，49项通过 |
| 新 Stage2/CUDA/Production | `NOT_EXECUTED` |
| 本阶段 GMTI 新性能结论 | `NOT_EVALUABLE` |
| `PERFORMANCE_VERIFIED` | `false` |
| V4 设计 | `DRAFT_NOT_FROZEN` |
| 确认矩阵授权 | `NOT_AUTHORIZED` |
| 研究方向 | `CONTINUE` 有界确认性评估准备 |
| AI | `AI_ENTRY_GATE=FAIL` |
| 当前项目磁盘占用 | 小于最终20 GB上限；P0原件保留本机 |

请使用 [Phase G ChatGPT 独立审查请求](CHATGPT_REVIEW_REQUEST_V4_PHASE_G_FINAL_DECISION.md)；机器证据入口为 [evidence/v4_phase_g](evidence/v4_phase_g/README.md)。
