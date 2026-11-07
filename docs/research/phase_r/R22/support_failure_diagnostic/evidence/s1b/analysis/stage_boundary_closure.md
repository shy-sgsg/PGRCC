# R22-S1b 处理阶段闭合审计

- 对齐单元数：`665`
- Mode-B 失败输入对照：`exact_match`，逐值相同 133/133 cells。
- C4 线性算子核对：`F2−γF1`，最大公式相对 L2 误差 1.09e-07。
- F1/F2 闭合：`ON - OFF - TO`；相对 L2 分母为 ON 的 L2。
- `F2·conj(F1)` 是双线性量，列出其差值作诊断，不作为闭合门。
- raw 与 CSI 从原始输入分叉，不能串读为同一条处理链。
- 该文件是单 seed、单 beam 的因果诊断，不构成检测性能结论。

| Path | Stage | Cells | F1 rel-L2 | F2 rel-L2 | F2·conj(F1) difference | Output rel-L2 |
|---|---|---:|---:|---:|---:|---:|
| raw | raw_dbs_pre_range_phase | 133 | 0.00936709461 | 0.0180555759 | 0.209012931 | NOT_AVAILABLE |
| raw | raw_post_range_phase | 133 | 0.00936709461 | 0.224942483 | 0.240975547 | NOT_AVAILABLE |
| CSI | csi_dbs_pre_range_phase | 133 | 0.0103156569 | 0.0180555759 | 0.26725489 | NOT_AVAILABLE |
| CSI | csi_post_range_phase_pre_c4_adapter | 133 | 0.0103156569 | 0.787703335 | 0.651998257 | NOT_AVAILABLE |
| CSI | csi_post_c4_adapter | 133 | 0.0103156569 | 0.787703335 | 0.651998257 | 0.69383424 |

详见相邻 JSON，包含各阶段 range phase 与 P38 拟合值摘要。
