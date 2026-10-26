# R19 WP0：R18 证据与复现能力盘点

状态：`COMPLETE`（快照、身份、可重算范围及 R19 实际产物均已核验）。审查基线为 `095d40996bd2a85c5d04b3c8db1b1e9528737b9d`，研究总纲 blob 为 `e637e3769bde7814298ff012d0f42301884e2985`。

## 快照完整性与数据清理事实

R18 的 12 个 `experiment_snapshot` 均可读取。按各自快照索引核对：12/12 的保留文件总数、总字节数及扩展名聚合一致；其中 1 个快照提供逐文件 SHA-256，1,056/1,056 匹配；另 11 个旧索引只有聚合清单，没有历史逐文件 SHA。R19 已为关键当前证据生成独立 SHA 清单，不能把这项新哈希说成旧快照源文件的历史哈希。

- 快照当前保留约 449,167,880 字节。
- 清理索引记录已省略 2,649 个 `.bin`（81,927,583,888 字节）和 14,364 个 `.npy`（27,387,660,384 字节）；12 个原始 `.scratch` source root 当前均不存在。
- 运行 manifest 仍记录原始输入路径与 SHA，但原始 IQ/数组文件已不可用，故无法重新计算这些输入 SHA。
- 当前逐关键文件哈希：[`r19_wp0_key_file_sha256.json`](evidence/manifests/r19_wp0_key_file_sha256.json)；快照索引对照摘要：[`r19_wp0_snapshot_hash_audit.json`](evidence/manifests/r19_wp0_snapshot_hash_audit.json)。

## 可用性分级

| 内容/指标 | 状态 | 实际留存证据与边界 |
|---|---|---|
| 场景、有效面积杂波、运行身份 | `AVAILABLE` | 12 场景的 `scenario_resolved.json`、Stage2 报告、杂波诊断、case/branch manifest、Production XML/log；可复核运行与注入摘要。原输入 IQ 本身已清理，不能重新计算输入 SHA。 |
| 独立真值机会 | `AVAILABLE` | ON/TO 的目标真值 CSV、周期和 beam 信息；离线 evaluator 可读取。Detection CSV 内嵌的 truth 字段不作为关联真值来源。 |
| Gamma 估计与信息来源 | `AVAILABLE` | 每分支 Gamma CSV、compact Gamma 汇总、Mode-A/Mode-B method contract；可复算复权幅相、support、excluded、phase coherence 和跨 seed 描述统计。 |
| CSI/杂波摘要 | `AVAILABLE` | CSI tap summary、ROI manifest、保留的 `.f32` diagnostics；可对保存摘要作描述比较。 |
| 目标专属复幅度/相位传递、可靠 SCNR 重算 | `REPLAY_REQUIRED` | 完整配对 IQ/CSI 复数数组及 target-only/背景支持数组不在快照；混合 ON 功率不能替代目标专属复增益。 |
| CFAR 几何、CUT 分母、hit 数 | `AVAILABLE` | 每分支 CFAR geometry/numerical diagnostics 保存有效 CUT、hit count、hit-index hash、配置及状态。 |
| CFAR hit 的逐点坐标、valid mask、power/threshold map 与空间/Doppler 归因 | `REPLAY_REQUIRED` | 快照中没有 power/threshold/hit-index map 文件；仅有 hit 数与 hash，不能从 hash 反演坐标。 |
| Tested-CUT hit fraction | `DERIVABLE` | 从 `cfar_geometry_diagnostics.csv` 的合格行按 `hit_cut_count / valid_cut_count` 计算；必须标作阈值测试 CUT 比例。 |
| 目标自由 OFF 的描述性 H0 false-hit fraction | `DERIVABLE` | 仅在 OFF 真值确认无目标、geometry 分母与 hit 数合法时，从 OFF CFAR 行计算；同时标注 C0/C1 固定处理、Mode-A OFF 样本内拟合、Mode-B ON 拟合后 OFF 反事实。不得升格为统计 Pfa。 |
| 统计意义 empirical Pfa | `NOT_EVALUABLE` | 三个独立背景 seed、CUT 空间相关，且缺独立 H0 holdout 与预注册不确定性估计；非目标 Detection 行不可替代 false-CUT 分子。 |
| Detection/目标周期一对一匹配 | `DERIVABLE` | 独立真值、最终 Detection CSV 和冻结门可支持统一离线重算；R19 匹配规则见 `02_frozen_task_evaluation_contract.md`。 |
| Detection/CFAR/cluster 层级传播 | `DERIVABLE` | Detection CSV、cluster lineage、CFAR counts 可做数量级分层；hit 的空间映射缺失，不能对单个 CFAR hit 分类。 |
| TrackManager 连续性与目标关联 | `DERIVABLE` | Production `track_frames`, `track_detections`, `track_events`, `track_states`, `track_output_payloads` 和 `track_association_accepts` 可重算可见生命周期与同周期输出关联；不得重建替代 tracker。 |
| ID-switch 物理/关联根因 | `NOT_IDENTIFIABLE_FROM_CURRENT_DEBUG` | 旧快照保存 legacy accepts，没有完整 `track_association_audit_v2` 候选竞争/拒绝原因证据；仅凭 track ID 变化不能归因。 |
| 参数估计误差 | `DERIVABLE` | 真值 CSV + Detection / 生产 TrackManager 输出字段可按一对一匹配重算方位、位置、range、folded Doppler、已解模糊 radial velocity 与 ground speed；缺字段/模糊时逐指标标记。 |
| A0/PK/PKR residual closure | `NOT_EVALUABLE` | R18 冻结矩阵未形成完整 PK/PKR 实际 Production 闭环；仅有摘要字段不能补成相应处理条件。未来需最小重放，且须另行授权 CUDA。 |
| seed909 独立参考差值 | `NOT_EVALUABLE` | attempt3 C3 的 117 组中 116 组 support count 不同，按原冻结 exact-support gate 停止；R19 只审查 gate 方法，不重写旧判定。 |

## 源码、配置和二进制身份

- R18 runner 源码 commit：`014352ace1c4ad0bade0d759a099a47fe9ecfaa9`。
- Release build 源码 commit：`4c6d4d303621324f5e174db0ba1811c5691df2e2`。
- R18 Production `GMTI_pipe_core` SHA-256：`3328d7c61bc36b6d3b906692e355c46c328150c0399fa457e6f871bcb79abaf0`。
- R18 配置 SHA-256：`3af60966332877095e5447247ab08cb39a54ef7a15681f37adf33cc251082ae1`。
- case manifests 记录每个场景输入和各分支身份；原始输入 SHA 可读但无法因源数组清理而重新验证。

## WP0 阶段判断

清理原始数组没有阻止全部离线工作：Detection、Gamma、CFAR 计数、TrackManager 和参数字段可继续审计。它确实阻止逐 CUT 空间归因、功率/阈值图比较、CSI 目标复数保持及完整 residual closure。R19 将分别计算可推导指标并保留受影响指标为不可评估，不将“不具备底层数组”扩展为停止整个复评。

## R19 实际评价产物与阶段门结果

- 已对 12/12 场景、84 个 ON 分支以及配套 OFF/TO 分支执行 `r19-task-eval-v1`；正式结果位于 `evidence/results/formal_matrix/`，代表性试评位于 `evidence/results/pilot_seed606_v6p7_snr30/`。
- 结果包含 Detection 严格一对一任务表、CFAR tested-CUT 与 OFF 描述性统计、Gamma 配对、CSI 混合 clutter/noise 摘要、TrackManager 指标、参数 RMSE、逐指标 evaluability 状态及身份 manifest。
- 3,336 个指标状态中：2,184 `EVALUABLE`、576 `EVALUABLE_DESCRIPTIVE_MIXED_CLUTTER_NOISE`、84 `EVALUABLE_DESCRIPTIVE_ONLY`、276 `NOT_EVALUABLE`、132 `REPLAY_REQUIRED`、84 `NOT_IDENTIFIABLE_FROM_CURRENT_DEBUG`。
- 发现并修正了离线评价脚本的直接 CLI 导入路径、Detection 每周期文件选择、Production Doppler axis 兼容、参数样本返回、TrackManager target-track lineage，以及运行时 dirty 状态采集问题；具体测试与命令见 `03_evaluator_implementation_and_tests.md`。
- 已保留高价值 R19 中间审计、正式结果和单张汇总图；未删除用户已有 `.scratch`、`outputs` 或历史证据。目录现约 2.0 GB，低于 20 GB 上限。
- 本阶段没有新的 CUDA 执行；R18 CUDA 事实仅作为输入历史证据引用，不能记为 R19 执行。
