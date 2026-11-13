# R21 最小 CUDA 因果 pilot：冻结实验设计

## 科研定位

R21 服务于研究总纲中的 airborne GMTI 长期问题：真实系统失配是否造成可归因的任务损失，测得的物理误差能否用确定性方法估计并校正。本阶段只验证一种固定通道时延，不训练 AI、不开发 Router、不扩展误差参数、不启动新阶段。

本阶段的具体问题是：在 `+4 ns` 通道时延、`6.7 m/s` 目标和连续面积地杂波下，Production GMTI 的目标命中是否低于无误差参考；Known Physical Correction 能否恢复；R9 fixed D3、Huber D3、M1 是否能在不读取真值的情况下达到相近任务表现。

## 冻结条件

冻结配置见 [`03_frozen_design.json`](03_frozen_design.json)。Stage1 配置 SHA-256 为 `ad47b0d02b05ddc4839da39a7498faf1af25ca3ba8eec678597c0ce7a0f651f6`；Stage2 场景模板 SHA-256 为 `1f4f9c8b9383614601dc019414b8c8a8788b195301526438b95a672b1ee13998`。

| 项目 | 冻结值 |
|---|---|
| 独立 seed | `1001`、`1002`，每个 seed 3 个目标周期 |
| 目标 | `6.7 m/s`、`30 dB`、beam 50、85 km 名义地面距离 |
| 注入误差 | B 条件 `+4 ns` 固定通道时延 |
| 面积杂波 | 平台高度 6000 m；场景距离窗 84.5–85.5 km；纹理 `sigma=0.25`；时间相关 `rho=0.98` |
| 配对角色 | `ON`、`OFF`、`TO`；每种条件都进入相同 Native CUDA Production runner |
| 方法数 | A0、B、Known、R9 fixed D3、Huber D3、M1；每 seed 18 个 method×role Production 分支 |
| 停止边界 | 不增加 seed、不做扫描、不改 Production 算法、不启动 AI 或 R22 |

## 条件与信息边界

| 条件 | 处理 | 估计器可见信息 |
|---|---|---|
| A0 | 0 ns ideal reference | 无误差参考输入 |
| B | 注入 `+4 ns`，Current 未校正 | 当前误差输入 |
| Known | 按真实 `+4 ns` 做物理校正 | 真值只供 evaluator correction adapter；不得传给盲估计器 |
| R9 fixed D3 | 使用固定 `3.4099650318669545 ns` 校正 | 跨运行固定值，不标作本次盲估计 |
| Huber D3 | 从该 seed 的 B/ON 原始 C1/C2 估计，再同算子校正 B 的 ON/OFF/TO | 不读真值、A0/Known IQ、目标 truth、Detection 或配对无误差参考 |
| M1 | 从该 seed 的 B/ON 原始 C1/C2 与 B Production 几何估计，再同算子校正 | 不读注入真值、A0/Known IQ、目标 truth 或 Detection |

所有校正候选共用 `estimate_channel_delay.rewrite_float32_protocol_delay` 和同一 Production 分支执行入口。B 的原始观测为估计器输入。每个 blind estimator 的输出、输入角色、校正值和真值使用标记保存在 run manifest 中。

## 预设门控

1. 地杂波必须由 Stage2 报告确认实际注入：连续面积 packet/sample 数为正、实测功率为正、几何可达；其余角色必须映射到同一个背景且 scale 为 1。只在 A0_ON 生成 packet、其他角色生成计数为 0 属于背景复用契约，不能单独据此判无杂波。
2. Known 校正的 F1/F2 输入须相对 A0 通过 `1e-3` relative-RMS 物理闭合门。
3. Production runtime XML、输入身份、逐 beam/stage 日志、CSI tap 与 CFAR geometry 均须保存；CFAR valid CUT 必须大于 0。
4. 真值距离/Doppler 轴映射逐机会通过后才进入任务命中分母；没有有效机会不能报告零命中率。
5. H0 命中比例只作描述值；没有独立虚警统计单元时，Pfa 设为 `STATISTICAL_PFA_NOT_EVALUABLE`。
6. 两个独立 seed 只作受限可行性与方向复核；三个周期及不同方法的配对周期不算独立 seed，不作统计显著性结论。

## 执行入口

seed1001 的完整 Native CUDA 分支使用 runner 源码 commit `6f261b218691cf99f2e1d90c2ed5cda70a96a4f4`；seed1002 使用 `0957f49c028377cbd7a15c2ff0b2eda3f75ce9a9`。Production binary SHA-256 两次均为 `9ffc232966bd92a39edfb39b90730851014dfa1ae7237a4ac172a764f31cc566`。

```bash
PGRCC_STAGE2_SIMULATOR=/tmp/pgrcc-r21-native-cuda-build/simulate_stage2_statistical \
python3 scripts/phase_r/run_r21_cuda_pilot.py \
  --output-root docs/research/phase_r/R21/evidence/cuda_pilot/seed1002_0957f49 \
  --seed 1002 \
  --production-binary /tmp/pgrcc-r21-native-cuda-build/GMTI_pipe_core
```

seed1001 用相同入口和二进制，将 `--seed` 改为 `1001`，输出目录使用 `seed1001_gatefixed_6f261b2`。此命令只记录复现实验入口；原始数据按完整 manifest 中的输入 SHA 和配置身份核验。

## 阶段验收状态

设计在运行前冻结并按上述范围执行。实际门控、运行身份、结果和失败记录见 [`06_minimal_cuda_causal_pilot.md`](06_minimal_cuda_causal_pilot.md)。阶段方向建议见 [`07_preliminary_direction_assessment.md`](07_preliminary_direction_assessment.md)。
