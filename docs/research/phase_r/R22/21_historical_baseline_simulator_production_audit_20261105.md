# R22：历史 Baseline、仿真与 Production 能力审计

- 日期：2026-11-05
- 审计工作树 HEAD：`d963e66c764bacb63db7aab5c0a377e9718c2428`
- 研究总纲 blob：`e637e3769bde7814298ff012d0f42301884e2985`（保持不变）
- 实验设计：`R22_EXTENSION_20261028_V2_RANGE_ROI`

## 结论摘要

R22 的 Native CUDA Production 路径和物理可达的连续面积杂波场景已经有实际运行证据。V2 screening 的失败不是“没有杂波”或 CUDA 进程崩溃：18 个终态归档的面积注入、配对三元组和运行身份记录完整；唯一适用 ON 任务行缺口为 C4 Mode-B 的 reference support。该行必须保持不可评价，不能降 `min_support`、自动回退 Current 或把它计成漏检。

V2 全方法矩阵仍是 `GATE_FAILED / INCOMPLETE`，V2 runner 禁止从该终态续跑。新的 screening 必须使用独立 V3 设计、manifest 和输出根，并重新运行全部 288 个 screening case，不能将 V2 的 18 个归档拼接成 V3 执行结果。V3 只调整“局部 reference 失败时如何保留其他方法结果并继续后续 case”的状态传播；物理、truth、生产和检测门槛维持冻结值。

## 1. 历史 Baseline 的可比边界

依据 [Baseline 注册表](evidence/results/baseline_registry.csv)、[可比性矩阵](evidence/results/baseline_comparability_matrix.csv)及各阶段身份报告：

| 阶段 | 可用证据 | 可比边界 |
|---|---|---|
| R13/R14 | Native CUDA 单 seed 结果 | Production binary 后续被 R17 识别为修复前 R7 版；保留作历史和同运行闭合审计，不纳入当前跨阶段排名。 |
| R16 | A0/C0/C1 同运行单 seed、0 ns | 只支持该场景内描述性比较；没有独立 H0 经验 Pfa。 |
| R18 | 12 场景、3 个不同 seed、0 ns | C0/C1 可在同运行内比较；C2/C3 的 Mode-A、Mode-B 分开比较，不能合并；Mode-A 使用配对 OFF 信息；C4 未执行。 |
| R19 | R18 保存结果的离线重评 | 不是新 CUDA，也没有原始 IQ/完整信号机理重放。 |
| R20 | R8 两个可评价 seed；R12 一个 seed | 两个数据集不可合并；Known 仅是指定 +4 ns 的物理校正参考，不是通用上限。 |
| R21 | 2 seed、1 目标、3 周期、+4 ns | A0/B/Known 与确定性盲估计在同场景内有描述性对照；样本不足以验证泛化或统计显著性。 |
| R22 六 seed 工程矩阵 | 2.5 m/s、20 dB、20 目标、10 周期；6 个 seed | A0 `922/1200`、C0(+4 ns) `166/1200`、PK `922/1200`、P1 `916/1200`、P2 Mode-A `948/1200`、PKR Mode-A `952/1200`。这些是工程命中比例，不是独立目标样本推断；Stage2 binary 在 seed1930101 与后续 seed 间有 Rmin 序列化版本边界。P1 的接近只适用于该配置与 target-free OFF 信息条件。 |
| R22 V2 screening | 本审计重核 18 个终态 case | 17 个通过、1 个 C4 reference 门失败、270 个未开始；只有 seed1932001。只支持可行性/正确性诊断，不支持跨 seed Pd/Pfa 或总体方法排名。 |

本地 `outputs/ai_csi_baseline_v21_*` 保留 470-case formal、90-case screen、48-case transition 的 CPU/Python 离线 Baseline 汇总。manifest 记录 source commit `47e094ab7e7d519bac756f13277e95f1bbf846fb`，GPU probe 返回码 9，边界注明 CPU offline。它们覆盖 13 个两/四通道方法族，但四通道自由度、输入/参考信息和 Production 链不同，不能与 Native CUDA Production 任务指标直接排名；本任务不重跑该历史矩阵。

## 2. V2 归档复核

本机输入：`outputs/r22_full_v2_compact/matrices/screening_diskquota_retry/matrix_manifest.json`。22 条 attempt 对应 18 个终态 case和4个中断 attempt。17 个成功 ZIP 的 SHA-256 与矩阵 manifest 一致且 ZIP CRC 通过；失败 ZIP `screening__seed_1932001__vr_2d5__snr_30__delay_4ns__targets_20__periods_10__GATE_FAILED_attempt_001.zip` 与审查 provenance 记录的 SHA-256 `be76237104d6c6f11f1681de556e49ecad5e694658e8d5c94ce3e7c05850890b` 一致且 CRC 通过。

离线读取 18 个归档的 `evaluation/task_metrics.csv`：

- 270 个适用的 ON 方法×模式×case 单元中，269 个 `EVALUABLE`，1 个 `NOT_EVALUABLE`；
- 唯一 ON 缺口为 seed1932001 / 2.5 m/s / 30 dB / +4 ns / C4 / Mode-B，原因 `reference_status_not_ok`；
- 其余 3 条 `NOT_EVALUABLE` 记录位于该 case 的 OFF/TO 角色，不计入 ON 适用分母；
- 17 个 `PASSED` case 的 `valid_cut_denominators` 和 `unique_final_union_hits` 聚合门通过；失败 case 的两项全方法完整性门保持 `NOT_EVALUABLE`；不能据此恢复完整方法排名。

所有 18 个终态 case 的 A0_ON Stage2 报告均记录连续面积杂波 `1300 packets / 6,508,782 samples`、实测平均功率 `0.0221596696662`、lag-1 相关 `0.98001127856`、热噪声约 `0.0010000462`，并有有效 area gate。0/+4 ns 配对复用背景时，A1 新注入计数为0属于复用设计，不能解释成无杂波。

失败组的冻结条件仍为：range processing `[666,1197]`、每组133 bins、`min_support=8`、相位残差门限0.35 rad。独立审查已复算 support 7/55/14（ON/OFF/TO）。所有 18 个 case 的 causal triplet gate 通过，17/18 的 CUT 与 union-hit 完整性门通过；单个方法 reference 缺口不等于场景输入失效。

## 3. Stage2 几何与杂波真实性

Stage2 连续面积散射代码先从平台参考点计算地面距离，再以

\[
R_{slant}=\sqrt{R_{ground}^{2}+\Delta h^{2}}
\]

得到斜距，并仅在实际注入散射样本时增加 packet/sample 计数。V2 场景使用斜距窗 `[84,500, 85,500] m`、平台高度6,000 m、地面高度0 m，对应地面距离 `[84,286.713, 85,289.214] m`，物理可达。历史上 2.5–5 km 的地面距离窗不适用于该平台高度；V2 的实际报告证实本次面积注入非零。

18 个同 seed 条件的 area packet/sample、功率、相关性、power-CV 和地面距离范围一致，支持 clutter 统计可比。配置的 `area_enabled=true` 不是本审计的有效性证据；实际计数和报告才是。

## 4. Production、输入信息条件和身份

18 个 case 的运行 manifest 一致记录：Native CUDA、`USE_COREX=false`、Production binary SHA `d9e4d2e0b8429c5415e94be1af0230f3c0148455c394dc6d661e7e6c13a4ad1e`、Stage2 binary SHA `47fa5ec34ca15cdc4edeb35226eefa941a78ee344aa2311e5e7984c811dcb40f`、20目标、10周期、beam 50。

17 个 case 的执行源码 commit 为 `5a60a514cf53cab9f23b17ab29992fd4485876fa`；一个最早的 0 ns case 使用 `7e5a7ee8ebd6c460622425e3d05831b25603e7ec`。相关 Production/Stage2 代码在审计 HEAD `d963e66` 与 `5a60a5` 间无差异，但 V3 将重跑全部 288 case，以保证 V3 结果具有统一执行身份，而不是拼接历史 case。

冻结方法配置保留各方法输入/估计角色：Mode-A 与 Mode-B 必须分别报告；Known/PK/PKR 标为 evaluator-only；盲估计不得读取真值或 Known 参数；`fallback_to_current=false`。Theoretical CFAR cell Pfa `1e-6` 不是本次实测系统 Pfa。独立 H0 的 60/60 矩阵是另一实验，不能替代 target-present Screening 的任务评价或解锁 V2 gate。

## 5. 矩阵门与资源

V2 `r22_experiment_matrix.py` 将 `completed_with_gaps` 汇总为 `GATE_FAILED`，遇到首个 gate failure 后停止，并拒绝从 `GATE_FAILED` 使用 `--resume`。此行为保护了 V2 冻结结果；不能修改旧 manifest 伪造续跑。

V2 17 个通过 case 的平均紧凑归档约5.12 MB，执行均值约16.13分钟；按单 seed 首批记录线性外推，288 case 约77.4小时，归档约1.47 GB。此估算不是性能承诺，未计外部 GPU 负载、失败处置及分析时间。当前 worktree 约3.0 GB、文件系统可用约16 GB；此前一次提权 GPU 采样记录 RTX 3050 Laptop GPU 约97%负载、86°C、显存已用约2.5/4 GB，因此实际开跑前必须重新检查。GPU 负载状态下的结果只能作正确性/可行性执行记录，不能当作性能基准。

### 当前裁决

- `RESEARCH_MASTER_PLAN.md` 未变；总纲 blob 保持不变。
- 历史传统 Baseline 仍有有限的同场景证据；跨阶段排名、统计 Pd/Pfa 和总体瓶颈结论仍未成立。
- R22 六 seed 的确定性 P1 在该固定场景接近 Known hit fraction，但不能据此证明未知系统误差已被普遍解决或证明 AI 有必要。
- V2 保持 `GATE_FAILED / INCOMPLETE`。采用新 V3 design ID 和输出根，离线重核 V2 归档，但不将它们计为 V3 运行。
- AI gate 仍为 `FAIL`；当前不训练 AI、不开发 Router、不调整算法参数。
