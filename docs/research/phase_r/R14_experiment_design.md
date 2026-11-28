# R14 SCC 与 Robust DDC 独立 seed 复核设计

状态：执行前冻结（2026-10-25）。实验结果与方向裁决分别记录在 R14 执行报告和 R14 研究方向评估中。

## 科学问题

在同样可获得的 OFF 标定信息条件下，Robust DDC（C3）相比 SCC（C2），能否改善目标信息保持与最终 GMTI 检测，同时控制错误输出和参数估计代价？C0 仅作为 Production Current 操作基线。该 pilot 不预设赢家，不构成总体 Pd/Pfa、统计显著性或泛化证明；当前没有 AI 必要性证据。

## R13 机制审查与本轮约束

- **C2 SCC：** 在完整 active support 上拟合一个复数全局权重，`Gamma = Σ(F2·conj(F1)) / Σ|F1|²`，并对该支撑上的全部输出 cell 应用 `F2−Gamma·F1`。
- **C3 Robust DDC：** 每个 Doppler row 单独计算 `F2·conj(F1)` 的单位相位 circular center；丢弃无效/近零交叉项及相对中心相位残差大于配置门限 `0.35 rad` 的拟合样本；在剩余样本上做复最小二乘，再将该 row 的 Gamma 应用于整条 active support。排除样本不参与拟合，不从 CSI 输出中挖空。
- R13 正常速度输入支撑为 `39×4095=159,705` cells/period。保存的 C3 group 记录覆盖 3 periods 共 117 rows，最终拟合支持共 93,587/479,115（19.53%）；每 row 支持数 min/median/mean/max 为 `646/795/799.9/940`，117/117 groups 有效。Circular phase coherence 为 `0.176–0.275`（mean `0.234`）。约 80% 被排除与固定 ±0.35 rad 门下宽相位分布一致；R13 聚合日志未把近零交叉项与相位门拒绝分别计数，因此不声称二者的精确贡献。不得调门限。
- R13 的 C2/C3 在 6 个 ON/TO period opportunity 中选到相同的目标检测 cell；共同正确机会上的 position、angle、folded Doppler 数值相同。C3 的匹配目标 `cfar_margin_db` 高约 4.45–4.82 dB，但 C2/C3 均为 ON 3/3 正确检测，故较高 cell response 没有增加本场景 ON 正确检出。TO CSI retention 则相差约 7.36 dB；这属于中间响应差异，必须复核是否影响 ON 任务。R13 TO 的地速输出另有差别，不能与其他参数合并成单一结论。
- R13 的 Mode-A reference 只由 target-free OFF 输入拟合，truth-used=false，固定应用于 ON/TO；C2/C3 使用同一 OFF Stage2 输入身份，生产 F1/F2 hash 相同。没有发现 estimator 读取 truth 或误差标签的实现错误。C2/C3 的 OFF 拟合样本同时作为 H0 检测背景，故 OFF 错误输出属于 **reference-in-sample** 描述结果；不得宣称独立背景泛化或统计 Pfa。
- paired OFF 在真实部署中的可用性取决于是否存在可获得的 target-free 校准段，并且其波束、平台状态、通道状态和杂波统计与评价时段足够匹配。R14 的 paired OFF 是仿真实验可控参考，不等价于已验证的在线部署来源。

以上限制只解释已保存的 R13 观测；不扩展算法、不改生产质量门、不增加 C3 模型或阈值搜索。

## 冻结场景与条件

| 项目 | 冻结值 |
|---|---|
| 独立 seed | `505`（不得依据结果追加 seed） |
| 目标 | 正常速度 `6.7 m/s`，SNR `30 dB`，斜距 `85 km`，beam `50` |
| 系统误差 | 通道 delay `0 ns` |
| 时间 | 3 periods；OFF=C+N、ON=S+C+N、TO=S，同场景配对 |
| 地杂波 | 复用 R13 Stage2 面积杂波场景和物理可达几何（平台 6 km，杂波斜距窗 84.5–85.5 km）；实际注入须由本轮 Stage2 报告确认，不能只检查配置开关 |
| 方法 | C0 Current、C2 SCC、C3 Robust DDC Mode-A |
| 主比较 | 同一 OFF reference 信息类别下 C2 对 C3；C0 是 Production Current operational anchor |
| 质量门 | 沿用 R13 Production、CFAR geometry/map/log、truth matcher 和 TrackManager 门；不降低门槛 |

R13 runner 固定将 A0 Production Current control 纳入每个 case，R13 evaluator 也强制检查 A0/C0/C2/C3 的 OFF/ON/TO 分支。故本次预计执行 12 条分支（A0 闭合控制 3 条 + C0/C2/C3 各 3 条）；A0 仅作 evaluator 必需控制，不参与方法排名。除此之外不运行其他方法或场景。

现有 runner 配置只显式列出 pilot seed101 和 formal seeds101/202/303；不能靠 CLI override 合成 seed505 case。R14 使用 `evidence/r14_scc_robust_ddc_seed505/inputs/r14_runner_config.json`，该配置从基线配置派生，只把 pilot 选择限定为本设计的 seed505 单一 case；Production、方法定义和其他配置保持原值。该配置及其 SHA-256 随证据包保存。

Mode-A 的 C2 与 C3 必须引用完全相同的 OFF 输入文件身份；manifest 与 paired-input audit 不闭合时停止比较。计划使用同一 OFF 数据拟合 reference 并评价 H0，报告中将 H0 错误输出标记为 reference-in-sample。

## 预先冻结的评价口径

1. **主任务：** ON correct detection / confirmed current-cycle association，以 3 个 `period×beam×target` truth opportunities 为分母；ON 是主要任务结果。TO 只评估 target-only transfer，不替代 ON 检测。
2. **H0 与错误输出：** 逐方法报告 final-union unique CFAR hit cells / valid CUT、OFF final detections、当前周期 Confirmed Track association 和唯一 track 数；C2/C3 标明 OFF reference-in-sample。另报告 ON unmatched detection rows、错误 Confirmed Track association/track 数。给出每项的计数、关联行数/检测数/3 个 period-frame 等对应分母。Observed hit fraction 不是 Pfa；经验 Pfa 固定为 `NOT_EVALUABLE`。
3. **参数：** 仅在两方法共同正确关联的同一 truth opportunity 集合上配对比较 angle、二维位置、folded Doppler 与 Production TrackManager ground speed。径向速度若仍未可信解模糊，标 `NOT_EVALUABLE`。同时保留每方法全体成功机会结果，不能以不共同的样本作成对比较。
4. **目标保持与检测 cell：** 报告 TO CSI target ROI retention、配对 ON−OFF 复响应、匹配目标候选是否变化；从 Production detection 记录报告目标候选 row/col、CSI 输出 power 与 `cfar_margin_db`，并用本轮 final-union map 核对候选 cell。明确目标保持差异是否改变 ON correct detection、参数和错误输出。
5. **处理输入与有效性：** 核实 Stage2 continuous-area packets/samples/power 与有效距离窗；核实同一 role/period 下 C2/C3 输入 F1/F2 hash、reference 数据 identity、Adapter method/support/valid groups、运行时 XML、CSI tap、CFAR geometry/unique map/log、Detection、TrackManager 与真值关联。
6. **代价：** 报告单次 branch 时间与运行前 GPU 状态作为本次工程记录；单次测量不作稳定速度排序。

## 阶段停止门

- Stage2 面积杂波未实际注入、C2/C3 OFF calibration input identity 不同、A0/C0/输入链不闭合、生产/CFAR/TrackManager 证据失败：停止性能判读，保留失败现场并定位根因。
- R13 的 C2/C3 target-retention 差异在 seed505 不复现：停止把该现象列为优先候选。
- retention 差异复现但 ON 正确检测、错误输出无实质改善，或定位/速度有不可接受退化，或收益主要依赖不具部署可用性的配对背景：不得声称 C3 净任务优势，裁决 `STOP` 当前 C3 优先候选。
- 同信息条件下出现可信且任务相关差异：只进入下一次研究价值审查；本阶段停止，不续 seed、不调 SNR、不训练 AI、不开发新算法。
- 单 seed 只能作独立场景可行性复核，不能声明总体检测概率、统计 Pfa、显著性或泛化。

## 复现命令

先以 Native CUDA 运行一个 normal case：

```bash
env USE_COREX=OFF GMTI_CFAR_DUMP_BEAM=50 python3 scripts/run_production_hierarchical_calibration_pilot.py \
  --config docs/research/phase_r/evidence/r14_scc_robust_ddc_seed505/inputs/r14_runner_config.json \
  --mode pilot \
  --output-root outputs/phase_r14_scc_robust_ddc_seed505_20261025 \
  --input-mode local \
  --delay-errors-ns 0 \
  --seeds 505 \
  --target-velocities-mps 6.7 \
  --target-snr-db 30 \
  --method-ids C0,C2,C3 \
  --calibration-modes Mode-A \
  --max-cases 1
```

随后将 runner manifest 中本轮唯一 case 的 `case_manifest.json` 交给 R13 evaluator 的单场景模式（详见 R14 执行报告）。原始 runner/evaluator 产物保存在本机独立输出根；纳入 Git 的紧凑证据包只保留审查和复现必需记录，并用 cleanup ledger 记录后续派生文件清理。
