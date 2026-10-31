# R22-S1e：当前 Mode-B 下配对 ON/OFF/TO 角色诊断

## 阶段裁决

**S1e：`PASS_WITH_LIMITATIONS / MECHANISM_EVALUABLE`。R22 target-present Screening 仍为 `GATE_FAILED / INCOMPLETE`，不得从该失败状态续跑。**

这次在相同 Native CUDA Production binary、相同 Mode-B runtime XML 下，对同一配对场景的 ON、OFF、TO 三种输入各运行一个 GMTI05 周期。原生 ON 的 robust support 仍为7，小于冻结门槛8；OFF 为55，TO 为14。逐角色逐距离格复数闭合显示，CSI 距离相位校正前 `ON−OFF−TO` 接近闭合，独立估计相位后 F2 不再闭合。ON/OFF CSI 相位向量差没有集中在目标真值距离格。

这支持“角色相关的 CSI range-phase 估计/校正是当前组 support 变化的近端机制”，但不能唯一证明 ON 相位变化由目标污染造成，也没有目标任务性能证据。

## 科研定位和冻结范围

- **总体目标：** 服务于研究总纲中传统 GMTI 方法能力边界与真实瓶颈的识别；不是算法改进或性能排名实验。
- **科学问题：** 在 S1d/S1c 固定失败组上，同一当前 Mode-B 生产链处理配对 ON（目标+杂波+噪声）、OFF（杂波+噪声）和 TO（目标分量）时，目标/背景复数分量是否闭合，角色间 CSI 相位差是否局部集中在目标距离格，以及 C4 support 如何变化？
- **冻结条件：** seed1932001、2.5 m/s、30 dB、+4 ns、20目标、Stage2 period index 4（GMTI05）、beam 50、azimuth row 52、range 1065–1197。
- **门槛：** 133-bin range group、`min_support=8`、robust phase residual `±0.35 rad`。runtime XML 保留 Mode-B、`robust_ddc_rb`、上述 support/phase 设置和 CSI tap。没有修改门槛或以 Current 结果替换失败 reference。
- **真值隔离：** 三个 Production 分支均未接收真值路径。真值只在处理完成后用于给距离格加标签。

## 输入和运行身份

S1e 复用原 Stage2 输入，没有重跑 Stage2：

| 角色 | 输入含义 | GMTI05 输入 SHA-256 |
|---|---|---|
| ON | 目标 + 连续面积杂波 + 噪声 | `d324584380c67b37ea19f3e7efb7ecfdc33ed7287b2e4c97fca5aa98e9d2f831` |
| OFF | 连续面积杂波 + 噪声 | `a8b253c035d387da24625f189a576fe41f56789fbc1c2870c652ef7a75e25f48` |
| TO | 目标信号单独分量 | `cb8f77cc3153fa9e8d9f3c71a4959692165ab725ce430677b02675adf823b667` |

三种 A1 role 的 10 期 `background_reuse_mapping.csv` 字节相同，全部映射到 source group 0，10/10 标记 `matched`。A1 报告中的面积注入计数为0是因为它们复用背景，不表示面积杂波缺失；对应 A0_ON Stage2 报告实测 1,300 个连续面积杂波 packet、6,508,782 个样本，面积平均功率 `0.0221596696662`，测得 lag-1 相关 `0.98001127856`。A1_TO 配置 `signal_only=true`；Stage2 源码在目标注入前清空 packet payload，使输出为目标单独分量。紧凑包保留 A0_ON 生成报告、A1 三角色配置/报告/映射及 Production 输入 SHA；原始 Stage2 `.bin` 未复制到 Git。

- **实验报告/代码快照：** 执行时 HEAD `9bb8c351d2d4ca8910a92f079d239a74ccbdddb2`；runner 当时是未跟踪文件，其 SHA-256 `f1aa85ec70454932622145bdb0278273e524b612af5fb4378abdcbb990dbae4f`，已原样纳入本阶段证据。
- **Production 实现与二进制：** source commit `461fbd949ea7c024c28f30e7aea36ae9642a4456`；Native CUDA Release，CUDA 12.4、架构75、`USE_COREX=OFF`；binary SHA-256 `c7db0d12f930b297851f1b3b53f3e56de5cafef0fa90394595de0b8babf7d7c9de`。三分支 binary SHA 完全相同。
- **GPU：** RTX 3050 Laptop，4,096 MiB。分支执行前的设备快照约为 1,085 MiB used、75% GPU 利用率、71°C；结束快照约为 1,085 MiB、79%、71°C。外部训练仍在运行；三分支均成功完成。该记录只描述执行环境，不是性能基准。
- **周期映射：** 输入文件 SHA 绑定 Stage2 零基 period 4 / GMTI05；单文件 Production runtime 使用本地 period index 0。Gamma CSV 使用 `period_id=1 / GMTI01`，CFAR geometry CSV 使用局部 `period_id=0`。这些局部编号按唯一输入 SHA 映射回 GMTI05，不跨文件混用。

## Production 与质量门结果

| 角色 | Production | C4 reference 状态 | Production support | 独立重算 support |
|---|---|---|---:|---:|
| ON | `passed`, return code 0 | `NOT_EVALUABLE / insufficient_robust_support` | 7 | 7 |
| OFF | `passed`, return code 0 | `OK` | 55 | 55 |
| TO | `passed`, return code 0 | `OK` | 14 | 14 |

ON 的7个被保留距离格为1071、1106、1134、1135、1141、1153、1176；与11个目标真值距离格无交集。OFF 的55格有3格落在同一组目标真值距离坐标，但 OFF 数据本身无目标信号。TO 的14格与这些目标距离格也无交集。ON/OFF、ON/TO、OFF/TO 的 support 成员交集分别为1、0、7格。

三个分支均有完整 runtime XML、配置日志、Production 日志、逐阶段 trace、raw/CSI phase sidecar、selected-cell F1/F2、Gamma summary、CSI tap 和 CFAR geometry。每个角色的两条 CFAR geometry 记录均为 `527,280 / 527,280` valid CUT/threshold tests。false CUT/cluster/track 统计需要 truth join，状态为 `NOT_EVALUABLE`；这些 geometry 数字不作为 Pd/Pfa 或性能结论。

## 逐格复数闭合与相位证据

相对 L2 定义为 `||ON−OFF−TO||₂ / ||ON||₂`，在选定133个 range bins 上计算。

| Production 阶段 | F1 相对 L2 | F2 相对 L2 |
|---|---:|---:|
| raw DBS，range phase 前 | 0.009367 | 0.018056 |
| CSI DBS，range phase 前 | 0.010316 | 0.018056 |
| 各角色独立 CSI range-phase 校正后、C4 adapter 前 | 0.010316 | 0.787703 |

前三行说明输入分量在相位校正前近似闭合；最后一行 F2 残差增至78.77%。三角色各自估计了 CSI range-phase vector，因此校正后不满足简单线性相加。结合 S1c/S1d 对同一 ON 输入换用 OFF 或另一 ON phase vector 会改变 support 的生产干预结果，这与“相位向量及其适配过程是 support 变化的近端机制”一致；它仍未分离目标注入、背景/噪声交互与 CSI 估计器响应各自的贡献。

TO-only 分量在11个目标真值距离格上明显更强：raw F1/F2 平均幅度分别为 `100.889 / 89.161`，其余122格分别为 `11.519 / 9.452`；对应 TO 能量约71.13% / 71.31% 落在目标距离格。CSI DBS、range phase 前，F1 目标格能量比例为75.64%，F2仍约71.31%。因此，TO 信号在所选距离行具有空间局部性。

但 ON 与 OFF 的 CSI phase vector 逐格 wrapped 差在目标格并未增大：133格平均 `1.824229 rad`，11个目标距离格平均 `1.727056 rad`，其余122格平均 `1.832990 rad`。这不支持“相位差局部集中在目标真值距离格”的简单解释；不能排除目标分量经 CSI 估计器全局影响向量，或目标与背景相互作用。

## 结论边界和阶段状态

- **`IMPLEMENTED`：** 单周期 ON/OFF/TO runner 与独立复数闭合、support membership、相位差和 CUT geometry 离线分析。
- **`EXECUTED`：** 当前 Mode-B、同一 Native CUDA binary 的三条 Production 分支；Stage2 未重跑。
- **`EVALUABLE`：** 输入身份、runtime XML、三角色 Production 执行、support 数/成员集合、选定行阶段复数闭合、CSI phase-vector 空间差与 valid CUT geometry 可核验。
- **`PERFORMANCE_VERIFIED=false`：** 单 seed/周期/波位/参考组；没有目标检测收益、目标保持、Output SCNR、速度/方位误差或统计显著性分析。
- 该结果加强了角色相关 CSI phase correction 与当前组 support 变化之间的机制证据，但仍不能唯一归因于目标污染。ON native support 仍为7，小于8；不修改门槛、不修复/替换 C4、不恢复 Screening、不训练 AI。
- **停止点：** 提交本次双层 ChatGPT 独立审查请求；收到审查后再由用户决定下一项最小任务和历史大数据清理。最终 `<20 GB` 占用尚未验收；原始运行目录和 Stage2 IQ 先保留到该次审查结束。

## 证据、复现与限制

- 紧凑证据目录：[evidence/s1e](evidence/s1e/)，72个文件、约1.5 MB；逐文件保存大小/SHA-256及其源路径。含三角色 runtime XML、日志、branch manifest、runtime dump、phase trace、selected cells、CSI tap、CFAR geometry、Stage2 role 配置/背景映射，以及 runner/analyzer 和逐格数据表。
- 为满足 Git 差异检查，三份紧凑日志副本统一了换行并移除了行尾空格；manifest 同时保留每份原始日志的源 SHA-256/大小和规范化副本 SHA-256/大小，`outputs/` 内原始日志未改动。
- `outputs/` 原始三分支结果约4 MB；Stage2 period-4 输入每角色49,204,480 bytes。输入二进制和原始 target truth CSV 未复制到 GitHub；审查快照可重算发布的逐格结果，但不能从 GitHub 独立重放原始 Stage2→Production。
- 实际执行命令：

```bash
python3 scripts/phase_r/run_r22_s1e_modeb_role_triplet.py \
  --output-root outputs/r22_s1e_current_modeb_role_triplet_20261031

python3 scripts/phase_r/analyze_r22_s1e_modeb_role_triplet.py \
  --output-root outputs/r22_s1e_current_modeb_role_triplet_20261031 \
  --case-root outputs/r22_s1_c4_support_replay_20261029/snr30/pipeline/cases/r22_multi_target__seed_1932001__v_2d5__snr_30__delay_4ns
```

输出目录已存在；如需重跑，必须选新的空 `--output-root`。原 target-present matrix 为 `GATE_FAILED`，runner 明确拒绝从该状态 `--resume`；本次只是在用户授权下执行的新三角色诊断，不是矩阵续跑。
