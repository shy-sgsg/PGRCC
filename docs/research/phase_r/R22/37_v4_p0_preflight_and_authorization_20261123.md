# R22-V4 P0：预检与授权记录

日期：2026-11-23
状态：单个 target-present Native CUDA 工程 pilot 已授权并执行；不代表 V4 确认性设计冻结。

## 研究定位与范围

本 pilot 服务于研究总纲所述的 GMTI 未知物理误差校正与任务评价目标。本次只验证一组 +4 ns 条件下，V4 周期因果校正与完整 Production 输出、压缩归档、断点续跑及清理流程能否真实运行并留下可审查证据。它不构成对因果估计器的统计性能检验，也不改变长期研究方向。

用户明确批准一个 target-present P0 Native CUDA pilot 及 GPU 执行。获准范围为 seed 1937003 的单个 engineering case、Stage2、18 个 Production 分支、完整输出归档、归档核验后的 raw 清理、报告与推送。没有获准启动 240-case 确认矩阵或 AI 训练；外部训练进程保持原样。

## 身份与预检

| 项目 | 身份 |
|---|---|
| 仓库分支 | shy-sgsg/PGRCC，codex/unknown-system-error-next-stage |
| 执行源码 commit | 715639c8e9aad7359eb1a5058f9238c0d51f99a1 |
| 设计 | docs/research/phase_r/R22/config/r22_v4_size_pilot_draft_20261123.json |
| 设计 SHA-256 | f9de67eab44c10e7f076e3852f76a216991fba29dbb7a9e1113f1021e4559954 |
| 研究总纲 blob | e637e3769bde7814298ff012d0f42301884e2985 |
| seed | 1937003 |
| 条件 | 2.5 m/s，20 dB，+4 ns，20 targets，10 periods，beam 50 |
| 后端 | 本机 Native CUDA Release，USE_COREX=OFF |

此前两个尺寸 pilot 候选 seed 已分别用于真实 CUDA 尝试，1937001 为 gate failure，1937002 为捕获不完整且不可续跑。全项目本地 registry 扫描后选定 1937003；扫描覆盖本地 docs、outputs、scripts、simulator、tests、416 个 ZIP、41,132 个文本文件及 Git 可达历史，未发现冲突。审计未覆盖不可访问的外部归档服务，因此结论是本地项目范围通过，不宣称全互联网/所有外部介质全局唯一。

启动时工作区无 dirty 实验源码。Stage2/Production binary SHA、CUDA 设备快照、空间监测及用户授权范围均记录在机器证据中。RTX 3050 Laptop GPU 显存 4 GiB；当时外部训练进程持续占满 GPU 利用率。保留该进程，pilot 依授权启动。

## 预检门

- 文件系统启动可用空间：约 53.61 GiB；pilot 中最低观测约 25.82 GiB；清理 raw 后最终观测约 41.44 GiB。
- 运行期间保留的最小空间余量：10 GiB；监测记录显示未跌破。
- 设计仍为 DRAFT，正式确认性实验的 MDE、检测覆盖率非劣界及航迹保护界值仍为空。
- seed registry 的限制、CUDA 设备状态、运行参数、build identity 和授权均在 [机器证据](evidence/v4_native_cuda_pilot_20261123/README.md) 中列明。

本次授权只解除单 case pilot 的执行门，不等于批准确认性矩阵，也不代表统计门已闭合。研究总纲正文未修改。
