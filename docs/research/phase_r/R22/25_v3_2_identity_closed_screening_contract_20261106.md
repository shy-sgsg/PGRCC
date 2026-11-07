# R22 V3.2：身份闭合的完整 Screening 契约

- 状态：`FROZEN_FOR_SCREENING`（尚未启动）
- 设计 ID：`R22_EXTENSION_20261106_V3_2_IDENTITY_CLOSED_FULL_SCREENING`
- 父设计：`R22_EXTENSION_20261106_V3_1_POST_REPLAY_FULL_SCREENING`
- 总纲 blob：`e637e3769bde7814298ff012d0f42301884e2985`

## 1. 版本原因

V3.1 在6/288个 case 后按冻结门控进入 `UNKNOWN_STOP`。V3.1 matrix manifest 的 `design_sha256` 是按排序键、紧凑分隔符计算的规范化 JSON 对象 SHA-256（`696dce5b18fab9209f57c7d49922ff3c1ae4ea52f0a524c34cede8e8b933e635`），不是原始文件字节 SHA-256；该规范化哈希与已提交 V3.1 JSON 的重新计算结果完全相同。原始文件字节 SHA-256 为 `26e202fca197ae1f62cfad099cdf895de7b834eed1622c553e80b8cd2b94a839`。此前“找不到对应设计文件/身份未闭合”的表述是把两种哈希语义误作同一种；详见[哈希语义更正](26_v3_1_design_digest_semantics_correction_20261106.md)。V3.1 的设计内容身份已验证，但原运行 manifest 没有单独记录设计路径和原始文件 SHA。V3.1 原始 manifest、case archives 和终态结论只读保留，不追溯修改。

V3.2 建立新的设计、源码验证和输出身份，供原定完整筛选从队列起点重跑，并在执行身份中同时保存解析后的设计路径、原始文件字节 SHA 与规范化设计 SHA。重新从队列起点开始是因为 V3.1 已是 `UNKNOWN_STOP` 终态，而不是因为 V3.1 设计内容无法识别。**不继承 V3.1 的通过 case，不续跑 V3.1 根，也不改变研究因素或评价门。**

## 2. 冻结实验范围

V3.2 沿用 [V3.2 机器可读设计](config/r22_extension_experiment_design_v3_2_identity_closed_20261106.json) 的科学内容：

- 4个速度 `{0.5, 1.5, 2.5, 6.7}` m/s × 3个 SNR `{10, 20, 30}` dB × 2种时延 `{0, +4}` ns × 12个 screening seed，共 **288 cases**。
- 每个 case 为20个目标、10个周期、beam 50；独立推断单位为 seed。
- 适用 ON method×mode 分母为 **4,320**：0 ns 的144个 case各12项，+4 ns的144个 case各18项。
- 配对 ON/OFF/TO、连续面积杂波、真值 lineage、Native CUDA、共同 Production range `[666,1197]`、133-bin reference groups、`min_support=8`、phase threshold `0.35 rad`、CFAR/Detection/TrackManager gates 均与 V2/V3.1 相同。
- 不训练 AI，不开发 Router，不提前启动 Confirmatory、Dense、Bridge 或 H0 矩阵。

V3.2 JSON 原始文件字节 SHA-256：`9fcc56e27c3a78a96ccfbecdf6ef1b3098fb1298d4bac9886058ea9af6081063`；规范化 JSON 对象 SHA-256：`ecdb5a3306d27f3298f8c4fd3f199728733ec0f2bae0fd13590e277579b765f5`。

V3.2 JSON 与 V3.1 只应在以下 execution/provenance 字段不同：design ID、父设计 ID、进入版本的 commit、V3.2 identity provenance。不得更改科学因素、方法、参数、真值限制、适用单元分母或质量门。

## 3. 运行身份闭合

启动时必须显式传入已提交的 V3.2 JSON 绝对路径，并在 matrix manifest 中分别记录：

- 顶层 `design_sha256`：matrix runner 对排序键、紧凑分隔符规范化 JSON 对象计算的 SHA-256；
- `execution_identity.design_path` 和 `execution_identity.design_file_sha256`：完整解析路径及原始文件字节 SHA-256；
- V3.2 design ID、源码 commit、tracked dirty 状态、Stage2/Production binary SHA；
- method config 与 scenario template SHA；
- Native CUDA GPU name/UUID/driver；
- 运行前 GPU 显存 total/used/free、GPU utilization、P-state、temperature、power、compute-process list 与 capture time；
- 磁盘总量、已用与空闲空间。

必须断言顶层 `design_sha256` 等于 V3.2 JSON 的规范化对象哈希，且 `execution_identity.design_file_sha256` 等于指定路径的原始文件字节 SHA；任一不一致则禁止执行。matrix/raw/archive 必须使用新的 `outputs/r22_v3_2_screening_20261106/` 子目录，和 V3.1/V2 分离。不得将 V3.1 的 archive 或 case 计入 V3.2 覆盖率。

## 4. 资源调度与早停

V3.1 失败 case 的一条 C2 Mode-B ON 日志明确为 CUDA OOM；V3.1 执行前 GPU 利用率99%，没有保存空闲显存。当前外部训练进程 PID 347130 使用约1,954 MiB。用户已明确授权在外部训练仍运行时并行启动本实验；最近一次提权快照为 RTX 3050 Laptop GPU 空闲1,223 MiB、利用率95%。不停止、不干预该外部进程。

启动门仅用于资源调度，不改变科学质量门或分母：

1. 启动前重新记录 `nvidia-smi` GPU状态和 `--query-compute-apps` 进程列表。外部训练可以继续运行；不得终止或干预。将并发占用列为本次已知资源风险。
2. 不设未经证据支持的显存最低阈值。启动前必须确认Native CUDA设备可见、记录显存总量/已用/空闲及磁盘空间；若Production发生OOM或其他资源错误，仍按 `UNKNOWN_STOP` 停止并完整归档。
3. 所有288个 case 从队列起点运行，不设置 `--max-cases`，不重用终态目录，不降低目标数、周期数、seed数、方法数、支持门或 CFAR 门。

每个 case 必须先生成 compact archive，再逐项验证 SHA-256 与 ZIP CRC；验证成功后才清理可重建 raw `.bin`/CSI `.npy`。保留 runtime XML、manifest、日志、CSI taps、CFAR geometry、task/truth rows 和 cleanup ledger。若 archive 校验失败，保留 raw 并终止。

任何 `UNKNOWN_STOP`、共享输入/真值/生产链失败、area-clutter gate 失败或归档身份失败都停止当前 V3.2 matrix，不续跑终态。性能分析只允许在冻结条件和公平共同样本下进行；单 seed、CUT数和目标机会行均不能充当独立推断重复。

## 5. 完成条件与当前状态

只有以下条件全部满足，V3.2 screening 才能标记完成：

1. 288个 case 均有明确终态，且V3.2 design path/SHA与matrix身份完全一致；
2. 4,320个适用方法单元均在覆盖报告中分类；
3. 所有 area、配对、真值、Production、valid-CUT、unique-hit、track lineage 与 archive integrity gates均有证据；
4. 汇总独立seed层任务结果、不确定性、不可评价覆盖率、杂波/CSI/目标保持诊断和失败案例；
5. 完成与研究总纲的双层方向判断，明确 `IMPLEMENTED / EXECUTED / EVALUABLE / PERFORMANCE_VERIFIED`；
6. 清理可再生大数组后项目占用小于20 GiB，并保留必要审查证据。

当前 V3.2 尚未执行 CUDA；用户已授权在外部训练进程 PID 347130 继续运行时并行启动。最近快照为空闲1,223 MiB、利用率95%；启动前仍需再次记录 GPU、compute-process list 和磁盘状态。检查时项目树约3.9 GiB，`outputs/` 约2.1 GiB，文件系统可用约17 GiB；每个 case 的 raw 数据仅在 compact ZIP SHA 与 CRC 校验通过后删除。研究总纲没有修改。
