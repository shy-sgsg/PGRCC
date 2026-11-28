# R22 V3.2 收尾磁盘清理记录

日期：2026-11-09

## 清理对象

- 删除路径：`.scratch/task5_targeted_gpu_zero_retry/cases/`
- 清理前文件数：918
- 清理前逻辑字节数：88,205,453 bytes（约 84.1 MiB；逻辑大小不等于实际释放量）
- 保留：父目录 `manifest.json`（13,597 bytes）。该 manifest 状态为 `not_started`，`cases=[]`；该遗留目录不纳入本阶段结果或结论。
- 清理理由：这是没有完整 case 身份记录的孤立旧 targeted-zero 重放产物，不参与 V3.2 分析。V3.2 已在独立冻结设计下完成零时延参考覆盖并留存核验归档。清理没有修改或删除 V3.2 archive、失败/缺口证据、设计配置、运行身份或高价值历史报告。

## 保留边界

- 保留全部 288 个最新 V3.2 ZIP 和 1 个已核验历史 attempt ZIP；完整 ZIP 约 1.5 GiB。
- 保留 V3.2 matrix manifest、per-case cleanup ledger、汇总分析和约 3.1 MB GitHub 精简审查包。
- 保留其他 scratch compact evidence；未清理其历史运行 manifest。

此清理只针对以上确认路径，不递归清理其他 outputs 或用户历史数据。
