# R22 延续执行契约（2026-10-28）

## 授权与范围

用户明确要求继续执行 R22 的扩展实验。本文件与 `config/r22_extension_experiment_design.json` 记录本次新执行范围；2026-10-27 的停止记录仍作为历史证据保留。研究总纲 `docs/research/RESEARCH_MASTER_PLAN.md` 不作修改；不训练 AI、不开发 Router、不把本轮实验扩展为新研究主线。

本阶段检验多目标、连续扫描和独立 seed 下的传统方法能力边界，完成历史 Baseline 可比性审计、Native CUDA Production 验证、任务级统计和研究方向判别。正式执行必须使用干净、已提交的源码版本及固定 Production/Stage2 binary。

## 修复后工程门

1. Stage2 仅在 `allow_target_free_h0=true` 且目标列表为空时接受 H0；无开关的空目标输入及带目标的 H0 输入均拒绝。
2. H0 false-alarm 行使用该 seed 自身的有效 CUT 分母，并保留 method、mode、condition 身份。配对 OFF 仍标为诊断，不混作独立 H0 seed。
3. PKR 保持 `min_support=8` 和原 scan beam-quality gate。`range_band_bins=133` 是待六个独立工程 seed 检验的候选支持分组配置；不是降低门槛，也不预先宣称最优或具备普适性。
4. CSI 标量 summary、实际运行 XML、runtime config dump、CFAR geometry、Production 日志、Stage2 报告和 evaluator CSV 必须保留。只在汇总和审计文件写入成功后，清理可重建 `.bin` 输入及 CSI `.npy` 全尺寸诊断图，并将路径、大小和理由写入 `raw_cleanup.json`。
5. 面积地杂波必须由 Stage2 的连续 packet/sample、功率和 F1/F2 输入证据确认；`area_enabled=true` 本身不算有效注入。

### 0 ns 无误差参考条件的校正适用性补充

首个扩展工程 case 显示：在真实无误差输入上，带噪盲估计可给出非零时延；将该估计直接送入只对实际存在、零扩展的分数时延输入成立的逆算子，会因首样本边界不符合算子前提而正确 fail-closed。此处不能用放宽边界、强制估计归零或更换输入来制造通过。

后续冻结执行设计将 `delay_error_ns == 0` 明确作为无误差参考角色：

- P1/P2 仍可在 target-free OFF 输入上运行并保存诊断估计，但只记 `NOT_APPLICABLE_ZERO_DELAY_REFERENCE`，不得应用物理校正。
- R9 固定 D3、Huber D3 和 M1 在 0 ns 条件下也只保留估计/诊断记录，不生成物理校正分支。A0/Current/PK 等适用分支仍按原定义执行。
- 非零误差（当前矩阵为 +4 ns）下，原盲估计和校正条件、Known upper-bound、真值隔离及全部 Production 质量门保持不变。
- 0 ns estimate-only 记录若不可得，应明确标记 `NOT_AVAILABLE`；这不改变其他可执行方法的门槛。任何必需生产分支失败仍使对应 case / engineering gate 失败。
- 原始失败目录保留；新设计使用新 ID、配置 digest 和证据根目录。seed 集合、目标数、周期数、方法集合、PKR `min_support=8`、CFAR/beam gate 及统计口径不变。

本补充只定义零误差参考下“物理校正是否适用”，不将一次 estimate-only 或软件测试作为 Production 证据。该补充之后仍须先重跑两个配对工程条件并通过真实 CUDA Production gate，才可继续其余工程 seed。

单 seed 工程探测已显示 width=133 的一个 H0 输入可形成 1,209/1,209 个有效 PKR reference groups，支持数范围为 8–87；对应 CSI tap 和 CFAR geometry 可在关闭完整 map dump 时保留。该结果只验证一个输入上的工程可行性。H0 上目标专属 CSI 残余、检测和任务收益均不可由该运行评价。

## 冻结矩阵

机器可读配置为 `config/r22_extension_experiment_design.json`，R22 矩阵入口为 `scripts/phase_r/r22_experiment_matrix.py`。配置冻结了：

- 工程：6 个独立 seed；2.5 m/s、20 dB，配对 0/+4 ns；20 个目标、10 个连续周期。
- 筛选：4 个径向速度 × 3 个 SNR × 2 个时延 × 12 个 seed，共 288 个场景。
- 正式确认：3 个径向速度 × 20 dB × 2 个时延 × 30 个全新 seed，共 180 个场景。
- 40 目标压力：4 个条件 × 12 个筛选 seed，共 48 个场景；与筛选的 20 目标场景作嵌套目标密度比较。
- 3 目标桥接：6 个工程 seed，在同一 2.5 m/s、20 dB、+4 ns 条件下与 20 目标 seed 配对。
- H0：30 个独立 seed 分别执行 0 ns 和 +4 ns，共 60 个 target-free 场景；CUT 不作为独立 seed。
- 方法：C0–C4、P1/P2、PK/PKR；runner 同时生成 A0、固定 D3、Huber D3 和 M1 条件。Known 参数只供 evaluator-side correction；盲估计不得读入 delay/target truth、Known 输出或 oracle 输入。

主场景先按 10 周期执行。若工程批次实测表明完整矩阵的运行时间或存储超出安全能力，必须在开始筛选前停止、记录测量结果，并冻结新的 5 周期版本；不得在查看筛选或确认结果后更改周期数。

## 门控和统计

每一阶段按独立 seed、配对输入运行。目标、周期、beam、CUT 均是 seed 内观测，不可提升为独立重复。主要终点为每 seed 内正确检测数/有效目标周期机会；确认阶段对预定方法比较报告 seed 级配对差、效应量、按 seed 聚类的 95% 区间及多重比较处理。目标保持、H0 虚警、参数估计、航迹错误和资源用量作为次要终点分层报告。

生产分支、杂波注入、输入配对、truth lineage 或评价器任一必需门失败时，停止受影响的性能比较。不得靠调低支持门槛、CFAR/beam gate、替换 seed 或筛掉困难目标恢复表面成功。所有状态分别报告 `IMPLEMENTED`、`EXECUTED`、`EVALUABLE`、`PERFORMANCE_VERIFIED`。

## 资源与清理

执行前项目占用约 6.7 GB，低于 20 GB 上限。历史 R21 的大体积输入仍是唯一的冻结基线复核证据，保留其文件及 SHA 身份。本轮按单 case 串行执行，避免并发造成磁盘峰值；每个 case 的紧凑 manifest 和审计输出写入独立目录。原始 binary/CSI maps 只有在结果表、cleanup ledger 和失败状态已保存后才删除。阶段性清理只针对本轮确认可再生的临时 probe，不递归删除历史 `outputs/`、`docs/` 或 `.scratch/`。
