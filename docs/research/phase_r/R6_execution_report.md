# Phase R6：A0/C 下游分歧定位执行报告

日期：2026-10-22
分支：`codex/unknown-system-error-next-stage`
R6 基线：`3492cde4129bc03f9fbe39960aa07e73a0b57a07`

## 结论摘要

R6 在 seed 101、同一冻结 Release Production 程序和配对输入下，逐字节复现了 R3 的 ON 角度、地速和位置 RMSE。A0/A0 两次重复、诊断开启/关闭控制在 CSI、CFAR 门限/命中、检测科学字段和 TrackManager 输出上保持一致，因此没有证据表明这些差异由运行非确定性或诊断插桩造成。

最早的连续数值差异在已知物理校正后的 F2/channel2；最早的离散分支差异在 CFAR hit map。更重要的是，CUDA GO-CFAR 的有效 CUT 中出现 471,017 个非正门限，且全部成为命中；一处独立 float64 复算显示，生产门限 `-2.624` 对应的理论门限应约为 `114.802`。因此本轮的 CFAR/Pfa 和下游性能归因受到生产数值稳定性缺陷限制。R3 RMSE 确实复现，但不能据此宣称未知时延已被证明是主要 GMTI 瓶颈，也不能宣称统计性收益。

## 状态标签

| 标签 | 本阶段判定 | 证据边界 |
|---|---|---|
| `IMPLEMENTED` | 已实现 | 新增只读全图比较器和 CFAR 门限/命中审计；未修改 Production 算法。 |
| `EXECUTED` | 已执行 | 9 次有效 CUDA Production：A0_ON 重复 2 次、A0_ON 诊断关/开各 1 次、A0/C 的 ON/OFF/TO 各 1 次。另有失败现场保留并排除。 |
| `EVALUABLE` | 部分可评估 | 输入闭合、CSI 全图、A0/A0 重复性和冻结 evaluator 指标可评估；负/零门限涉及的 CFAR margin/Pfa 不可评估。 |
| `PERFORMANCE_VERIFIED` | 仅限复现 | seed101 的历史 ON RMSE 在 R6 精确复现；跨 seed、总体 Pd/Pfa 或普遍性能收益未验证。 |

## 输入、配置与程序身份

- 场景：seed 101，3 periods，beam 50，16 GHz，50 MHz 带宽，60 MHz 采样，1300 Hz PRF，平台高度 6000 m；注册目标速度 6.7 m/s、目标 SNR 30 dB、目标距离 85 km。
- 条件：A0 为 0 ns；C 为注入 +4 ns 后用真值物理逆校正，属于 evaluator-only known-correction upper bound。ON/OFF/TO 分别为目标+杂波+噪声、杂波+噪声、目标-only。
- 历史 R3 登记的 6 组 A0/A1 Stage2 period 数据和 3 组 Known-C period 数据，共 27 个文件，重建后 SHA-256 为 **27/27 匹配**。A0/A1 的原始输入和 C 的校正输入均可追溯到 R3 `case_manifest.json`。
- Stage2 有效面积杂波：连续面积散射注入 390 packets、1,952,655 samples；测得平均功率 0.111676、lag-1 相关系数 0.9800065。未以 `area_enabled` 替代有效注入检查。
- 六个 A0/C runtime XML 的语义比较显示，每一配对仅 `GMTI_data_new`、`result_add`、`track_debug_dir` 三个路径字段不同；算法参数相同。
- 所有 R6 Production runs 使用 commit `3492cde` 的 Release `build/GMTI_pipe_core`，SHA-256 `f65a8aa446754cc79b6417f9275b3a01e73eb85098c6a4f9eaea0cb999c38962`。`src/processOnePeriod.cpp` SHA-256 `07d875c9ba7cd727b6f33dc69eda234a6ea2d49924bea509aea90d170bb62957`；时延校正实现 `scripts/estimate_channel_delay.py` SHA-256 `c62fc2b54c5980ded2d4aaa9e2b4f884613b697dc2093fbf6040fa73bfcf77d7`。
- Production 窗口采集的 CUDA 状态为 RTX 3050 Laptop、driver 580.178.04、P0、约 63–64°C、39–40 W、96% GPU 利用率；当时另一个 Python 进程正在运行。此状态不用于性能基准或吞吐结论。
- R3 历史 manifest 记载 tracked worktree dirty，且未记录 Production binary SHA；所以只能确认输入和历史 evaluator 数字，**不能声称旧 R3 A0/C 使用的二进制与 R6 二进制逐字节相同**。R6 内部的 A0/C 二进制身份则相同。

## 重复性与 Production

A0_ON_rep1、A0_ON_rep2、A0_ON_complexdiag 和 A0_ON_diagoff 使用同一 A0_ON 输入、独立输出目录和 TrackManager 状态。rep1、rep2、诊断开输出三 period 的 CFAR power/threshold/hit 文件、CSI 六类 map、GMTI 二进制、TrackManager 记录均相同；检测 CSV 只在 `run_id`、`source_file` 两个路径/运行元数据字段不同，其余字段逐行相同。诊断关/开时 CSI dump 本身按设置不存在，但 GMTI、检测科学字段和 TrackManager 结果相同。

配对的 A0/C 运行在当前生产 XML、三 period 的有效输入、同一 binary 下完成。完整 CSI taps 为 130×4096，每支 3 periods。A0/C 的具体 stagewise 数值、CFAR 几何、阈值和命中索引见本目录其余报告及 [紧凑证据目录](evidence/r6_stagewise_divergence_20261022/README.md)。

TrackManager 使用正式 `audit_track_manager_run.audit_debug_dir` 审计，6 个条件均为 `pass`、0 violations；任务误差使用现有 `run_track_manager_e2e._evaluate_branch`，不是简化 tracker。

## 失败现场及排除项

失败现场保留在本地 R6 输出中，不用于性能归因：

1. 一次不支持的 `simulate_stage2_statistical --help` 实际启动默认场景，触碰既有 `outputs/stage2/`；无可恢复的先前快照，故保留现场、不回滚。
2. 第一次 CFAR isolated run 未显式指定 debug mode，未生成可审计的 CFAR maps；不用于 branch margin 结果。
3. 后续一轮 echo 输入没有强制 result ID，导致首两 period 的 per-ID map 被覆盖；保留但不用于逐 cell 统计。最终结果使用 `1=path`、`2=path`、`3=path` 强制 period 映射。
4. `A0_ON_complexdiag/track_debug/local_test_20261022_175736_388154_pid47964` 有 118 个重复状态审计违规；不进入 task metrics。有效目录为 `local_test_20261022_175013_307832_pid42871`。

## 复核入口

以下分析命令不重跑 Production，只读取已保留的 R6 原始 maps；输出目录必须尚不存在：

```bash
python3 scripts/phase_r/analyze_r6_stagewise_divergence.py \
  --root outputs/phase_r6_stagewise_divergence_20261022 \
  --output /tmp/r6_stagewise_recheck
python3 scripts/phase_r/analyze_r6_cfar_branch_maps.py \
  --root outputs/phase_r6_stagewise_divergence_20261022 \
  --output /tmp/r6_cfar_recheck
python3 -m pytest -q tests/test_phase_r6_stagewise_divergence.py tests/test_phase_r6_cfar_branch_maps.py
```

Production 运行日志证明使用 `--runtime-mode debug`、指定 runtime XML 和强制 result ID 1/2/3；runner 的 `run_manifest.json` 未保存完整 argv。根据 `src/pipe/main.cpp` 的 CLI，复跑形式为：

```bash
GMTI_CFAR_DUMP_BEAM=50 build/GMTI_pipe_core \
  --runtime-mode debug --local-test <runtime.xml> \
  1=<period_0000.bin> 2=<period_0001.bin> 3=<period_0002.bin>
```

使用各条件自身的独立 runtime XML 和输出目录。运行需要原始输出树中的输入文件；R6 原始 IQ/maps 未提交到 GitHub。完整 runtime XML、logs、每 period `run_manifest.json`、CSI/CFAR maps 及失败现场在 `outputs/phase_r6_stagewise_divergence_20261022/`。

## 清理

关闭阶段时整个工作目录约 6 GB，已低于用户提出的 20 GB 上限。本阶段未删除文件：原始配对输入、全图 taps、失败现场均仍支持审计；提交的只有报告、脚本、测试和约 1.6 MB 的压缩 CFAR 精确索引/汇总证据。详见 [清理台账](evidence/r6_stagewise_divergence_20261022/cleanup_ledger.md)。
