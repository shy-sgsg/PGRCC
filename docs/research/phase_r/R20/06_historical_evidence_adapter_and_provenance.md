# R8/R12 历史证据适配与来源

## 适配边界

[r20_historical_evidence_adapter.py](../../../../scripts/phase_r/r20_historical_evidence_adapter.py) 以只读模式适配三组历史数据；不改写历史 CSV。R8 的方法子集为 A0 / Current unknown +4 ns / Known correction upper bound，明确排除与本任务不符的其它方法及重试目录；R12 保留 A0/B/Known/D3/M1 五个条件。case、condition、ON/OFF/TO 角色、truth、Detection、轴、runtime/config、TrackManager 和 correction manifest 都按来源路径写入分支 identity manifest。

XML semantic hash 只归一化 `result_add`、`track_debug_dir` 两个输出路径。输入 IQ hash 被保留为声明值并允许随干预变化；background/target/seed 和 Production identity 等共享信息必须由其它 identity 字段核验。

机器清单中的本仓库路径写为 repository-relative；外部路径会被缩减为 `<external>/<basename>`，避免把工作站用户目录写进公开证据。

## 来源与可重算边界

| 证据 | 当前可读 | 本次重算 | 限制 |
|---|---|---|---|
| R8 seed101/202/303 ON/TO Detection 与 target truth | 是 | final Detection task、符合映射门的 opportunity、TrackManager、参数字段 | seed101 缺 Production Doppler 轴，R20 不评任务；三 seed 的原始 IQ 路径均缺失。|
| R8 Known F1/F2 closure | manifest 数值可读 | 不重算 complex closure | 原始 IQ/complex array 未保留；R20 输出明确标成 retained manifest result。|
| R12 seed404 A0/B/Known/D3/M1 Detection 与 truth | 是 | final Detection task、轴映射、TrackManager、参数和 common-success | 原始 IQ 路径缺失，不能 replay/重算 CSI。|
| R12 delay estimator | case/run manifest 可读 | 重新汇总真值误差字段与 detection result | evaluator 只检查真值使用标志和已保存估计；不重跑估计器。|
| R12 Known correction closure | manifest 数值可读 | 不重算 complex closure | 同样无 raw IQ。|

未提交或已不可用的原始 IQ 不通过把 path/hash 列入新清单伪装成可复现输入。历史 Detection/track/truth 表足够支持本次离线表级重评，但不支持重新生成产品。

## 运行身份

R8 frozen Production binary SHA=`de3026ed4f1263be4143dcbcd10ccbb5e53b4be357e21906e1bf19ea0ec29d00`，来自 R8 `r8_experiment_manifest.json`。R12 SHA 同值，来自 R12 `source_manifests/run_manifest.json`。R8 experiment base commit=`c5200b2f280f01f5e2e46f3a6fe5983a936a633f`；R12 baseline commit=`3e23235180f59fbb09b890af000544ae7a7c9fd0`。这些字段不自动等同于 compiler source commit；字段来源逐项保留在 [input identity manifest](evidence/manifests/r20_input_identity_manifest.json)。

Known F2 closure retained values：R8 seed202 ON relative RMS `8.68168e-5`、seed303 ON `9.12385e-5`；R12 seed404 ON `8.73383e-5`。这些数来自历史 manifests，不是 R20 本次处理。
