# R20 证据盘点与身份审计

## 范围与结果标签

本阶段仅读取 R18/R8/R12 已保存的 Stage 2、Production Detection、TrackManager、truth、配置和 manifest 证据，并执行离线评价。没有运行 CUDA、重放 Production、生成 IQ 或修改实验配置。

| 数据集 | 纳入场景 | 读取的 ON/TO/OFF 分支 | 本次可评价边界 |
|---|---:|---:|---|
| R18 | 12 | 168 个 ON/TO 条件分支 | 84 个 ON 条件用于 R19 v1/v2 回归；84 个 TO 分支用于正控制/机会审计。R18 全矩阵原始记录是 252 branches。|
| R8 | 3 seeds（101/202/303） | 27 | 每 seed 的 A0/B/Known × ON/TO/OFF。seed101 缺保存的 Production Doppler 轴，目标任务三条件均不可评价；202/303 可评价。|
| R12 | seed404 | 15 | A0/B/Known/D3/M1 × ON/TO/OFF；任务指标仅为一个 seed、每条件三个目标机会。|

R20 适配器发现 210 个分支记录，其中 196 个 ON/TO 分支进入 truth/target 评价，14 个 OFF 分支只提供描述性 H0 记录。所有 16 个 case identity group 的状态为 `IDENTITY_VALID`。该状态表示保存的 identity 字段符合声明的比较 schema；它不表示重新运行或重新验证原始 IQ。

R18 输入快照来自 `docs/research/phase_r/R18/evidence/formal_matrix/`；R8/R12 源记录来自历史本机 `outputs/` 及各自的 `docs/research/phase_r/evidence/`。本次将适配器所需的 566 个小型 R8/R12 文件复制为 Git 紧凑包 `evidence/inputs/`，并逐项记录源路径、size 和 SHA；raw protocol IQ 没有打包。原始 IQ 路径仍在历史 manifest 声明，但当前不可用；其 SHA 只能作为历史声明保留，不能声称本次重算验证。

## 身份核对

- R8 与 R12 的已记录 Production binary SHA 均为 `de3026ed4f1263be4143dcbcd10ccbb5e53b4be357e21906e1bf19ea0ec29d00`。R8 SHA 来源于其 `frozen_build.production_binary`；R12 SHA 来源于 R12 `source_manifests/run_manifest.json`。两者都未从本次设备重新构建或重哈希。
- R8 实验基线 commit 声明为 `c5200b2f280f01f5e2e46f3a6fe5983a936a633f`；R12 baseline commit 为 `3e23235180f59fbb09b890af000544ae7a7c9fd0`。逐 branch 的 case/run manifest commit、snapshot runner commit、Production binary SHA 和配置 SHA 分字段保留。没有清晰来源时不推定 binary 的 build-source commit。
- R18 snapshot 中的 Production binary SHA 为 `3328d7c61bc36b6d3b906692e355c46c328150c0399fa457e6f871bcb79abaf0`；R18 的条件配置身份按其角色与方法范围作为变化字段记录，不跨方法误判为相同配置。
- R8/R12 Production XML 的语义摘要仅规范化输出路径字段 `result_add` 与 `track_debug_dir`；其他字段都参与摘要。两套数据各自的条件间语义配置摘要一致，且 background/target/seed/condition、truth SHA、Production binary 和分支运行身份逐项保存在 [R20 输入身份 manifest](evidence/manifests/r20_input_identity_manifest.json)。干预后 IQ SHA 可以不同，作为角色变量记录，不要求相等。
- R8/R12 的输入 IQ 原始文件均不可用；关键目标 truth、逐期 Detection、TrackManager、runtime/config、校正 manifest、保存 Doppler axis 和 CFAR geometry diagnostics 在紧凑包中可读。Known 校正 F2 闭合仅引用历史 manifest 结果，状态标记为 `RETAINED_MANIFEST_RESULT_RAW_IQ_RECOMPUTE_NOT_AVAILABLE`。H0/ON/TO 的 CUT counters 和 hit fractions 可做描述性记录，但统计 Pfa 不可评价。
- 提交的机器 manifest 将仓库内证据路径规范为 repository-relative；站外路径只保留 `<external>/<basename>` 标识，不写入本机 home/worktree 绝对路径。

## 磁盘与保留

本机工作树检查为约 2.1 GB，小于 20 GB 上限。本阶段没有删除历史数据。新增 R20 证据仅保存小型 CSV/JSON、脚本与 Markdown；不复制 IQ、完整 CFAR map 或大数组。

## 可复核入口

- R20 机器可读输入身份与分支清单：[r20_input_identity_manifest.json](evidence/manifests/r20_input_identity_manifest.json)
- R20 逐机会与汇总表：[results](evidence/results/)
- 适配器：[r20_historical_evidence_adapter.py](../../../../scripts/phase_r/r20_historical_evidence_adapter.py)
- 重评入口：[r20_harmonized_task_review.py](../../../../scripts/phase_r/r20_harmonized_task_review.py)

状态：`IMPLEMENTED`=身份适配器和 manifest；`EXECUTED`=以上已保存数据离线读取与评价；`EVALUABLE`=逐字段、逐输入可评价状态详见报告/CSV；`PERFORMANCE_VERIFIED`=未验证总体统计性能。
