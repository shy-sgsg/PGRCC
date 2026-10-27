# R22-08：下一项实验冻结契约状态

## 状态：未冻结，不得启动

原始 R22 screening/confirmatory 矩阵因 PKR support gate 未闭合而没有冻结。当前文件只限定再次讨论时必须满足的进入条件，不构成新的实验授权。

## 已确定且不可放宽的约束

- `research_calibration_min_support=8` 保持不变；Production scan beam quality gate 保持不变。
- 不将 A1 Stage2 direct-area count=0 单独判为无杂波；配对输入必须通过 scene manifest、background path、mapping 和有效杂波源证据闭合。
- Known error 只用于 evaluator-side physical correction。
- Blind estimator 不得读取 delay truth、target truth、A0/Known 输入或配对 OFF reference（除非作为独立 Mode-A privileged contract 并分层报告）。
- H0 统计 Pfa 需要独立 target-free seed。
- 统计单位是 seed；同 seed targets/CUT/beams/periods 不作为独立 seed。
- `IMPLEMENTED`、`EXECUTED`、`EVALUABLE`、`PERFORMANCE_VERIFIED` 分开标记。

## 需要离线审计的唯一根因

核对 PKR range-band 划分、group 的 sample/support 定义以及 minimum support 的物理统计含义。seed900203 实际每个 6-bin group 仅产生 support=6（末尾短组 3），与 min-support=8 数学上不兼容。可能的修正只能在统计定义和实现被证实后提出，例如增加可用独立 range support 或重构 group/support 计算；不得把阈值从 8 降低来让本例通过。

离线审计交付应包括：源码行、输入维度与 group 算法、support 单位推导、端到端当前输出复算、至少一个不会跑 CUDA 的边界单测设计，以及候选配置/代码改动对历史兼容性的影响。用户审查方案前，不修改配置或算法。

## 重新冻结所需产物

经用户授权完成修复后，单独运行一个 seed/最小分支 CUDA engineering gate。只有 reference groups 有效、CSI summary 存在、beam/CFAR gates 通过、A0/PK/PKR residual closure 可计算、输入身份与方法公平性闭合后，才可另行提交完整筛选与确认设计供审查。该 gate 失败则停止，不扩 seed。
