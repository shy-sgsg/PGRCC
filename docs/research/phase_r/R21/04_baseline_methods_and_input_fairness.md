# R21：方法定义与输入公平性

## 条件和信息边界

| 方法 | 输入与算子 | 估计信息边界 | 角色 |
|---|---|---|---|
| A0 | 0 ns 无误差参考 | 不使用校正估计 | 理想对照 |
| B | 同一场景注入 `+4 ns`，Current 不校正 | 不读 truth / 配对 A0 / Known | 失配基线 |
| Known | 对 B 的协议 IQ 按真实 `+4 ns` 做同一 float32 delay rewrite | truth 仅 evaluator correction adapter 使用；不进入估计器 | 已知物理校正上限参考 |
| R9 fixed D3 | 对 B 应用固定 `3.4099650318669545 ns` | 固定历史校准量；不是本次 blind estimate | 简单传统校准基线 |
| Huber D3 | 从同 seed 的 B/ON 原始 C1/C2 估计，再校正 B 的 ON/OFF/TO | 不读 truth、A0/Known、配对 OFF 或目标检测 | 同输入盲估计基线 |
| M1 | 从同 seed 的 B/ON C1/C2 与 B Production 几何估计，再校正 B 的 ON/OFF/TO | 不读注入真值、A0/Known IQ、truth 或 detection | 物理匹配盲估计候选 |

六种条件共用同一 Native CUDA Production binary、协议布局、Detection/TrackManager 和冻结 CFAR 门。六种条件 × ON/OFF/TO 每 seed 18 个分支；两 seed 共 36 个分支。所有校正方法进入同一个 `rewrite_float32_protocol_delay` 校正算子。完整 contract 见 [`r21_baseline_information_contract.csv`](evidence/results/r21_baseline_information_contract.csv) 与 [`r21_input_identity.csv`](evidence/results/r21_input_identity.csv)。

## 公平性结论

- A0 与 B 的差异是注入时延；Known 与盲估计均从 B 的对应输入构造校正结果。
- Huber 与 M1 使用同一 seed 的 B/ON C1/C2 信息。Huber 按原有相位斜率稳健拟合；M1 另用 B Production 几何。二者均不访问配对 A0、Known、truth 或 Detection。
- R9 fixed D3 只使用固定校准量，不能称为同输入估计器；报告中单独列示。
- Known 不能被称为盲方法，也不是普通基线；它是 evaluator-only 已知误差校正参考。
- 所有 ON/OFF/TO 都使用同一 seed 的面积杂波背景映射，scale 为 1。Ground clutter 对比有 packet/sample 与实测功率证据，不能只依赖 `area_enabled`。

## 限制

该公平性只覆盖一个固定 `+4 ns` 状态、一个目标速度/SNR 和两个 seed。它不验证其他时延、时变误差、幅相误差、多参数联合估计或跨场景可推广性。
