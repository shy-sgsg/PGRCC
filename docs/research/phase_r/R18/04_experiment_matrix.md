# R18：冻结实验矩阵与评价机会

状态：`IMPLEMENTED`（先于 R18 CUDA 结果冻结）。机器可读配置：[`phase_r18_native_cuda.json`](../../../../configs/research/phase_r18_native_cuda.json)。

## 固定变量

| 变量 | 冻结值 |
|---|---|
| 通道时延误差 | 0 ns |
| 目标距离 | 85 km slant range；expected bin 2048；beam 50 |
| 目标速度 | 6.7 m/s、0.5 m/s |
| Stage2 target SNR | 30 dB、20 dB |
| 正式种子 | 606、707、808 |
| Period | 3 个连续 period |
| 平台/波形 | 60 m/s 平台、6,000 m 高度、16 GHz、PRF 1,300 Hz、130 pulse/period |
| 面积地杂波 | 连续纹理模型；目标斜距窗 84.5–85.5 km；地面 0 m；配置均值功率 0.02、texture sigma 0.25、rho 0.98 |
| 热噪声 | 配置功率 0.001 |
| 方法 | 核心 C0、C1、C2 Mode-B、C3 Mode-B；Mode-A C2/C3 单独信息子矩阵 |
| 目标/CFAR门 | 延续已审计 Production 和 R7 固定 truth matcher；不为 R18 更改阈值或放宽质量门 |

场景地面斜距与 6 km 平台高度相容；84.5–85.5 km 是已经在 R13 真实报告中验证过连续面积散射 packet/sample 和功率的距离窗。R18 仍需对本次每个 seed 的 Stage2 报告逐项复核，不能将旧 seed 的注入成功外推到新输入。

## 正式 12 场景

| seed | 速度 | SNR | delay | period | scene id |
|---:|---:|---:|---:|---:|---|
| 606 | 6.7 m/s | 30 dB | 0 ns | 3 | normal / high |
| 606 | 6.7 m/s | 20 dB | 0 ns | 3 | normal / low |
| 606 | 0.5 m/s | 30 dB | 0 ns | 3 | low-speed / high |
| 606 | 0.5 m/s | 20 dB | 0 ns | 3 | low-speed / low |
| 707 | 6.7 m/s | 30 dB | 0 ns | 3 | normal / high |
| 707 | 6.7 m/s | 20 dB | 0 ns | 3 | normal / low |
| 707 | 0.5 m/s | 30 dB | 0 ns | 3 | low-speed / high |
| 707 | 0.5 m/s | 20 dB | 0 ns | 3 | low-speed / low |
| 808 | 6.7 m/s | 30 dB | 0 ns | 3 | normal / high |
| 808 | 6.7 m/s | 20 dB | 0 ns | 3 | normal / low |
| 808 | 0.5 m/s | 30 dB | 0 ns | 3 | low-speed / high |
| 808 | 0.5 m/s | 20 dB | 0 ns | 3 | low-speed / low |

正式矩阵按 seed、速度、SNR 确定排序运行。不得因某个方法结果不利或场景失败而换 seed、速度或 SNR。Stage2 无效或 truth/CFAR intersection 不足的场景保留在执行清单中并报告为失败/不可评估。

## 方法/角色计算量

- 核心 Mode-B：`12 scenes × (C0,C1,C2,C3) × (OFF,ON,TO) = 144` branch slots。
- paired-OFF Mode-A：`12 × (C2,C3) × (OFF,ON,TO) = 72` additional branch slots；OFF 为估计 reference。
- 独立 secondary：参考 seed 909，应用于 seed606/6.7m/s/30dB 的 C2/C3 三角色，单独执行和统计，不进入 144/72 主矩阵。
- 执行门 pilot：seed101/6.7m/s/30dB，仅用于质量门和实际处理链检查，不计入正式 12 场景。

## 真值机会与错误指标

- 场景级目标机会按真值 period 计。正式主矩阵最多 36 个 ON 与 36 个 TO target-period truth opportunities；报告每个 SNR/速度/seed 分层的 `n`、正确命中、漏检、额外 detections，并保留各方法自己的成功机会和严格配对共同成功机会。
- 位置、角度、folded Doppler、ground speed 与 radial velocity 按现有单位和 evaluator 语义分别统计。radial ambiguity 未解除时保持 `NOT_EVALUABLE`。每项同时提供 own-success 与 common-success 样本数。
- OFF/H0 报告 valid CUT、unique final-union hits、非目标 cluster/Detection/confirmed-track rows；没有适用 truth-filtered false-cell estimation 时 `Pfa=NOT_EVALUABLE`。
- 输出 SCNR、杂波残余和目标复幅相保持是机制指标，不能单独替代最终检测、参数估计与航迹指标。

## 运行顺序与冻结

1. seed101 技术门 pilot 与 GO-CFAR 独立数值 gate。
2. 完成输入信息隔离、secondary 路径可行性、当前 Stage2 物理注入与 build identity gate。
3. 顺序运行 12 个正式 case；每个 case 完成关键 compact evidence 后才清理本次该 case 的派生原始数组，并在 cleanup ledger 记录实际删除路径和字节数。保留首次正式 case 原始输入至 independent-secondary 检查完成。
4. 运行预注册的 independent-secondary 条件（只在同质性和 group/period support gate 满足时报告 Evaluable）。
5. 生成方法内配对和跨方法共同机会分析，不以周期或 CUT 冒充独立 seed。

冻结版本、配置 SHA、执行源码 commit 和所有偏离见 `evidence/` manifest；本文件不在看过正式性能结果后回改条件。若出现代码错误，只修根因并重新运行受影响的预注册场景，保留失败现场与重跑链。
