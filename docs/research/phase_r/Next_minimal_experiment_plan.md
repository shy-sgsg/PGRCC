# 唯一下一最小实验方案：R3 seed-101 A0/C 阶段定位

状态：**仅建议，未执行，也不是 CUDA 运行授权。** 目标只回答现存证据无法回答的一个问题：A0 与 Known Correction C 的逐格输出最早从哪一个 Production stage 开始分离。不是重跑 baseline 矩阵，不加 seed，不研究 AI。

## 实验范围

- 条件：A0 `0 ns Production Current` 对 C `+4 ns evaluator-only Known Physical Correction`；只用原 seed 101、原 target/几何/SNR 与原 Stage2 generator 配置。
- 输入/角色：优先 byte-identical 重建原 seed101 的 ON/H1、OFF/H0、TO/S-only triplets；若保留 manifest 的 Stage2 文件 SHA-256 对不上，立即停止，不把新随机数据称为原配对复现。
- Production：两条件各自三角色、三 period，共六条配对运行；保持原 XML 除运行目录外一致。不得改 CFAR、target、beam gate 或算法阈值。
- 输出：实际 runtime XML、run/binary/source/config identity、Stage2 report、逐 beam stage timing；配对 F1/F2 输入、CSI complex 输出及 detector input 的逐格 power/complex map；两 CFAR branch 的有效掩码、threshold map、完整 hit indices；cluster/detection/TrackManager truth join。

## 预注册的定位顺序与报告量

逐一比较：`Stage2 F1/F2 → fusion/phase-corrected F1/F2 → CSI input → CSI output → dynamic-mixed/full-CFAR input and threshold → hit-cell masks → clusters/detections → TrackManager outputs`。每级报告形状、有限值数、相对复 RMS、绝对/相对功率差、超过阈值的 cell 数和 provenance；首次出现非零差异的 stage 才是“首个观察差异级”。沿用已冻结的 F1/F2 `1e−3` 复 RMS 闭合门；其他级先报告数值与误差来源，不临时发明容差或以命中率代替 map closure。

## 停止门

1. 原 Stage2 输入 SHA 不匹配：停止，不运行 Production、不作 paired causal claim。
2. `nvidia-smi`/Release executable identity/runtime XML 缺失或两条件处理链不一致：停止并标 `NOT_EVALUABLE`。
3. 面积杂波、H0/H1/TO 输入配对、valid CUT 或 target truth gate 失败：停止算法比较。
4. A0↔C 的 F1/F2 closure 失败：先定位误差注入/物理逆模型，不解释 CFAR/TrackManager。
5. CSI map 近闭合但 hit indices 分离：报告 CUT threshold-margin 分布与跨 branch unique union；不把单 seed hit 差异说成 Pfa 或性能显著性。

运行后只保留能复核首次差异的 stage 摘要、每期 hit indices/union、必要 map 片段、manifest、XML、日志及失败案例；已核实可重建且不再支撑判定的全图临时文件按 cleanup ledger 删除。此试验完成后停止并重新评估是否需要其他单独实验；本方案没有批准 D3 复验、R4 seed 扩展或 AI 训练。
