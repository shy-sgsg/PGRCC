# R22-07：执行身份与质量审计

> **最新执行状态（2026-10-28 11:34:54 UTC）：** 本文下方历史 pre-screening pilot 身份不代表 V2 最新矩阵。V2 screening 17 个 case 通过，随后 `seed1932001 / 2.5 m/s / 30 dB / +4 ns` 因 C4 Mode-B ON 的一组 robust support=7 < 8 触发 `GATE_FAILED`。失败 case compact ZIP、源码/二进制身份和 paired SNR 对照见 [05](05_resource_and_execution_plan.md)；未继续算法比较。

> **版本边界：** 下文具体运行身份和表格记录 seed900101/900202/900203 的历史 pre-screening pilot，不是当前 V2 screening 的执行身份。seed1932001 的 +4 ns attempt004 已通过并归档；完整 screening 已恢复。当前活动 case 和源码/二进制身份以 matrix manifest 与 compact ZIP 为准。最新入口见[资源和执行记录](05_resource_and_execution_plan.md)与[ChatGPT checkpoint 提示词](CHATGPT_REVIEW_PROMPT_SCREENING_CHECKPOINT_20261028.md)。

## 版本和执行身份

- **仓库/分支：** `https://github.com/shy-sgsg/PGRCC` / `codex/unknown-system-error-next-stage`
- **入口 commit：** `6fdc4a9d08d91293d47ba6bdd967e8a22a688c4e`
- **总纲保护：** 入口 blob `e637e3769bde7814298ff012d0f42301884e2985`；本阶段禁止编辑总纲正文。
- **执行源码：** runner manifest 记录 commit 为入口 commit 且执行时 tracked worktree dirty；7 个参与文件有逐文件大小/SHA 的 `source_identity` 快照。执行源码身份不是单一“干净 commit”。
- **Stage2/Production：** Native CUDA binaries 与身份见 [run manifest](evidence/preflight/seed900203_closure/r22_run_manifest.json)，Production `USE_COREX=OFF`。
- **执行命令入口：** `python3 scripts/phase_r/run_r22_cuda_pilot.py --help` 查看 runner 参数；seed900203 的具体参数见 run manifest 的 `design`、`experiment` 与 case manifest。禁止把历史 CPU/synthetic 输出当本次 CUDA 证据。

## 生产路径和结果门

| 检查 | 结果 | 证据/限制 |
|---|---|---|
| seed900101 多目标真实 CUDA Production smoke | 通过 | 20 targets/20 beams/1 period，return code 0；smoke 不作性能比较 |
| seed900202 方法 pilot | `completed_with_gaps` 原始包装状态 | 保存结果经运行时 XML、CSI、CFAR 与日志回查；方法范围无 PK/PKR |
| seed900203 生产记录 | 36 条方法/角色记录；30 passed、6 NOT_EVALUABLE | 两个 PKR estimator branch failed；六个 PKR role rows 不可评价 |
| 面积杂波 gate | 通过 | A0_ON 130 packets / 650,892 samples；A1 使用配对 A0 背景 |
| causal triplet | 通过 | 3/3 状态 passed，最大误差约 `9.54e-6`，门限 `5e-5` |
| CSI taps | 部分通过 | 成功分支存在逐 beam CSI summary；PKR 失败分支没有 CSI summary |
| CFAR valid CUT | 部分通过 | 成功方法每图两个汇总各 527,280 valid CUT；PKR 不存在有效 geometry 输出 |
| residual closure | 不通过阶段门/不可评价 | A0/PK/PKR 所需 PKR CSI tap 缺失 |
| H0 统计 Pfa | 不可评价 | 20 条只标为 `DESCRIPTIVE_ONLY_NOT_STATISTICAL_PFA` |
| 航迹/速度 | 不可评价 | 一周期无 confirmed tracks；radial/ground speed 有效数为 0 |

## PKR 失败链

配置文件的 `min_support=8`、`range_band_bins=6`；实际两个 PKR 估计输出各 26,637 groups，support=6 的有 26,598 个、support=3 的有 39 个；全部 `insufficient_phase_support`。adapter 的 `valid_groups=0`、reason=`all_groups_not_evaluable`。后续 Production 日志记 `scan beam quality gate failed: valid=0/1 required=1 min_ratio=0.95`，return code 1。因此没有 CSI/CFAR 结果可供 PKR 任务评价，不能将失败归咎于 CUDA 算子性能或降低质量门。

## 紧凑归档完整性

seed900203 closure archive 包含 953 个记录项，payload 39,577,372 bytes。`archive_manifest.json` 记录逐文件来源路径、大小和 SHA-256；另有 `stage2/input_file_identities.json` 保存七个被省略 Stage2 原始 `.bin` 的 SHA-256/大小。归档后独立校验清单中的文件 SHA 与大小；未收录 `.bin`、`.npy`、`.f32` 或 `.png`。输入与运行身份可由 case manifest 及该 SHA 清单复核。

归档内 `cluster_detection_lineage.csv` 是现有 Production 的异构记录表：`beam_summary` 行含 15 个字段，`cluster` 行含前 11 个字段；第 12–15 列只对 beam summary 有定义。行宽按 `record_type` 区分，原始记录保留且未用于关键数值表。普通 CSV 行宽检查应对该文件使用按 record type 的 schema，而不能误判为坏行或静默改写历史诊断。

## 限制

- 单周期 pilot 无法评估跨周期航迹确认、连续性、ID-switch、radial velocity 或 ground speed。
- 两个 seed 只供工程可行性和方向提示，不能提供统计显著 Pd/Pfa。
- Stage2 仿真当前是 ideal straight platform，不能代表包含真实 POS/姿态误差的实测 airborne 场景。
- PKR failure 是本次阶段门结果；尚未修复，也没有重跑。
- 完整 R22 V2 matrix 尚未完成；screening 已部分执行，attempt004 的 +4 ns case 尚无终态。`PERFORMANCE_VERIFIED=false`；当前状态见[资源和执行记录](05_resource_and_execution_plan.md)。

## 软件与交付检查

- 相关 delay Stage1、Production calibration、TrackManager 和 R22 测试合计 `147 passed`。单独使用仓库根默认 `build/` 路径时，1 个测试因该路径没有当前 Native CUDA binaries 而在启动检查失败；将 `PGRCC_STAGE2_SIMULATOR` 与 `PGRCC_GMTI_PIPE_CORE` 指向本阶段实际二进制后，全套回归通过。
- 六个本阶段 Python 文件 `py_compile` 通过；生成描述图的脚本实际运行并产出 PNG。
- R22 目录内 237 个 JSON 可解析；963 个 CSV 的结构检查通过。58 个 `cluster_detection_lineage.csv` 按 `beam_summary`=15 列、`cluster`=11 列做 record-type schema 检查。
- R22 与更新过的导航/动态状态/决策日志共检查 281 个本地 Markdown 链接，未发现缺失目标；`git diff --check` 通过。
- 当前总纲 Git blob 仍为 `e637e3769bde7814298ff012d0f42301884e2985`，其文件 diff 为空。
