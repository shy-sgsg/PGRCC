# R22 V3 method-level screening：早停审计

更新日期：2026-11-06
决策编号：`D-2026-11-06-R22-V3-EARLY-STOP`
状态：`INCOMPLETE / UNKNOWN_STOP`

## 结论

按冻结的 R22 V3 门控，screening 在第 4 个唯一 case 停止。该 case 的 54 个 Native CUDA Production 分支中，53 个正常结束，唯一失败为 `P2 / Mode-A / ON`；该方法单元因此为 `UNKNOWN_STOP`，不能归类为普通方法缺口。矩阵计划为 288 个 case，本次有 3 个 case 完整通过、1 个 case 触发未知停止；没有完成总体覆盖率或方法性能排名。

失败出现在 `P2_modea_ON` 的第 10 个处理周期：`raw_align` 返回失败，随后 scan beam quality gate 报告 `valid=0/1 required=1 min_ratio=0.95`。该分支开始处理时，Production 日志报告 CUDA 空闲显存约 `0.056 GiB`；其他成功分支曾记录约 `1.10 GiB`。这使设备资源竞争成为合理解释，但并未证明它是根因。调用链没有保存失败的 CUDA 错误码，因此不能排除输入或处理状态导致的失败。保留 `UNKNOWN_STOP` 是正确的 fail-closed 判定。

随后完成了同场景的诊断性 Production 重放：`P2 / Mode-A / ON` 以及该 runner 生成的其余8个控制分支均返回成功。此重放不修改、不覆盖 V3 原始 `UNKNOWN_STOP`，也不计入新的 Screening 分母。它使资源竞争解释更有依据，但仍不能唯一确定原失败根因。

## 固定身份与运行范围

| 项目 | 身份 |
|---|---|
| 仓库 / 分支 | `shy-sgsg/PGRCC` / `codex/unknown-system-error-next-stage` |
| V3 执行源码 commit | `003366a7c04877d36281b268c83bfcb08bf7b810` |
| 研究总纲 blob | `e637e3769bde7814298ff012d0f42301884e2985`，未修改 |
| V3 design ID | `R22_EXTENSION_20261105_V3_METHOD_LEVEL_CONTINUATION` |
| 后续 V3.1 design ID / 文件 SHA-256 | `R22_EXTENSION_20261106_V3_1_POST_REPLAY_FULL_SCREENING` / `26e202fca197ae1f62cfad099cdf895de7b834eed1622c553e80b8cd2b94a839` |
| 设计文件 SHA-256 | `bd845ad42f23e630af0a4188a09b155b298eb5fb0b744410b42989dec97e0a74` |
| 矩阵 canonical design SHA-256 | `02ce0973ffd8eeb8e20bbd984274b8a1c9895d25fb79bb69741333537271eb18` |
| 方法配置 SHA-256 | `588c36904f62febad8476ddccb789ba67de2e9c23dc2ff12b52418eb06706bbc` |
| Stage2 binary SHA-256 | `47fa5ec34ca15cdc4edeb35226eefa941a78ee344aa2311e5e7984c811dcb40f` |
| Production binary SHA-256 | `d9e4d2e0b8429c5415e94be1af0230f3c0148455c394dc6d661e7e6c13a4ad1e` |
| Production backend | 本机 Native CUDA，`USE_COREX=false` |
| GPU | NVIDIA GeForce RTX 3050 Laptop GPU，driver 580.178.04 |

设计文件 SHA 是原始文件字节哈希；矩阵 canonical SHA 是规范化 JSON 身份哈希，两者口径不同，均与保存值相符。

## Case 进度

所有已执行 case 均为同一个 seed `1932001`，因此不能把这些条件当作独立随机重复。

| 速度 | SNR | 时延 | Case 状态 | 备注 |
|---:|---:|---:|---|---|
| 0.5 m/s | 10 dB | 0 ns | `PASSED` | 12/12 适用方法单元可评价 |
| 0.5 m/s | 10 dB | +4 ns | `PASSED` | 首次 attempt 中断；case-level retry 的 attempt 2 完成并通过 |
| 0.5 m/s | 20 dB | 0 ns | `PASSED` | 12/12 适用方法单元可评价 |
| 0.5 m/s | 20 dB | +4 ns | `UNKNOWN_STOP` | 17/18 方法单元可评价；`P2 / Mode-A / ON` 未知停止 |

第 4 个 case 的 Stage2 面积杂波门通过：连续面积 packet `1300`、样本 `6,508,782`、实测平均功率 `0.0221596696662`；地面斜距范围约 `84,286.713–85,289.214 m`。因此，本次停止不是面积杂波缺失造成的。

失败 case 有 54 条 task rows、7,000 条目标机会记录，其中 17 条 ON 方法记录可评价，独立 H0 seed rows 为 0。机会行和处理周期不是独立 seed；当前没有可报告的确认性 Pd/Pfa 或跨 seed 性能结论。

## 失败证据与归因边界

失败分支的配置阶段返回码为 0，Production 返回码为 1。前 9 个周期完成；第 10 个周期在 beam 50 的 `raw_align` 阶段失败，beam quality gate 随后停止该分支。其余 Production 分支通过，`P2_modea_OFF`、`P2_modea_TO` 和 `P2_modeb_ON` 均通过。

源码定位：

- `src/processOnePeriod.cpp`：`cuda_stage_align_async()` 返回 false 时只记录 `stage=raw_align`。
- `src/gpu/gpu_kernels.cu`：该函数在 launch 后仅返回 `cudaGetLastError() == cudaSuccess`，没有把具体 CUDA 错误码写入日志。
- `src/processPeriodsParallel.cpp`：未通过的 beam 导致 scan beam quality gate 失败。

因此目前能确认的是**失败阶段和失败传播**，不能确认 CUDA launch error 的具体类别。低显存和外部训练并发支持“设备资源竞争”假设，但不是该假设的独立证明。此故障不得按 `PRODUCTION_FAILED` 方法缺口继续筛选，也不得重试后覆盖原始失败记录。

## 磁盘与证据保留

失败 case 的 compact archive 在 CRC 与 SHA-256 校验通过后，runner 删除了该 case 的 raw 目录。其 ZIP 为 5,866,062 bytes，SHA-256：

`c5535d6733d381a3c3a5a22e190fe09868c434f2513568323afddb2d5e4767d0`

当前 V3 输出约 97 MiB，项目 worktree 约 3.1 GiB，低于 20 GiB 上限；所在分区可用空间约 18 GiB。保留矩阵 manifest、五份 compact/interrupted/failure archives 和必要审计日志；不保留已完成 case 的可再生 raw arrays。未清理其他阶段的历史数据。

本地矩阵路径：`outputs/r22_v3_screening_20261105/matrix/matrix_manifest.json`。本地 compact archives 位于 `outputs/r22_v3_screening_20261105/archives/`；完整 ZIP 未提交到 Git。可直接审查的精选门控证据见 [早停证据 JSON](evidence/v3_screening_stop_20261106/early_stop_evidence.json)。

## UNKNOWN_STOP 后的单场景 CUDA 重放

| 项目 | 重放记录 |
|---|---|
| 输出 | `outputs/r22_v3_diagnostics_20261106/p2_modea_on_case/attempt_001/` |
| 执行源码 commit | `003366a7c04877d36281b268c83bfcb08bf7b810` |
| Production binary SHA-256 | `d9e4d2e0b8429c5415e94be1af0230f3c0148455c394dc6d661e7e6c13a4ad1e`，与 V3 原失败相同 |
| 场景 | seed1932001，0.5 m/s，20 dB，+4 ns，20目标，10周期，beam 50 |
| 运行前设备 | RTX 3050 Laptop，4096 MiB VRAM，其中空闲1215 MiB；利用率94%，76°C，P0，59.62 W，1912 MHz |
| Production 分支 | 9/9 成功；`P2_modea_ON` 返回码0 |
| 面积杂波 | 1300 packets、6,508,782 samples，测得均功率0.0221596696662；Stage2 gate通过 |
| 任务评价 | 3条 ON 行可评价，12条 task rows，1200个目标机会行，独立 H0 为0；`PERFORMANCE_VERIFIED=false` |
| Pfa | `NOT_EVALUABLE` |
| raw 清理 | 清理401个可重建 `.bin`，共6,888,974,776 bytes；cleanup ledger 记录其 SHA-256；剩余 `.bin/.npy/.npz` 为0 |

该重放期间 GPU 仍高负载，`P2_modea_ON` 正常完成；相较原失败时约 `0.056 GiB` 空闲显存，这支持“原失败与当时设备资源状态相关”的解释。由于重放没有复现失败、原始失败日志也没有具体 CUDA error code，根因仍是 `NOT_VERIFIED`，不能声称已完成因果证明。

诊断 preflight：`outputs/r22_v3_diagnostics_20261106/p2_modea_on_case_attempt_001_preflight.json`；运行 manifest SHA-256：`4dba49207346e1c4391c5ec8a491ca6a14cda7b8f883bb7ac83240d6d2b314c6`；cleanup ledger 位于该 case 的 `raw_cleanup.json`。重放结束后诊断目录约200 MiB，项目 worktree 约3.3 GiB，磁盘余量约17 GiB。

## 科研判断与下一步

- V3 screening：`INCOMPLETE / UNKNOWN_STOP`；按冻结门控停止，不继续当前矩阵。
- 当前证据只证明一个 Production 分支的 `raw_align`/beam quality failure，不能证明 P2 方法普遍失效，也不能据此比较 GMTI 性能。
- 后续同场景重放9/9分支成功，说明原 V3 失败不能复现；该证据不回写旧矩阵状态，也不能确定原 CUDA 错误根因。
- 未知通道误差是否降低 GMTI 任务性能、已知校正可恢复多少、传统估计器与已知校正差距以及 AI 必要性，本阶段均未回答。
- 总体研究方向保持 `CONTINUE`；`AI_ENTRY_GATE=FAIL`。不修改研究总纲、不训练 AI。
- 新建独立 V3.1 design ID 和 matrix/raw/archive 输出根，从头执行全部288个 case、4,320个适用 ON 方法单元；V3 历史 case 与本次诊断重放均不计入 V3.1 覆盖率。不得续跑终态 `UNKNOWN_STOP` 的 V3 root，不得更改方法、质量门或实验因子。

## 状态标签

| 项目 | IMPLEMENTED | EXECUTED | EVALUABLE | PERFORMANCE_VERIFIED |
|---|---|---|---|---|
| V3 runner / evaluator | 是 | 是 | 部分；UNKNOWN_STOP 门按契约执行 | 不适用 |
| 3 个通过 case | 是 | 是 | 是 | 否；单 seed screening |
| 第 4 个 case | 是 | 是 | 部分；17/18 方法单元 | 否 |
| 全部 288-case screening | 是 | 否 | 否 | 否 |
