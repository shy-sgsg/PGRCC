# R19 离线任务评价器实现与测试

## 范围与身份

本评价器只读取 R18 已保存的 Production 与真值记录，不调用 CUDA、不创建新 IQ、不训练模型。冻结规则见 [R19 评价契约](02_frozen_task_evaluation_contract.md)。R18 Production runner commit 为 `014352ace1c4ad0bade0d759a099a47fe9ecfaa9`，Release build 源码 commit 为 `4c6d4d303621324f5e174db0ba1811c5691df2e2`，Production binary SHA-256 为 `3328d7c61bc36b6d3b906692e355c46c328150c0399fa457e6f871bcb79abaf0`，配置 SHA-256 为 `3af60966332877095e5447247ab08cb39a54ef7a15681f37adf33cc251082ae1`。

## 已实现

- `scripts/phase_r/r19_task_evaluator.py`：实现可见 truth opportunity 的字段校验、候选门控、确定性一对一任务匹配、参数误差样本生成及 evaluability 标记。
- `scripts/phase_r/r19_r18_snapshot_evaluation.py`：适配 R18 snapshot；核验 case/role/二进制/配置身份，读取生产调试记录并生成汇总 CSV 与 manifest。
- Detection 输入优先使用逐周期 `detection_results_GMTI*.csv`，避免将只含最后周期内容的 aggregate CSV 当作完整逐周期记录；重复主键和非法关键字段不会静默通过。
- 参数评价在存在 Production Doppler 轴时使用该轴；不因历史表缺少冗余中心频率字段而拒绝有效行。radial velocity 仅在 ambiguity resolved 且值有效时评价。
- TrackManager 指标来自 Production 的 TrackManager debug；输出目标 track IDs 以供确认时延、碎片及 lineage 评价。不构造替代 tracker。
- manifest 记录 R18 runner/build/binary/config、各 case role identity、评价契约与脚本 SHA，并记录评价时工作树状态。历史输入 SHA 若只存在于 manifest 而源数组已清理，明确标注为不可重新验证。
- 派生 CSV 使用 LF 行尾，便于 Git whitespace 审查；行尾格式修正后重跑 12-case 与 pilot，所有 CSV cell 值和行数均与此前结果一致。

## 测试和执行

测试文件为 `tests/test_r19_task_evaluator.py`，覆盖匹配门、输入校验、重复行、环形 Doppler、样本隔离、参数样本、TrackManager 关联、逐周期 Detection 读取、Doppler 轴回退、CLI 直接启动、CSV LF 行尾和失败状态等边界，共 28 项。测试数据只验证 evaluator 软件语义，不构成 GMTI 性能证据。

```bash
python3 -m pytest tests/test_r19_task_evaluator.py -q
```

最后一次执行结果：`28 passed`。

离线 R18 snapshot 复评入口：

```bash
python3 scripts/phase_r/r19_r18_snapshot_evaluation.py --help
```

正式 12-case 结果和 manifest 已保存在 [`formal_matrix`](evidence/results/formal_matrix/)。Pilot 结果在 [`pilot_seed606_v6p7_snr30`](evidence/results/pilot_seed606_v6p7_snr30/)。结果 CSV 是派生审计数据；完整 source input 数组已按历史清理状态缺失，不能通过本脚本恢复。

## 缺陷修复记录

实现中暴露并修复了直接执行脚本时项目根目录未进入 Python import path、aggregate Detection 文件不覆盖全部 period、有效 Doppler 轴被过度拒绝、参数样本未传回适配层、目标 track lineage 未输出，以及 manifest 工作树状态固定写值等问题。更改均限于 R19 离线评价器；没有修改 Production 算法、参数、仿真器或 CUDA 路径。

## 状态

- `IMPLEMENTED`：离线 evaluator、snapshot adapter、28 项语义测试。
- `EXECUTED`：pilot 与 12 个 R18 场景离线复评。
- `EVALUABLE`：仅适用于契约定义且来源、分母、字段完整的指标，逐项见 `metric_evaluability.csv`。
- `PERFORMANCE_VERIFIED`：R19 未建立新的统计性能验证；测试通过不代表算法性能。
