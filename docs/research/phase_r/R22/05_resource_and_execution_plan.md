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
