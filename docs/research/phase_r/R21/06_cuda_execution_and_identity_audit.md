# R21：Native CUDA 执行与身份审计

## 实际执行

本阶段两个 seed 均由本机 Native CUDA Production 执行，不是 CPU 模拟或历史 CSV。每个 seed 执行 6 方法 × ON/OFF/TO，共 18 个分支；**36/36 分支 manifest 均为 passed**。Runtime XML、Production 日志、CSI tap、CFAR geometry、检测与 TrackManager 记录均有逐分支身份并进入紧凑审计包。

| 身份 | seed1001 | seed1002 |
|---|---|---|
| CUDA runner 源码 commit | `6f261b218691cf99f2e1d90c2ed5cda70a96a4f4` | `0957f49c028377cbd7a15c2ff0b2eda3f75ce9a9` |
| 完整 CUDA 外层状态 | `failed_or_incomplete`（包装器在所有分支结束后遇到 `KeyError('case_manifest')`） | `completed` |
| Production 分支 | 18/18 passed | 18/18 passed |
| 离线评价状态 | 修复 runner 后对已有 CUDA 输出完成 `evaluation_replay_completed` | `completed` |
| Binary SHA-256 | `9ffc232966bd92a39edfb39b90730851014dfa1ae7237a4ac172a764f31cc566` | 相同 |
| backend / CoreX | 本机 Native CUDA / `use_corex=false` | 本机 Native CUDA / `use_corex=false` |

seed1001 的 wrapper 包装失败如实保留；CPU replay 只重新评价真实 CUDA 输出，不能替代 CUDA 执行，也未将外层失败改写为成功。seed1002 使用修复后的 runner 完整完成。

## 生产路径完整性

- 两 seed 每个保存 18 份 runtime XML、18 份 Production 日志、18 份 CFAR geometry 文件；各方法角色的 CSI taps 和 ROI manifest 亦有记录。
- 每 seed 108 条 CFAR geometry period/band 记录中，每条 valid CUT 为 `527280`，状态可评价。
- 每 seed 36 条 ON/TO truth opportunity 的 range/Doppler 逐机会映射通过。全表见 [`r21_doppler_truth_mapping.csv`](evidence/results/r21_doppler_truth_mapping.csv)。
- 每 seed 面积杂波 390 packets、1,952,655 samples；实测功率分别为 0.111666783002 和 0.110645343505。84.5–85.5 km 地面距离窗在 6 km 平台高度下物理可达；背景映射匹配且 scale=1。
- Stage1/Stage2 配置 SHA、runtime XML / branch manifest / log hash、Production source commit 与输入文件 SHA 见 [`r21_input_identity.csv`](evidence/results/r21_input_identity.csv) 与 [`r21_experiment_manifest.json`](evidence/results/r21_experiment_manifest.json)。

## 执行状态

- `IMPLEMENTED`: runner 与审计/评价器均已实现。
- `EXECUTED`: 两次独立 seed 均有真实 Native CUDA Production 输出。
- `EVALUABLE`: 物理映射通过的检测机会、有效 CUT 和记录齐全的指标可描述性评价。
- `PERFORMANCE_VERIFIED`: 否；两个 seed 不支持统计显著性或推广结论。

文本审计证据 ZIP：[`r21_cuda_text_audit_evidence_20261027.zip`](evidence/cuda_pilot/r21_cuda_text_audit_evidence_20261027.zip)。本地保留的必要原始数组及清理记录见 [`evidence/cleanup_ledger.md`](evidence/cleanup_ledger.md)。
