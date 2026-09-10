# AI-CSI-23：Production CFAR 与泛化统计

## 评价口径

V2 screen 的 primary Pfa 使用生产 `GMTI_core` 的 GO-CFAR 命中单元和生产几何重建的 valid CUT 分母：

```text
Pfa = production CFAR hit_cells / valid_cfar_test_cells
```

随后记录生产 clustering 的 `false_clusters`/`selected`。score-map all-cell quantile 仅作为 `score_map_pfa_diagnostic`，不能替代生产 CFAR 结果。阈值/质量 gate 只从 calibration 或 calibration+validation 冻结，held-out Test-V2 不反向参与。

## Targeted screen 实测结果

本次每个 scene、每个 J0–J6 方法都实际跑了 target-off 和 target-on 生产链路，共 `336` 条 CFAR 记录，`measured=336、unavailable=0`。生产 runtime 的 configured `pf=1e-6`，每条 dynamic-CFAR 记录的 valid CUT 分母为 `519168`；这两个值分别是配置量和实测几何，不应混称为经验 Pfa。

target-off scene-block 均值如下：

| 方法 | Pfa 均值 | Pfa 中位数 | false clusters 均值 |
|---|---:|---:|---:|
| J0 Current | 0.008529 | 0.008799 | 39.21 |
| J1 | 0.008519 | 0.008799 | 39.42 |
| J2 | 0.008530 | 0.008799 | 39.33 |
| J3 | 0.008520 | 0.008799 | 39.50 |
| J4 | 0.008524 | 0.008799 | 39.83 |
| J5 | 0.008525 | 0.008800 | 39.75 |
| J6 | 0.008527 | 0.008799 | 39.88 |

这些 screen 数字显示 J5/J6 尚未证明没有 Pfa/cluster 回归；最终判断必须使用 fresh Test-V2，而不能用本 screen 代替。

## Scene-block bootstrap

Pfa 和 false clusters 的置信区间按 scene 重采样，不把约 16M 个 RD cells 当作 IID 样本。结果见 `outputs/physics_adaptive_selective_v2_screen/production_cfar_bootstrap.csv`，字段明确记录 `bootstrap_unit=scene`。目标 Pd 使用 target-on 的生产检测记录，target preservation 和 target-off paired evidence 保留在 `target_rows.csv`。

## 证据路径

- `outputs/physics_adaptive_selective_v2_screen/production_cfar_rows.csv`：逐 scene/role/method 的 production GO-CFAR 计数；
- `outputs/physics_adaptive_selective_v2_screen/production_cfar_bootstrap.csv`：scene-block 均值与 95% bootstrap CI；
- `outputs/physics_adaptive_selective_v2_screen/target_rows.csv`：paired target-on/off 目标指标；
- `outputs/physics_adaptive_selective_v2_screen/formal_matrix_manifest.json`：production 口径、resource snapshot、清理策略和 score-map diagnostic 分离声明。

完整 screen 复现入口：

```bash
python3 scripts/run_joint_physics_selective_v2.py \
  --mode screen --output-dir /tmp/pgrcc_v2_screen_replay --build-dir build
```
