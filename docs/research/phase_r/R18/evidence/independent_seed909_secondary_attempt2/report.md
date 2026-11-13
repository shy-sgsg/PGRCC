# R18 独立 seed909 参考校正次级评估

状态：`NOT_EVALUABLE`；性能结论：单 seed 描述性比较，不作统计显著性声明。

## 预注册信息边界与执行身份

- 独立参考 seed：909；评价 case：seed 606、6.7 m/s、30 dB。
- 参考场景：`area_clutter_only`、目标关闭、truth 输出关闭、配对背景字段已剔除；估计器 truth 使用：`False`。
- Production binary SHA-256：`3328d7c61bc36b6d3b906692e355c46c328150c0399fa457e6f871bcb79abaf0`。
- CUDA 设备探测：`passed`；name, driver_version, pstate, temperature.gpu, power.draw [W], utilization.gpu [%], clocks.current.graphics [MHz], clocks.current.memory [MHz]; NVIDIA GeForce RTX 3050 Laptop GPU, 580.178.04, P8, 42, 6.36 W, 49 %, 225 MHz, 405 MHz

## 有效面积杂波与跨 seed 可比性

- Stage2 area gate：`passed`；packets=390，samples=1952655，measured mean power=0.0225120348144。
- seed909 与 seed606 预注册统计门：`None`（None）。
- 群组 key 与每组 support count 要求完全一致；Gamma 分布只作描述性比较。

## C2/C3 生产闭环

- Gamma 参考生成：`None`。
- 六个 OFF/ON/TO 生产分支：`None`。
- 外部 Gamma 在实际 runtime XML 和生产 tap 中的应用审计：`None`。
- Mode-B 基线与独立参考条件的逐分支数值见 `branch_comparison.csv`。CSI tap 同时含杂波与噪声，不作为目标 SCNR；检测行数不作为经验 Pfa。
- 本次 `cfar_pfa` / 经验 Pfa 若没有独立 truth-filtered valid-CUT 估计仍标为 `NOT_EVALUABLE`；seed909 参考不是独立统计重复数。

## 限制与解释边界

本次只检验一个 seed606 评价场景与一个独立 seed909 模拟参考。即使所有运行门和可比性门通过，也只证明该固定场景上的独立 Gamma 参考应用可执行并可评价；不证明 airborne 实测统计同质、在线可部署性、普遍 GMTI 收益或 Pd/Pfa 显著改善。

审计清单：`/home/shy/AIR/SAR+AI/project/PGRCC/.worktrees/unknown-system-error-next-stage/docs/research/phase_r/R18/evidence/independent_seed909_secondary_attempt2/experiment_snapshot/snapshot_index.json`；完整运行 manifest：`manifest.json`。

## 执行门失败诊断

seed909 本身的 Stage2 报告通过面积杂波 gate（390 packets、1,952,655 samples、实测均功率 0.0225120348144）。本次 combined gate 失败的原因是 runner 错读了 seed606 的 A0_OFF 报告；该角色复用已生成背景，因此其本地注入数为 0，不能代表 seed606 面积杂波注入源。

seed606 case_manifest.area_clutter_gate 指向实际注入 owner A0_ON，记录为 390 packets、1,952,655 samples、实测均功率 0.0227469995924。下一次执行将按该 gate 指向的 scenario/report 路径和 SHA 重算审计。本次记录保持 NOT_EVALUABLE，不将其改判为通过；Production 方法分支未启动。
