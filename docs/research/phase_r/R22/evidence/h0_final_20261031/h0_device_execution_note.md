# H0 CUDA 续跑设备观察记录（2026-10-31）

本记录用于说明并行训练期间的设备状态，不作为性能基准。

- 用户明确要求在外部训练仍运行时继续本机 CUDA H0 矩阵。
- runner 启动前 `nvidia-smi`：NVIDIA GeForce RTX 3050 Laptop GPU，4096 MiB 显存；总用量 2231 MiB、空闲 1865 MiB；GPU 利用率 97%；59°C；P3；39 W / 40 W。
- 同次进程表中外部训练 Python 进程 PID 31526 占用约 1650 MiB；另有桌面进程占用显存。
- H0 runner 执行期间抽样观察到 GPU 利用率约 6%–100%、温度 50–61°C，空闲显存最低约 393 MiB；工作区磁盘最低约 3.3 GB 可用。runner 最终退出码为 0，未观察到 CUDA OOM 或设备错误。
- 执行源码 commit：`a688ca52616cc380b07810d4f8fbd79ed73d46bc`；Stage2 和 Production binary SHA-256 见同目录 `h0_analysis_manifest.json`。
- 这些是运行期间的抽样观测，不是完整遥测时序；未用于性能或吞吐量比较。Native CUDA、`USE_COREX=false` 和每 case preflight 身份由最终分析 manifest 核验。
