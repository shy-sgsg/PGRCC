# R8：未知通道时延的多 Seed CUDA 因果复验设计

日期：2026-10-23
状态：实验方案冻结；实际执行状态见 [R8 Native CUDA 执行记录](R8_native_cuda_execution.md)。

## 科学问题

GO-CFAR R7 数值修复后，通道 2 的 +4 ns 时延误差是否会在独立场景中降低 GMTI 生产链的正确目标检出和参数估计能力？使用注入真值的 evaluator-only 物理逆校正，能否把任务表现恢复到 0 ns Current 附近？性能差异主要出现于 CSI、CFAR、Cluster/Detection 还是 TrackManager？

R8 只验证这个因果问题。R8 不训练 AI，不实现 Router，不运行盲 D3，不扩大时延水平、速度水平、误差参数或传统 Baseline 矩阵。

## 冻结实验设计

| 维度 | 冻结值 |
|---|---|
| 基线条件 | A0：0 ns，Production Current |
| 未知误差条件 | B：通道 2 注入 +4 ns，Production Current，不校正 |
| 已知校正参考 | C：与 B 相同的 +4 ns 输入，仅用注入真值执行 evaluator-only 物理逆校正 |
| 新增独立 seed | 202、303；seed101 复用 R7 修复后完整证据，不重跑 |
| 每 seed 数据角色 | ON = S+C+N；OFF = C+N；TO = S-only；OFF 与 ON 共享目标注入前背景 |
| 每个新 seed 的 Production 规模 | 3 conditions × 3 roles × 3 periods = 27 个 period-case 结果；两 seed 共 18 组 Production 配置 |
| 物理场景 | 沿用 Stage1 冻结配置：6000 m 高度、16 GHz、50 MHz 带宽、60 MHz 采样率、1300 Hz PRF、约 85 km 目标距离、6.7 m/s 配置速度、30 dB 目标 SNR、连续面积地杂波、beam 50、三个 period |
| 真值 | 位置、方位、径向速度、多普勒和 period 状态从实际 Stage2 truth 读取；不得把 6.7 m/s 配置速度当成径向速度真值 |
| Production | 本机 Native CUDA Release；`USE_COREX=OFF`；固定修复后 `build_r7/GMTI_pipe_core` SHA-256 `de3026ed4f1263be4143dcbcd10ccbb5e53b4be357e21906e1bf19ea0ec29d00` |

Stage1 已有 A/B/C 运行器用于生成配对场景和执行生产链。R8 专用入口在启动 smoke 前校验上述 Production SHA，并同时设置 Stage1 与 TrackManager E2E 所使用的二进制路径，防止误用旧的 `build/GMTI_pipe_core`。CoreX 不参与构建、运行或验收。

## 历史输入身份

先使用保留的 R3 seed202/303 case manifest 和 per-period SHA-256 核验重建输入。seed202/303 的旧 `.bin` 已清理，历史文件 SHA 仍登记在 `docs/research/phase_r/evidence/r3_minimal_cuda_causal_pilot_20261021/manifests/` 的每个 condition/role `period_paths` 中。

- 所有实际重建的 A0/B/C × ON/OFF/TO period 输入 SHA 与对应历史 manifest 相符：标记 `HISTORICAL_INPUT_RECONSTRUCTED_AND_VERIFIED`。
- 任一关键输入 SHA 不同或历史 SHA 缺失：不得称为历史复现；另登记为 `NEW_PAIRED_SCENE`，保留差异与当前输入身份，独立报告，不混入历史输入复现统计。
- seed101 来源为 R7 `post_fix_matrix_3period_final` 和 `task_metrics_final_v4`，要求每个实际输入 SHA、runtime XML 身份及 Production 二进制 SHA 与归档 manifest 相符。

## 阶段门控

1. **身份门：** 确认分支、源码状态、Stage2 simulator SHA、Production SHA、CUDA 驱动/GPU 和 runtime XML。Production SHA 必须等于 R7 修复版；若设备或二进制不可验证，不运行或解释实验结果。
2. **Stage2 杂波门：** 对每个 seed 从实际 Stage2 报告读取 `continuous_area_packets > 0`、`continuous_area_samples > 0` 和有限正的实测面积杂波功率。核对距离窗可由平台几何到达，检查 A0/B/C 及 ON/OFF/TO 的输入功率、背景身份和杂波可比性；`area_enabled=true` 不构成通过。
3. **配对门：** ON/OFF 必须来自同一 seed、同一基础 C+N 背景；B/C 时延输入相同；C 的校正只读取注入真值，不读取目标真值、无误差配对输入或其他 oracle 信息。
4. **物理闭合门：** 每 seed、每角色检查 A0 与 Known Correction 的 F1/F2 相对复 RMS。预注册门限为 `<= 1e-3`；记录最大绝对误差、功率差和圆周相位误差，不通过则停止该 seed 的性能比较。
5. **GO-CFAR 数值门：** 验证有效 CUT 几何/分母、有限门限、修复后二进制身份及检测地图。若发现几何或数值异常、负/异常零门限或参考闭合失败，停止算法比较并定位实现根因。
6. **Production 证据门：** 每个有效运行保留实际 runtime XML、命令日志、逐 beam stage、CSI tap/ROI manifest、CFAR geometry、truth、检测、cluster→detection lineage 和 TrackManager debug。必须有三个 period 的完整记录。
7. **任务评价门：** 使用 R7 冻结的关联规则：同 period、同 beam、Range ±2 bins、Circular Doppler ±2 bins；先最大化一对一匹配数，再最小化归一化残差。仅正确关联的目标计算参数误差；径向速度未解模糊时为 `NOT_EVALUABLE`。OFF 只报告描述性候选计数，不计算 Pd 或统计 Pfa。

任一新增 seed 的 Stage2 杂波、配对、物理闭合、CUDA Production、GO-CFAR、cluster lineage 或身份门失败时，保留失败证据、停止该 seed 的比较；seed202 失败则不运行 seed303。seed202 结果与 seed101 不同但质量门全部通过时，仍继续 seed303 判断场景异质性。

## 评价量与判读

逐 seed、逐 period 保存：

- Stage2 杂波包数、样本数、实测功率；A0/B/C 输入功率与 F1/F2 配对指标。
- CSI 杂波残余功率、truth-centered 目标复传递系数、幅度/相位/能量保持、ON 相对配对 OFF 的输出 SCNR。
- CFAR branch hits、unique union hits、有效 CUT、目标邻域 hit、门限/功率和 signed margin。
- Cluster、selected candidate、raw/final detection 与 cluster lineage；TrackManager confirmed/matched-this-frame 目标机会。
- 正确目标关联后的方位、位置、地速、径向速度误差和 Doppler ambiguity 状态；漏检仍计入机会和 miss，不填零 RMSE。
- OFF/H0 的 hit、cluster、detection、tentative/false-confirmed track 描述计数。H0 CUT 数、候选数和虚假航迹不解释为统计 Pfa。

跨 seed 比较使用 `B − A0` 作为失配差异、`C − B` 作为已知校正恢复，并逐 seed 展示，不把 period 当独立重复。三 seed 仅支持描述性重复性和场景异质性判断，不据此声明显著的总体 Pd/Pfa 改善。

损失定位按证据判读：CSI 残余变化支持对消层；CSI 接近但 CFAR margin/hit 改变支持 CFAR 敏感性；目标局部 hit 存在但 cluster/detection 消失支持候选/目标保护问题；detection 存在而轨迹或参数失败支持关联、解模糊或跟踪问题。任一层的推断必须由该层逐 period 记录支撑。

## 状态标签与停止

- `IMPLEMENTED`：本方案、R8 执行/评价入口及冻结判据存在。
- `EXECUTED`：仅在真实本机 CUDA Production 实际运行且有 runtime/log/input/source identity 后标记。
- `EVALUABLE`：对应 seed 通过杂波、配对、物理闭合、GO-CFAR 和完整任务 lineage 门后标记。
- `PERFORMANCE_VERIFIED`：R8 不宣称统计显著性；若证据仅覆盖三个 seed 的描述性重复，则保持 `NO`，同时报告可评估的逐 seed 任务因果观察。

完成 seed101/202/303 对照后停止。下一方向只能依据证据在低速目标保护、复杂杂波/检测统计稳健性、未知系统物理失配之间选择。D3 只有在 +4 ns 损失及 Known Correction 恢复跨 seed 一致时再评估；无论结果如何，不自动训练 AI、不进入 R9。

## 复现入口

```bash
python3 scripts/phase_r/run_r8_multiseed_causal_pilot.py \
  --output-root outputs/phase_r8_unknown_delay_multiseed_20261023/seed202 \
  --seed 202

python3 scripts/phase_r/run_r8_multiseed_causal_pilot.py \
  --output-root outputs/phase_r8_unknown_delay_multiseed_20261023/seed303 \
  --seed 303
```

按顺序单独运行；只有 seed202 通过本文件中的前置门后才启动 seed303。完整运行身份、输入核验、评价命令和结果路径记录在 R8 Native CUDA 执行文档及 machine-readable manifest。
