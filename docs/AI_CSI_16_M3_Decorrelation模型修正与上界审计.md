# AI_CSI_16：M3 去相关模型修正与诊断 oracle 候选审计

## 结论

M3 的 AR(1) 初始化已修正：pulse 0 直接使用静态网格单元的
`cell.reflectivity`，仅从 pulse 1 开始加入创新项，且

```text
Var(innovation) = base_abs^2 * (1-rho^2)
```

因此 `rho=1` 保持静态复反射率，`rho<1` 与静态模型共享初始 realization 后
发生可控的 temporal decorrelation，不再把初始化瞬态误判成模型机制。

## 正式 M3 诊断

真实 CUDA 生产 probe 为
`outputs/mechanism_estimators/probe_m3_seed2026091011/`，四个 level 均
`completed=4, failed=0`。诊断汇总如下：

| level | rho 请求 | lag-1 实测 | correlation time | mean power | power CV |
|---|---:|---:|---:|---:|---:|
| static_regression | 1.00 | 1.000000 | null | 1.2838 | 1.7265 |
| weak_motion | 0.99 | 0.987858 | 76.54 ms | 1.3212 | 2.3930 |
| moderate_motion | 0.95 | 0.950564 | 15.00 ms | 1.3055 | 2.5199 |
| strong_motion | 0.80 | 0.801862 | 3.45 ms | 1.2824 | 2.6073 |

每个 level 的原始诊断位于 `M3_internal_clutter_motion/<level>/stage2/reports/`；
清理后保留 JSON/CSV/MD/XML/TXT，raw BIN、NPY、log 和 PNG 已删除。

## O1–O5 机制感知诊断 oracle 候选

下表是基于已测 F1/F2 tap 的机制感知诊断 oracle 候选，不是可部署生产校正器，也不是
数学意义上的性能上界或支配关系。表中只能称为本次输入、代码和评价口径下的
**best observed diagnostic headroom**；不同输入、窗口、Pfa 或目标保护口径不能外推。

| 方法 | 定义 | strong cancellation | 相对 O1 |
|---|---|---:|---:|
| O1 Current | production F1/F2 tap | 4.8851 dB | 0 |
| O2 | row phase + current amplitude | 5.1597 dB | +0.2746 dB |
| O3 | row complex scalar LS | 3.1233 dB | −1.7618 dB |
| O4 | range-block complex LS | 4.3040 dB | −0.5811 dB |
| O5 | short Wiener/LMMSE | 3.8543 dB | −1.0308 dB |

证据：`outputs/mechanism_estimators/probe_m3_seed2026091011/oracle_audit/M3_internal_clutter_motion/`。
O2–O5 只使用已测 F1/F2 tap；其 residual score 不是生产 detector，原始结果明确标记
`target_preservation_status=not_evaluable_from_F1_F2_clutter_tap` 和
`pfa_status=not_evaluable_from_F1_F2_clutter_tap`。

因此 O1–O5 的排序不代表任意场景下的最优性；M3 的主要结论仍是
Type-II decorrelation-dominated failure：当 CSI 内部去相关主导时，单纯依赖静态
通道复权不能据此宣称存在可回收的生产收益。

另有一个显式标注的后验诊断器
`scripts/evaluate_m3_oracle_fixed_pfa.py`，只在 paired positive/negative 的
active support 上校准经验 Pfa。strong M3 下 O1–O5 在 Pfa=0.01、0.001、0.0001
均未形成 paired causal hit，不能把小幅 O2 headroom 写成 target detection 收益。
结果见 `outputs/causal_detection_audit/m3_strong/oracle_fixed_pfa/`。

## 复现与验证

```bash
cmake -S . -B build -DCMAKE_BUILD_TYPE=Release
cmake --build build -j4
ctest --test-dir build --output-on-failure
python3 -m unittest tests/test_mechanism_oracle_methods.py tests/test_m3_oracle_fixed_pfa.py
```

低层 selftest 另外验证 `rho=0.93` 的初始化、功率和 lag correlation；8-pulse
smoke 仅用于回归，不替代上述正式 128-pulse 生产 probe。
