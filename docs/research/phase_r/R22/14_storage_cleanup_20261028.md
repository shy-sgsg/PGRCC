# 2026-10-28 存储清理记录

## 目的

在继续 R22 本机 CUDA 工程 seed 时，将项目目录保持在 20 GB 以下，同时保留足够的历史实验记录、配置、审计和高价值汇总结果。

## 清理范围

只处理以下两个已完成 R21 CUDA seed 的生成型数组：

- `docs/research/phase_r/R21/evidence/cuda_pilot/seed1001_gatefixed_6f261b2/`
- `docs/research/phase_r/R21/evidence/cuda_pilot/seed1002_0957f49/`

每个目录删除 24 个 `.bin` 文件（1,771,361,280 字节）和 342 个 `.npy` 文件（652,087,152 字节），共 366 个文件、2,423,448,432 字节。两目录合计删除 732 个文件、4,846,896,864 字节。

这些文件是由保存的运行配置和 Stage2 场景生成的 IQ/Production 输入数组，以及可重建的 CSI 派生数组。两份 `manifest.json`、配置、源码和二进制身份、输入 SHA 记录仍保留。重新执行可按 [R21 实验设计](../R21/05_experiment_design.md) 的命令重建数组；数组删除后，若需复核其逐文件内容，必须先重新生成并与 manifest 中记录的 SHA 比较。

## 保留内容

保留上述 seed 目录中的 manifest、配置、Stage2 报告、runtime XML、Production 日志、CSV 评价与 tap 汇总、CFAR/几何审计、图表及其他文本记录。R21 的实验结论和结果报告没有改写。

另外保留 `seed1001_attempt2` 的既有失败 manifest 与清理审计，不触碰当前 R22 正在运行的目录，也不删除本机其他实验目录。

## 清理后状态

清理完成后项目占用约 13 GB（当时 seed1930103 的 +4 ns CUDA case 仍在运行，大小会随运行变化）。R22 case 的原始数组仍由 runner 在每次运行结束时按独立 cleanup ledger 清理。
