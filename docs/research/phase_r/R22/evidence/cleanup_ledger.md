# R22 磁盘清理台账

更新日期：2026-10-27。只清理本阶段明确生成、且高价值结果已归档验证的临时运行目录；既有 `outputs/`、`.scratch`、用户数据和任务开始时 4,007 项未跟踪/忽略资料未触碰。

## 保留

- `evidence/preflight/seed900101/`：20 target / 20 beam Native CUDA smoke 的配置、run/branch manifest、Stage2 报告、Production runtime/log、CSI summary 与 CFAR/Detection 诊断。
- `evidence/preflight/seed900202/`：紧凑单周期 pilot 汇总与 27/27 posthoc production gate 审计；约 11 MB。
- `evidence/preflight/seed900203_closure/`：953 个 manifest 登记项、约 39.58 MB payload；包括失败 PKR reference/adapter、Production logs/XML、Stage2 杂波/配对与输入身份、成功分支 CSI/CFAR/Detection/TrackManager 证据。所有登记文件经 SHA-256/大小核验。
- `evidence/results/`：baseline 注册/可比性矩阵、机器可读实验 manifest、逐条件任务指标、面积配对审计和一张明确标注为单周期描述统计的图。
- runner、scenario factory、实验配置、测试及报告。

## 已删除的本阶段临时结果

| 删除路径 | 删除前大小 | 原因与保留替代 |
|---|---:|---|
| `/tmp/r22_multitarget_method_preflight_seed900201` | 1,962,014,009 bytes apparent（此前已删除） | 较早的筛选前方法 smoke；由后续冻结的 seed900202/900203 生产分支审计与评估替代 |
| `/tmp/r22_runner_smoke_seed900202` | 3,181,607,889 bytes apparent（此前已删除） | 原始单周期 IQ/生产中间文件；compact seed900202 包保留 manifest、配置、日志、tap、CFAR、任务输出与输入 SHA/大小 |
| `/tmp/r22_runner_smoke_seed900203` | 2,107,212,248 apparent / 2,112,536,576 allocated bytes | 与 closure run 的 A0/A1 及 `A0_background` 七份 Stage2 `.bin` SHA 全部相同；Production 和 Stage2 binary SHA 相同；目标关联、参数、航迹、H0、Doppler 评估表与 closure 版一致（task 表仅多/少一个空 reason 列）。closure 版包含完整 PKR 失败现场，故旧目录冗余 |
| `/tmp/r22_runner_smoke_seed900203_closure` | 2,116,247,403 apparent / 2,121,658,368 allocated bytes | 953 项紧凑归档逐文件验证完成后删除；raw IQ、数组和图像不留在临时目录 |
| `/tmp/pgrcc-r22-mpl` | 286,460 apparent / 286,720 allocated bytes | 本任务生成的临时 Matplotlib 缓存，随运行 scratch 一并清理 |

此次本轮删除的临时目录共释放约 **4,234,481,664 allocated bytes（约 3.94 GiB）**。此前两项临时目录按记录共 5,143,621,898 apparent bytes，已在本轮之前删除。

## 输入身份与清理边界

未保留生成/校正后的大 `.bin`、`.npy`、`.f32` arrays、临时 PNG 或重复日志副本。七份 seed900203 Stage2 输入的 SHA-256、原路径与 49,204,480-byte 文件大小保存在 `preflight/seed900203_closure/stage2/input_file_identities.json`；Production 输入/输出身份、程序 binary SHA 与运行配置保存在原始 run/case manifest。输入内容未伪装为可从压缩包重建；若需信号级重放，应按清单重新运行 Stage2。

## 空间验收

清理后 nested Git worktree `du -sh .` 为约 **6.7 GB**，低于用户要求的 20 GB。`/tmp` 清理后可用空间约 7.2 GB。后续大型运行仍需在启动前核验容量并使用独立输出目录。
