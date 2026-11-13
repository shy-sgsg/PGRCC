# R21 最小 CUDA 因果 pilot：执行与结果

## 结论摘要

在本次限定的 `+4 ns`、`6.7 m/s`、`30 dB`、3 周期、连续面积杂波场景中，B 的 ON 目标命中在两个独立 seed 均低于 A0；Known correction 两个 seed 都恢复到 A0 命中数。R9 fixed D3、Huber D3 和 M1 在两个 seed 的命中数都不低于 Known，但其 F2 输入与 Known 之间仍有明显物理残差，且样本只有两个独立 seed。因此这是受限可行性证据，不是统计显著的 Pd/Pfa 或普遍性能证明。

状态：`IMPLEMENTED=true`，`EXECUTED=true`，`EVALUABLE=true`，`PERFORMANCE_VERIFIED=false`。

R19/R20/R21 evaluator、历史回放和 CUDA runner 相关回归共 `107 passed`。实际命令：

```bash
python3 -m pytest -q \
  tests/test_r19_task_evaluator.py \
  tests/test_r20_task_evaluator.py \
  tests/test_r20_harmonized_task_review.py \
  tests/test_r20_historical_evidence_adapter.py \
  tests/test_r21_cuda_pilot.py \
  tests/test_r21_task_evaluator.py \
  tests/test_r21_historical_task_review.py
```

## 运行身份与实际执行

| 项目 | seed1001 | seed1002 |
|---|---|---|
| Native CUDA 分支 runner commit | `6f261b218691cf99f2e1d90c2ed5cda70a96a4f4` | `0957f49c028377cbd7a15c2ff0b2eda3f75ce9a9` |
| 最终 evaluator replay 源码 commit | `0957f49c028377cbd7a15c2ff0b2eda3f75ce9a9` | 同 Production runner commit |
| Production binary SHA-256 | `9ffc232966bd92a39edfb39b90730851014dfa1ae7237a4ac172a764f31cc566` | 相同 |
| 配置 SHA-256 | Stage1 `ad47b0d02b05ddc4839da39a7498faf1af25ca3ba8eec678597c0ce7a0f651f6`；Stage2 模板 `1f4f9c8b9383614601dc019414b8c8a8788b195301526438b95a672b1ee13998` | 相同 |
| CUDA 状态（运行前） | RTX 3050 Laptop，P5，53°C，6.96 W，9%，465 MHz | RTX 3050 Laptop，P0，50°C，11.44 W，22%，1110 MHz |
| Production 分支 | 18/18 分支 manifest 均为 `passed` | 18/18 分支 manifest 均为 `passed` |
| evaluator 汇总 | 18 条 task rows、36 个 ON/TO truth opportunities；0 个不可评价机会 | 同左；outer manifest `completed` |

seed1001 的 CUDA 分支实际全部完成，但其旧 wrapper 在 GPU 运行后因 `KeyError('case_manifest')` 将 outer manifest 标为 `failed_or_incomplete`。保留该失败状态，没有回写伪装成功。将同一已运行数据交给 commit `0957f49...` 的 CPU 评价重放后，得到 `evaluation_replay_completed` 和完整 18 条任务评价。seed1002 用已修复 runner 完整执行，outer manifest 为 `completed`。CPU 重放只评价真实 CUDA 产物，不替代或冒充 CUDA 执行。

两个 seed 每个均保存：18 份 runtime XML、18 份 Production 日志、18 份 CFAR geometry 文件、54 份 CSI tap summary、54 份 CSI ROI manifest，以及检测/航迹审计输出。每 seed 的 108 条 CFAR geometry period/band 记录均有 `527280` 个 valid CUT，状态为 `evaluable`。每 seed 有 36 条 ON/TO truth-opportunity audit rows（两 seed共 72）；逐机会 range/Doppler 物理映射全部有效。每 seed 的 18 条 condition-role task rows 中，所有 ON 条件均为 `EVALUABLE`；每种方法每 seed 有 3 个 ON target opportunities。

## 面积地杂波与可比性门

| Seed | 平台高度 | Scene 距离窗 | 新生成面积杂波 | 样本数 | 实测面积功率 | 背景复用 | 判定 |
|---|---:|---:|---:|---:|---:|---|---|
| 1001 | 6000 m | 84.5–85.5 km | 390 packets | 1,952,655 | 0.111666783 | 6 个 Stage2 scene 均引用同一背景，mapping matched，scale=1 | passed |
| 1002 | 6000 m | 84.5–85.5 km | 390 packets | 1,952,655 | 0.110645344 | 同上 | passed |

`scenario_resolved.json` 记录 ground Z=0、`use_ground_range_for_position=true`。新的 84.5–85.5 km 场景窗在 6 km 平台几何下物理可达。面积杂波只在 A0_ON 生成一次；其余 A0/B 的 ON/OFF/TO 角色复用同一 Stage2 背景，因此报告中的新生成计数为 0 不表示处理输入无面积杂波。A0 和 B 条件共用相同的背景源；估计方法都从同 seed B 输入校正。两个 seed 面积功率相差约 0.9%，样本和 packet 数相同。

Known correction 与 A0 融合输入闭合：F1 relative RMS 为 0；F2 在 seed1001 为 `8.76e-5`，seed1002 为 `8.82e-5`，均低于冻结的 `1e-3` 门限。F1/F2 均通过 Production 同算子处理；全部六种条件 `processing_chain_passed=true`。

## 目标检测结果

每个格子是同 seed 三个目标周期中的命中数；括号为 `hit fraction`。完整汇总见 [`evidence/cuda_pilot/two_seed_summary.csv`](evidence/cuda_pilot/two_seed_summary.csv)，可视化见 [`evidence/cuda_pilot/two_seed_hit_fraction.svg`](evidence/cuda_pilot/two_seed_hit_fraction.svg)。

| 条件 | seed1001 | seed1002 | 两 seed 描述性合并 |
|---|---:|---:|---:|
| A0 | 2/3 (0.667) | 2/3 (0.667) | 4/6 (0.667) |
| B，+4 ns | 1/3 (0.333) | 0/3 (0.000) | 1/6 (0.167) |
| Known | 2/3 (0.667) | 2/3 (0.667) | 4/6 (0.667) |
| R9 fixed D3 | 2/3 (0.667) | 3/3 (1.000) | 5/6 (0.833) |
| Huber D3 | 2/3 (0.667) | 3/3 (1.000) | 5/6 (0.833) |
| M1 | 2/3 (0.667) | 3/3 (1.000) | 5/6 (0.833) |

按冻结口径，`recoverable space = Known − B` 在 seed1001 为 `1/3`，seed1002 为 `2/3`；各估计方法的 `actual recovered = Estimated − B` 分别为 `1/3`、`3/3`，对应样本内恢复比为 1.0 和 1.5。合并描述值为 `4/6` 对 `3/6`，恢复比 `1.33`。恢复比超过 1 是小样本中估计条件比 Known 多命中一周期的描述结果，不表示估计器物理上超过 Known 上限，也不能当作稳健收益。

无目标匹配的 confirmed track 数（按 seed 相加，属于唯一轨迹数，不是 Pfa）：A0 41、B 63、Known 41、R9 fixed D3 51、Huber D3 47、M1 41。B 的计数较高；估计方法回到 A0/Known 附近，但仍有不少未匹配 confirmed tracks。

## 物理误差与 Known 差距

| 方法 | 校正延迟估计（ns） | 剩余误差 `4 ns − estimate`（ns） | F2 relative RMS，seed1001 / 1002 | Known 严格闭合门 |
|---|---:|---:|---:|---|
| Known | 4.000 / 4.000 | 0 / 0 | `8.76e-5 / 8.82e-5` | passed |
| R9 fixed D3 | 3.409965 / 3.409965 | 0.590035 / 0.590035 | `0.06355 / 0.06370` | 未通过；仅记录距离 |
| Huber D3 | 3.800393 / 3.449202 | 0.199607 / 0.550798 | `0.02229 / 0.05968` | 未通过；仅记录距离 |
| M1 | 3.890649 / 3.635411 | 0.109351 / 0.364589 | `0.01232 / 0.04018` | 未通过；仅记录距离 |

R9 fixed、Huber、M1 三者的估计输入与信息边界不同，表中分别报告。三种估计校正都未达到 Known 的 `1e-3` 相对输入闭合标准；M1 两个 seed 的 residual RMS 均低于 R9 fixed，Huber 在 seed1001 更接近 Known、seed1002 则不如 M1。任务命中记录没有出现对应的 Known 以上损失，但样本过少，不能据此消除这些物理差异。

## CSI、目标保持与虚警描述

| 条件 | Output SCNR proxy，seed1001 / 1002 (dB) | H0 CSI cancellation，seed1001 / 1002 (dB) | TO 目标 CSI retention，seed1001 / 1002 (dB) |
|---|---:|---:|---:|
| A0 | 67.329 / 77.913 | 5.625 / 5.425 | -15.601 / -4.435 |
| B | 70.505 / 77.447 | 5.180 / 5.015 | -11.295 / -4.298 |
| Known | 67.329 / 77.913 | 5.625 / 5.425 | -15.601 / -4.435 |
| R9 fixed D3 | 67.650 / 77.720 | 5.592 / 5.417 | -15.020 / -4.450 |
| Huber D3 | 67.460 / 77.720 | 5.625 / 5.419 | -15.430 / -4.450 |
| M1 | 67.390 / 77.700 | 5.626 / 5.423 | -15.510 / -4.450 |

SCNR proxy 在 seed1001 的 B 高于 A0，而命中率更低；seed1002 的方向相反，说明该 proxy 不能替代任务命中。H0 CSI cancellation 在 B 比 A0/Known 低约 0.4 dB，估计方法接近 A0/Known，但变化较小且只是描述结果。

TO 目标 CSI retention 均为负，范围约 `−4.3` 至 `−15.6 dB`，表明 CSI 输出中的目标 ROI 功率低于 equalized CSI 输入；该衰减在同 seed 各条件间接近，属于处理链的共同目标衰减信号，不能归因于 +4 ns。ON/OFF/TO 的 paired complex transfer 对跨 seed 变化很大：A0 从 seed1001 的 `−4.75 dB / −1.34°` 变为 seed1002 的 `−22.47 dB / 130.83°`；其定义依赖按 role 自适应的 CSI，且归一化残差 RMS 约 0.25–0.40。因此不能把它当作稳定的固定线性目标幅相保持量。本试验没有显示估计校正导致明显高于同 seed A0/Known 的额外目标衰减，但也没有闭合可靠的跨 seed绝对幅相保持结论。

H0 CUT hit fraction 约 `1.88e-4`–`2.44e-4`，每条记录含约 1,581,840 valid CUT。空间相关 CUT 不能作为独立统计单元，所有记录均为 `NOT_EVALUABLE_NO_INDEPENDENT_H0_CUT_UNIT`；正式统计 Pfa 为 `STATISTICAL_PFA_NOT_EVALUABLE`。

## 参数估计记录

以下为 own-success RMSE，只对该条件成功匹配且有参数的机会计算；括号中的 `n/3` 是有效样本数。不同条件机会集合不同，不能将这些值当作同机会配对比较。

| 参数 | seed | A0 | B | Known | R9 fixed D3 | Huber D3 | M1 |
|---|---:|---:|---:|---:|---:|---:|---:|
| 方位角 (deg) | 1001 | 0.0315 (2) | 0.3219 (1) | 0.0315 (2) | 0.0833 (2) | 0.0496 (2) | 0.0415 (2) |
| 方位角 (deg) | 1002 | 0.0240 (2) | — (0) | 0.0240 (2) | 0.0893 (3) | 0.0857 (3) | 0.0684 (3) |
| 平面位置 (m) | 1001 | 48.29 (2) | 495.78 (1) | 48.29 (2) | 127.56 (2) | 76.03 (2) | 63.59 (2) |
| 平面位置 (m) | 1002 | 37.00 (2) | — (0) | 37.00 (2) | 137.09 (3) | 131.61 (3) | 105.04 (3) |
| 距离 (m) | 1001 | 0.269 (2) | 0.826 (1) | 0.269 (2) | 0.269 (2) | 0.269 (2) | 0.269 (2) |
| 距离 (m) | 1002 | 0.643 (2) | — (0) | 0.643 (2) | 0.525 (3) | 0.525 (3) | 0.525 (3) |
| 折叠 Doppler (Hz) | 1001 | 4.045 (2) | 4.625 (1) | 4.045 (2) | 4.045 (2) | 4.045 (2) | 4.045 (2) |
| 折叠 Doppler (Hz) | 1002 | 3.271 (2) | — (0) | 3.271 (2) | 4.247 (3) | 4.247 (3) | 4.247 (3) |

径向速度 `radial_velocity_mps` 在所有条件和两个 seed 均为 `0/3` 个有效参数机会，原因是输出 ambiguity 未 resolved；RMSE 不可评价。底层参数记录的 `evaluator_status` 表示该行存在真值机会，不代表该参数 RMSE 可计算；应以 `valid_parameter_opportunities` 和 `own_success_count` 判定。相关完整数值和缺失原因保存在各 seed 的 `r21_parameter_records.csv`。

## 失败与保留记录

1. seed1001 初次 Stage2 尝试缺少 simulator position 输入，在 CUDA Production 前终止；保留其小型失败记录。
2. `seed1001_attempt2` 的旧面积杂波 gate 将“新生成计数”误要求为每个复用角色正数。完整 Stage2 的共同背景实际有效；旧 manifest 保留失败状态。SHA-256 审计确认 66 份重复输入共 `4,133,176,320` bytes 与 gate-fixed 数据逐字节相同，另有 513 个重复 CSI `.npy` 数组共 `978,130,728` bytes；已删除这些重复派生文件，保留失败 manifest、审计清单和关键文本证据。详见 `evidence/cuda_pilot/seed1001_attempt2/cleanup_record.md`。
3. seed1001 gate-fixed 全部 18 个 CUDA 分支通过，但旧 wrapper 在结果打包时缺少 `case_manifest` 字段，导致外层 manifest `failed_or_incomplete`。分支和错误都保留；之后使用修正后的 CPU evaluator 对同一 CUDA 产物完成只读重放。
4. seed1002 在修复后的 runner 下完成，outer manifest 状态为 `completed`。

工作区实占用在 seed1002 后为 `16 GB`（`du -sh .`），低于项目 `<20 GB` 目标。原字节文本审计材料（runtime XML、日志、CSI/CFAR、配置、Stage2 报告和逐机会 CSV）打包在 [`r21_cuda_text_audit_evidence_20261027.zip`](evidence/cuda_pilot/r21_cuda_text_audit_evidence_20261027.zip)；raw binary 和 CSI ndarray 留在本地证据目录，不把多 GB 原始/可再生成文件纳入提交。

## 证据入口

- seed1001 CUDA manifest：`evidence/cuda_pilot/seed1001_gatefixed_6f261b2/manifest.json`
- seed1001 CPU 评价重放：`evidence/cuda_pilot/seed1001_evaluation_replay_0957f49/manifest.json` 与 `evaluation/`
- seed1002 完整 CUDA manifest 与评价：`evidence/cuda_pilot/seed1002_0957f49/manifest.json` 与 `evaluation/`
- 实际 Production 子目录：各 seed `cases/base__seed_.../production/`；包含 runtime XML、日志、CSI taps、CFAR geometry、detections、track debug 和 runtime 配置快照。
- Stage2 几何和背景复用：各 seed `cases/base__seed_.../scenes/`；包含场景配置、Stage2 报告、area clutter source、period mapping 和 runtime log。
- 历史回归：[`02_historical_evaluator_regression.md`](02_historical_evaluator_regression.md)；R19 冻结的 336 个统计不变。
- Git 提交前再核对研究总纲 blob 与入口值 `e637e3769bde7814298ff012d0f42301884e2985` 一致。
