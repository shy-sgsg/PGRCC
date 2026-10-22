# R6 清理台账

检查日期：2026-10-22。

- 检查目标：PGRCC 外层工作目录。阶段恢复时占用约 6 GB，低于用户要求的 20 GB 上限。
- 本次删除：0 个文件。未为达到已满足的空间目标而删除原始证据、用户数据或失败现场。
- 保留的高价值内容：seed101 27 组 hash-match 输入、已知校正 C 数据、9 次有效 CUDA run 的 runtime XML/manifest/log/CSI/CFAR/detection/TrackManager 结果，以及被排除失败案例。CFAR 数值根因需要这些原始 map 复核。
- 已知失败现场：错误 `--help` 触碰的 `outputs/stage2/`、第一次缺 debug mode 的 CFAR run、bare-path/result-ID 覆盖尝试、A0_ON 重复状态违规 track-debug 目录，全部保留并标注为不可用于正式性能归因。
- 进入 GitHub 提交的内容仅为 R6 报告、分析脚本/测试、机器可读摘要、全图比较 CSV 和约 1.6 MB 的压缩 CFAR exact-index/summary package；未纳入原始 IQ、完整运行日志或大体积 maps。

本台账不声称已完成历史 `outputs/` 全量清理；仅记录本阶段关闭时检查到的空间状态和 R6 证据处置。
