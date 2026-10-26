# R21 方法公平性与冻结设计

## 目标与范围

R21 复核固定 `+4 ns` 通道时延对 GMTI 任务的影响，并比较已知校正上限、跨运行固定估计值和同 seed 盲估计。设计服务于研究总纲中“真实系统未知误差 → 可观测估计 → 物理修正 → 杂波抑制 → GMTI 任务评价”的长期问题；不训练 AI、不启动 Router、不扩展误差维度，也不运行完整传统 Baseline 矩阵。

配置冻结在 [`03_frozen_design.json`](03_frozen_design.json)。Stage1 基础配置以 SHA-256 `ad47b0d02b05ddc4839da39a7498faf1af25ca3ba8eec678597c0ce7a0f651f6` 绑定，Stage2 场景模板以 SHA-256 `1f4f9c8b9383614601dc019414b8c8a8788b195301526438b95a672b1ee13998` 绑定。每次实际 run manifest 还记录解析后的 Scene identity、实际 runtime XML、输入文件和 Production binary 身份。

## 新 seed 资格

候选 seed 为 1001、1002。已对当前 `docs/configs/scripts/tests/outputs` 中的文件名和文本记录搜索 seed 专用路径、`seed_1001/seed1001`、`--seed 1001` 等明确表示，并对本地 `git --all` 历史执行对应 `-G` 搜索；当前树的两个命中均来自 R21 自身计划，历史提交与路径无命中。该审计支持将二者作为未使用候选，不证明未连接的仓库副本或未保存的外部记录也没有使用。

一个 seed 先执行 Stage2 与 A0/B/Known 基础 Production gate。该基础运行同时作为首个 CUDA smoke；只有杂波注入、Known 物理闭合、runtime 配置可比和 Production evidence gate 全部通过，才在同一冻结输入上运行校正分支。首 seed 全链路通过后才启动第二 seed。两个 seed 是独立实验单位；同 seed 的 ON/OFF/TO 和三个周期不得充当额外独立样本。

## 条件与处理链

| 条件 | 输入/校正 | 信息边界 | 角色 |
|---|---|---|---|
| A0 | 0 ns ideal reference | 无误差输入 | ON、OFF、TO |
| B | +4 ns 当前通道误差 | 未校正 | ON、OFF、TO |
| Known | B 的 +4 ns 物理校正 | 真实参数仅用于 evaluator 所在的 correction adapter；不得提供给盲估计器 | ON、OFF、TO |
| R9 fixed D3 | B 原始输入，固定 `3.4099650318669545 ns` | 跨运行固定校准，不称为本次盲估计 | ON、OFF、TO |
| Huber D3 | B ON 原始 C1/C2 的 Huber 加权相位斜率估计 | 不能读真值、A0/Known IQ、目标真值、Detection 或其他 oracle；不可用时写 `NOT_AVAILABLE` | ON、OFF、TO |
| M1 | B ON 原始 C1/C2 与同一 B Production XML 报告几何估计 | 不读注入误差、A0/Known IQ、目标真值或 Detection | ON、OFF、TO |

每个可用方法都对其同 seed B 的 ON/OFF/TO 输入使用同一个 `estimate_channel_delay.rewrite_float32_protocol_delay` 校正适配器，然后进入同一 Native CUDA `run_production_branch`。各分支的 runtime XML 经去除结果目录字段后必须完全一致。Known 校正须对 A0 融合输入通过物理闭合门；D3/M1 的剩余闭合误差如实报告，不使用 Known 的严格闭合门替代或掩盖其误差。

## Huber D3 资格与限制

R12 保留的 `source_manifests/run_manifest.json` 和 `R12_blind_validation.md` 将 `3.5880556759918814 ns` 标为与 M1 同一 B ON 盲输入上的 `D3_Huber_weighted_LS`，记载 390 pulses、2364 个支撑频点、B ON raw 文件 SHA 和 B Production XML SHA；输入角色是 `A1_Current_unknown_error_ON_raw_C1_C2_only`，`truth_used_in_estimator=false`。当前 R21 仍保留在工作区的紧凑包不含这些 raw IQ，因此该历史数值不能重新计算，也不单独证明此方法已跑过校正后的 Production。

实现中，R21 先从新 seed 的 B ON 原始 C1/C2 与 B 报告几何调用相同物理观测估计入口，并单独记录 Huber baseline 的支撑和估值；只有估计器明确报告 B ON 输入、未读真值且结果有限时，才将 Huber 加入本 seed。其结果随后用与其他校正方法相同的 delay adapter 和 Production 分支执行；任何前置条件失败都保留为 `NOT_AVAILABLE` 并跳过该方法，不伪装成盲估计结果。

R9 fixed D3、Huber D3 和 M1 是三种不同信息边界，分别记录。固定 R9 值不能被标记为同 seed blind；M1 输出中的 D3 baseline 也只作为独立拟合值使用，不让 M1 delay 或其任务结果影响 Huber 校正值。

## 评价和停止门

首先验证每个 A0/B Scene 的连续面积地杂波报告：`continuous_area_packets > 0`、`continuous_area_samples > 0`、面积功率为正；各 A0/B 角色的 packet/sample 数必须一致。再检查 A0/Known 融合输入物理闭合、各方法 Production operator 身份、CSI tap、CFAR geometry 和每个 truth opportunity 的距离/Doppler 物理映射。有效机会为零时不报告零命中率。

对每 seed 分别报告 B 相对 A0 的差值、Known−B 可恢复空间、每个估计方法相对 B 的实际恢复量、幅相保持、CSI 杂波残余、SCNR、命中/漏检、H0 描述计数、TrackManager lineage 和参数误差。两 seed pilot 只用于可行性与方向复核，不做显著性 Pd/Pfa 主张；统计 Pfa 保持不可评估。

若 Area/CFAR/Production/真值映射门失败，停止算法比较并留失败现场。若 A0/Known 物理闭合失败，停止盲方法比较并先查 correction adapter。若已知校正相对 B 没有跨 seed 可重复任务改善，暂停固定时延估计主线；若 Known 有改善而盲法明显落后，只将其作为传统盲估计的性能限制证据，不据此自动转向 AI。

## 当前资格状态

- seed1001/1002：仓库内历史审计无既往使用记录；正式使用待 CUDA 前配置和数据门确认。
- R9 fixed D3：`IMPLEMENTED`，固定参考值来自 R9 既有校准。
- Huber D3：`IMPLEMENTED`，新 seed 盲估计和 Production 执行尚未 `EXECUTED`。
- M1：`IMPLEMENTED`，新 seed 盲估计和 Production 执行尚未 `EXECUTED`。
- 当前 Native CUDA Production：`NOT_EXECUTED`；本文件不构成实验结果。
