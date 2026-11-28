# R22-V4 P0 独立 compact 归档与复算验证

日期：2026-11-24

状态：单个 target-present、+4 ns P0 compact 归档通过独立校验与复算；不代表其余三种 case schema 已有真实归档尺寸测量。

## 1. 原件与compact身份

唯一完整 P0 ZIP 保存在本机：

`outputs/r22_v4_p0_seed1937003_20261123/archives/engineering/engineering__seed_1937003__vr_2d5__snr_20__delay_4ns__targets_20__periods_10__attempt_001.zip`

- 大小：13,037,254,578 bytes。
- SHA-256：`0974e1dfd1467cdd2bdced856e8c673d40c8966a53ab05261d0c5db2d99c4b02`。
- 本阶段没有修改、移动或删除原件。

独立 compact ZIP 也只保存在本机 `outputs/`，不提交 Git：

`outputs/r22_v4_p0_seed1937003_20261123/archives_compact_candidate/p0_target_present_plus4ns_compact_schema_v2.zip`

- 大小：17,603,158 bytes；SHA-256：`71b50b6110e04403b2152f8e292bb3e7df9ae4201fd2790d9d97595e7b12ae5a`。
- 430 个成员：422 个 V4 必需成员及 8 个核心 evaluator 成员；未压缩 115,657,515 bytes。
- ZIP 成员压缩数据合计 17,407,218 bytes，ZIP 目录和封装开销 195,940 bytes。
- compact 为完整 ZIP 的约 0.1350%（约缩小 740.6 倍）。

## 2. 完整性与证据保留

compact 重新打开并核验通过：ZIP CRC、430/430 成员 SHA、零重复成员、零符号链接、零不安全路径、manifest 身份一致。保留了18个 Production 分支、每分支十周期 Detection、CFAR geometry、八类 TrackManager lineage 文件、truth/association、目标复数 ROI、估计与校正记录及八张 evaluator 表。成员级清单见 `evidence/v4_final_freeze_readiness/compact_archive_manifest.json`。

校验与复算汇总见 `compact_archive_validation.json`，逐表 parity 见 `compact_archive_parity.csv`。新 compact 是独立重分析的唯一 P0 数据输入；重分析没有打开完整原始 ZIP。11 张核心表与完整 P0 的冻结重算表逐字节一致，状态 `COMPACT_REANALYSIS_PARITY=PASS`，耗时约2.58秒。

## 3. 复算范围和边界

可从 compact 独立复算六方法正确检测、方法位置汇总、M1与Fixed/Huber/Known共同成功位置误差、逐周期/逐目标任务、TrackManager lineage、CFAR geometry 和目标复响应摘要。 compact 中也保留原始 Production 逐期输出，可供进一步离线检查。

这是真正生成的独立科研归档，不是对全量 ZIP 内成员压缩字节的估计。不过当前实测样例只有 target-present、+4 ns。H0、0 ns 的完整运行产物体量仍未测量；四种 schema 的 CPU 夹具测试不等价于真实 Stage2/Production 归档。

## 4. 磁盘治理

已按 `cleanup_ledger.json` 删除两份旧 schema compact 副本，净释放 35,206,116 bytes；保留 schema-v2 compact 与唯一完整 P0 原件。项目根目录实测约18.38 GB，低于最终20 GB上限。实际 ZIP 留在 `outputs/`，不会进入提交或 GitHub。未来若确认性结果使目录暂时超过20 GB，按用户说明可在运行期间超过；应先完成汇总与审查，再依据清理台账删除可再生 raw。原始 P0 唯一证据在用户批准前不得删除。
