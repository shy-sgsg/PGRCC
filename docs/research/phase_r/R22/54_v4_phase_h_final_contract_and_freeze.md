# 54｜R22-V4 Phase H Task 1：统计契约修正与用户决策入口

**日期：** 2026-11-26
**范围：** Phase H Task 1；本报告不代表设计冻结或确认性实验授权。
**研究总纲 blob：** `e637e3769bde7814298ff012d0f42301884e2985`（保持不变）

## 阶段状态

Task 1 的统计定义、CPU 方法审查和离线分析链路已完成实现与验证。用户于 2026-11-26 批准本报告提出的研究方向筛选参数包；seed 注册和真实完整归档资源门仍在执行。V4 设计保持 `DRAFT_NOT_FROZEN`，正式确认矩阵仍未授权。本报告没有运行 Stage2、Production、CUDA 确认矩阵或 AI。

## H0 统计定义修正

H0 主事件统一为：**独立 H0 seed 的十个有效 beam-50 周期中，至少出现一次冻结后处理 false Detection 的比例**。独立单位是 seed；周期事件比例只作辅助描述。

| 指标 | 统计角色 | 缺失处理 |
|---|---|---|
| Seed-any-event | H0 主事件率；逐方法、逐误差条件分别统计 | 任一必需周期无效或缺失时，该 seed 不计为零事件 |
| False-confirmed-track-event | 独立 seed 中至少一个唯一错误 confirmed track ID 的比例 | TrackManager 输出缺失时为 `NOT_EVALUABLE`，不计为零 |
| Period-event fraction | 每 seed 的辅助统计量 | 不作为独立样本；周期嵌套于 seed |
| Unique false-confirmed tracks per seed | 辅助统计量 | 仅在航迹输出完整时计算 |
| False CUT fraction | 描述性指标 | 不解释为完整系统 Pfa |

设计 JSON、统计常量、执行器校验和分析器现统一采用同一个 H0 主端点常量。执行器拒绝旧的 period-level 定义；授权模板由当前 registry 生成，携带冻结统计契约的端点身份。模板仍默认 `NOT_AUTHORIZED`。历史 Phase F readiness 模板保留为当时证据，没有回写。

## 离散覆盖率区间适用性审查

使用 CPU 对三个候选区间进行合成审查：paired seed-level t、seed-cluster percentile bootstrap、BCa bootstrap。每个场景 1,500 个外层数据集；每个数据集 30 个 seed；二项配对场景每 seed 200 个机会；bootstrap 每次 1,000 次重抽样。外层随机流为 NumPy PCG64 seed 22029。另以 500 个数据集检查单个 seed 覆盖差增加 10 个百分点的敏感性。

覆盖率按**可评价数据集**计算，非评估比例单独报告；全相同差值始终 `NOT_EVALUABLE`。代表性结果：

| 合成情景 | paired t 覆盖率 / 非评估率 | percentile 覆盖率 / 非评估率 | BCa 覆盖率 / 非评估率 |
|---|---:|---:|---:|
| 重复值离散，真差 +3 pp | 94.73% / 0% | 94.40% / 0% | 92.53% / 0% |
| 配对离散，真差 0 | 94.80% / 0% | 93.93% / 0% | 94.33% / 0% |
| 零值占多数的偏态分布 | 99.93% / 4.40% | 99.52% / 3.67% | 99.65% / 4.73% |
| 近零方差 | 95.80% / 0% | 95.73% / 0% | 95.33% / 0% |
| 所有 seed 差值相同 | 不评估 / 100% | 不评估 / 100% | 不评估 / 100% |

在非劣效边界（真实差恰为 −1 pp，margin 为 1 pp）处，错误通过率为：paired t **2.93%（Monte Carlo SE 0.44 pp）**、percentile **3.20%（SE 0.45 pp）**、BCa **4.40%（SE 0.53 pp）**。paired t 与 percentile 的差小于模拟误差，不能据此称 paired t 显著更优。单 seed +10 pp 扰动使区间端点最大移动中位数约 0.53–0.55 pp，中位区间宽度增加约 18%。

**用户决定：** 检测覆盖差和航迹覆盖差采用 `paired_t`。这是对预先指定筛选方法的批准，不声称该区间在所有分布下都具有名义覆盖率；全相同数据仍不可评价。正确确认航迹覆盖为 `DESCRIPTIVE_ONLY`，不作为成功门。位置主终点继续使用 seed-cluster studentized bootstrap-t，不与覆盖率区间混用。

完整机器结果见 [区间适用性 JSON](evidence/v4_confirmatory_contract/coverage_interval_suitability.json)、[CSV](evidence/v4_confirmatory_contract/coverage_interval_suitability.csv) 和 [审计报告](evidence/v4_confirmatory_contract/coverage_interval_suitability.md)；生成代码为 [audit_r22_v4_coverage_interval_suitability.py](../../../../scripts/phase_r/audit_r22_v4_coverage_interval_suitability.py)。以上全部是 CPU 统计方法证据，不是 GMTI 性能证据。

## 矩阵级离线分析链路

新增 [analyze_r22_v4_confirmatory_matrix.py](../../../../scripts/phase_r/analyze_r22_v4_confirmatory_matrix.py)，只分析冻结 registry 和通过 SHA/CRC、manifest、case schema 验证的完整归档。它复核 registry/checkpoint 身份，按归档 truth 构造计划机会，并将检测覆盖、共同成功定位、航迹覆盖与 H0 seed-any/错误航迹事件分开汇总。

缺失关联行保持为缺失机会，不推断成漏检；缺失真值状态不能默认视为有效；归档评价行的 case/seed 必须与 registry 一致；非法的 `matched` 布尔值直接报错。H0 的 Detection 主端点可独立评价；缺失 TrackManager 数据只使航迹端点不可评价，不会变成零错误航迹。CUT hit fraction 保持描述性，不称为统计 Pfa。

CPU synthetic archive fixtures 的结果：

| Fixture | 分析状态 | 检查结论 |
|---|---|---|
| 归档、方法、计划机会和 H0 输出完整 | `EVALUABLE_ARCHIVE_SET` | 单 seed fixture 的跨 seed 区间仍正确保持不可评价；这是链路验证，不是科研通过 |
| 缺一个目标关联机会且缺 H0 航迹输出 | `INCONCLUSIVE_REQUIRED_EVALUATION_DATA` | 目标 seed 不完整；H0 seed-any 可保留，错误 confirmed-track 上界为 null |
| 缺真值状态行 | `DATA_OR_TRUTH_FAILURE` | 不能当作有效真值机会 |
| 评价行 seed 与注册身份不符 | `INCONCLUSIVE_MISSING_OR_UNVERIFIED_CASE_DATA` | 对应归档被拒绝 |
| `matched` 字段不是合法布尔值 | `ValueError` | fail-closed，不把未知值当漏检 |

尚未对真实确认性矩阵运行此分析器：当前设计尚未冻结，也没有获授权的确认矩阵归档。命令入口可从仓库根目录直接调用：

```bash
python3 scripts/phase_r/analyze_r22_v4_confirmatory_matrix.py \
  --registry <frozen_registry.json> \
  --checkpoint <confirmatory_checkpoint.json> \
  --output-dir <analysis_output_dir> \
  [--archive-root <archive_root>]
```

## 已批准的研究方向筛选参数

以下参数由用户于 2026-11-26 明确批准，作为 Phase H 研究方向筛选标准；**不是产品验收要求**。结构化记录见 [用户参数批准记录](evidence/v4_phase_h/user_parameter_approval_20261126.json)。

| 参数 | 建议值 |
|---|---|
| 主比较及假设 | M1 causal-prefix vs Fixed D3；Scheme A：95% 双侧 bootstrap-t 上端点 `< 0`，且 seed-level 点估计 `≤ −3 m` |
| 定位 MDE | 3 m |
| Target-present / 独立 H0 | 各 30 个 seed |
| 每速度最低共同成功数 | 10 |
| 检测覆盖率非劣界值 | 1.0 个百分点 |
| 正确确认航迹覆盖 | `DESCRIPTIVE_ONLY`，必须报告 |
| H0 两项门 | seed-any-event 与 false-confirmed-track-event 单侧 95% exact 上界均严格 `< 10%` |
| 位置区间 | 10,000 次 seed-cluster studentized bootstrap-t；PCG64 seed 22029 |
| 覆盖率区间 | 检测与航迹覆盖差均用 paired t |
| AI | 不训练 |

在 30 个完整 H0 seed 中，单侧 95% exact 上界为：0 个事件时 9.503%，1 个事件时 14.860%。因此，严格 `<10%` 的两个 H0 门各自都要求零事件。该结论只适用于冻结的 beam-50、十周期 H0 端点，不代表系统级产品指标。

外部 seed registry 尚未提供；本阶段会固定并哈希当前本地可验证 registry，同时明确外部/离线身份未覆盖。用户已批准三个缺失 schema 的真实 size pilot；当前任务继续核验它们的物理存储可行性，不要求迁移唯一完整 P0 ZIP。正式 240-case CUDA 矩阵仍需之后单独授权。

## 磁盘与保留状态

Task 1 期间对顶层项目目录的测量为 **18,708,881,408 bytes allocated**，距 20,000,000,000-byte 上限余 **1,291,118,592 bytes**；该卷当时可用 17,423,712,256 bytes。新增代码与 CPU 摘要为小型文本文件，没有删除或迁移历史结果。既有 Phase F 资源规划的确认性 compact 估算为 2.46–5.28 GB，因此现有保留余量不足以支持全部新增最终归档；完整 P0 原始 ZIP 仍保留在原位置。是否迁移它及目标路径由用户决定。size pilot 获准后仍需按实际临时目录配额和可用空间单独过资源门。

## 验收状态

| 项目 | 状态 | 范围 |
|---|---|---|
| H0 端点及缺失语义 | `IMPLEMENTED` | 代码、draft、执行器和分析器一致 |
| 覆盖率区间方法审查 | `EXECUTED` | 6 种合成场景、3 种方法、CPU |
| 矩阵分析链路 | `IMPLEMENTED / EXECUTED` | 完整/缺失 synthetic fixtures 与 CLI import smoke |
| synthetic fixtures | `EVALUABLE` | 仅证明离线处理路径和 fail-closed 语义 |
| 真实确认性数据 | `NOT_EXECUTED` | 设计未冻结、无矩阵授权 |
| GMTI 性能 | `PERFORMANCE_VERIFIED=false` | 本任务没有运行新的 GMTI 实验 |

阶段机器摘要见 [`task1_cpu_validation.json`](evidence/v4_phase_h/task1_cpu_validation.json)。
