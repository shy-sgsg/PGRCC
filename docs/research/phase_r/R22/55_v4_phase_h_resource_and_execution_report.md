# 55｜R22-V4 Phase H：现有 P0 完整归档资源核验

**日期：** 2026-11-26

**范围：** Phase H Task 3.1；仅审计已存在的 P0 完整 ZIP 与独立 compact 的保留口径。

**研究总纲 blob：** `e637e3769bde7814298ff012d0f42301884e2985`（保持不变）

**机器证据：** [归档 profile 对照](evidence/v4_phase_h/archive_profile_comparison.json)

## 结论

已有 17.6 MB compact **不能**作为正式 `r22_v4_output_complete` 矩阵的单 case 归档预算。compact 精确保留了完整归档的 422 个 V4 必需成员，并加 8 张离线 evaluator 表；完整 profile 则保留更多 case 文件和 symlink 记录。

本地实际存在的一份 target-present、+4 ns P0 完整归档为 **13,037,254,578 bytes**。这是该单 case 身份的实测归档大小，不代表 target-present 0 ns 或任一 H0 schema，也不能直接乘算成整个矩阵的精确预测。

## 对照证据

| 指标 | V4 output-complete P0 | P0 independent compact |
|---|---:|---:|
| ZIP 大小 | 13,037,254,578 bytes | 17,603,158 bytes |
| 保留普通成员 | 4,461 | 430 个清单数据成员 |
| V4 必需成员 | 422 | 同一组 422 |
| 额外 evaluator 表 | 包含在完整 case 输出中 | 8 |
| 完整 profile 独有普通文件 | — | 4,031 个（15,378,127,720 bytes 未压缩） |
| symlink 记录 | 180 | 0 |

完整归档的 manifest 声明策略为：保留全部 case 文件，排除可再生 raster 与经哈希绑定的 dense CSI 数组。1,116 条排除记录由180张 per-case plot、900个 dense CSI array、36个可再生 dense map 组成。完整 ZIP 另含180条 symlink 记录及 `ARCHIVE_MANIFEST.json`。

compact 的 430 个数据成员是完整归档成员的子集；对齐 case 前缀后，422 个 required V4 member 集合完全相同。compact 省去了完整 profile 的4,031个普通成员；其中主要体量来自：

| 完整归档独有类别 | 文件数 | 未压缩字节 | ZIP 成员压缩字节 |
|---|---:|---:|---:|
| `inputs` | 121 | 6,396,582,400 | 5,375,956,093 |
| `production` | 3,660 | 5,377,711,383 | 4,690,257,021 |
| `scenes` | 237 | 3,599,348,486 | 2,951,200,349 |

因此，17.6 MB compact 证明的是**独立离线复算所需的紧凑证据子集**可用，不是完整实验归档只需17.6 MB。正式完整归档单 case 的容量测算应使用完整 profile 的实测数据和目标存储实际情况。

## 身份与审计范围

完整 ZIP 路径：

`outputs/r22_v4_p0_seed1937003_20261123/archives/engineering/engineering__seed_1937003__vr_2d5__snr_20__delay_4ns__targets_20__periods_10__attempt_001.zip`

配对 compact 路径：

`outputs/r22_v4_p0_seed1937003_20261123/archives_compact_candidate/p0_target_present_plus4ns_compact_schema_v2.zip`

审计读取 ZIP central directory、原归档 `ARCHIVE_MANIFEST.json` 和 compact manifest；未解压完整 case 数据。原 ZIP SHA-256 `0974e1dfd1467cdd2bdced856e8c673d40c8966a53ab05261d0c5db2d99c4b02` 与 compact SHA-256 `71b50b6110e04403b2152f8e292bb3e7df9ae4201fd2790d9d97595e7b12ae5a` 沿用既有 P0 验证记录；本次没有重读13 GB文件以重新计算其外层 SHA。嵌入归档 manifest 的本次读取 SHA-256 为 `4f96e4f1fb1cf7db1ebe2d1d07c79a8537b47fccac1efa3f4fbd7c7a0e4036a0`。

本次没有运行 Stage2、CUDA 或 Production，没有修改、迁移或删除 ZIP，也没有把本地归档加入 Git。

## 资源门状态

| Schema / 工作 | 状态 |
|---|---|
| Target-present +4 ns 完整归档 | `MEASURED`：上述 P0 单 case，13.037 GB |
| Target-present 0 ns 完整归档 | `NOT_MEASURED` |
| H0 +4 ns 完整归档 | `NOT_MEASURED` |
| H0 0 ns 完整归档 | `NOT_MEASURED` |
| 缺失 schema 的真实 size pilot | `NOT_AUTHORIZED`：等待用户决定 |
| 唯一原始 P0 ZIP 迁移 | `NOT_AUTHORIZED`：等待用户指定精确目标路径 |
| 正式确认性矩阵 | `NOT_AUTHORIZED` |

用户允许运行期间暂时超过项目20 GB最终保留上限，但这不提供外部文件系统空间，也不授权迁移唯一原始 ZIP。完整 P0 ZIP继续留在原处；等用户提供迁移路径并批准后，才可执行复制、SHA 校验和按批准方案处理原副本。没有新的 size pilot 授权前，不启动缺失 schema 的 GPU/Stage2 运行。

下一步仍需用户集中决定研究参数、外部 seed registry（如有）、size pilot 和原 ZIP 迁移路径。正式矩阵授权是之后的单独门控。
