# 修复后 CSI 残余纹理诊断

## 方法

[`audit_csi_residual_texture.py`](../scripts/audit_csi_residual_texture.py) 只读取 production CSI tap 导出的当前对齐后复数 `F1/F2`，不重新实现生产 CSI。有效 clutter cells 取 active support 内、有限且 `min(|F1|²,|F2|²)` 位于第 5–99 百分位的单元。

所有统计先分三类：

1. `coherent_csi`：`rho >= 0.5`；
2. `h0_bypass`：`rho < 0.5`；
3. `support_outside`：active clutter support 之外，不作为“零残差”处理。

相位残差定义为：

```text
angle(F1 * conj(exp(+j*(p38_k*fa+p38_b)) * F2))
```

同时输出 phase 2D map、Doppler/range 统计、幅度残差、equalization ratio、coherence map 和 IFFT-derived slow-time diagnostic。tap 没有导出 raw pre-Doppler pulse，因此 slow-time 结果是诊断量，不冒充原始脉冲真值。

## 当前 probe 结果

输入：seed `2026091010`，Current gate=true，128 pulses × 4096 range cells，active rows 45–83。

| row class | rows/cells | phase RMSE | amplitude residual median/std | equalization ratio median |
|---|---:|---:|---:|---:|
| coherent CSI | 35 / 134948 | `0.3635 rad` | `0.0754/6.1786 dB` | `0.6630` |
| H0 bypass | 4 / 15173 | `1.3839 rad` | `-0.6395/7.4919 dB` | `0.5819` |
| support outside | 89 / 0 | N/A | N/A | N/A |

all-valid-cell phase RMSE 为 `0.38996 rad`，coherence gate pass/bypass fraction 为 `0.8974/0.1026`，rho 的 min/median/max 为 `0.02861/0.94565/0.99997`。相位对 Doppler 的线性拟合 `R²=0.0553`，对 range-frequency 的拟合 `R²=0.2759`、估计 delay `-0.8926 ns`；这不足以把 wideband delay 判为唯一主因。slow-time IFFT-derived fit 的 `R²=0.00936`，未显示清晰线性漂移。

生产 tap 同一输入的 power-weighted summary 为 phase RMSE `0.13763 rad`、coherence `0.99907`；它和本诊断的 valid-clutter unweighted/class-separated 统计口径不同，不能混写成同一指标。

## 证据路径

- 汇总：[`residual_texture_summary.json`](../outputs/csi_residual_texture/probe_seed_2026091010/residual_texture_summary.json)
- 分组表：[`residual_by_row_class.csv`](../outputs/csi_residual_texture/probe_seed_2026091010/residual_by_row_class.csv)
- maps：`outputs/csi_residual_texture/probe_seed_2026091010/`

复现：

```bash
python3 scripts/audit_csi_residual_texture.py \
  --manifest outputs/csi_residual_probe/run/variants/c_plus_n/stage2/algorithm_result/period_0000/csi_metrics/20260910_105934_926615/csi_roi_manifest.csv \
  --output-dir outputs/csi_residual_texture/probe_seed_2026091010 \
  --period-id 0 --beam-id 1
```

结论是“修复后仍有 texture，且低相干行与 coherent rows 的统计完全不同”，而不是再次修改 complex multiplication sign。
