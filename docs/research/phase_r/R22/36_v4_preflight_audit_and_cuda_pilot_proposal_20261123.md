# R22 V4 预检审计与 CUDA 尺寸 pilot 提案

日期：2026-11-23
审计基线：`7a35435d39959e9f7191df4014009072ab6308ea`
研究总纲 blob：`e637e3769bde7814298ff012d0f42301884e2985`
状态：**`BLOCKED`**；没有启动 CUDA。

## 1. 范围与结论

本补充只完成 V4 冻结前的 seed、统计和尺寸 pilot 准备审计。先读取了当前分支的完整研究总纲、动态状态、决策日志、治理协议、R22 V4 草案、V4 CPU 集成报告、pilot readiness、机器执行计划以及 V3.2/V3.3 seed 与运行记录。研究总纲正文和历史实验结果未修改。

当前 V4 实现保持 `DRAFT_NOT_FROZEN`。V4 CPU 集成此前记录为 `110 passed`，只能说明代码路径的 CPU 集成行为；没有 Stage2、Native CUDA Production 或真实 V4 数据。因此：

| 项目 | 状态 |
|---|---|
| V4 执行计划/归档/续跑实现 | `IMPLEMENTED=true` |
| 受影响 CPU 集成回归 | `CPU_INTEGRATION_TESTED=true`（110项） |
| V4 Stage2/CUDA Production/pilot | `EXECUTED=false` |
| V4 真实结果可评价 | `EVALUABLE=false` |
| GMTI 性能验证 | `PERFORMANCE_VERIFIED=false` |
| 当前准入裁决 | **`BLOCKED`** |

## 2. Seed 注册和历史碰撞审计

### 2.1 实际执行证据

读取 `outputs/r22_v3_2_screening_20261106/analysis/case_audit.csv` 与对应 `analysis_manifest.json`：

- V3.2 matrix 状态为 `COMPLETED`，完成 288 个 case；
- case audit 中有 12 个已执行 target-present seeds：`1932001–1932012`；
- V3.2 设计把 `1933001–1933030` 保留给 Confirmatory，V3.2 Screening 的实际 case 没有使用这段预留 seed；
- 已完成的独立 H0 seed 为 `1935001–1935030`，与 V4 提出的 H0 seed `1936001–1936030` 不重叠。

因此，V4 target Confirmatory 复用 `1933001–1933030` 是沿用此前未执行的 Confirmatory 预留，不是与 V3.2 已完成 Screening 的执行冲突。它们仍不得用于尺寸 pilot，否则会把 pilot 场景并入确认集身份。

在本次新增审计和 pilot 草案配置之前，既有本机 R22 设计、case audit、matrix manifest、case 路径和 compact archive 命名中没有发现 `1937001` 的 R22 seed identity。它被选为尺寸 pilot 的本地候选 seed，明确标为 `PROPOSED_NOT_RESERVED_OR_EXECUTED`。新建审计/配置文件现在会有意记录它们，不构成历史执行证据。机器审计记录在[seed registry 审计](evidence/v4_integration_closure_20261123/seed_registry_audit.json)，单 case 提案配置在[尺寸 pilot 草案](config/r22_v4_size_pilot_draft_20261123.json)。

**审计限制：** 这是本地 R22 范围内的检查，不等于完整项目、未挂载外部归档、其他研究阶段或所有历史 Git object 的全局 seed registry 证明。最终冻结前仍需对完整项目 registry/归档索引复核 `1937001`，并锁定用于 pilot 的配置与数据身份。

### 2.2 Pilot seed 规则

pilot 仅用一个 `1937001`，不得取自以下确认/历史数据集：

- V4 target Confirmatory：`1933001–1933030`；
- 已执行 V3.2 Screening：`1932001–1932012`；
- V3.2 H0：`1935001–1935030`；
- V4 H0 candidate：`1936001–1936030`。

若最终全局审计发现 `1937001` 已占用，应从完整 registry 中按相同排除规则选择第一个尚未出现的 seed，并在 pilot 配置冻结前更新配置和审计记录；不得运行后再换 seed。

## 3. 独立统计设计审计

V4 草案的主要比较为 `+4 ns / 20 dB` 下 M1 Causal Prefix 相对 Fixed D3 的 seed-level 位置误差差值。每个 seed 对三个速度分层等权汇总；推断单位为独立 seed，bootstrap 对 seed 的完整速度/目标/周期簇重抽样。这一统计单位优于把目标、周期、beam 或 CUT 当独立样本。

但作出确认性成功裁决所需的阈值尚未定义：

- `position_mde_m = null`；
- `detection_coverage_noninferiority_margin_pp = null`；
- 若要声明完整航迹保护，还没有相应的工程非劣界值。

这些值是工程需求，不可从 V3.3 的 full-case batch 筛选差值、V4 的因果前缀历史外推或单 seed pilot 反推。尤其 V3.3 的 M1/Huber 使用整十周期 batch，与 V4 当前/历史周期前缀估计的信息时序不同。30 个独立 seed 的计划样本量在没有工程 MDE、非劣界值及可迁移的因果估计方差前，无法判定是否有足够功效。

`M1_CAUSAL_PREFIX − HUBER_D3_CAUSAL_PREFIX` 被标为预注册次要比较；草案没有独立工程 MDE 时，只能报告描述性区间，不可据此宣称 M1 相对同信息传统估计器有工程显著优势。H0 false-alarm 终点也必须以 seed 聚类，并与 target-present Pd/参数任务分开。

**统计裁决：** 统计推断框架的单位与配对方向基本明确，但决策阈值和功效依据未闭合。**禁止冻结确认性 V4 或解释确认性结果，直到工程需求方批准 MDE、检测覆盖非劣界值及必要的航迹保护界值。** 不代替用户设值。

## 4. 最小 Native CUDA 尺寸 pilot 提案

### 场景与处理

仅一个 target-present `+4 ns` case，用于测量完整分支、归档和清理路径：

| 项 | 冻结候选值 |
|---|---|
| seed | `1937001`（待全局 seed registry 最终确认） |
| 径向速度 / SNR | `2.5 m/s / 20 dB` |
| 时延 | `+4 ns` |
| 目标数 / 周期 | `20 / 10` |
| 波位 | beam 50，1 beam |
| 连续面积杂波距离窗 | 84.5–85.5 km；设计均值功率 0.02、纹理 sigma 0.25、lag-1 rho 0.98、噪声功率 0.001 |
| 后端 | 本机 Native CUDA；`USE_COREX=false` |

此 case 通过 V4 专属路径覆盖 `A0`、`C0`、evaluator-only `PK_KNOWN`、Fixed D3、Huber D3 Causal Prefix、M1 Causal Prefix；其中 M1 batch-10 只保留离线诊断身份，不进入在线排名。每种适用 Production 方法需要 ON/OFF/TO 三角色处理，OFF 与辅助 TO 分支不得误计为部署科学比较；ON/TO 为 target-present 任务分支。相关参考拟合方法之外的 Mode-A/B 不适用项不人为复制成新方法。盲估计仅读取当期及历史 ON C1/C2，不读取真实误差、目标真值或配对参考；PK 的真实误差只用于 evaluator-only 校正。

该 pilot 不含 0 ns 配对，不提供因果性能对照，只测资源、归档和完整执行证据。机器配置有一个可执行的 `engineering` case；其他 seed-set 项仅用于满足现有矩阵 schema，不能增加到本 pilot 的 phase 中。

### 预期归档与测量

预期每个 case 有 matrix checkpoint、`r22_run_manifest.json`、实际 runtime XML、Stage2 与 Production 日志、输入和源码身份、每方法/角色分支结果、逐周期 Detection/TrackManager/PIPE lineage、CFAR geometry、逐格目标复数响应证据、V4 archive manifest 与 compact ZIP、raw cleanup ledger。成功归档后，逐成员、SHA、CRC 和必需 V4 members 全部校验通过才可删除 raw。

只报告可行性度量：Stage2/Production 调用数与耗时、分支状态、raw/CSI/Production 各部分字节数、峰值占用、ZIP 大小与压缩率、验证耗时、cleanup ledger 回收字节、恢复身份校验结果。不得用这一 seed 报 Pd/Pfa 显著性、方法性能改善或总体科学结论。

### 磁盘结论

单个 `+4 ns` case 的 Stage2 原始输入和四类校正三角色数组未压缩下界约 **8.856 GB = 8.25 GiB**，还不含 CSI/Production 输出、runtime/audit 文件以及 raw 与 compact ZIP 并存空间。审计时项目所在卷约有 **8.5 GiB** 可用，独立 `/tmp` 约有 **7.3 GiB** 可用；两者各自都不足以安全容纳原始输入下界及额外输出。工作树约 4.8 GiB、项目目录约 5.3 GiB，仍低于最终 20 GiB 上限。中途允许超过20 GiB不代表可以超出底层文件系统的可用空间。

没有删除 R22 历史产物：已检查的 V3.2/V3.3 case、失败现场和归档仍支撑当前审计，没有证据能证明其可由其他材料替代。新增文档/配置仅为小型文本；本次没有生成大数据。

## 5. Pilot 阻断条件与停止规则

当前明确裁决为 **`BLOCKED`**。在下列各项解除前不执行命令：

1. 工程需求方确认 V4 主比较定位 MDE、检测覆盖率非劣界值及必要的航迹保护界值；
2. 完成覆盖所有可访问项目 registry 和归档索引的最终 seed 核查，确认 `1937001` 未用；
3. 独立审查通过当前源代码/二进制/运行时身份，先建立正式 CUDA 构建身份；
4. 用户单独授权这个尺寸 pilot；
5. 提供专用卷，空间门须覆盖8.25 GiB未压缩下界、实测/保守估计的其他输出、ZIP 与 raw 并存和系统余量。目前没有足够实测值据以给出安全的固定 GB 阈值，不能把8.25 GiB当作空间门。

运行时若连续面积杂波 gate、输入/seed/真值映射、Production branch identity、CFAR/beam gate、目标响应或必需归档任一失败，保留失败现场并停止；不降低阈值、不切换 fallback、不继续确认性矩阵。archive/续跑身份不一致或空间跌破批准余量时立即停止；只有完整归档通过全部验证后，才按 ledger 清理 raw。pilot 完成后停止并交由用户审查，不自动启动确认矩阵或 AI。

## 6. 仅供后续批准后使用的命令草案

下列命令针对单 case V4 archive profile，输出放在单独挂载卷，runner 会先保留 raw、验证完整 compact archive，再写 cleanup ledger 并清理 raw。**该命令没有执行；现阶段因为本报告所列条件未满足，复制执行仍不合适。** 通用矩阵 runner 不会仅凭 JSON 中 `cuda_authorized=false` 自动禁止运行，用户/操作者必须先完成上述外部门控。

```bash
: "${PGRCC_V4_PILOT_VOLUME:?先将此变量设为经批准且容量合格的专用挂载卷}"
test -d "$PGRCC_V4_PILOT_VOLUME"
df -h "$PGRCC_V4_PILOT_VOLUME"
python3 scripts/phase_r/r22_experiment_matrix.py \
  --design docs/research/phase_r/R22/config/r22_v4_size_pilot_draft_20261123.json \
  --phase engineering \
  --output-root "$PGRCC_V4_PILOT_VOLUME/matrix" \
  --raw-output-root "$PGRCC_V4_PILOT_VOLUME/raw" \
  --compact-archive-root "$PGRCC_V4_PILOT_VOLUME/archives" \
  --stage2-binary build/simulate_stage2_statistical \
  --production-binary build/GMTI_pipe_core \
  --archive-profile r22_v4_output_complete
```

只在该唯一 case 被明确记录为可恢复的中断状态、输入/源码/配置/二进制身份全部相同且原始 checkpoint/失败证据保留时，才可在同一输出根按 runner 规则续跑；不得为 `GATE_FAILED` 改用 `--resume` 绕过质量门。尺寸 pilot 不授权完整矩阵。

## 7. 研究方向裁决

本轮只是工程/统计/资源准入审计，没有新增 GMTI 实测。因此对未知通道误差的任务收益、Known recovery、盲估计差距、低速目标和复杂杂波的科学判断均为 `NOT_VERIFIED`；不支持修改研究总纲、不支持调整长期研究方向，也不支持 AI 训练。总纲 blob 保持 `e637e3769bde7814298ff012d0f42301884e2985`。

**最终门控：`BLOCKED`。** 用户下一步需先确定工程统计阈值并提供可用专用存储；完成后可审查并批准单 case 尺寸 pilot。确认性矩阵仍需另行审批。

## 8. 交付后磁盘复测

提交和推送后重新检查：工作树约 **4.8 GiB**，项目目录单独统计约 **5.3 GiB**，仍低于最终 20 GiB 上限；项目卷可用空间已降至约 **6.1 GiB**，`/tmp`约 **7.3 GiB**。较早预检时项目卷约有8.5 GiB可用；空间下降的来源无法由本次文件差异解释，也没有证据表明是本阶段新增产物造成。没有清理历史实验数据或其他未跟踪产物；单 case pilot 继续保持 `BLOCKED`。
