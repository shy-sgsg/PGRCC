# R22-S1：C4 参考支持失败的离线诊断与最小补采方案

## 任务定位

依据 `docs/research/RESEARCH_MASTER_PLAN.md`（blob `e637e3769bde7814298ff012d0f42301884e2985`）的传统方法能力边界与瓶颈归因目标，本任务检查 C4 Mode-B 在一个多目标场景中的支持不足。用户提供的独立审查建议先做局部因果诊断，再决定是否进行受控重放；审查不构成长期开题或总纲修改授权。

## 本次已核验的证据

- 筛选矩阵保留 `GATE_FAILED`：17 个 case 通过，一个 case 因期 5、beam 50、az 52、range 1065–1197 的 `support=7<8` 而失败；仅一个筛选 seed，不能对总体性能作推断。
- 原生产实现 `src/production_calibration_adapter.cpp:221` 对各有效单元的 F2·conj(F1) 归一化求相位中心，筛选 ±0.35 rad 后计数。这里的 7 是筛选后数量；筛选前有效单元数、逐单元相位及筛选集合无法从 gamma 汇总表反推。
- 本次对齐了 SNR20 ON、SNR30 ON 与 SNR30 OFF 的 1,560 组身份；[完整组级对照](group_comparison.csv)及[机器摘要](group_comparison_summary.json)由 [compare_groups.py](compare_groups.py) 生成。OFF 是 Mode-A 的 OFF estimator 输出，仅作因果诊断，不是免参考在线方法。

| 期 5 / beam 50 / az 52 距离单元 | ON 20 dB support | ON 30 dB support | OFF 30 dB support |
|---|---:|---:|---:|
| 666–798 | 77 | 78 | 69 |
| 799–931 | 74 | 73 | 72 |
| 932–1064 | 46 | 46 | 58 |
| 1065–1197 | 25 | 7 | 55 |

全部 1,560 组中，ON 30 相对 ON 20 的支持数下降 791 组、上升 642 组、不变 127 组；它们是同一 seed 内的相关组，不是独立重复。失败组 coherence 为 ON20 `0.202899567021942`、ON30 `0.11720003232345`、OFF30 `0.741813125064615`。T010 的 period 5 range bin 1175 落入失败距离组，但其处理后二维响应是否覆盖 az 52 尚未证实。

- 已核对 ON20/ON30 的 C4 gamma 文件来源为已提交的 screening checkpoint；OFF30 gamma 原件来自失败 case 的 `references/Mode-A/C4/reference_gamma.csv`，原始 SHA-256 为 `4c9699fa318ae83db4a47e62e44ec22d87d41101bda3038aa928f306b19bbd13`。交付副本 [snr30_off_reference_gamma.csv](snr30_off_reference_gamma.csv) 只将 CRLF 换行为 LF，原始数值不变；三个交付输入的 SHA 和目标组原始行见 [摘要](group_comparison_summary.json)。
- 失败 case 的 `raw_cleanup.json` 记载已移除 1,781 个可重建数组，共 `11,320,004,032` bytes；现存失败目录中未发现 `.bin/.npy/.npz`、`production_calibration_failure_cells.csv` 或 complex real/imag 调试文件。现存 `.f32` 是 `paired_range_phase_*` 距离相位校正向量，不能替代筛选前 F1/F2。该结论限于已检查的失败目录，不能断言整个计算机不存在其他副本。SNR20 的原始 case 目录也已按记录归档移除。
- SNR20 和 SNR30 的场景模板已分别从原归档及失败目录保存，并与其运行 manifest 中的 SHA 一致：[SNR20](scenario_template_snr20.json)、[SNR30](scenario_template_snr30.json)。当前本机 Stage2 和 Production 可执行文件的 SHA 分别与记录的 `47fa5ec34ca15cdc4edeb35226eefa941a78ee344aa2311e5e7984c811dcb40f`、`d9e4d2e0b8429c5415e94be1af0230f3c0148455c394dc6d661e7e6c13a4ad1e` 一致。

## 裁决与不可评估项

直接门控原因是相位筛选后支持不足。目标污染是与 ON/OFF 和配对结果相符的假设；背景相位变化、目标在处理后的二维扩散和相位中心移动仍是竞争解释。`RAW_COMPLEX_INPUT_NOT_AVAILABLE`：现有检查范围内缺少拟合输入的逐单元复数记录；筛选前有效数、相位中心、单元残差、ON/OFF 支持集合交并集及目标在 az 52 的响应均 `NOT_EVALUABLE`。不能用组级 coherence、目标距离 bin 或距离相位校正向量冒充这些证据。SNR20/SNR30 的散射体与背景生成统计身份一致，但不能仅凭摘要声称两次 raw 背景逐字节一致。

既有 R22 运行配置将 `csi_metrics_dump_power_maps` 和 `csi_metrics_dump_intermediate_maps` 均设为 false。生产 `recordProductionCalibrationFailureCells` 在两个开关开启时仅导出失败组；因此即便启用，它不能保证获取通过的 ON20 和 OFF30 对照组。不能以单独复用这个钩子宣称已覆盖全部反事实。

## 可审阅的最小补采方案（未执行）

如用户批准新诊断实验，先将诊断 tap 限定为预先固定的 period 5 / beam 50 / az 52 / range 1065–1197，记录各 role 的筛选前 F1/F2 复数值、有效掩码、相位中心、逐单元残差及保留掩码；对 ON20、ON30 和 target-free OFF30 都采集，即使组通过也不能省略。同步留存 TO 作为目标专属观察；其目标专属输入包含全部 20 条目标，不能未经额外验证将作用归给 T010 一条轨迹。真值仅用于离线解释，不能进入 estimator 或支持选择。

1. 使用上述两个原始场景模板、seed1932001、2.5 m/s、+4 ns、20 targets、10 periods、beam 50 及冻结 V2 方法配置；Stage2 使用已核对身份的原二进制。若需生成完整 10 周期才能保持随机子流和第 5 周期时序，则保留完整生成，不把一个新建单周期场景冒充原第 5 周期。
2. 使用原源码基础上的**诊断专用** Production 构建：只加固定位置的旁路输出，记录新的源码与二进制身份。原始 Production 二进制及算法、`min_support=8`、相位门限、CFAR 和 beam 门保持不变。输出目录独立，显式保留 raw 数据至审查结束。需先补齐适当的软件检查，证明 tap 不改变原有处理结果。
3. 在相同底层场景身份及 ON/OFF/TO 配对门通过后，核对 replay 的原始背景和目标输入身份、运行 XML、beam/period 映射；在可比前提下检查原失败组的 ON20=25、ON30=7、OFF30=55 能否重现，记录数值容差和任何差异。原 raw hash 可用则核对；若不可用或重放未闭合，则报告差异，不能声称重现原现场。
4. 对每个单元计算 `z=F2·conj(F1)`、有效性、`arg(z)`、组中心和残差；比较 ON/OFF 的支持交并集、相位中心移动、单元自身相位变化及 TO 空间响应。ON=OFF+TO 的闭合只在实际检验过的处理阶段成立；非线性预处理后不得预设该关系。
5. 在一个 seed 的诊断范围内报告可识别项和仍不能识别项；仅在机制证据支持时提出版本化、保持 `min_support=8` 的候选方法契约。支持数达标不等于目标保持或任务性能改善。

若身份或配对门、原参考重现、数据完整性或非侵入性检查失败，停止归因并保存失败记录；不修改原冻结矩阵，不恢复大规模 screening，不训练 AI。具体命令需在诊断 tap 的实现及接口确定后冻结，现有 `run_r22_cuda_pilot.py --keep-raw` 可以保留生成的 `.bin`，但现有 failure-only 钩子本身不能完成上面三条件的逐单元采集。

## 状态与复核

`IMPLEMENTED`：离线组级对照；`EXECUTED`：读取保存的 gamma、源码、清理账及二进制身份，未进行新实验；`EVALUABLE`：组级 support/coherence 与直接门控，逐单元物理机制不可评价；`PERFORMANCE_VERIFIED=false`。本次结论为 `INCONCLUSIVE / STOP_AT_GATE`，离线阶段已达到“现有资料不能唯一归因并提出最小补采范围”的退出条件。

在仓库根目录执行：

```bash
python3 docs/research/phase_r/R22/support_failure_diagnostic/compare_groups.py
```
