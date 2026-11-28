# R22-V3 方法级可评价性与失败覆盖契约（草案）

> **状态：`PROPOSED_NOT_FROZEN`。** 本文是供用户审阅的实验契约草案，不改变V2协议，不修改 runner/evaluator，也不授权新的 CUDA/Production 实验。实现和冻结前需用户明确批准具体差异。

## 1. 目的和研究边界

V2 的全方法汇总把“一个方法的一条 reference 不可评价”传播为整个 screening case 的 `GATE_FAILED`，从而中断其他方法仍可评价的记录。本契约建议把**场景输入完整性**与**方法级输出可评价性**拆开记录，回答：

> 在保持所有物理、质量和检测门槛不变的条件下，如何报告某个方法的局部失败，同时保留同场景中其他方法可审计的任务数据？

本契约不回答哪种方法性能更好，也不把 C4 support 失败改成检测漏失。V2仍保持 `GATE_FAILED / INCOMPLETE`，其原设计和历史结果不回写。

## 2. 冻结内容沿用 V2

除失败传播和结果状态的粒度外，V3候选设计沿用 `R22_EXTENSION_20261028_V2_RANGE_ROI`：

- Screening：288 case，12个冻结seed，速度0.5/1.5/2.5/6.7 m/s，SNR 10/20/30 dB，时延0/+4 ns；每case20目标、10周期、beam50。
- 保持连续面积杂波配置、配对输入与truth规则、源码/二进制身份记录、CFAR geometry、任务评价定义和统计单位。
- 保持所有冻结方法、Mode-A/Mode-B信息条件及物理校正 evaluator-only 约束。
- `min_support=8`、相位残差门限 `0.35 rad`、range ROI `[666,1197]`、Production quality gate 和 truth/identity检查均不变。
- 0 ns下的物理 correction 适用性沿用V2；不适用项从方法可评价率分母中排除，并单列报告。
- confirmatory、dense、bridge、独立H0的范围和计划不因V3草案自动启动。

V3须有独立设计ID、manifest与空输出根；不得对V2 `GATE_FAILED` manifest使用 `--resume`，不得覆盖V2目录或把旧归档伪称为V3新执行。

## 3. 两层状态模型

每个 case 先判定场景完整性，再对每个预注册的 `case × method × mode` 判定方法状态。每个状态都保留原因、证据路径和源分支身份。

### 3.1 场景完整性

| 状态 | 含义与动作 |
|---|---|
| `CASE_VALID` | 本次运行中输入/场景身份、ON/OFF/TO配对、truth lineage、面积杂波、共同几何和全局Production身份门均有直接证据通过；允许继续分类各方法 |
| `CASE_RECORDED_GATE_PASS_NOT_REPLAYED` | 归档 manifest 记有全局门通过，但原始IQ/truth或必要运行材料不可用，未能独立重放验证；可重分类归档任务行，不能声称新验证或新执行 |
| `CASE_INVALID` | 任一全局必要条件失败，例如输入或真值错配、杂波未注入、共享Production输入/配置身份错误；停止该场景及依赖比较，按V2 fail-closed 原则停阶段并查因 |
| `CASE_UNKNOWN_STOP` | 证据不足以判断失败是全局还是单方法；停止矩阵，不猜测、不将其降格成方法缺口 |

面积杂波 `continuous_area_packets/samples` 必须按现有门要求大于0；配对、truth、范围坐标、共同Production身份等原有门不得降低。环境或共享数据损坏仍属于全局失败。

### 3.2 方法级状态

对每个预注册适用单元至少分别保存 `production_execution_status`、`reference_status`、`task_evaluation_status`，并给出聚合的 `method_case_status`：

| `method_case_status` | 含义 |
|---|---|
| `EVALUABLE` | 该方法的必需角色/参考、Production输出、valid-CUT/unique-hit检查及truth-linked任务记录均有效 |
| `REFERENCE_NOT_EVALUABLE` | Production进程可以返回0，但reference/adapter未满足冻结要求，例如support不足；不能把进程成功当作方法有效 |
| `PRODUCTION_FAILED` | 该方法自己的Production分支失败、缺产物或运行身份不完整；保留失败原因，不计为目标漏检 |
| `TASK_NOT_EVALUABLE` | 方法输出存在，但目标机会、检测关联或该方法的评价分母不满足既定规则 |
| `NOT_APPLICABLE` | 仅用于冻结设计明确不适用的条件，如V2零时延物理校正；不计入适用分母 |
| `NOT_RUN_GLOBAL_GATE` | 场景级门已失败，方法未运行；不是该方法性能失败 |
| `UNKNOWN_STOP` | 不能可靠定位状态归属；触发全局停止，避免把不明故障报告为方法级 |

### 3.3 重新归类 gate 的作用域

- area clutter、输入配对、truth lineage、统一源码/二进制身份、共享Stage2/Production输入损坏属于**场景级门**。
- reference support、单方法分支退出码、该方法OFF分支CUT denominator、unique-hit集合和任务关联属于**方法级门**。
- 现有聚合 `valid_cut_denominators` / `unique_final_union_hits` 仍可报告“全方法完整性”，但一个方法缺失时应同时保留每方法独立门状态，不能让聚合字段覆盖其他方法的有效记录。
- `empirical_pfa` 必须保持独立H0估计口径；不得由目标存在case的CUT hit fraction或H0另一个矩阵替代。

## 4. 覆盖率与性能报告

主coverage单位为预注册的、适用的 `screening case × method × mode` ON任务单元：

\[
Coverage_{m,c}=\frac{\#\;EVALUABLE\;适用单元}{\#\;冻结计划的适用单元}
\]

按V2规则，288个screening case中144个0 ns case各有12个适用ON条件，144个+4 ns case各有18个适用ON条件，共 **4,320个计划适用方法×case单元**。V3若修改任何适用性，必须在执行前给出新的分母表和理由；不得根据观察到的成功文件反推分母。

必须并列提供：

1. 全部计划场景的场景完整性覆盖率；
2. 每个方法/模式的 `evaluable / applicable` 数量与比例；
3. 各不可评价原因的数量及速度、SNR、时延、seed分布；
4. 方法性能指标和其真实分母；
5. 依预先冻结的对比集合计算的共同可评价配对结果及其覆盖率。

不可评价单元不得填成0次命中、漏检、零性能或经验Pfa。方法在自身可评价子集上的均值必须标为“条件于可评价子集”；若方法失败与SNR/速度等条件相关，完整计划域的性能差异视为未识别，不得用事后筛选的共同样本宣布全域排名。覆盖率是结果的一部分，不可只报告成功case。

推断单位仍是seed。相同seed内的速度、SNR、时延、目标机会、CUT和处理周期不是独立seed。单seed screening只作筛选/可行性判断，不报告统计显著Pd/Pfa改善。

## 5. 矩阵继续与停止规则

- 若 `CASE_VALID` 且仅出现已分类的方法级缺口：完整保存该case全部可用方法记录，缺口方法标记为不可评价；继续执行冻结队列中后续case，不自动fallback、不降低门槛、不跳过困难条件。
- 若 `CASE_INVALID`：立即停止受影响阶段，先修复输入/物理/工程根因；不要让其他分支的部分结果覆盖场景无效事实。
- 若 `CASE_UNKNOWN_STOP` 或核心身份缺失：fail-closed 停止阶段；不得通过“方法级”标签绕开未知的全局问题。
- 单方法失败后的重试策略必须在V3冻结前单独规定；原始失败attempt始终保留，新attempt不得覆盖旧结果。本草案不增加自动重试。
- 若所有方法级覆盖完整但任务指标未闭合，仍不可声称 `PERFORMANCE_VERIFIED`。
- V2原始状态、结果表、门槛和失败ZIP全部只读保留。

## 6. 18-case 离线演练的预期语义

离线演练报告见 [V2 方法级离线复核](19_v2_screening_method_level_offline_dry_run_20261105.md)。它显示18个终态case的归档中，area gate 18/18记录通过，+4 ns triplet 9/9记录通过；270个适用ON方法单元的历史task状态为269个可评价，唯一缺口为C4 Mode-B。全方法CUT/unique-hit聚合门仍为不可评价。由于compact ZIP没有原始IQ和完整target truth，V3离线演练对这些终态只能标为 `CASE_RECORDED_GATE_PASS_NOT_REPLAYED`，不能标 `CASE_VALID`。

按本草案，失败case的归档dry-run应显示：`CASE_RECORDED_GATE_PASS_NOT_REPLAYED` + `C4/Mode-B = REFERENCE_NOT_EVALUABLE` + 其余方法保留各自历史行级状态 + `all-method completeness = INCOMPLETE`。这**不**等于全方法排名通过；也不等于新Production执行。V2历史结果仍显示原 `GATE_FAILED`。

## 7. 实现前必须冻结的交付

取得用户对本契约具体范围的批准后，才实现新的V3 evaluator/matrix state machine。实现交付至少要包含：

- 新的V3 machine-readable design ID与独立输出根；
- 方法级状态、场景级门与coverage manifest schema；
- 对所有18个终态V2归档的离线dry-run，确认V2数据未被改写；
- 单方法reference失败、Production失败、全局输入失败、不可适用和未知失败的离线状态用例；
- 在新V3 matrix root冻结与资源审计完成前，不启动CUDA。

离线用例只验证状态传播和归档一致性，不替代Native CUDA证据。任何新CUDA Screening仍需用户对冻结后的V3设计单独明确授权；此前完成离线契约不构成该授权。

## 8. 当前状态

- `IMPLEMENTED`：V3 runner/evaluator **未实现**。
- `EXECUTED`：只完成V2归档的离线重分类；V3 CUDA **未执行**。
- `EVALUABLE`：V2历史case数据可按现有task rows做上述有限离线审计；V3设计仍是草案。
- `PERFORMANCE_VERIFIED`：否。
- 研究总纲保持不变；AI、低速目标和复杂/非平稳杂波均没有因本次离线复核升级为主线。
