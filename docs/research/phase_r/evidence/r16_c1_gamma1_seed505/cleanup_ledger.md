# R16 临时输出清理账本

## 清理对象与前置核验

- 唯一清理目标：`.scratch/r16_c1_gamma1_seed505_20261025/`，由本次 R16 pilot 创建；未触碰其他 `outputs/`、`.scratch/` 或用户文件。
- 清理前包含 1,433 个 regular files 与 27 个 symlink entries；regular-file apparent size 合计 3,727,946,373 bytes（约 3.47 GiB；文件系统 allocated size 显示约 3.5G）。目录中包括 Stage2/Production IQ 与 merge `.bin`、完整 CSI `.npy` maps、CFAR `.f32` component/final maps、图像、临时日志和派生输出。
- 清理前已完成证据封存与核验：9 个 Production XML/log、runner/case/evaluator manifests、配置/输入 SHA、truth/Stage2 报告、CSI taps/ROI、CFAR geometry 和审计 CSV、Detection/TrackManager 记录及全部 27 张 final-union maps 的 gzip 版本。压缩地图解压 SHA 与原始 map SHA 全部匹配；R14/R16 输入 SHA 9/9 匹配。

## 保留内容

- 研究报告、冻结设计、方向评估、证据 README、完整 provenance、runner/case/evaluator manifests 和 CSV 审计表。
- 当前 R16 config；Stage2 可读配置、场景 JSON、truth、报告和日志；全部 9 个分支 runtime XML、Production 日志、adapter/CSI/CFAR/Detection/TrackManager 诊断文本。
- 27 个 final-union CFAR `.f32` maps 的无损 gzip 文件，原始与压缩 SHA、字节数和 map 统计见 `cfar_union_maps/compression_manifest.csv`。
- 运行时 Python 源快照、测试源快照及与执行前 HEAD 的 patch。

## 删除内容与原因

清理整个且仅清理上述 R16 临时 output root。删除可从仓库源码、已保存 config/seed/scenario 重建的大型 raw IQ/merged `.bin`、完整 CSI `.npy` maps、可由保留 final-union map 与 CFAR audit 重建/替代的 `.f32` component maps、图像和临时派生产物。Stage2/Production 输入身份仍由 manifest 的 bytes/SHA 记录；raw IQ 不在 Git 证据包内，README 明确说明需要重跑生成，不声称当前可直接复用这些数据文件。

删除命令仅针对此 R16 路径：

```bash
rm -rf .scratch/r16_c1_gamma1_seed505_20261025
```

## 清理后核验

- 删除命令成功，`test ! -e .scratch/r16_c1_gamma1_seed505_20261025` 通过，目标临时目录不存在。
- 保留证据包：571 files，14,070,668 bytes（`du -sh` 约 16M）。
- 清理后文件系统快照：464G 总量、415G 已用、26G 可用（95%）。该快照以 1G 为单位显示，与清理前快照相同；不能据此声称系统空闲空间净增加了 3.47 GiB，其他 workspace 活动可能影响整体用量。
