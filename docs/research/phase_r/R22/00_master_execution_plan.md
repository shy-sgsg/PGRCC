# PGRCC R22 总执行计划

状态：`STOPPED_AT_GATE / INCOMPLETE`。工程 pilot 已执行并达到阶段停止门；完整筛选与确认实验没有启动。PKR 支持量矛盾修复及独立复核前，不得扩展 seed 或方法矩阵。

> **状态时间界限：** 上述状态记录的是 2026-10-27 的停止裁决。用户于 2026-10-28 明确要求继续 R22 并完成扩展实验；本轮续行范围、修复后工程门和冻结设计见 [09 延续执行契约](09_extension_execution_contract.md) 与 [机器可读扩展设计](config/r22_extension_experiment_design.json)。旧 pilot 结论保留为历史记录，不作为当前禁止执行的指令。研究总纲仍保持不变。

> **当前执行更新（2026-10-28）：** 续行采用 V2 range/ROI 冻结设计 `config/r22_extension_experiment_design_v2_range_roi.json`（288 screening / 594 全设计 case）。17 个 screening case 已通过；seed1932001、2.5 m/s、30 dB、+4 ns case 因 C4 Mode-B ON reference 有一组 support=7 < `min_support=8` 而 `GATE_FAILED`，runner 已停止。当前无活动 case；confirmatory/dense/bridge/H0 未启动。单 seed 仍为 `PERFORMANCE_VERIFIED=false`，不据此改变长期科研方向。用户明确 20 GB 是结果汇总、审查和清理后的最终项目上限，运行中可超过；最新阶段结果见 [05 资源与执行记录](05_resource_and_execution_plan.md)、[07 执行质量审计](07_execution_and_quality_audit.md) 和 [ChatGPT screening 审查提示词](CHATGPT_REVIEW_PROMPT_SCREENING_CHECKPOINT_20261028.md)。

## 科研定位

- **研究总纲：** `docs/research/RESEARCH_MASTER_PLAN.md`，入口 commit `6fdc4a9d08d91293d47ba6bdd967e8a22a688c4e`，blob `e637e3769bde7814298ff012d0f42301884e2985`。R22 不修改研究总纲。
- **总体目标：** 在真实、可追溯且输入公平的 GMTI Production 处理链中识别可重复的任务瓶颈，公平比较传统方法，并依据任务级证据选择下一项研究问题。
- **科学问题：** R21 的 +4 ns 时延退化、Known 恢复和 M1 估计优势能否在有更多独立场景、更多连续目标及更完整物理条件的任务级比较中复现；目标速度、SNR、误差和目标密度如何改变检测、目标保持、参数估计及多目标航迹表现；强传统基线是否仍留下可恢复的任务差距。
- **研究必要性：** R21 只有两个 seed、三个目标周期；旧阶段又存在 Production binary、配置和评价器不一致。需先区分可直接比较、可重评和仅历史参考的证据，再扩大样本。
- **方法边界：** Known/A0 仅作参考条件；盲方法不得读取真值、Known、A0 配对输入或未来信息。优先比较实现可达且信息条件可审计的传统方法。不训练 AI。
- **主要验收：** 预注册的 seed 级任务指标及其 95% 置信区间、输入/源码/二进制/配置身份、物理生成有效性、方法公平性和阶段质量门均可复查。未达到样本量时标记 `INCOMPLETE` / `INCONCLUSIVE`，不降低门槛。
- **停止条件：** 生成器或生产链不能形成有效的独立多目标场景、统计独立性不成立、方法输入不可比、评价门失效、GPU/存储预算不可承受，或强传统基线已解决任务差距时，停止受影响的性能比较并记录原因。

## 入口快照与保护

- 仓库：`https://github.com/shy-sgsg/PGRCC`
- 分支：`codex/unknown-system-error-next-stage`
- 入口 HEAD：`6fdc4a9d08d91293d47ba6bdd967e8a22a688c4e`
- R21 实验证据基线：`61da2ae010adb72ab824869031fa5c6a5df69ad4`
- 入口总纲 blob：`e637e3769bde7814298ff012d0f42301884e2985`
- 已跟踪工作区：干净。R22 开始时有 4,007 项未跟踪/忽略环境条目；它们均视为已有资料，禁止清理、覆盖或纳入本阶段提交。
- R22 开始时工作树约 6.7 GB，挂载卷可用约 65 GB。这是当时的空间快照，不构成运行期项目占用上限；20 GB 仅适用于结果汇总、ChatGPT 审查和最终清理完成后。
- 提权设备检查（2026-10-27）：NVIDIA GeForce RTX 3050 Laptop GPU，4,096 MiB 显存、约 932 MiB 已占用、GPU 利用率 29%、P8、42°C、约 6.24 W。该状态显示 GPU 正被其他工作使用；未启动 R22 CUDA 批次。启动时须重新检查设备与可用显存。
- 当前 R22 目录此前不存在；截至本计划创建时，没有 R22 实验、seed 或 Production branch 已执行。

## 冻结前工作包与阶段门

| 工作包 | 内容 | 当前状态 | 进入下一步的门 |
|---|---|---|---|
| WP0 | 任务原文、总纲、动态状态、治理规则、分支、空间、GPU、进程检查 | `COMPLETE` | 入口身份固定，保护未跟踪资料 |
| WP1 | R13–R21 Baseline 注册及可比性分类；必要时追溯 R8/R12；核对源码、Production binary、配置、输入和 evaluator | `COMPLETE_WITH_LIMITS` | 历史异构证据已登记；不同 binary/evaluator/信息条件不得混合排名 |
| WP2 | 多目标/多周期/速度/SNR/时延/H0/杂波生成能力、随机子流、配对与物理有效性审计 | `ENGINEERING_PILOT_EXECUTED` | 20 目标单周期真实 CUDA pilot 可生成并进入 Production；多周期航迹与 PKR residual closure 尚未通过 |
| WP3 | 冻结方法集合、24 条筛选条件、C1–C6 确认条件、目标密度/H0 方案、seed 清单、评价器和统计协议 | `BLOCKED_BY_PKR_GATE` | 先解决并复核 min-support 与分组支持量矛盾，再冻结矩阵 |
| WP4 | 6 个独立 seed 工程验证 | `NOT_STARTED` | WP3 冻结且所有质量门通过；当前不得启动 |
| WP5 | 24 条条件 × 每条至少 12 个独立 seed 的筛选实验；每场景至少 20 条独立目标轨迹 | `NOT_STARTED` | WP4 全部质量门通过，不能以场景失败为由换 seed |
| WP6 | 六个预注册 C1–C6 条件 × 每条件至少 30 个全新独立 seed；精度不足时按 10 个 seed 分批，最多 50–60 | `NOT_STARTED` | 方法、源码、binary、配置、evaluator 冻结且与筛选集隔离 |
| WP7 | 40 目标密集压力场景（4 条件 × 至少 12 seed）及 3 目标桥接；独立 H0，每个主要杂波条件至少 30 seed | `NOT_STARTED` | 多目标物理与 Production 生成门通过；不得将 H0 target-scene rows 称 Pfa |
| WP8 | seed 聚类统计、方向判决、审计、数据清理台账、ChatGPT 审查请求和阶段提交 | `NOT_STARTED` | 实际执行与评价完成；未执行内容显式标记 |

## R22 设计量级（待 WP1–WP3 源码审计后冻结）

- 筛选：4 个径向速度 × 3 个 SNR × 2 个时延 = 24 条件；每条件至少 12 seed，共至少 288 个 seed-condition 场景。
- 正式确认：0.5/2.5/6.7 m/s × 20 dB × 0/+4 ns = 6 条件；每条件至少 30 seed，共至少 180 个新场景。
- 密集目标：0.5/2.5 m/s × 20 dB × 0/+4 ns = 4 条件；每条件至少 12 seed，共至少 48 个 40 目标场景；3 目标桥接必须使用相同配对 seed 的共同真值子集。
- 工程 seed：6 个，与筛选/正式 seed 分离；正式确认 seed 与筛选 seed 完全分离。
- H0：每个主要 clutter 条件至少 30 个独立 target-free seed。需要与方法和处理分支相乘的数量将在 runner 审计后计算。
- 目标轨迹：主实验至少 20 条独立定义的连续真值轨迹，优先 10 个连续扫描周期，系统限制时至少 5 个；单个目标周期不作为独立统计样本。
- 初步下界为 522 个 seed-condition 主场景（6 工程 + 288 筛选 + 180 确认 + 48 密集），尚未包括 H0、方法角色分支和桥接重复。实际 Production branch 数、CPU/GPU 时间与数据量必须按 runner 的实际执行结构计算。

## 冻结设计规则

1. 每个随机成分有记录可追溯的 seed 子流或确定性配置身份；不同 seed 必须核验目标状态、杂波和噪声实现确实变化。相同 seed 不同方法/时延条件使用配对基础场景。
2. 目标 ID 唯一；多周期真值连续；速度明确区分径向速度与地速；目标距离、方位、Doppler、可分辨性及难例在执行前冻结。
3. 每个场景实测 continuous-area packets/samples、功率、有效距离/多普勒支持、F1/F2 输入身份与方法间可比性；`area_enabled=true` 不构成有效杂波证据。
4. H0 虚警只用独立、有效的 target-free 场景和有效 CUT 分母；目标场景中的未匹配 detection/track 只作描述性错误输出。
5. 主要推断单位是 seed。先聚合 seed 内目标及扫描周期指标，再报告配对 seed 差、效应量、按 seed 聚类的 95% 区间及冻结的多重比较方法。
6. 不在中间结果出来后替换方法、seed、场景、指标、匹配门或精度目标。代码修复后保留旧结果，按冻结集合重跑受影响比较。

## 当前执行记录

### 2026-10-27 入口恢复

- 已读取用户提供的完整 R22 任务原文及当前分支研究总纲。
- 入口已跟踪状态 0 项修改；完整状态盘点 4,007 项既有未跟踪/忽略路径，均未触碰。
- 项目工作树约 6.7 GB；可用空间约 65 GB；未见本任务实验进程。
- 已提权读取 GPU：RTX 3050 Laptop、4 GB 显存、检查时 29% 利用率；未运行 R22 CUDA。
- 已读取 R21 冻结设计与方法信息合同、R18 方法能力/公平设计、Phase R 当前决策入口。R21 两 seed 的任务比较为描述性 pilot，并未达到 R22 独立重复需求。
- 初步代码证据：Stage2 run config 的 target 容器是 `std::vector<Stage2RunTarget>`，并在 validation 中检查 duplicate `target_id`；周期数可配置。场景/packet RNG 由独立路径生成，但目标状态当前由显式配置给定。是否能在不改模拟器的情况下生成 20/40 个物理有效、可配对目标及其资源成本，仍待完整验证。
- R22 当前结论：`IMPLEMENTED` 仅指本计划和既有程序能力；`EXECUTED=0`；`EVALUABLE=NOT_YET`；`PERFORMANCE_VERIFIED=false`。

### 下一动作

1. 先由 ChatGPT 对 R22 的研究价值、杂波配对证据、盲估计信息边界和 PKR 根因作独立审查。
2. 如用户决定继续，先做 PKR 的离线实现/配置契约审计，解释 6-bin 分组如何产生 support=6 而 `min_support=8`；保持门槛不变，提出最小可验证的分组或支持定义修正方案。
3. 只有离线审计和用户授权后，才运行一个不降门槛的 PKR 修复后工程 gate；若仍不能生成有效参考或 CSI tap，停止该比较。
4. 该 gate 通过并完成多周期/配对/H0 资源审计后，才重新冻结 screening/confirmatory 设计；不得沿用当前单周期 pilot 充当确认实验。

## 2026-10-27｜最小多目标 Native CUDA pilot 与停止门

- **工程执行：** seed900101 在零时延、20 目标、20 beam、1 period 下完成 Native CUDA Production smoke，Production return code 0，Stage2 连续面积杂波 2,600 packets / 13,195,469 samples；`USE_COREX=OFF`。该运行只验证生成和生产路径，不是方法比较。
- **单周期方法 pilot：** seed900202 与 seed900203 均为 2.5 m/s、20 dB、+4 ns、20 targets、1 beam、1 period。seed900202 运行 A0/C0/C2/C3；seed900203 追加 PK/PKR、R9 fixed D3、Huber D3 和 M1。两个 seed 的 target-level 行是 seed 内机会，不是独立统计重复。
- **seed900203 处理链：** 本地 Native CUDA Production，GPU NVIDIA GeForce RTX 3050 Laptop；执行检查记录 P8、6.43 W、48°C、40% utilization、SM 210 MHz。Stage2 binary SHA-256 `5dd0ac4e2536b8bcf2508cc53db8a30780eba75f40c025e13ffaa930f8ba7b9b`；Production binary SHA-256 `9ffc232966bd92a39edfb39b90730851014dfa1ae7237a4ac172a764f31cc566`，`USE_COREX=OFF`。
- **Production 结果门：** case manifest 有 36 条方法/角色记录：30 `passed`、6 `NOT_EVALUABLE`；总状态 `completed_with_gaps`。两个实际 PKR estimator 分支返回 1，相关六个 ON/OFF/TO 评价记录不可评估。其余可评估 ON 方法含 400 target-opportunity rows；种内目标不是独立 seed。
- **观测到的命中数：** seed900202 A0 17/20、C0 4/20、C2/C3 Mode-A/B 各 4/20、R9/Huber/M1 各 17/20。seed900203 A0 16/20、C0 1/20、PK known 16/20、R9 17/20、Huber/M1 各 16/20、C2/C3 Mode-A/B 各 1/20。PKR 为 `NOT_EVALUABLE`。这些单周期数字只作可行性和故障定位记录，`PERFORMANCE_VERIFIED=false`。
- **杂波 gate：** seed900203 A0_ON 物理可达的 84.5–85.5 km 距离窗产生连续面积杂波 130 packets / 650,892 samples，测量平均功率 0.0227563850584，CV 2.04351886688，rho(lag1)=0.98005849427，CTDR lag=2 时 rho=0.960526664864。A1 各报告显示 direct injection=0，但 `scenes_manifest.json`、A1 场景中的 `background_input_dir` 和 `background_reuse_mapping.csv` 证实其复用 A0_ON 写入的同一配对 C+N 背景；A0/A1 causal triplet 均通过 5e-5 门。有效面积杂波 gate 通过。
- **停止根因：** PKR 分组 `range_band_bins=6`，`min_support=8`。两个失败分支各生成 26,637 gamma groups，其中 26,598 组 support=6、39 组 support=3；所有组状态 `NOT_EVALUABLE / insufficient_phase_support`，adapter `valid_groups=0`。Production 日志随后记录 `scan beam quality gate failed: valid=0/1 required=1 min_ratio=0.95`，未产生 CSI tap；A0/PK/PKR residual closure、最终 union hit 与 valid-CUT 汇总因此不可评价。没有降低支持阈值或 beam gate。
- **统计与跟踪限制：** 每个通过的 Production 分支有 527,280 个 `union_dynamic_in_band` 和 `union_full_band` valid CUT；但 H0 经验 Pfa 全部为 `NOT_EVALUABLE_NO_INDEPENDENT_H0_CUT_UNIT`。1 个周期没有 confirmed track；径向速度/地速无有效参数机会。
- **结果与方向：** 未知 +4 ns 下 C0 在两个工程 seed 的单周期检测命中均低于 A0；Known correction 只在 seed900203 有一组可评价命中记录且回到 A0 命中数。盲确定性结果只在 seed900203 作描述观察。由于 PKR gate 失败、统计单位不足和周期数不足，不能估计总体 Pd/Pfa、Known 恢复率或盲估计—Known 的一般差距；AI 必要性未建立，低速保护/复杂杂波不支持升级为新主线。
- **证据与清理：** seed900202、seed900203 紧凑证据包包含 run/case manifest、Stage2 配置与报告、运行 XML/日志、CSI/CFAR 诊断、检测/航迹 CSV 和评估表；raw IQ 与可再生数组不入包，哈希/大小身份保留。空间台账见 `evidence/cleanup_ledger.md`。
- **阶段状态：** `IMPLEMENTED=true`（scenario factory、runner、task evaluator 接入）；`EXECUTED=true`（seed900101/900202/900203 Native CUDA pilot）；`EVALUABLE=部分指标`（单周期命中、部分参数、杂波注入和有效 CUT）；`PERFORMANCE_VERIFIED=false`。R22 full screening/confirmatory `INCOMPLETE`，在 ChatGPT 审查及用户决定前停止。

## 最终完成门

只有在用户 R22 清单要求的历史证据审计、生成器审计、预注册设计、按门控可执行的实际 CUDA 实验、统一评价、统计分析、科学方向决策、质量审计和交付材料均有当前证据时，才能标为完成。若硬件/工程限制导致某批次不能执行，保留完整计划和配置，准确标出 `INCOMPLETE` / `INCONCLUSIVE`、未完成项及具体成本；不得以缩减样本的试验代替确认性结论。

## 2026-10-28 续行执行计划

**当前阶段定位：** 本任务服务于研究总纲的“传统方法能力边界”和“瓶颈识别与机制归因”模块。核心问题是 R21 的 +4 ns 任务退化与 M1 相对传统盲估计的差异能否在多目标、连续扫描、独立场景中复现，并判断低速、SNR、目标密度与 H0 虚警是否提供更重要的 GMTI 瓶颈证据。研究总纲 blob 固定为 `e637e3769bde7814298ff012d0f42301884e2985`，本任务不得修改总纲。

**入口状态：** 当前工作分支 `codex/unknown-system-error-next-stage`，HEAD `fcaa9e26fe9d4af2b2536f3d6b64acccd78e54e2`；跟踪文件无既有修改，约 4,087 个既有未跟踪路径均保留。工程 pilot 首个 0 ns case 已执行 Stage2 并通过面积杂波/配对审计，盲估计得到 +0.188762554 ns；零扩展物理逆算器按边界条件拒绝对无延迟参考输入执行非零校正。该失败发生在 Production 分支启动前，既有失败证据不覆盖、不复用为成功结果。

**必要的设计修订：** +0 ns 是无误差参考，物理 Known correction 为恒等映射；R9 固定 D3、Huber D3 与 M1 的估计值仍可作为估计偏差诊断记录，但不在该参考条件上执行非零物理逆校正。P1/P2 同样不在 0 ns 条件执行逆算；它们的零误差估计只记录为 `ESTIMATE_ONLY / CORRECTION_NOT_APPLICABLE`。+4 ns 条件保留全部盲估计与固定 D3 校正及原有零扩展边界门、PKR `min_support=8`、Production beam-quality gate，不改门槛。该修订须先作为版本化 amendment 写入设计、配置与报告，再以全新证据根重跑受影响工程条件。

**执行顺序与可检查结果：**

1. 在 `run_r22_cuda_pilot.py` 增加 0 ns 的估计-only 分支语义；增加回归检查，证明 0 ns 不调用非零逆校正、+4 ns 仍保留同一严格算子和支持/beam gate。记录失败 case 的估计偏差与原因，不覆盖原证据。
2. 重新冻结 amendment 后的机器可读设计；先运行 seed1930101 的 0/+4 ns 配对两个工程条件。若任一生产门、杂波门、因果配对、真值/航迹 lineage 或 PKR 有效支持失败，停止受影响方法/阶段，保留失败现场并定位根因。
3. 两个首轮条件通过后，完成六个工程 seed 的 12 个配对条件；工程门全部通过后，按实测 wall-time、GPU 占用、存储峰值和 Production 分支数核算完整筛选、确认、密集目标、桥接与 H0 成本。
4. 只有工程门通过且资源门可承担时，执行完整 24 条件×12 seed 筛选、6 条件×30 独立 seed 确认、40 目标压力、3 目标桥接及独立 H0。不得在中间结果后缩 seed、换 seed、减方法或减目标；确需更改周期数时必须先停在筛选之前，保留原设计并冻结有证据支持的新版本。
5. 统一汇总 seed-level、target-level、track-level、H0、参数与资源指标；以 seed 为推断单位，给出配对效应量、按 seed 聚类 95% CI 和多重比较控制；据此更新阶段报告、方向决定、执行审计与 ChatGPT 审查请求。

**续行开始时的资源快照（非 20 GB 运行限制）：** 项目约 6.9 GB、卷可用约 51 GB；当时 GPU 为 RTX 3050 Laptop，但由另一个训练进程占用（约 1.3 GiB 显存、96% GPU 利用率）。不终止该进程；当时 Stage2/静态审计和代码工作可继续，CUDA Production 批次等待设备可安全使用。正式运行前再次记录供电、P-state、功耗、温度、利用率与显存。

**停止与验收：** 不降低既有有效 CUT、CFAR、beam-quality、PKR support 或真值/轨迹匹配标准。若检测退化不能跨独立 seed/场景复现、Known 无任务恢复空间、M1 不优于强传统方法，或资源门证明正式规模不可承担，则明确报告 `INCOMPLETE / INCONCLUSIVE` 与实测成本；不得把工程 seed 或筛选结果写成确认性性能结论。AI 本阶段不训练。

### 2026-10-28 续行检查点

- **零时延修订源码：** commit `490e405343a14be636cb5707fd4135d143e86947`；工作分支为 `codex/unknown-system-error-next-stage`。研究总纲仍为 blob `e637e3769bde7814298ff012d0f42301884e2985`。
- **软件验证：** `tests/test_r22_cuda_pilot.py`、`tests/test_r22_experiment_matrix.py`、`tests/test_r22_scenario_factory.py` 共 29 项通过；Python 语法检查、V1A JSON 解析、`git diff --check` 通过。它们不构成 CUDA Production 证据。
- **设备：** 2026-10-28 02:27 UTC 检查时 RTX 3050 Laptop GPU 利用率 95%，显存占用 2014/4096 MiB，63°C、39 W、P0；PID 1427260 的既有训练进程仍在运行，未终止、未与其并发启动 Production。
- **空间：** 当前 worktree 约 6.9 GB，挂载卷可用约 51 GB。约 5.5 GB 的 `docs/` 主要是 R21 审计输入与证据；唯一可追溯的高价值基线输入继续保留。无任何新清理删除；本阶段生成物按独立目录和 manifest 管理。
- **静态资源展开：** V1A 共 594 cases（294 个 0 ns、300 个 +4 ns）。当前 runner 每个 0 ns case 计划 36 次 Production branch；+4 ns 为 48–54 次（固定 D3必需，Huber/M1依 support gate 而定），全矩阵上界为 24,984–26,784 次 branch。时间和保留产物大小须用修订后工程批次实测，不用旧的一周期时长外推。
- **下一动作：** GPU 空闲后，在新证据根执行 seed1930101 的 0/+4 ns 两个工程 case；若通过，再运行剩余工程 seed。每个 case 完成后立即核验真实耗时、磁盘峰值、杂波配对、CSI/CFAR 和 task outputs；六 seed 工程门与资源门未通过前不启动筛选。

### 2026-10-29｜S1b 处理阶段闭合审计

- **授权范围：** 用户批准在 S1 独立审查后开展局部阶段 trace。只复用 seed1932001、2.5 m/s、SNR30、+4 ns、20 targets、10 periods、beam 50 的既有 ON/OFF/TO Stage2 输入；不生成 Stage2、不扩展矩阵、不改算法门槛。
- **诊断目的：** 定位 `GMTI05 / azimuth 52 / range 1065–1197` 的 `ON−OFF−TO` 闭合从哪一个 Production 处理边界开始偏离。保留 `min_support=8`、相位筛选 ±0.35 rad、range group 133 bins 和现有扫描/CFAR gate。
- **采集边界：** raw DBS 相位校正前、raw 距离相位校正后、CSI DBS 距离相位校正前、C4 adapter 实际输入、C4 adapter 输出。selector `PGRCC_C4_TRACE_GROUP=5,50,52,1065,1197` 限制到一个 period/beam/row/range；未选择时不增加 D2H tap。
- **实现身份：** 新增 `scripts/phase_r/run_r22_s1b_stage_trace.py` 与复数闭合分析器；Production binary 在独立 `/tmp/pgrcc_r22_s1b_cuda_20261029` Release/CUDA build 生成，不覆盖历史 binary。执行时的精确源码、binary SHA、输入 SHA、XML 与运行日志写入隔离 manifest。
- **执行状态：** `IMPLEMENTED=true`、`EXECUTED=true`。RTX 3050 Laptop GPU / CUDA 12.4 上 ON/OFF/TO 三个10周期 Production 分支均返回0；Stage2 未重跑；设备运行前为 P0、87°C、84%利用率、62.85 W、2237/4096 MiB。binary SHA-256、执行源码文件 SHA、逐 period 输入 SHA、实际 XML、分支 manifest 与日志收录于 `support_failure_diagnostic/evidence/s1b/`。
- **直接结果：** Mode-B 原失败组输入与 Mode-A ON C4 前 F1/F2 在133个单元逐值一致。CSI range-phase correction 前 F2 `ON−OFF−TO` 相对 L2 残差为1.8056%，校正后为78.7703%；C4 固定γ adapter 与 `F2−γF1` 公式误差最高约`1.09e-7`。因此最大已观测闭合恶化位于角色独立估计的 range-phase correction 之后，尚未唯一解释 SNR20→30 的 support 25→7。
- **杂波身份核验：** A1 角色报告的连续面积杂波生成计数为0，是因为复用 A0 背景；A0_ON 生成报告为1300 packets、6,508,782 samples、平均功率0.0221596696662，A1_ON/OFF/TO 背景映射均10/10 matched。Stage2 因果 triplet 对61,464,000样本通过`5e-5`容差。
- **裁决：** Production tap/replay `PASS_WITH_LIMITATIONS`；支持数25→7的唯一因果来源 `INCONCLUSIVE`；`PERFORMANCE_VERIFIED=false`。保留 support=8 与±0.35 rad 门限，不恢复矩阵、不宣称检测收益、不进入 AI。详细结果见 `support_failure_diagnostic/S1B_STAGE_BOUNDARY_AUDIT.md`。
- **停止规则：** 重放已完成；如任一输入、role、边界或 CSI tap 身份不一致，保留失败并停止，不回退 Current、不删失败组、不启动筛选/确认/密集/H0 实验。后续仅可在用户审查 S1b 证据后另行决定是否开展共同距离相位反事实。该单 seed 诊断只支持实现正确性与机制定位，不构成性能或统计结论。

### 2026-10-29｜S1c GMTI05 共同 OFF CSI 相位反事实

- **授权与范围：** 用户批准 S1c；仅使用 seed1932001、2.5 m/s、+4 ns、SNR20/30、beam50、GMTI05 的既有 Stage2 输入。不重跑 Stage2，不扩 seed/period/beam，不更改 C4/CFAR/扫描门槛。
- **周期身份修正：** 原十期试跑因相位 sidecar 文件名不携带处理周期、存在覆盖歧义，判定 `INVALID_NOT_EVALUABLE`。原文件已隔离并保留。有效 v2 每分支只输入 `period_0004.bin`，以输入 SHA 将 Production 单输入 `GMTI01` 映射回源 `GMTI05`。
- **执行：** 五个 Native CUDA Production 分支均通过；support gate 仍8、相位门仍0.35 rad。五阶段 trace 覆盖5×133个单元，selected-cell CSV 的离线 C++ 规则重算与 CUDA 结果闭合。完整配置、manifest、日志和关键数据见 `support_failure_diagnostic/evidence/s1c/v2/`。
- **结论：** 自估 CSI phase 下 SNR20/30 support 为25/7；共同 target-free OFF CSI phase 下为21/22，support gap 从18缩小到1。该选定组的 C4 support 对 CSI phase 向量强敏感；ON vector 偏离 OFF 的唯一成因、目标污染根因和任务收益仍未证实。`PERFORMANCE_VERIFIED=false`，R22 Screening 仍 `GATE_FAILED / INCOMPLETE`。
- **当前停止点：** S1c 完成后停止。等待 ChatGPT 独立科研审查；在用户作下一步决定前，不恢复 Screening、confirmatory、dense、bridge、H0 或 AI。
