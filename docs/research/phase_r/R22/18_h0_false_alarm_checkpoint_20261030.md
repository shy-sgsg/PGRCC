# R22 独立 H0 虚警筛选检查点

**检查点状态（2026-10-30）：** `INCOMPLETE / RESUMED`。以下检查点数据保持为当时快照。恢复后最终矩阵与分析结果见文末“2026-10-31 恢复后最终结果”。

## 科研问题与边界

本子实验在无目标、有效连续面积地杂波条件下，比较冻结 R22 方法于 0 ns 与 +4 ns 通道时延下的 CUT-hit fraction。它补充 H0 虚警侧证据，不评价目标 Pd、低速目标保持、参数估计或跟踪性能，也不能绕过 R22 Screening 在 C4 Mode-B ON support `7 < 8` 处触发的 `GATE_FAILED`。

研究总纲仍是 PGRCC 的最高层级科研指导。未知系统误差估计是否值得继续，应结合 H0、目标存在条件、传统方法能力边界和 GMTI 任务收益综合判断。当前证据没有建立 AI 的必要性；不得据此启动 AI 训练或 Router。

## 冻结设计

| 项目 | 冻结条件 |
|---|---|
| 独立随机单位 | 30 个 seed：1935001–1935030 |
| 配对条件 | 每个 seed 的 0 ns 与 +4 ns |
| 目标 | 0 个；速度 0 m/s；SNR 配置 20 dB（无目标场景字段） |
| 时序 | 每 case 10 个周期，beam 50 |
| 场景 | Stage2 连续纹理面积杂波 + 噪声 |
| 矩阵规模 | 60 个唯一 case |
| 主指标 | 每 seed `observed_cut_hit_count / valid_cut_count` |
| 推断单位 | seed；不把 CUT、距离单元、Doppler 单元或周期当作独立样本 |

完整分析计划为按 seed 聚类的 10,000 次 percentile bootstrap；+4 ns 与 0 ns 的差值按同一 seed 配对重抽样。完整 30 seed 前，方法/条件区间与配对区间均标 `INCOMPLETE`，不插补缺失 seed、不把 CUT 级样本量当作独立重复。

## 执行身份与真实 CUDA 证据

- 运行源码 commit：`a688ca52616cc380b07810d4f8fbd79ed73d46bc`
- Stage2 binary SHA-256：`47fa5ec34ca15cdc4edeb35226eefa941a78ee344aa2311e5e7984c811dcb40f`
- Production binary SHA-256：`d9e4d2e0b8429c5415e94be1af0230f3c0148455c394dc6d661e7e6c13a4ad1e`
- 后端：本机 Native CUDA；`USE_COREX=false`
- 研究快照中记录的源码文件 SHA、CUDA preflight、每 case manifest 和紧凑归档 SHA 见 [H0 分析 manifest](evidence/h0_checkpoint_20261030/h0_analysis_manifest.json)。

本实验是本机 CUDA Production 执行。CPU 模拟、历史输出或单纯软件测试没有用来替代这些 case。单 case 的运行时间不作为 GPU 性能基准。

## 当前检查点结果

暂停后、attempt003 启动前的完整分析快照为：

| 项目 | 检查点值 |
|---|---:|
| 已完成且已归档 case | 29 / 60 |
| 完整 0/+4 ns seed 配对 | 14 / 30 |
| 额外 0 ns 单边 case | 1 |
| 完整方法/条件汇总 | 0 / 72 |
| 完整配对时延汇总 | 0 / 36 |
| 全矩阵分析状态 | `INCOMPLETE_NOT_EVALUABLE_AS_FULL_MATRIX` |

29 个 case 的 per-seed CSV、部分条件汇总和分析 manifest 见 [H0 检查点数据](evidence/h0_checkpoint_20261030/)。部分汇总内可有描述性均值，但没有满足冻结 30-seed 条件的完整置信区间，不作正式方法排名或虚警改善结论。

seed1935015 / +4 ns 有两个中断 attempt。attempt001 完成 Stage2 和部分盲校正输入准备，尚无 Production 分支；attempt002 有 51 个 Production branch 目录，其中 `R22_D3_Huber_B_ON_TO` 缺少终态 manifest，尚未生成 evaluator 表。两次中断现场已压缩、校验并发布在 [attempt archive index](evidence/h0_checkpoint_20261030/h0_interrupted_attempt_archive_index.json) 与 [两份中断审查档](evidence/h0_checkpoint_20261030/interrupted_attempt_archives/)。raw 二进制清理量及限制见[清理台账](evidence/cleanup_ledger.md)。

用户要求恢复后，runner 以 attempt003 重跑该 case。runner 支持**case 级**续跑：会跳过 29 个已有紧凑归档、为 interrupted case 新建 attempt；不在单个 Production branch 内部续算。运行时矩阵状态和 attempt003 终态以本次最终提交对应的检查点记录为准。启动时 RTX 3050 Laptop GPU 显存为 4096 MiB，其中 3102 MiB 已用；外部训练仍在运行，用户明确要求在该显存状态下恢复。该状态不用于性能比较。

## 面积杂波与配对可比性

对检查点中 14 个完整 seed 配对，逐项核对了 Stage2 面积杂波和噪声摘要：14 对的面积杂波模型、有效几何、注入 packet/sample 数、测得面积功率、杂波时间相关统计和噪声功率均完全相同，没有字段差异。

- 每 case：1,300 个 continuous-area packets、6,508,782 个 samples。
- 平台高度：6,000 m；slant range window：84.5–85.5 km；对应地面距离约 84.287–85.289 km，物理可达。
- 这 14 对的测得面积功率范围：`0.0222964064483–0.023308819045`。
- 噪声功率范围：`0.000999645705034–0.00100010972546`。

这些检查证明已完成配对 case 的杂波/噪声摘要匹配，不代表剩余 16 对已验证，也不代表 H0 false-alarm 指标已有统计结论。

## 阶段门与后续判断

完整矩阵只有在 60/60 case 终态归档、全部冻结适用条件具备 30 个 seed、分析 manifest 为 `COMPLETE_EVALUABLE` 后，才形成最终 H0 汇总。H0 结果只报告经验 CUT-hit fraction 与 seed-clustered 区间，不称作独立 CUT 级二项统计，不据此宣称 target Pd/Pfa 改善。

本 H0 子实验不会修改冻结门槛、筛选方法、R22 主线或研究总纲。R22 Screening gate 仍是既有的局部 C4 support 失败；是否继续未知误差方向、改看低速目标保护或转向复杂杂波建模，需要结合完整任务证据另行裁决。



## 2026-10-31 恢复后最终结果

### 矩阵状态与执行身份

| 项目 | 最终结果 |
|---|---:|
| V2 冻结 H0 设计 | `R22_EXTENSION_20261028_V2_RANGE_ROI` |
| 唯一 case | 60/60 |
| 独立 seed | 30（1935001–1935030） |
| 配对条件 | 每 seed 0 ns / +4 ns |
| Matrix 状态 | `COMPLETED` |
| 每 case 终态 | 60 个 `H0_EVALUABLE_WITH_GAPS` |
| Matrix attempt 记录 | 63（含 3 次中断 attempt；每次均保留 archive） |
| H0 analysis | `COMPLETE_EVALUABLE` |
| 执行源码 | `a688ca52616cc380b07810d4f8fbd79ed73d46bc` |
| Stage2 binary SHA-256 | `47fa5ec34ca15cdc4edeb35226eefa941a78ee344aa2311e5e7984c811dcb40f` |
| Production binary SHA-256 | `d9e4d2e0b8429c5415e94be1af0230f3c0148455c394dc6d661e7e6c13a4ad1e` |
| 后端 | 本机 Native CUDA，`USE_COREX=false` |

恢复采用 case-level retry：attempt 中断后创建新 attempt，不在单个 Production branch 内断点续算。3 个中断现场分别保留于 [interrupted attempt archive index](evidence/h0_checkpoint_20261030/h0_interrupted_attempt_archive_index.json) 和其 ZIP 中；Matrix 最终有 60 个唯一 case，不把旧 attempt 当成额外 seed。

### 面积杂波和 0/+4 ns 配对可比性

所有 60 个 case 的 `A0_ON` Stage2 区域通过面积杂波 gate。每 case 生成 1,300 个 continuous-area packets 和 6,508,782 个 samples。平台高度 6,000 m，slant-range window 84.5–85.5 km，对应 ground-range window 84.2867–85.2892 km；测得面积功率范围为 `0.0221935448633–0.023308819045`，实测噪声功率范围为 `0.000999645705034–0.00100025854155`。

逐 seed 比较 `A0_ON` 的面积开关/模型、packet/sample 数、面积功率、地距几何、时间相关统计、power CV 和实测噪声，30/30 对所有审计字段完全匹配。`A0_OFF` 与 `A0_TO` 报告的面积注入数为 0，是关闭杂波的对照 variant；实际有杂波的 `A0_ON` gate 报告记录了正注入计数和正实测功率。逐对数值和两个 Stage2 report SHA 见 [H0 配对杂波/噪声审计](evidence/h0_final_20261031/h0_pair_clutter_noise_audit.csv)。

### Seed-clustered H0 结果

最终分析按每 seed 的 `observed_cut_hit_count / valid_cut_count` 计算经验 CUT-hit fraction，不把 CUT、距离单元、Doppler 单元或周期当作独立样本。Bootstrap 为 10,000 次、NumPy PCG64、seed 22029，配对时延差按同一 seed 联合重抽样。

- Per-seed CSV：1,800 行。
- 方法/条件汇总：60/72 完整，另外 12 个是零时延冻结不适用条件。
- 配对时延汇总：24/36 完整，另外 12 个是冻结不适用条件。
- 不进行方法间多重比较显著性检验；区间用于描述独立 seed 变异，不等于 CUT 级二项区间。

下表给出部分方法的 +4 ns 减 0 ns 配对变化，方向定义为正值代表经验 CUT-hit fraction 增大。它们不是系统 Pfa，也不用于确认性排名。

| 方法 | CUT 区域 | 配对均值差 | 95% seed-bootstrap 区间 |
|---|---|---:|---:|
| C0 Production Current | 动态带内 | `+8.648e-6` | `[+7.194e-6, +1.011e-5]` |
| C0 Production Current | 全带 | `-6.745e-6` | `[-7.554e-6, -5.987e-6]` |
| PK Known correction → Current | 动态带内 | `-3.161e-8` | `[-1.835e-7, +1.264e-7]` |
| PK Known correction → Current | 全带 | `-8.218e-8` | `[-3.098e-7, +8.218e-8]` |
| C4 Robust DDC-RB Mode-B | 动态带内 | `+1.650e-6` | `[+2.086e-7, +3.085e-6]` |
| C4 Robust DDC-RB Mode-B | 全带 | `-2.529e-8` | `[-2.402e-7, +2.655e-7]` |

C0 在两个冻结 CUT 区域呈相反方向；Known correction 的这项 H0 proxy 与 0 ns 差很小。该结果不能据此推断通道时延普遍提高或降低系统 Pfa，也不能外推目标检测收益。

### 科研判断与停止条件

H0 完成了无目标虚警侧的 30-seed 可评价汇总，但不验证 target Pd、目标保持、参数估计或跟踪。它不解除 seed1932001、2.5 m/s、30 dB、+4 ns 的 R22 Screening `GATE_FAILED`（C4 Mode-B ON support=7 < 8），也没有开始 target-present confirmatory matrix。当前没有 H0 证据证明 AI 必要，也不足以转向低速目标保护或复杂杂波建模。

- H0 子任务：`IMPLEMENTED=true`、`EXECUTED=true`（60/60）、`EVALUABLE=true`（分析 `COMPLETE_EVALUABLE`）、`PERFORMANCE_VERIFIED=false`（未验证 GMTI 目标任务性能）。
- 全项目方向门：R22 target-present Screening 仍 `GATE_FAILED / INCOMPLETE`；`AI_ENTRY_GATE=FAIL`。
- 下一步：把本报告和精简证据交给 ChatGPT 做固定 commit 的双层独立审查；审查前不启动新实验，由用户根据审查建议决定。

### 清理与证据边界

60 个完成 case 的 compact ZIP 合计 180,896,331 bytes，留在本机 `outputs/r22_h0_engineering_post_review_20261029/archives/h0/`；所有 raw array members 均已从这些 ZIP 中排除。完成 case 的清理记录合计 72,060 个生成 `.bin` 路径、472,370,816,936 bytes；3 个中断 attempt 合计另清理 21,830,424,640 bytes，并保留经过 CRC/SHA 验证的中断审查档。清理汇总见[最终证据包](evidence/h0_final_20261031/h0_raw_cleanup_summary.json)和[清理台账](evidence/cleanup_ledger.md)。用户规定的项目 `<20 GB` 上限在 ChatGPT 审查结束后验收；当前 compact case ZIP 暂时保留供审查。

GitHub 精简包包含 per-seed 表、条件/配对汇总、30 对 Stage2 配对审计、矩阵与 archive SHA 索引、分析脚本和运行身份。完整 60 个 case ZIP、raw IQ 与全部逐分支运行日志没有上传；因此独立审查者可以核对汇总与摘要哈希，但不能声称已读取或重放这些本机 ZIP/原始信号，相关信号级结论应标 `NOT_VERIFIED`。
