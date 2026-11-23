# R22-V4-P0 execution 002：诊断采集未闭合

- Seed 1937002 已产生真实 Native CUDA 执行身份，不再复用。
- V4 需同时启用 `csi_metrics_dump_power_maps` 与 `csi_metrics_dump_intermediate_maps`；该次只开启前者。
- 已检查 150 行 CSI ROI manifest，150 行缺少所需复数/前级功率数组路径；在矩阵评估前人工中断。
- 中断前 15 个 Production 分支均返回成功，但这不是完整 case 评价。
- 原始 9,350,321,206 bytes 已在 423 个审计包成员通过 CRC、精确成员、逐成员大小与 SHA-256 验证后清理。
- 状态：`INTERRUPTED_NOT_RESUMABLE`；终态侧车见该 seed 的 `matrix/execution_terminal_state.json`。
