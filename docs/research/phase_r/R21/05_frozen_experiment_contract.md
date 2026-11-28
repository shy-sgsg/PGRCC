# R21：冻结实验 contract

本文件是最终 contract 导航；运行前冻结的详细设计保留在 [`05_experiment_design.md`](05_experiment_design.md) 和 [`03_frozen_design.json`](03_frozen_design.json)。本阶段没有事后修改指标、门限或场景来改善结果。

## 冻结条件

- Seeds：1001、1002，各 3 个目标周期。
- 平台：高度 6000 m；目标地速 6.7 m/s、SNR 30 dB、beam 50、名义斜距约 85 km。
- 失配：B 注入固定 `+4 ns` 通道时延。
- 面积杂波：物理可达地面距离窗 84.5–85.5 km；纹理 sigma 0.25，时间相关 rho 0.98。
- 方法：A0、B、Known、R9 fixed D3、同输入 Huber D3、M1。
- 配对角色：ON、OFF、TO；每 seed 18 个 Production 分支，共 36 个。
- 停止范围：不加 seed、不做参数 sweep、不修改 Production 算法、不训练 AI、不启动 R22。

## 配置与代码身份

- Stage1 config SHA-256：`ad47b0d02b05ddc4839da39a7498faf1af25ca3ba8eec678597c0ce7a0f651f6`
- Stage2 template SHA-256：`1f4f9c8b9383614601dc019414b8c8a8788b195301526438b95a672b1ee13998`
- Production binary SHA-256：`9ffc232966bd92a39edfb39b90730851014dfa1ae7237a4ac172a764f31cc566`
- 分支身份、runtime XML、原始输入身份与日志摘要：[`r21_input_identity.csv`](evidence/results/r21_input_identity.csv)
- 两 seed 运行状态与 clutter manifest：[`r21_experiment_manifest.json`](evidence/results/r21_experiment_manifest.json)

## 冻结门

1. Stage2 实际杂波注入、物理可达几何和跨角色背景映射必须有效。
2. Known 相对 A0 的 F1/F2 输入闭合误差小于 `1e-3`。
3. 每个 Production 分支的 XML、日志、CSI tap 与 CFAR geometry 可追溯，valid CUT 大于 0。
4. Detection 分母只纳入逐机会 range/Doppler 物理映射有效的 truth opportunity。
5. H0 observed fraction 只能作描述；统计 Pfa 缺少独立 H0 抽样单位时必须标 `NOT_EVALUABLE`。
6. 两 seed 的结果只作可行性证据，不作统计显著性或总体 Pd/Pfa 声明。

六个正式门在已保存的实际运行记录中均有明确值；Known 闭合与有效 CUT 通过。统计 Pfa 与径向速度精度仍不可评价。
