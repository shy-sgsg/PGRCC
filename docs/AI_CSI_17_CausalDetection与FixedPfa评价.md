# AI_CSI_17：Causal Detection 与 Fixed-Pfa 评价

## 评价定义

成对场景固定为：

- A = `S+C+N`：target on；
- B = `C+N`：target off；
- C = `S-only`：target-only 参考。

三者共享 seed、杂波、噪声、impairment 和几何，只改变 target presence。脚本先输出
ROI 连续量（on/off peak、on-minus-off 增量、before/after 增量和 target-only），
增量不解释为线性 `S-only` 功率。`legacy_hit` 与 `causal_hit` 分开；causal hit
要求生产检测命中且 target-off ROI 严格更弱。

Fixed-Pfa 使用 negative-control score map 的实测分位数，分别记录 requested Pfa、
empirical Pfa、threshold、causal Pd 和 paired causal hit；默认统计的是整张
524,288-cell production map，不声称是 CFAR 单 cell Pfa。

## 实际 CUDA 结果

单因素强档的 compact evidence 位于：

- `outputs/causal_detection_audit/m1_strong/`
- `outputs/causal_detection_audit/m2_strong/`
- `outputs/causal_detection_audit/m3_strong/`

M1 strong 的连续 after 增量为 `+22,014.79`，经验 Pfa=0.01 时 paired hit=true；
Pfa=0.001/0.0001 时正例低于阈值。M2 strong 的 after 增量为 `+22,499.56`，
同样仅在 Pfa=0.01 命中。M3 strong 的 after 增量为 `−18,598.09`，三个 Pfa
均未命中，且 target-off ROI 更强；这与 temporal decorrelation floor 一致。

## Mixed/OOD 配对结果

`outputs/mechanism_mixed_design/` 运行了 12 个 mixed/OOD production case；四类代表
场景各有 target-only/negative-control 配对：

| 场景 | 连续 after 增量 | Current Pfa=0.01 | Pfa=0.001 | Pfa=0.0001 |
|---|---:|---|---|---|
| M1+M2 (`m1m2_ood_b`) | +945,900.21 | hit | hit | miss |
| M1+M3 (`m1m3_ood_b`) | +1,305,411.03 | hit | miss | miss |
| M2+M3 (`m2m3_ood_b`) | +2,587,899.58 | hit | miss | miss |
| M1+M2+mild-M3 (`all_ood_b`) | +1,330,208.30 | hit | hit | miss |

对应 `evaluation/causal_detection_summary.json` 与 `fixed_pfa/fixed_pfa_summary.json`
位于各 `outputs/causal_detection_audit/mixed_*_b/` 目录。前三个代表场景 legacy
检测快照未命中，但 continuous delta 和 matched-Pfa score 命中仍被单独保留；全混合
场景 legacy 与 causal 均命中。

## 校正后的 paired Pfa

为避免把 cancellation 改善冒充检测改善，又运行了同一估计量处理 on/off/target-only：

- M1+M2：raw-observable D3 frequency-domain correction，
  `outputs/causal_detection_audit/mixed_m1m2_b/fixed_pfa_corrected_d3/`。
  Pfa=0.01/0.001/0.0001 均 paired hit；target-on 峰 1,293,981，target-only
  峰 1,306,656，target preservation 未见损伤。
- M2+M3：raw-observable P1 phase correction，
  `outputs/causal_detection_audit/mixed_m2m3_b/fixed_pfa_corrected_p1/`。
  Pfa=0.01 paired hit；0.001/0.0001 未命中；target-on/target-only 峰比为 −0.038 dB，
  未见明显 target loss。

## 复现入口

```bash
python3 -m unittest tests/test_causal_and_fixed_pfa.py
python3 scripts/evaluate_causal_detection.py --help
python3 scripts/run_fixed_pfa_evaluation.py --help
```

所有控制 run 完成后仅保留 compact JSON/CSV/MD/XML/TXT；raw BIN、NPY、log、PNG
按 `outputs/cleanup_manifest_20260910.json` 清理。生产目标选择仍由当前周期检测/score
map 触发，truth 只用于 ROI 评价，不进入生产估计器。
