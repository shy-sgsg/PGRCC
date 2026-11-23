# R22-V4-P0 首轮失败审计

- 状态：`GATE_FAILED`。
- 原因：V4 目标复数响应抽取需要 CSI 功率图/复数 tap；首轮运行的 180 行 ROI manifest 中 artifact 路径均为空，触发 fail-closed 完整性门。
- Stage2 与 18 条已注册 Native CUDA Production 分支均返回成功；这不构成性能结论。
- 约 9.5GB 的首轮 raw 仅在 compact metadata ZIP 完成 CRC、成员清单、逐成员大小和 SHA-256 校验后删除。
- ZIP 保留运行身份、配置、Stage2 汇总、Production 分支审计、runtime XML、日志和 CSI ROI/tap 汇总；大型 IQ/派生数组未复制。
- 文件哈希和 ZIP 身份见 `attempt_001_failure_audit.json`；清理记录见 `cleanup_ledger.json`。
