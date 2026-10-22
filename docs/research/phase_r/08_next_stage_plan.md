# Phase R 下一阶段计划

日期：2026-10-22。状态：**建议方案，尚未执行，也不授权启动新 CUDA 运行或 AI 训练。** 依据[方向决策](07_research_direction_decision.md)和[合并实验结果](06_bottleneck_experiment_results.md)。目标是先使 E1/E4/E5 的关键指标可解释，再决定 A/B/C；不是增加实验数量。

## 总决策与固定边界

近期唯一优先项是**修复/冻结 E1/E4/E5 的评价契约，并只重审 seed 101 既有结果**。不降低 CFAR、ROI、扫描波束、目标支持或 residual-closure 的质量门。没有完整门证据，不扩 seed、不做新算法比较。

```text
留存证据盘点
      ↓
评价定义与回归测试（ROI / hit-cell / closure / 目标关联）
      ↓
seed 101 既有产物重评 ── 未过门 → 停止，保留 NOT_EVALUABLE
      ↓ 全部预注册门通过
E1/E4/E5 独立 seed 扩展（仅冻结条件）
      ↓ 与 R3 E2 联合决策
D3 target-free blind-delay 复验（单独登记/资源门）
      ↓ 只有稳定确定性残差仍有任务代价，才评审 AI 小试验
```

长期开关维持：`ai_training=false`、`router_enabled=false`、`native_4ch_stap=false`。已知误差仅作为 evaluator-side upper bound；blind estimator 不读 truth、A0 配对输入、目标标签或 ON 数据。

## 分阶段任务与验收

| 阶段 | 输入 | 工作与输出 | 验证/验收门 | 停止或转向条件 |
|---|---|---|---|---|
| 0. 只读证据盘点 | R4 compact manifest/CSV；本地 R4 case roots；R3 manifests、cleanup ledger；当前 runner/evaluator 源码身份 | 生成一份文件级可用性表：CSI tap、CFAR geometry、检测/track、runtime XML/log、Stage2 IQ、真值、方法输入、hash/size、当前是否存在。区分可从原始行重算与只有摘要/hash 的字段 | 路径、case/seed/role/period、SHA/尺寸、源码/配置身份互相闭合；不修改、不删除现存 evidence | 若关键输入不存在，明确标 `BLOCKED_BY_MISSING_INPUT`；不得用 hash 冒充数值重算，不开始算法复跑 |
| 1. 冻结评价契约并补针对性测试 | R4 现有 evaluator (`scripts/run_p5_csi_eval.py`、`scripts/phase_r/aggregate_r4_evidence.py`)、R4 case evidence、05 冻结指标定义 | (a) 依据逐期径向真值/多普勒映射核对 P5 background ROI 与实际活动杂波支持；ROI 必须避开目标/guard 且落在预定义的背景支持，不能在看到 method 结果后挑 bin；(b) CFAR 保存唯一 hit-cell identity 或可审计的逐 cell 索引，使多 detector branch 可去重，并保留每 period/branch 的 valid CUT 与阈值测试分母；(c) residual closure 明确 A0/PK/PKR 适用范围，C0–C3 缺 PK/PKR 时仍是 `NOT_EVALUABLE`，不得改标通过；(d) E5 分开机会数、正确关联数、条件参数误差和 radial-velocity ambiguity 状态 | 对 ROI：已知活动支持/目标 guard 的正反例；零有效 cell 应 fail-closed。对 CFAR：重叠 hit 去重、分母计数、空分母行为及 target-free truth join。对 closure：缺分支时明确 `NOT_EVALUABLE`。代码有改动时新增先失败后通过的回归测试，保留 Production Current 语义 | 若现存 tap/geometry 无法判定实际支持或 unique cells，记录缺数据；不得放宽质量阈值，也不得将 branch-specific hit fraction 称为 Pfa |
| 2. 仅重审 seed 101 | 阶段 0 判定可用的 8 case seed-101 文件；修复后 evaluator；原 manifest 的 source/input identities | 重算 P5、CFAR、目标关联、E5 参数表及覆盖；所有角色仍为 OFF/ON/TO 配对。若必须重新运行，先冻结具体必要 case/分支及 runtime XML；输出 runtime config、Stage2 area report、per-beam stage、CSI taps、CFAR cell identity、detection/track records 和轻量输入功率摘要 | 真实 CUDA 才能支持 Production 结论；area packets/samples/mean power 有限且为正；selected branches 完整；valid CUT>0；truth 与 detection/track 可追溯；P5 ROI 通过其未降低的有效性规则；输出参数有明确单位/坐标/ambiguity 状态；OFF/ON/TO 使用同一算子/reference | 任一物理注入、Production、真值关联或指标门失败：停在 seed 101，报告原因并修复。若 P5 几何本身没有背景支持，指标仍 `NOT_EVALUABLE`，不要造值 |
| 3. E1/E4/E5 独立 seed 扩展 | 仅阶段 2 全门通过后的冻结 seed-101 配置与输入生成器；独立 seeds 202/303 | 按 05 原冻结的速度 `{0.5,1,2,4}`、SNR `{10,30}`、delay=0 和配对 OFF/ON/TO 生成/运行；保留每 seed 原始计数、P5、CFAR、正确关联参数与失败案例；不增加方法或 operating points | 每个 case 重过 area/Production/ROI/valid CUT/关联 gates；seed 是统计单位，period/CUT 不是独立重复；报告逐 seed 值、均值/中位数、预注册 paired 95% CI (df=2) 与相关性限制；configured Pfa 不冒充 empirical | 不完整 case 不静默剔除。若任一 decision-critical gate 未过，不再加 seed；若零失配下低速任务损失稳定且方法无恢复，转向 A；若差异仅随杂波统计而变，注册独立最小 B，不复跑旧 128 场景 |
| 4. 独立复验 D3 blind delay（单独任务） | R3 seed 101 D3 规则/代码；经阶段 0 确认可用的 seed 202/303 A1_OFF F1/F2 复数样本；配对 ON/OFF/TO Production 输入；冻结 estimator commit/config | 仅从 target-free A1_OFF F1/F2 估计延迟；输出估计值/单位、支持样本/频点、协方差或置信区间、拟合残差、失败标记；校正后记录与 B/C 的物理闭合、CSI/目标传递/CFAR/角度/速度/位置差异。数据不足时生成最小 target-free 输入且同时留存必要 summary | 估计接口不得见 truth、A0 参考、ON、目标位置或 evaluator 输出；回退/支持不足必须 `NOT_AVAILABLE`；使用 same-seed paired comparisons 与 independent seeds；真实 CUDA 证明 production | 若 D3 贴近 C 且任务指标无稳定损失，不进入 AI；若 D3 跨 seed 失败但观测不可辨识/输入质量不足，先修模型/采集；只有可观测且确定性估计仍留稳定、任务相关残差，才形成 AI entry proposal |
| 5. 研究主线/AI 决策门 | 阶段 2–4 的通过结果、文献矩阵 L01–L19、R2 物理上限与误差可观测性 | 更新 A/B/C 排名，明确实测性能差异与机制解释；如满足 AI 条件，单独提出小型、参数化、带不确定度的估计任务和同信息强传统 baseline | C 相对 B 的端到端差异在独立 seed 重复且 Known correction 有效；D/强传统基线存在可观测、可复现、有任务代价的残差；目标/CFAR/泄漏 gates 全过；AI 只能使用允许观测并输出物理参数、残差、置信度 | 任一必要条件不满足，维持 `AI_NOT_JUSTIFIED`；若 Known correction 不改善任务指标则暂停未知时延主线；若 A 或 B 出现更稳定且可恢复的损失，依证据重排 |

## 指标与数据保留约定

1. E1/E4：逐 case/seed 报实际 radial truth、目标机会与正确关联命中；SCNR before/after、target gain/loss、CSI clutter residual 分开列。负值含义按 06 manifest 定义。没有有效背景 ROI 时，不以 CSI cancellation 替代。
2. E5：先报告全部可见目标机会命中数；只对正确关联目标报告 radial velocity / angle / position 条件误差，并列 unmatched、错配和 ambiguity/unreported 数量。TrackManager ground-speed RMSE 作为独立补充项。
3. CFAR：报告每 period/beam 的 `threshold_test_count`、`valid_cut_count`、唯一 `hit_cut_count`、H0 定义及相关性。只有分子分母及身份闭合时才报告观测 false-hit rate；即使有效 CUT 很多，也不能忽略 period/seed 相关性并宣称已证实 `Pfa=1e−6`。
4. 配对：同 seed、背景、几何、处理配置；方法只使用规定的 OFF 估计输入；ON/OFF/TO 使用同一固定 reference。不同信息量或不同筛选样本不直接排名。
5. 输入：大型 Stage2 IQ 若无必要可不长期留存，但新运行必须保存路径、size、SHA-256、来源关系和每角色/period F1/F2 数值功率/有效样本数摘要；恢复执行所必需的 estimator 输入须在运行前确认为可复用或明确申请重新生成。
6. 运行：任何 CUDA 扩展前检查 `nvidia-smi`、磁盘、Git commit/dirty、配置、runner/evaluator/binary identity；结果存到新建独立目录。CPU 测试仅验证代码契约，不能替代 CUDA Production 证据。

## 预先冻结的主线转向规则

- **支持 C（未知物理时延估计）：** A/B/C 的任务级退化/恢复在新增独立 seed 上维持配对方向，A↔C 物理闭合通过；D3 的残差与任务差可重复且并非不可辨识或数据质量问题。先做确定性/可观测性研究，不自动进入 AI。
- **转向 A（低速/目标保护）：** 无误差条件下低速目标的有效 P5/正确关联检测或参数估计出现跨 seed 可重复退化，并且相同信息条件的目标保持方法改变任务结果；先排除 Doppler/几何与 ROI 错误。
- **转向 B（非均匀杂波/有限样本）：** 在固定校准/目标条件下，仅改变杂波统计或训练支持就造成可重复任务损失，且其损失不能由 C 校正解释；只注册最小 H0/H1 单因素对照。
- **暂停某方向：** 关键分子/分母、truth join、source/input identity 或 Production gate 不成立；Known correction 未恢复任务指标；或物理观测不可辨识。此时结论为 `INCONCLUSIVE`/`NOT_EVALUABLE`，不以换模型绕过。
- **进入 AI 审议而非训练：** 只有同信息条件下强传统估计器留下跨独立 seed/场景、可观测、物理可解释且会损害任务的稳定残差，且学习方法能明确使用未被显式估计充分利用的观测信息并通过外部 holdout 与泄漏审计时，才提交新的用户审批。

## 最短复现与当前执行状态

本文件没有触发运行。现阶段的结果复核可从以下已有入口重算：

```bash
python3 scripts/phase_r/aggregate_r4_evidence.py \
  --output-dir docs/research/phase_r/evidence/r4_e1_e4_e5_seed101_20261022_rebuilt
python3 -m pytest -q tests/test_cfar_geometry_diagnostic.py \
  tests/test_production_calibration_tap.py \
  tests/test_production_hierarchical_calibration_pilot.py \
  tests/test_delay_stage1_contract.py
```

上述命令是已有证据重聚合/软件契约测试，不会补回已清理的 Stage2 protocol IQ，也不构成 CUDA 生产验证。实际下一阶段尚待用户批准；R4 的 `seed 202/303` 扩展及 R3 blind-D 复验均未执行。
