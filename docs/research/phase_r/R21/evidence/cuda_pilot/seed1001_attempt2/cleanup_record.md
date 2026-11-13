# seed1001 attempt2 冗余产物清理记录

- 清理范围：`cases/base__seed_1001__v_p6d7__snr_p30__delay_p4ns/`。
- 删除：66 个与 `seed1001_gatefixed_6f261b2` 对应输入逐字节相同的重复二进制文件，共 4,133,176,320 bytes；513 个重复 CSI 诊断 `.npy` 数组，共 978,130,728 bytes。
- 删除依据：`cleanup_duplicate_input_identity.csv` 记录 66 组 SHA-256 比对，全部 `byte_identical=true`。删除后复核，审计清单中的旧输入均已清除，完整 gate-fixed 运行仍保留作为可复现数据来源。
- 保留：本次 attempt2 的外层 `manifest.json`、`case_manifest.json`、9 个 Production 分支的 `branch_manifest.json`、`production.xml`、`gmt_pipe_core.log`，以及 Stage2 报告、配置、场景、周期映射、CSI 汇总、CFAR geometry 和检测/航迹审计表。
- 清理后状态：9 个 Production 分支的 manifest、runtime XML 和日志均存在；该 attempt2 外层 manifest 保留原失败状态，不能作为成功实验结果引用。
- 用途：保留旧 gate 误报失败的审计记录，同时由逐字节身份比对避免重复存储完整输入和 CSI 中间数组。
