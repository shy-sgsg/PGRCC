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

R20 将所需的 R8 seed101/202/303 与 R12 seed404 保存记录打包到 Git 路径 `evidence/inputs/`：bundle manifest 列出 566 个文件（当前约 30 MB），逐文件保存 size 与 SHA-256；另记录 42 个与 `GMTI03` byte-identical 的 aggregate Detection 重复文件及对应 SHA，适配器直接读取逐期文件。raw protocol IQ、中间数组、maps、binary 与可重建 Stage2 产物均未纳入。bundle 完整性由 `test_compact_input_bundle_matches_its_file_hash_manifest` 自动核验。离线 runner 从这个 Git 输入包即可重新生成全部统一表，不再依赖 ignored `outputs/` 目录。

## 运行身份

| 数据 | seed | case/run runner 与 build-audit base commit | Production binary SHA-256 | 配置身份 | build source commit |
|---|---:|---|---|---|---|
| R8 | 101（未纳入 target 评价） | case manifest `74877fe1463b9362dfce7c935a136d82a26ad26b`；build-audit base `c5200b2f280f01f5e2e46f3a6fe5983a936a633f` | `de3026ed4f1263be4143dcbcd10ccbb5e53b4be357e21906e1bf19ea0ec29d00` | branch semantic/XML SHA 逐项记录；缺 Production axis | manifest 未提供 |
| R8 | 202 / 303（可评价） | case manifest 与 build-audit base 均为 `c5200b2f280f01f5e2e46f3a6fe5983a936a633f` | 同上 | 各 case 内 A0/B/Known semantic config 一致；跨 seed 分开记录 | manifest 未提供 |
| R12 | 404 | case manifest 与 build-audit base 均为 `3e23235180f59fbb09b890af000544ae7a7c9fd0` | 同上，来源 `source_manifests/run_manifest.json` | 同 case 五条件 semantic config `399818bf3b996d6cb276c57de7a1ce4eab7a0fdcec1cb02d7b5777bc8e5ee2cd` | manifest 未提供 |

R8 seeds202/303、R12 seed404 的 experiment runner commit 与 build-audit base commit 是已记录的来源字段；这些字段不自动等同于实际 compiler source commit。R8 seed101 的来源身份与可评价 seeds 不同，且缺 Production axis，故保留记录但排除任务效应汇总。字段逐 branch 保存在 [input identity manifest](evidence/manifests/r20_input_identity_manifest.json)。

Known F2 closure retained values：R8 seed202 ON relative RMS `8.68168e-5`、seed303 ON `9.12385e-5`；R12 seed404 ON `8.73383e-5`。这些数来自历史 manifests，不是 R20 本次处理。
