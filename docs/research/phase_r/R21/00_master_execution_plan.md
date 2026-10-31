# R21 总执行计划

## 目标

修复 R20 评价器已知缺陷并新增独立 `r21-task-eval-v3`，回归历史证据，审计五种冻结方法的公平性；若 Huber D3 能通过同输入与 Production 校正路径审计，则纳入第六种方法。通过数据、物理和生产路径门控后，才使用本地 Native CUDA Production 完成最多两个新 seed 的配对 pilot，并据此评估未知静态通道时延的 GMTI 任务价值。

**当前科研问题：** `+4 ns` 通道时延导致的任务损失能否在独立 seed 中重现；Known 校正可恢复多少；传统确定性估计器离 Known 上限还有多远；静态时延估计是否值得继续研究。

**范围外：** 不修改 `docs/research/RESEARCH_MASTER_PLAN.md`；不训练 AI、不启动 R22、不增加第三个 seed、不做参数扫描或其它误差类型、不修改 Production 信号处理算法、不重新运行完整传统 baseline 矩阵。

## 基线与保护

- 仓库：`https://github.com/shy-sgsg/PGRCC`
- 工作树：`.worktrees/unknown-system-error-next-stage`
- 分支：`codex/unknown-system-error-next-stage`
- 入口 HEAD：`646088b37431703b20a59d1acc424b57ffe011f2`
- 研究总纲：`docs/research/RESEARCH_MASTER_PLAN.md`；入口提交 blob 应为 `e637e3769bde7814298ff012d0f42301884e2985`
- R20 evaluator 与历史 CSV 是只读历史基线；R21 修复放在独立 v3 evaluator，禁止覆写 R20/R19 代码或结果。
- 工作树包含大量已有未跟踪资料；不得批量清理、覆盖、纳入提交。R21 新产物限定在 `docs/research/phase_r/R21/` 和明确的临时输出目录。

## 工作包与阶段门控

### WP0：恢复上下文、证据清点与空间检查

- 核对研究总纲、动态状态、决策日志、治理协议和 R20/R19 阶段报告。
- 清点 R20 v2 evaluator、历史输入 bundle、输出表和现有测试；核实 `range_bin` 真实距离轴来源及 R12 TrackManager 原始行。
- 盘点 seed1001/1002 是否曾用于开发、调参或历史实验；确认 R12 运行配置、Stage2、Production runner 和 correction adapter 的可复用边界。
- 记录磁盘占用；只删除证据确认无审计价值且已被汇总的派生产物。目标工作区总占用小于 20 GB。

### WP1：R21 v3 评价器与历史回归

先写失败测试，再实现最小修复。至少覆盖：

1. track ID 分类：任意未匹配输出 ID、从未目标匹配的 ID、混合 ID、payload 级未匹配数、目标匹配周期、handoff、同时多目标、错误/跨真值关联；使用 R12 seed404 M1 原始保存行重放并检查 ID 集合，不推断根因。
2. Doppler 轴正、负有符号间隔的完整物理映射，含行号换算、轴方向、PRF 闭合及逐机会 Production 物理映射门控；不能仅依赖全局 positive control。
3. 真值 `range_bin` 必须存在、有限、整数、在实际场景合法距离轴内且与其一致；异常真值留审计但从有效漏检分母排除，禁止取整或强制转换。
4. 无目标确认导致空值的指标统一为 `NOT_EVALUABLE_NO_TARGET_CONFIRMATION`；状态 token 大小写统一；不得 `EVALUABLE` 且值空白。
5. 复核 current-frame Detection、Confirmed TrackManager payload、真值匹配 ID 一致性、重复 payload 和跨目标关联。
6. 历史 R19 84 个 ON hit/opportunity/miss/fraction 完全不变；R20 v1/v2 与历史 CSV 原样保留。

**门控：** 新增测试、历史重放及 R8/R12/R18 regression 全部通过后才进入方法执行器和 CUDA 准备。若评价器无法提供有效机会门控或历史冻结数字变化，停止实验并先定位根因。

### WP2：方法公平性与 Huber 可用性

- A0、B(+4 ns)、Known evaluator-only、R9 fixed D3 (`3.4099650318669545 ns`)、M1 (`3.759357402 ns`) 做同输入信息边界及同 Production 算子审计。
- 独立审查 R12 Huber D3 (`3.588055676 ns`) 是否能只读取 B ON raw C1/C2、保持无真值/无 A0/Known IQ/无未来/无 Detection 调参，并经已验证的相同 correction operator 进入 Production。
- 若不存在安全且可审计的 Production correction path，Huber 标记 `NOT_AVAILABLE`，实验最多五方法；不得以离线改写结果冒充 Production。
- 保留估计方法来源和输入角色显式字段；静态 R9 D3 与 R12 Huber D3 不得混称。

### WP3：冻结两个独立 seed 与配置

- 只允许两个新 seed，候选 seed1001/1002；先证明其未参加开发/调参，否则用确定性规则选取此前未使用 seed，并记录选择依据。
- 冻结 `6.7 m/s`、`30 dB`、`+4 ns`、三个目标周期、R12 同一物理/配置和连续面积地杂波；5 方法为 30 个 method×role branches，Huber 通过后 6 方法最多 36 branches。
- 各条件配对 H0/OFF、H1/ON；记录 manifest、配置、输入身份、方法代码身份及全链路公平性。配置冻结后不得根据 Detection 结果调参。

### WP4：真实 Native CUDA Production

- 仅在 WP1、WP2、WP3 的评价器、数据、物理与可比性门控全部通过后执行。
- 先跑一个不计入统计的 smoke。记录 `nvidia-smi` 设备状态、实际 runtime XML、源码/构建身份、Stage2 杂波报告、逐 beam/处理 stage、CSI tap、CFAR geometry、日志、输入/配置身份和运行结果。
- smoke/数据任一质量门失败即停算法比较，保留失败证据并定位根因；不得降低门槛制造成功。
- 生产正式实验每个冻结分支均按 manifest 执行，记录完整 H0/H1 数据与异常退出。没有 CUDA 运行证据就不能写 `EXECUTED` 或 `PERFORMANCE_VERIFIED`。

### WP5：分析与方向判断

- 先核对杂波注入/功率/距离单元、F1/F2 可比性、物理闭合、轴与机会映射、有效 CFAR CUT、目标保持、检测/参数/TrackManager lineage。
- 比较杂波残余、目标幅相、SCNR、检测、可评估虚警描述、参数误差、track 输出与计时。
- 按 seed 给出 `Known−B` recoverable space、`method−B` actual recovered、恢复比例和各参数 common-opportunity 误差；明确指标方向和分母。
- 独立 seed 是统计单位；每 seed 三周期不能当作独立 seed。没有合格独立 H0 设计时 Pfa 标 `STATISTICAL_PFA_NOT_EVALUABLE`，不作显著性主张。
- 对“固定延迟估计”与“广义未知物理状态估计”分别给 `CONTINUE / STOP / PIVOT` 建议；不自动展开后续研究。

### WP6：交付、总纲保护、提交与推送

- 完成 `00`–`12` 阶段报告、manifest、配置、脚本、关键数值表/图和失败案例；统一标注 `IMPLEMENTED / EXECUTED / EVALUABLE / PERFORMANCE_VERIFIED`。
- ChatGPT 审查请求先读取指定审查 commit 的 Master Plan；最终内容提交 SHA、实验源码 SHA 和证据入口在运行与内容提交完成后填写，review prompt 可按仓库既有做法单独追加提交。
- 提交前逐项审阅提交清单；不纳入既有未跟踪资料或忽略的实验目录。验证 Master Plan Git blob 与基线一致；不强推。
- 完成交付后停止，不自动开始任何新科研任务。

## 当前进度（建立计划时）

- WP0 已完成入口阅读、历史证据清点和 seed1001/1002 的当前仓库路径/文本及本地 Git 历史搜索；没有发现这些 seed 的既往使用记录。结论范围与命令见 `01_entry_audit_and_evidence_inventory.md`。
- WP1 已实现 v3 独立评价器并完成历史回放：R19 冻结的 336 项数值全不变；R8/R12 84 项中 66 项不变、18 项两版都不可评估；210 个分支距离 crop 轴闭合；空值可评估状态为 0。详细结果见 `02_historical_evaluator_regression.md` 和 `evidence/historical_replay/`。
- 修复回放发现的无有效机会零值语义后，R21 evaluator/历史脚本/盲方法资格测试共 23 项通过，Python 编译与 `git diff --check` 通过。
- R12 Huber D3 符合进入新 seed pilot 的输入边界：历史 manifest 声明输入仅为 B ON C1/C2，且其新 seed 实际结果会重新从同角色观测估计；历史 raw IQ 已不保留，因此历史 3.588 ns 只作审计线索，不宣称重算。R21 使用同一 `rewrite_float32_protocol_delay` 和 Production `run_production_branch` 路径。
- 当前 worktree 约 2.1 GB，低于 20 GB；没有删除历史证据或其他用户产物。R21 Native CUDA 二进制尚未找到/构建，WP2/WP3 配置冻结和真实 CUDA 执行仍未完成。
- ChatGPT 提示词模板已存在于 `11_ChatGPT_review_request.md`；完整提交 SHA、实验身份和结果须在实际运行与报告完成后回填。

## 最终完成记录（2026-10-27）

WP0–WP5 已完成。执行结果、参数/闭合、因果解释、方向裁决和质量审计分别见 R21 `02`–`11` canonical reports；精简阅读入口见 [`README.md`](README.md)。最终状态：`PASS_WITH_LIMITATIONS`；固定时延只作为有界确定性候选保留，广义状态估计 `NOT_VERIFIED`，AI entry gate `FAIL`，`PERFORMANCE_VERIFIED=false`。

正式汇总器已从 2,109 项文本审计 ZIP 重建 17 CSV 与 6 SVG；111 项离线测试通过。两 seed 的必要 A0/B 原始闭合输入、六方法 ON 合并复数输入和 ON CSI arrays 保留在本机清理后的原路径，未纳入 Git；保留文件 SHA 清单与删减依据见 [`evidence/cleanup_ledger.md`](evidence/cleanup_ledger.md) 和 [`evidence/inputs/r21_retained_binary_manifest.json`](evidence/inputs/r21_retained_binary_manifest.json)。研究总纲正文未改，Git blob 仍为入口值 `e637e3769bde7814298ff012d0f42301884e2985`。

R21 已停止。下一项仅建议在 ChatGPT 独立审查及用户决定后，离线分解 M1–Known F2 复数残差；未启动新实验或 AI 训练。
