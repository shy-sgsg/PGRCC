# R11 评价器一致性复核证据包

本包保存 R11 六个已运行 Production 条件的离线重评结果、原输入的小型复算子集，以及 R8/R9 旧 evaluation 的一致性复算记录。此次没有运行 CUDA、Stage2 或 Production。

## 文件

- `r11_reanalysis.json`：R7/R8 冻结任务门、R11 原几何匹配诊断、同 cell CTDR/Jacobian/CSI 结果、源码和输入 SHA-256。
- `r11_reanalysis.csv`：每个校正条件的任务匹配与关键数值汇总。
- `r11_candidate_pair_audit.csv`：逐条件、逐候选的 range/Doppler gate、signed circular Doppler delta 和接受/拒绝原因。
- `r11_evaluator_difference_records.csv`：R11 原报告 cell、复现的几何匹配 cell 与 R7/R8 冻结任务 cell 的逐条件差异。
- `r8_r9_historical_consistency.json`：18 个 R8 与 12 个 R9 evaluation manifest 的原归档结果和当前复算结果逐项比较；全部 `UNCHANGED`。
- `inputs/`：六份 `GMTI02` detection CSV、对应 cluster lineage、seed303 A0 truth、只保留 A0 reference/条件名的精简 manifest、R11 summary 与 CSI target-cell response。总量不足 1 MB；不含原始协议 IQ、Production 日志或全 CSI maps。

## 复现

在包含本地 R8/R9 历史 evaluation 输入的工作区运行：

```bash
python3 -m pytest -q tests/test_r11_evaluator_consistency.py
python3 scripts/phase_r/reanalyze_r11_evaluator_consistency.py
```

只从本包的紧凑 R11 输入重算：

```bash
python3 scripts/phase_r/reanalyze_r11_evaluator_consistency.py \
  --r11-root docs/research/phase_r/evidence/r11_evaluator_consistency_20261024/inputs \
  --output-dir /tmp/r11_evaluator_recheck
```

历史 R8/R9 深比较另需本机保留既有 evaluation manifests 引用的 Production CSV；本包的 JSON 已保存本次 30 个历史 case 的逐项复算差异（均为空）。

## 核心判定

R7/R8 冻结门在六个 R11 条件中都关联 `(48,2154)`。`(129,2155)` 的 range 差为 `+1` bin，但 Doppler 圆周有符号差为 `−49` bins，六次都以 `doppler_gate` 被拒绝。R11 原几何 matcher 对前五点选中 `(129,2155)`，其输出已重现并保留为诊断记录；它不构成 R7/R8 任务关联。
