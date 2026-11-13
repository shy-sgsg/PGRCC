# R22 V2：Production 距离支持域修订

状态：`FROZEN_BEFORE_V2_ENGINEERING_GATE`
日期：2026-10-28
设计：`R22_EXTENSION_20261028_V2_RANGE_ROI`
替代关系：V2 是 V1A 的独立后继版本；V1A 的设计、失败 case 和诊断证据保持原样。

## 修订原因

V1A 的 seed1930101 同 seed 诊断重放保留了原有 `min_support=8` 和相位残差阈值 `±0.35 rad`。四个 C4/PKR failure sidecar 共记录 50 条分支/group 失败；每条失败区间均不与实际连续面积杂波 ROI 相交。四个完整 133-bin group 覆盖该 ROI，且在该次重放中没有 gamma failure row。此结果支持“全 1–4095 处理范围要求面积杂波 ROI 外的组也通过 reference gate”这一可检验解释，但单个 seed 不能证明全部物理根因，也不能产生任务性能结论。

场景距离轴为 origin `82,802.6768996 m`、spacing `2.498270483333333 m/bin`。按面积地杂波距离窗 `84,500–85,500 m` 和闭区间映射，对应 0-based bins `680–1079`。V1A seed1930101 的目标 bins 为 `688–1032`，均处于这一 ROI 内。

## V2 冻结的共同处理窗口

所有 Production 方法、角色、误差条件和 evaluator 使用相同的闭区间：

```text
rg_st = 666
rg_ed = 1197
```

这覆盖四个完整 133-bin group：`[666,798]`、`[799,931]`、`[932,1064]`、`[1065,1197]`，包含整个连续面积杂波 ROI 与按同一物理距离窗生成的目标。边界按 Production 的 0-based 闭区间语义记录。运行时 XML 必须审计实际 `rg_st/rg_ed`，每个分支都必须等于上述值。

这是对本次任务处理与评价域的显式定义，不是把失败组过滤掉，也不是让 reference validator 忽略缺组。V2 要求该共同窗口内的全部预期组仍通过原有 reference、Production、CFAR 和任务评价门槛。ROI 外场景数据仍由 Stage2 按场景生成；V2 不对窗口外距离单元宣称结果。

V1A 的全距离 gate failure 作为独立历史结果保留。V1A 与 V2 的处理/评价支持域不同，不能合并统计或用 V2 覆盖 V1A。

## 未改变的冻结条件

- seeds、条件数、20/40/3/0 目标规模、10 periods、方法集合、Mode-A/Mode-B、配对方式及盲估计信息边界与 V1A 相同。
- Screening、confirmatory、dense、bridge 和独立 H0 的计划数保持 594 case；不得因资源不足而静默缩减。
- `min_support=8`、`range_band_bins=133`、`robust_phase_threshold_rad=0.35` 不变。
- 不忽略失败组、不静默回退 Current、不放宽任何 CUDA Production、CFAR、truth lineage 或 evaluator 门槛。
- 不训练 AI、不开发 Router、不扩展六参数联合估计。

机器可读冻结定义见 [V2 实验设计](config/r22_extension_experiment_design_v2_range_roi.json) 和 [V2 Production 方法配置](config/r22_method_config_v2_range_roi.json)。V1A 原配置和设计文件不修改。

## V2 工程门

先运行 seed1930101 的 0 ns 与 +4 ns 配对工程 case，再运行余下五个独立 engineering seeds。每个 case 必须确认真实 CUDA Production、Stage2 面积杂波注入、共同窗口、输入配对、全方法 XML 实际值、CSI tap、CFAR geometry、任务输出和 truth lineage。任何必需方法在共同窗口内 gate 失败、任意条件的实际 XML 不一致、杂波/配对不合格或 evaluator 不完整，都停止该阶段，不开始 screening。

只有六个 engineering seeds 都通过预先冻结的质量门且测量后的时间和磁盘预算支持完整矩阵，才可进入 screening。单 seed 仅作正确性/可行性判断。

## 结果状态

本文件和配置只定义实现后的 V2 设计；在真实 CUDA engineering gate 完成前，V2 为 `IMPLEMENTED`，而非 `EXECUTED`、`EVALUABLE` 或 `PERFORMANCE_VERIFIED`。V2 的工程门结果和后续方向判断应分别记录，不能修改本设计以适配已观察结果。
