# R22 V3.1：UNKNOWN_STOP 后全量 Screening 契约

- 状态：`FROZEN_FOR_SCREENING`
- 设计 ID：`R22_EXTENSION_20261106_V3_1_POST_REPLAY_FULL_SCREENING`
- 上一设计：`R22_EXTENSION_20261105_V3_METHOD_LEVEL_CONTINUATION`
- 基础科学设计：`R22_EXTENSION_20261028_V2_RANGE_ROI`
- 研究总纲 blob：`e637e3769bde7814298ff012d0f42301884e2985`

## 1. 版本原因与诊断边界

V3 在第4个唯一 case 的 `P2 / Mode-A / ON` 分支遇到 `raw_align` 失败并以 `UNKNOWN_STOP` 终止。失败发生时日志报告约 `0.056 GiB` 空闲显存，但未保存具体 CUDA error code。

之后，对同一 seed1932001、0.5 m/s、20 dB、+4 ns、20目标、10周期、beam 50 进行了诊断性 Native CUDA 重放。相同 Production binary 下9/9个执行分支通过，包含此前失败的 `P2_modea_ON`；运行前记录到1215 MiB空闲显存、94% GPU利用率和76°C。该重放支持“设备资源状态可能相关”的解释，但不能证明原失败根因。

V3 的 `UNKNOWN_STOP`、失败归档及矩阵 manifest 保持原样。诊断重放不计入 V3.1 的 case、method-unit 覆盖、重复次数或性能估计。

## 2. 冻结实验范围

V3.1 只更换设计版本和执行输出身份。科学因素、处理算法、处理范围、适用方法、质量门和评价规则继承[V3 Screening 契约](22_v3_screening_contract_20261105.md)，逐项保持不变：

- Screening 为4个速度 `{0.5, 1.5, 2.5, 6.7}` m/s × 3个 SNR `{10,20,30}` dB × 2个时延 `{0,+4}` ns × 12个 seed，共 **288 cases**。
- 每个 case 为20目标、10周期、beam 50；独立推断单位仍为 seed。
- 适用 ON method×mode 分母仍为 **4,320**：0 ns 的144个 case各12项，+4 ns的144个 case各18项。
- 连续面积杂波、配对 ON/OFF/TO、真值 lineage、Native CUDA、Production/CFAR/Detection/TrackManager gates 不变。
- Production range `[666,1197]`、133-bin reference groups、`min_support=8` 和相位门限 `0.35 rad` 不变。
- 不训练 AI、不开发 Router、不启动 Confirmatory、Dense、Bridge 或 H0 矩阵。

V3 的3个通过 case、V3失败 case、诊断重放和V2历史归档都不补入 V3.1。V3.1 从冻结队列起点重新执行全部288个 case。

## 3. 执行状态与断点规则

- 使用新的 matrix、raw 与 compact archive 根；不得续跑 V3 的 `UNKNOWN_STOP` 根。
- V3.1 只允许在自身 manifest 与 design/config/source/binary identities一致、已完成 case 的 archive SHA/CRC复核通过时进行 case-level resume。
- V3.1 出现 `UNKNOWN_STOP`、shared gate、输入/真值/生产链问题或 archive integrity failure 时，按契约停止并保留失败证据。不得降低门槛、缩小288-case设计、静默重试失败分支或把诊断性重放当作筛选通过。
- 每 case 先完成 compact archive 和 SHA/CRC校验，再删除可重建 `.bin`/CSI `.npy`；保留 runtime XML、身份、关键日志、CSI tap、CFAR geometry、task rows、truth与 cleanup ledger。
- 运行前和阶段检查记录 GPU 状态及磁盘余量。资源预检只用于安全调度，不改变结果质量门或实验分母。

## 4. 解释与停止边界

Screening只用于冻结条件下的可行性、正确性和方法能力边界筛选。不得以 CUT 数、目标机会行或单 seed case 代替独立 seed 推断；没有独立 H0 就不报告实测 Pfa。即使288个 case全部可评价，也不自动等于统计显著性验证或 `PERFORMANCE_VERIFIED=true`。

只有全部288个新 case 均有明确终态、4,320项适用方法单元均纳入覆盖报告、compact archives可校验且研究方向评估完成，V3.1 Screening 才可记为完成。资源不足或质量门失败时如实报告部分状态，不缩小设计后宣称完成。

## 5. 实验身份与入口

- 机器可读设计：[V3.1 design JSON](config/r22_extension_experiment_design_v3_1_resource_replay_20261106.json)
- V3.1 design JSON SHA-256：`26e202fca197ae1f62cfad099cdf895de7b834eed1622c553e80b8cd2b94a839`
- V3早停及同条件重放报告：[阶段审计](21_v3_screening_early_stop_20261106.md)
- 重放 manifest：`outputs/r22_v3_diagnostics_20261106/p2_modea_on_case/attempt_001/r22_run_manifest.json`
- V3.1 matrix manifest 和 compact archives：启动后记录在其独立输出根；该路径不与V3/V2输出复用。
