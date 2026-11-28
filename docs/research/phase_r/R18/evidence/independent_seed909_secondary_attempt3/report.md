# R18 独立 seed909 参考校正次级评估

状态：`NOT_EVALUABLE`；性能结论：单 seed 描述性比较，不作统计显著性声明。

## 预注册信息边界与执行身份

- 独立参考 seed：909；评价 case：seed 606、6.7 m/s、30 dB。
- 参考场景：`area_clutter_only`、目标关闭、truth 输出关闭、配对背景字段已剔除；估计器 truth 使用：`False`。
- Production binary SHA-256：`3328d7c61bc36b6d3b906692e355c46c328150c0399fa457e6f871bcb79abaf0`。
- CUDA 设备探测：`passed`；name, driver_version, pstate, temperature.gpu, power.draw [W], utilization.gpu [%], clocks.current.graphics [MHz], clocks.current.memory [MHz]; NVIDIA GeForce RTX 3050 Laptop GPU, 580.178.04, P8, 40, 6.21 W, 34 %, 210 MHz, 405 MHz

## 有效面积杂波与跨 seed 可比性

- Stage2 area gate：`passed`；packets=390，samples=1952655，measured mean power=0.0225120348144。
- seed909 与 seed606 预注册统计门：`passed`（compatible_under_frozen_tolerances_not_statistically_proven）。
- 群组 key 与每组 support count 要求完全一致；Gamma 分布只作描述性比较。

## C2/C3 生产闭环

- Gamma 参考生成：`executed_both_estimators_valid_rows`。
- 六个 OFF/ON/TO 生产分支：`NOT_EXECUTED`。
- 外部 Gamma 在实际 runtime XML 和生产 tap 中的应用审计：`NOT_EXECUTED`。
- Mode-B 基线与独立参考条件的逐分支数值见 `branch_comparison.csv`。CSI tap 同时含杂波与噪声，不作为目标 SCNR；检测行数不作为经验 Pfa。
- 本次 `cfar_pfa` / 经验 Pfa 若没有独立 truth-filtered valid-CUT 估计仍标为 `NOT_EVALUABLE`；seed909 参考不是独立统计重复数。

## 限制与解释边界

本次只检验一个 seed606 评价场景与一个独立 seed909 模拟参考。即使所有运行门和可比性门通过，也只证明该固定场景上的独立 Gamma 参考应用可执行并可评价；不证明 airborne 实测统计同质、在线可部署性、普遍 GMTI 收益或 Pd/Pfa 显著改善。

审计清单：`/home/shy/AIR/SAR+AI/project/PGRCC/.worktrees/unknown-system-error-next-stage/docs/research/phase_r/R18/evidence/independent_seed909_secondary_attempt3/experiment_snapshot/snapshot_index.json`；完整运行 manifest：`manifest.json`。

## 执行门失败诊断

seed909 Stage2 有效面积杂波与预注册统计可比性均通过。C2 的 3 个 Gamma groups 与 seed606 Mode-A OFF support 完全一致。C3 两边均有 117 个 group，group key 完全一致且全部 status=OK、truth_used_in_estimator=false；但严格冻结的每组 support_count 完全相等门不通过：116/117 groups 不同，support 总数从 95803 变为 92859。

这不是缺失群组或生产运行失败：C3 seed909 Production 分支 returncode=0，track audit、CSI tap、CFAR valid-CUT 与 Gamma tap 均通过。support_count 是 Robust DDC 在该 realization 上得到的有效拟合样本数，会随独立杂波 realization 变化；本次预注册门要求完全一致，故按规则停止，不放宽门槛、不运行 seed606 六个评价分支，也不报告性能差值。

## 保留的启动/审计失败记录

- seed909 runner launch 1: `NOT_EXECUTED`；ModuleNotFoundError: No module named 'scripts'；记录：`/home/shy/AIR/SAR+AI/project/PGRCC/.worktrees/unknown-system-error-next-stage/docs/research/phase_r/R18/evidence/independent_seed909_secondary_attempt3/preflight_failures/attempt1_manifest.json`。
- seed909 runner launch 2: `NOT_EVALUABLE`；RuntimeError: effective Stage2 area-clutter gate failed；记录：`/home/shy/AIR/SAR+AI/project/PGRCC/.worktrees/unknown-system-error-next-stage/docs/research/phase_r/R18/evidence/independent_seed909_secondary_attempt2/manifest.json`。
