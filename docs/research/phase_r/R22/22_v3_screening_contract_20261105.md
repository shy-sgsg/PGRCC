# R22 V3：方法级可评价性 Screening 契约

- 状态：`FROZEN_FOR_IMPLEMENTATION_AND_SCREENING`
- 设计 ID：`R22_EXTENSION_20261105_V3_METHOD_LEVEL_CONTINUATION`
- 基线设计：`R22_EXTENSION_20261028_V2_RANGE_ROI`
- 基线研究总纲 blob：`e637e3769bde7814298ff012d0f42301884e2985`

## 1. 目的和实现边界

R22 V2 在一个方法的 reference 不可评价时将整个 case 设为 `GATE_FAILED`，同时丢弃本 case 其他方法仍可审计的任务行，并停止后续队列。V3 将**场景有效性**与**单方法×模式可评价性**分开记录，使已证实的局部 reference support 缺口不再抹掉同场景其他方法证据。

V3 是新版本，不恢复或改写 V2。所有 V2 case/ZIP、配置和状态保持只读；V3 使用独立 design ID、manifest、raw root 和 compact archive root。V2 的18个终态归档只用于离线 dry-run；由于原始 IQ/truth 不可由 Git 快照完整重放且存在一个源码版本边界，它们不计入 V3 的288个新 case。

本次不改变算法、实验因子、目标/周期数量、方法集合、处理路径、门槛或科研主题；不启动确认性/密集/桥接/H0 矩阵，不训练 AI 或开发 Router。完成 V3 Screening 后依据预注册阶段门决定是否进入下一矩阵。

## 2. 保持冻结的实验条件

V3 从 V2 设计文件复制并版本化，以下条件逐项相同：

- Screening 为 `4 speeds × 3 SNR × 2 delay × 12 seeds = 288 cases`；速度 `{0.5, 1.5, 2.5, 6.7}` m/s，SNR `{10,20,30}` dB，时延 `{0,+4}` ns，seed `1932001–1932012`。
- 每 case 20目标、10周期、beam 50；先按设计顺序执行，独立统计单位仍为 seed。
- 方法与模式集合不变：V2 冻结的 C0/C1/C2/C3/C4/P1/P2/PK/PKR、R22 固定 D3/Huber/M1 扩展及其原有 Mode-A/Mode-B 适用规则。
- 连续面积杂波几何、ON/OFF/TO 配对、真值 lineage、共同 F1/F2 Production 链、CFAR/Detection/TrackManager/PIPE 与评价脚本不变。
- processing range `[666,1197]`，每个 C4 range group 133 bins，`min_support=8`，robust phase threshold `0.35 rad`，cell Pfa 配置 `1e-6`。任何一项不得为使结果通过而修改。
- Known physical correction 仅 evaluator-side；盲估计器不得读取通道误差真值、目标真值或配对无误差参考；零时延下不执行物理 correction。

设计文件 SHA-256、method-config SHA-256、runner commit/dirty 状态、Production/Stage2 binary SHA、设备状态在执行 manifest 固定。V3 完整重跑288个 case；不以历史 V2 归档补齐覆盖率。

## 3. 场景级与方法级状态

每 case 先判场景公共有效性，再判预注册的每个 `case × method × mode` ON 任务单元。任何场景级字段缺失均不得降级为方法级失败。

### 3.1 场景级门

新执行的 case 只有同时满足以下条件才是 `CASE_VALID`：

1. Stage2/Production binary、源码/配置、runtime XML 与输入身份完整；CUDA backend 为本机 Native CUDA，`USE_COREX=false`。
2. 配对 ON/OFF/TO 和背景映射、场景身份、真值 lineage 与 truth opportunity 通过现有检查。
3. 面积杂波报告的 `continuous_area_packets > 0` 且 `continuous_area_samples > 0`，实测功率与范围字段可读；`area_enabled=true` 单独不算通过。
4. 共有因果 triplet 与处理几何门通过；无 shared Stage2、全局 CSI、truth、输入、设备或 Production runtime 故障。
5. 不使用 fallback；`truth_used_in_estimator=false`。

对历史 V2 ZIP，最高只能标记 `CASE_RECORDED_GATE_PASS_NOT_REPLAYED`：可检查保存的 gate/manifest/CSV，不得称为本次重放或新的 CUDA 执行。

### 3.2 方法级状态

| 状态 | 规则 |
|---|---|
| `EVALUABLE` | 该方法 ON 与 TO 任务行可评价；必要的 ON/OFF/TO 分支 identity、return code、Production gate、valid-CUT geometry、reference 和 truth mapping 均完整。OFF denominator/unique final-union hit 只按该方法分支计算；跨方法总门仍单独报告。 |
| `REFERENCE_NOT_EVALUABLE` | 该方法 reference 有明确的逐组 NOT_EVALUABLE/support 失败；相关 Production 分支仍正常返回，公共场景门通过，且 `fallback_to_current=false`。保留原因和 support；不填成检测漏失。 |
| `ESTIMATOR_NOT_AVAILABLE` | 盲估计器仅在已有冻结 qualification 明确给出 `NOT_AVAILABLE` 时使用；这是覆盖率缺口，不得从适用分母中删除。 |
| `PRODUCTION_FAILED` | 可定位到单方法分支的真实执行失败时记录方法状态；只有结构化证据证明不是 shared/runtime/input 故障才可继续矩阵。 |
| `TASK_NOT_EVALUABLE` | 任务行因方法专属必需产物缺失或方法专属关联门失败而不可评价；若原因不明确，升级为全局停止。 |
| `NOT_APPLICABLE` | 仅用于冻结设计明确不适用的组合，如零时延物理修正；不进入适用覆盖率分母。 |
| `NOT_RUN_GLOBAL_GATE` | 场景公共门失败后未执行的方法；不计成性能失败。 |
| `UNKNOWN_STOP` | 证据无法确定故障是共享还是方法专属；立即停矩阵，不猜测、不降级。 |

V3 首次执行允许继续的最小局部缺口限定为：已识别的 reference support / estimator qualification 缺口，且该 case 公共门通过、对应 Production 分支返回码为0、无 fallback。若遇到未预注册的 Production/CUDA/输入/truth 故障，先停止并审查，不以“方法级”标签自动放行。

## 4. 可评价覆盖率和公平比较

主覆盖率单位是预先冻结的适用 `screening case × method × mode` ON 任务单元。288个 case 中144个0 ns case各有12个适用单元，144个+4 ns case各有18个适用单元，因此分母为：

\[
144\times12+144\times18=4,320
\]

任何适用规则变更都需在看 V3 结果前生成新设计版本和新分母，不得根据成功文件倒推分母。

报告必须同时给出：全局场景有效覆盖、每个方法/模式的 `EVALUABLE / applicable`、不可评价原因和其 speed/SNR/delay/seed 分布、每个方法真实任务分母、预注册共同可评价配对集合及其覆盖率。方法自有子集均值要标明“条件于可评价子集”。不能只在事后共同成功样本上挑条件排名。

不可评价单元不填0命中、漏检或零性能。CUT、目标机会、周期和多个 mode 不是独立 seed。Screening 的单 seed/场景结果只作筛选与正确性证据，不作显著性 Pd/Pfa 声明；理论 cell Pfa不等于实测系统 Pfa。

方法缺口只解除全方法完整性汇总，不抹掉已评价方法各自的记录。跨方法 `valid_cut_denominators`、`unique_final_union_hits` 聚合若缺项，仍报告 `INCOMPLETE`；只有共同场景、共同机会以及所有预注册方法状态明确时才做相应成对比较。

## 5. 矩阵运行、断点与空间

- V3 使用新的空 matrix/raw/archive 输出根；启动时拒绝复用 V2 路径或不同设计 SHA 的 manifest。
- 每个 case 开始前写 `RUNNING` checkpoint；完成后写方法状态和输入/执行身份，先创建并校验 compact ZIP/SHA，再删除该 case raw 数据。
- 已归档 case 续跑前重验 SHA；已完成 case 不重跑。中断 case 保留旧 attempt，只有用户明确的当前任务授权下可用 `--resume --retry-interrupted` 新建 attempt；这是 **case-level retry**，不是 branch-level checkpoint。
- 已验证的 `CASE_VALID` / `CASE_VALID_WITH_METHOD_GAPS` case 可续跑并跳过。`GATE_FAILED` / `UNKNOWN_STOP` 终态不得直接续跑；先诊断并创建新版本/新输出根。
- 全局失败 ZIP 先完整校验，随后移除对应 raw；若 archive 创建或校验失败，状态为 `ARCHIVE_PENDING` 并停止，不删除 raw。
- 实验过程允许暂时超过20 GB；阶段审查、汇总和最终清理后，项目占用需低于20 GB。每 case 归档后删除可再生 raw，保留 manifest、源码/配置/输入身份、runtime XML、关键日志、CSI tap summary、CFAR geometry、task rows、失败原因和高价值结果。

## 6. V2 离线 dry-run 与 V3 执行前验收

运行状态机前，用 V2 18 个归档做只读离线 dry-run：

- 17个旧 `PASSED` 不改写成 V3 execution，历史任务行应全部保留；
- 旧 `GATE_FAILED` 仍保持该状态；归类为 `CASE_RECORDED_GATE_PASS_NOT_REPLAYED`，C4 Mode-B 为 `REFERENCE_NOT_EVALUABLE`；
- 269/270个适用 ON 单元应评价为可评价，唯一缺口仍为该 C4 Mode-B 单元；
- V2 全方法 CUT/union-hit 门仍标 `INCOMPLETE`；不自动 fallback，不降低门槛，不计算 Pfa；
- 任何无法从归档证明的共享条件均标 `NOT_VERIFIED`，不冒称 replay。

Native CUDA 首个 V3 case 使用冻结队列的第一个 case，不使用 CPU 模拟。验收实际 runtime XML、Stage2 面积杂波实测数、ON/OFF/TO 输入身份、全部预期 Production branch、CSI taps、CFAR geometry、Detection/TrackManager、truth-linked任务行、源码/binary SHA及压缩归档。任何共享生产链、配对、真值或面积门失败，立即停止矩阵。

## 7. 阶段门与交付

V3 Screening 的唯一成功终态为全部288个计划 case有明确终态、归档可校验、方法级 coverage 与失败分布可审查。若资源或外部 GPU 状态使阶段停下，manifest 必须保留 `PARTIAL`/`INTERRUPTED`，报告已完成数量、可恢复入口和未完成条件，不能缩小设计后声称完成。

至少交付：冻结 V3 design JSON、matrix manifest、每 case compact archive、方法覆盖与任务统计、CUDA 身份和资源审计、失败案例、研究方向评价、ChatGPT 固定快照审查提示词。完成 Screening 后停止；不自动启动 Confirmatory、Dense、AI训练或新方向。

## 8. 状态定义

| 标签 | V3 Screening 前/中/后 |
|---|---|
| `IMPLEMENTED` | V3 状态分类和矩阵续跑逻辑实现并通过离线验证。 |
| `EXECUTED` | 仅在本机 Native CUDA 新执行且保存产物后标记；历史 V2 ZIP 不算。 |
| `EVALUABLE` | 场景和具体方法单元按上述门具有完整评价记录；覆盖率需逐方法披露。 |
| `PERFORMANCE_VERIFIED` | 本 288-case screening 本身不自动达到统计确认；只有另行冻结并完成独立 seed 确认性实验后才可标记。 |
