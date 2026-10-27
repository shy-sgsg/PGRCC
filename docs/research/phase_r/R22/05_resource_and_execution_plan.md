# R22-05：资源与执行记录

## 已完成的设备运行

seed900203 使用本地 Native CUDA：NVIDIA GeForce RTX 3050 Laptop GPU，运行前记录 P8、约 6.43 W、48°C、40% GPU utilization、SM 210 MHz。Stage2 与 Production 可执行文件来自 `/tmp/pgrcc-r21-native-cuda-build`，SHA-256 分别为：

- Stage2 `simulate_stage2_statistical`: `5dd0ac4e2536b8bcf2508cc53db8a30780eba75f40c025e13ffaa930f8ba7b9b`
- Production `GMTI_pipe_core`: `9ffc232966bd92a39edfb39b90730851014dfa1ae7237a4ac172a764f31cc566`，`USE_COREX=OFF`

manifest 记录 seed900203 Stage2 总耗时约 19.13 s、Production 分支耗时总计约 52.31 s；这些是本机单次 elapsed，不是性能基准。seed900101 smoke 的 Stage2 杂波生成为 2,600 packets / 13,195,469 samples，Production elapsed 约 10.92 s。

## 磁盘

- R22 开始时本地项目约 6.7 GB，低于 20 GB 上限。
- `seed900203_closure` 紧凑证据包 952 个文件，约 39.9 MB；raw IQ 和可再生大数组未归档。
- seed900202 紧凑证据约 11.1 MB；seed900101 preflight 保留配置、manifest、生产日志与诊断。
- 临时运行目录只有在紧凑证据逐项 SHA 验证完成后才按 [清理台账](evidence/cleanup_ledger.md) 删除。

## 完整矩阵未估算为可执行计划

初始草案至少包含 522 个主场景 seed-condition，尚未包括 H0、方法/角色分支与桥接重复。单个多目标 case 的 Production 数量依方法模式和 role 数变化；不能用 seed900203 的 52 秒简单外推完整 GPU 预算。Stage2 原始数据会迅速超过当前紧凑结果规模。PKR gate 失败前未冻结、未启动完整矩阵。

重新估算需要：冻结实际 runner 任务分解、从成功和失败日志分开估算每角色时长、记录原始 IQ bytes/保留策略、计算 CSI/CFAR 诊断空间，并设并发与磁盘上限。此估算留到 PKR gate 可行后进行。

## 2026-10-28 V1A 扩展矩阵静态预算（执行前估算）

机器可读设计 `R22_EXTENSION_20261028_V1A` 共 **594 cases**：engineering 12、screening 288、confirmatory 180、dense 48、bridge 6、H0 60。其中 294 个 0 ns case、300 个 +4 ns case。计数由当前冻结配置及 `r22_experiment_matrix.build_case_specs` 生成，不是已执行数量。

根据当前 runner 的 method contract 和角色展开，每 case 的 Production 分支数为：

| 时延条件 | 基础 branch 数 | 额外盲校正 branch | case 数 | branch 总数 |
|---|---:|---:|---:|---:|
| 0 ns | 36 | 0（校正不适用于无误差参考） | 294 | 10,584 |
| +4 ns | 45 | 固定 D3 3；Huber/M1 各最多 3 | 300 | 14,400–16,200 |
| 合计 | — | — | 594 | **24,984–26,784** |

Huber/M1 只有通过各自 support/qualification 才会运行其三个角色；表中下界已计入固定 D3。额外 `NOT_APPLICABLE` / `NOT_AVAILABLE` 记录不计作 Production 执行次数。该展开从 `run_production_hierarchical_calibration_pilot.py` 的当前方法集、`REFERENCE_FIT_METHOD_IDS`、每方法 OFF/ON/TO 角色和 R22 的 delay-specific method selection 计算得到。

Stage2 也必须按实际 simulator variant 计数，不能只报 594 个场景。V1A 的 0 ns 角色复用契约预计每 case 5 个唯一 Stage2 variant、+4 ns 6 个；在 10 个周期下对应约 32,700 个 variant-period 单元。0 ns 的五 variant 来源于上一失败 case 实际生成 50 份十周期 raw IQ（其 SHA/大小见该 case 的 `raw_cleanup.json`）；+4 ns 六 variant 是配置展开推算，需由首个成功 case manifest 核验。

### 时间与磁盘停止门

- 旧 seed900203 单周期运行及首次失败 seed1930101（未进入 Production）不能可靠外推 V1A 的十周期成本。GPU/CPU 时间预估必须在 V1A 的首个 0/+4 ns case 和六 seed engineering batch 后分别更新；记录 Stage2 elapsed、Production branch elapsed、端到端耗时及设备状态。
- 首次失败 case 的 52 个可再生 `.bin` 已清理前，合计约 2.95 GB；单 case 是当前观测到的 raw peak 参考。成功 case 按 runner compact policy 清理 raw `.bin` 和 CSI full maps，但保留运行 XML、日志、CSI summary、CFAR geometry、关键任务 CSV、manifest 和 SHA 清单。
- 失败 case 的原目录在部分失败前保留约 112 MB；若将其直接按 594 case 外推，会超过 20 GB。因此不得启动 screening 直到工程批次实测成功 case 的 retained bytes、case 临时峰值和全项目占用可证明完整矩阵能按串行清理政策安全执行。该 112 MB 只是偏保守的单 case 观测，不是假定未来每个 case 大小相同。
- 启动每批前检查 `du -sh .` 与 `df -h .`；全项目目标 `<20 GB`。如果预测触顶，先审计可保留的 summary/identity 与可再生成中间产物，再形成独立、可审查的压缩/清理策略；不得删除唯一失败现场或高价值 R21 基线输入，也不得把资源问题转成减 seed、目标、周期或方法。
- 当前 worktree 约 6.9 GB，卷可用约 51 GB。`docs/` 约 5.5 GB，主要含 R21 高价值基线输入与审计证据；本轮已核验没有必要删除这些唯一输入。现有 `outputs/`、`.scratch/` 中未跟踪材料视为既有资料，未纳入清理或提交。

### 执行顺序

1. RTX 3050 空闲后，执行 V1A seed1930101 的 0 ns 与 +4 ns pilot，写入新证据根；每个 case 单独记录原始运行身份和清理台账。
2. 两个 case 都通过后，再完成 seed1930101–1930106 全部 12 个工程条件。若任一工程 gate 失败，停止受影响比较并处理根因，不开始 screening。
3. 六 seed 的耗时和空间数据齐备后，重新计算上述矩阵全量成本。如果在 `<20 GB` 工作区约束和现有设备窗口内不能安全执行，保留完整冻结设计并报告未完成成本；不静默下调规模。
