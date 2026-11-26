# 56｜R22-V4 Phase H 持续执行与资源门状态

**日期：** 2026-11-26
**执行基线：** `5328fa3a51efc44f311d8fd7d28ddbcf2ade0b7e`
**研究总纲 blob：** `e637e3769bde7814298ff012d0f42301884e2985`（保持不变）
**本阶段状态：** `PARTIALLY_BLOCKED`；只有依赖额外可写空间的 size pilot 暂停，其他任务已推进。

## 结论

用户已批准 Phase H 报告54的统计筛选参数，并批准三个缺失 schema 的 Native CUDA size pilot。统计与 seed 身份工作已完成；确认矩阵执行器 dry-run 与受影响 CPU 回归通过。真实 size pilot runner 现在会在每个 case 开始前检查其输出文件系统的可用字节数，不足时将 manifest 写为 `PAUSED_RESOURCE_GATE`，不创建 attempt，也不调用 CUDA case；容量恢复后可用 `--resume` 重新检查。

本机没有满足门槛的可写实验卷。项目 ext4 仅余12,116,500,480 bytes，独立 `/tmp` 余7,991,930,880 bytes；外接卷余90,118,549,504 bytes但只读。根据已测的 P0 完整输出案例，单 case 实际峰值消耗为29.821 GB，保留空间为10.737 GB，所以 runner 当前采用40,558,522,368 bytes/输出文件系统作为保守启动代理。三个新 schema 的体量尚未实测，该代理不冒充其测量值。

因此，三个已授权 pilot 在首个 case 前暂停；没有新 Stage2、Production 或 CUDA 运行。正式确认矩阵仍未授权，设计保持 `DRAFT_NOT_FROZEN`。完整资源快照见[机器预检](evidence/v4_phase_h/resource_preflight_20261126.json)。

## 任务状态

| 任务 | 状态 | 证据与边界 |
|---|---|---|
| 统计合同 | `COMPLETED` | 用户批准的参数写入配置、统计分析器与执行器；受影响回归120项通过。 |
| Seed registry | `COMPLETED_WITH_LOCAL_SCOPE_LIMITATION` | 本地身份审计无已检查范围内冲突，registry已固定哈希；不声称外部全局唯一。 |
| 资源方案 | `PARTIALLY_BLOCKED` | 检查了项目 ext4、`/tmp`、`/dev/shm`、`/run`和外接卷；当前无足够可写位置。未迁移 P0。 |
| 三 schema size pilot | `PAUSED_RESOURCE_GATE` | 用户已授权，但在任何 case 启动前停止；没有实验产物需要清理。 |
| 设计冻结准备 | `IN_PROGRESS / NOT_READY` | 240-case/120-batch dry-run为默认拒绝状态；资源、真实完整输出体量、Native CUDA Production 审计尚未闭合。 |
| 正式矩阵授权 | `NOT_REQUESTED` | 尚不具备真实可执行条件；也未请求正式矩阵授权。 |

## 已固定的统计合同

- 位置主比较采用 Scheme A，MDE为3 m；每个速度至少需要10个共同成功 seed 支持。
- 检测覆盖率非劣界值为1.0个百分点；正确确认航迹覆盖只作 `DESCRIPTIVE_ONLY`，仍完整报告。
- Target-present与H0分别使用30个 seed。
- 位置区间采用 seed-cluster studentized bootstrap-t，10,000次重抽样，PCG64 seed 22029；检测和航迹覆盖差使用 paired seed-level t 区间。
- H0 的 seed-any-event 与 false-confirmed-track-event 两个端点，均要求完整30 seed集合的单侧95% exact 上界严格小于10%。30 seed中0事件上界为9.503%，1事件为14.860%，所以每个端点都必须为零事件才可能通过。
- 继续保留既有缺失数据处理与 fail-closed 规则；不以未完成 seed 计零事件。

机器可读用户决策见[user参数批准记录](evidence/v4_phase_h/user_parameter_approval_20261126.json)。确认性草案配置文件 SHA-256 为 `643b2168b3cd4bff7f26fe29618686db5877a5c4a29492d2f5333e2a47ac4ee7`；此哈希绑定当前草案，不代表设计已冻结。

## Seed registry 身份核验

本地检查覆盖 `docs/`、`scripts/`、`configs/`、`experiments/`、`simulator/`、`outputs/`和`.scratch/`：共48,698个普通文件、418个 ZIP、3,697个嵌入身份元数据条目；未发现损坏 ZIP。新增 pilot seed 1937004/1937005没有在检查到的路径、成员名或身份清单中出现；与确认性 seed cohort分离。旧 pilot seed 1937001–1937003按历史状态排除。

固定 registry 的规范化内容哈希为：

`ec73bb776882a25cde4250d0092fec24157b7d38aaea6961729a909f1826abfd`

该哈希按排序、紧凑 JSON 编码计算，并排除文件内自引用的 `registry_sha256` 字段。没有外部或离线 registry 可供核验；GitHub Actions artifacts、LFS、无关仓库和外部存储也未扫描，因此不作全局唯一性声明。详细范围写在[registry](evidence/v4_phase_h/seed_registry_20261126.json)。

## 资源预算与 GPU 预检

历史 target-present +4 ns P0 的完整 `r22_v4_output_complete` ZIP 为13,037,254,578 bytes；从当时磁盘起始可用空间到实测最低可用空间的峰值消耗为29,821,104,128 bytes，记录的保留余量为10,737,418,240 bytes。由此得到单 case 保守资源代理：

`29,821,104,128 + 10,737,418,240 = 40,558,522,368 bytes`

本次测量：

| 位置 | 可用空间 | 可写性 | 结果 |
|---|---:|---|---|
| PGRCC 所在 ext4 | 12,116,500,480 bytes | 可写 | 低于代理门槛 |
| `/tmp` tmpfs | 7,991,930,880 bytes | 可写 | 低于代理门槛 |
| `/run/media/shy/C61C9A341C9A200B` | 90,118,549,504 bytes | 只读 | 不可作为输出位置；未重挂载或改权限 |
| `/dev/shm`、`/run` | — | 只读 | 不可作为输出位置 |

同一时刻 GPU 快照：RTX 3050 Laptop，显存总量4096 MiB、空闲2501 MiB，GPU利用率14%，P0，56°C。没有停止或修改外部训练进程。GPU 状态本身允许后续继续评估，但磁盘门不满足，所以本阶段没有启动它。

PGRCC 项目目录在该次预检时为18,631,958,528 bytes，低于20,000,000,000-byte最终上限，余1,368,041,472 bytes；该上限是最终保留限制，不是本次 pilot 的峰值限制。没有删除历史数据或 P0 ZIP。

## 执行器与验证

`scripts/phase_r/r22_experiment_matrix.py` 对这个三 schema pilot 增加了逐 case 磁盘门：

1. 从已测 P0 完整输出资源记录计算峰值消耗与保留空间；不引用17.6 MB compact尺寸。
2. 检查 matrix manifest、raw case、完整归档及失败归档路径所在文件系统。
3. 空间不足时记录 `PAUSED_RESOURCE_GATE`、磁盘路径、实测可用字节和要求字节；在 case attempt 目录创建及 runner 调用前退出。
4. `--resume` 会重新检查当前容量；仍不足则继续暂停，足够时沿原设计身份继续。
5. 三 schema pilot 强制提供完整归档输出根，避免无归档运行被误认为尺寸测量。

资源门的 CPU 回归先在无实现时失败（runner错误地进入case并得到 `GATE_FAILED`），补入后验证了“暂停不调用 case runner”以及“容量恢复后从同一 manifest 续跑”。该用例使用受控磁盘余量，不是文件系统或 CUDA 性能证据。

随后使用当前真实 `/tmp` 余量对授权的 `engineering` pilot runner 做了一次 live preflight：结果为 `PAUSED_RESOURCE_GATE`，三个输出目录的临时写入探针均成功，`cases=[]`、没有 attempt 目录、runner未调用；临时 manifest读出判定后随临时目录删除。该动作只验证执行器会在当前容量不足时停在首个 case 之前。

本轮对 M1/Huber prefix输入范围、估计失败的当期零校正、同一估计对 ON/OFF/TO 的应用、CSI_AFTER证据结构及完整归档验证路径做了定向只读源码检查。它没有替代独立 ChatGPT 源码审查，也没有验证实际 Native CUDA 运行行为；这两项仍属冻结前证据缺口。

当前草案确认矩阵的真实 CPU dry-run结果：

- 240个 case、120个 paired batch；
- 状态 `NOT_AUTHORIZED`；
- `execution_started=false`、`stage2_executed=false`、`production_executed=false`、`cuda_executed=false`、`ai_training=false`。

完整摘要见[executor dry-run](evidence/v4_phase_h/executor_dry_run_20261126.json)。该执行计划 registry SHA-256 为 `23251fcec8871406ec512678364450f785e91cee04316f9e6b8be33cae66a3fc`；它与前文用于固定候选 seed 身份的 registry 内容哈希是不同对象。用于生成报告的临时 dry-run目录已删除。

受影响测试命令：

```bash
python3 -m pytest -q \
  tests/test_r22_experiment_matrix.py \
  tests/test_r22_cuda_pilot.py \
  tests/test_r22_v4_confirmatory_executor.py \
  tests/test_r22_v4_confirmatory_statistics.py \
  tests/test_r22_v4_confirmatory_analysis.py
```

结果：`121 passed`。这些是 CPU 测试，不是 Native CUDA 实验。

## 科研状态与下一步

- 本阶段统计参数属于用户预先批准的研究筛选规则，不是产品验收标准。
- 本地 seed registry不声称全局唯一；当前没有发现可访问范围内的冲突。
- 三个 size pilot 用于真实归档和资源测量，不作为确认性科研结果。三个 schema 的 archive、expanded data、峰值和最终保留量均仍 `NOT_MEASURED`。
- `IMPLEMENTED=true`（统计合同和资源门）；`EXECUTED=true`只适用于 CPU 回归与 dry-run；size pilot真实 CUDA `EXECUTED=false`、`EVALUABLE=false`、`PERFORMANCE_VERIFIED=false`。
- 正式确认性矩阵仍未授权，AI训练和研究总纲修改均未授权。

若需一次性完成三个 case，并在整组结束前保留每个完整归档，按历史 P0 输出体量代理估算约需66.63 GB峰值可用空间；建议提供**至少70,000,000,000 bytes的本机可写目录**。这是容量规划代理，不是三个缺失 schema的实测结论；达到该容量后，仍会逐 case复测并按资源门续跑。该位置只用于本次新 pilot，不需要迁移现有完整 P0 ZIP，也不应使用只读卷或云盘。pilot 完成后只把审计摘要和必要配置留在项目目录，完整临时输出按清理台账处理。
