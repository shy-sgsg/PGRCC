# R13 运行产物清理账本

清理范围严格限于本次新生成的两个 R13 case roots 和本次 evaluator 的三个旧迭代目录；未触碰其他 `outputs/`、`.scratch/`、源码、配置、输入真值记录或历史失败证据。

## 删除范围与理由

| 路径 | 删除内容 | 理由 |
|---|---|---|
| `outputs/phase_r13_minimal_baseline_cuda_20261024/normal_seed101_v6p7/` | 100 个 `.bin` +36 个内部 `.bin` symlinks、684 `.npy`、132 `.f32` | Stage2/Production 原始 IQ、全 CSI 与 CFAR 大图可由固定配置、seed、源码和命令重建；输入 hash、图几何/命中汇总、CSI tap、运行时配置和日志已保留。 |
| `outputs/phase_r13_minimal_baseline_cuda_20261024/low_speed_seed101_v0p5/` | 100 个 `.bin` +36 个内部 `.bin` symlinks、684 `.npy`、132 `.f32` | 同上；仅清除此 R13 case root。 |
| `outputs/phase_r13_minimal_baseline_cuda_20261024/analysis/` | 13 个早期 evaluator 输出文件 | evaluator 迭代版，缺 F1/F2 输入审计和 paired-area 输入审计。 |
| `outputs/phase_r13_minimal_baseline_cuda_20261024/analysis_final/` | 13 个早期 evaluator 输出文件 | 被后续 `analysis_reviewed` 与最终完整审计取代。 |
| `outputs/phase_r13_minimal_baseline_cuda_20261024/analysis_reviewed/` | 14 个 evaluator 输出文件 | 已由最终 `analysis_complete/` 取代；最终版在保留 F1/F2 审计基础上新增 6 行 Stage2 配对面积杂波输入闭合。 |

## 实测清理前大小

两个 case roots 合计有 1,904 个数组路径：1,832 个独立 regular files 加 72 个 `.bin` 符号链接。所有符号链接均指向各自 R13 root 内的 Stage2 IQ；regular files 没有清理范围之外的硬链接。路径表观大小合计 11,926,537,704 bytes（符号链接目标会重复计数）；按独立 regular-file inode 实际分配块合计约 8,389,836,800 bytes。数组统计：

| 场景 | `.bin` | `.npy` | `.f32` |
|---|---:|---:|---:|
| normal seed101 | 100 个 regular files（2,657,386,600 bytes）+36 个内部 symlinks | 684 个 / 1,304,174,304 bytes | 132 个 / 230,424,576 bytes |
| low-speed seed101 | 100 个 regular files（2,657,230,784 bytes）+36 个内部 symlinks | 684 个 / 1,304,174,304 bytes | 132 个 / 230,424,576 bytes |

清理已执行：删除 1,832 个 regular array files（logical bytes 8,383,815,144；allocated blocks 8,389,836,800）及 72 个指向本地 Stage2 IQ 的 symlinks。另删除三个已被最终分析覆盖的 evaluator 迭代目录，共 40 个文件、2,358,027 logical bytes（2,424,832 allocated bytes）。按文件系统块计，清理释放约 8.392 GB。

删除前 `du -sh` 两个 case roots 各约 `4.0G`；清理后分别约 `47M` 和 `32M`，整个 `outputs/phase_r13_minimal_baseline_cuda_20261024/` 约 `80M`。最终 `analysis_complete/`、runner/case manifests、日志、配置、真值表、CSI/CFAR/TrackManager CSV 和证据包均保留。

## 保留内容

- 两个 runner manifest、case manifest、Stage2 scene/scenario manifest、实际 runtime XML、resolved config、运行日志和 compact-evidence 报告；
- Stage2 truth CSV、`period_files.csv` 路径/size/hash、面积杂波与时间相关诊断；
- 24 个 Production branch 的 runtime XML/config dump、core/config logs、CFAR geometry/meta、CSI tap/ROI CSV、adapter/reference CSV、detection CSV 和 TrackManager debug CSV；
- 最终 `analysis_complete` 机器可读表、target ROI samples、输入/F1-F2/paired area hashes、主结果图及复现命令。

原始 manifest 保留执行时路径和身份；路径在原运行主机上可定位，包内不携带大型 IQ/CSI/CFAR 数组。按 `reproduce.sh` 可在当前 CUDA 环境重新生成完整 case。
