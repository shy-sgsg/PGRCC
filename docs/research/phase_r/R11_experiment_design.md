# R11：残余通道时延对测角与定位精度的因果验证设计

冻结日期：2026-10-24（Native CUDA Production 运行前）
基线提交：`911a7dec3316c87179e2e5d54bae10d9d4bf6a0e`
分支：`codex/unknown-system-error-next-stage`

## 研究问题与边界

只检验 seed303、period 1、ON 连续面积地杂波输入中，通道2固定时延校正量的变化是否能因果解释目标 cell `(row=48, col=2154)` 的 CTDR 相位、测角和 EN 定位变化。输入使用 R8 已生成的 Stage2 B/ON 原始四通道协议 IQ；不重跑 Stage2，不增 seed，不改 CFAR、关联或处理参数，不训练 AI。

六个校正量按注册顺序运行：`3.009965, 3.209965, 3.409965, 3.609965, 3.809965, 4.000000 ns`。仅把校正算子作用于物理通道2（协议通道索引 1）；四通道融合仍按 `(1,3)->F1`、`(2,4)->F2`。4 ns 是 evaluator-only 的已知输入参数，不进入任何盲估计或方法选择。

## 冻结输入、程序和生产路径

- A1 ON period 1：`outputs/phase_r8_unknown_delay_multiseed_20261023/seed303/cases/base__seed_303__v_p6d7__snr_p30__delay_p4ns/scenes/A1_ON/data/stage2_statistical_newprotocol_period_0001.bin`，49,204,480 bytes，SHA-256 `f7506d535f54e77da8f74843b7cf2e7506e656f45d2430ab81ee6bfb6c236a0f`。
- A0 ON period 1 参考：同目录 `scenes/A0_ON/data/stage2_statistical_newprotocol_period_0001.bin`，49,204,480 bytes，SHA-256 `8f2e089f89ad29080146837cd93d964e29ed1daf6e39b17a2e472a2f42905b81`。
- 已有 R8 Known Correction ON period 1 输入：`inputs/A2_Known_error_correction_upper_bound/ON/prepared_inputs/A2_Known_error_correction_upper_bound/period_0001.bin`，49,204,480 bytes，SHA-256 `0d31a9630a1e2583ee86d6bf52afde3bdf91de7708aaaaa787a94411a42536bd`。它是 A1 原始文件应用 4 ns 逆算子的独立复现参考。
- 目标真值：同场景 `scenes/A0_ON/truth/truth_targets_by_beam.csv`，period 1 / beam 50 / `stage1_target_1`，EN `(429650.329794, 4425907.59262) m`，truth range bin 2154，真值方位 `38.1825478958 deg`。A1 ON 对应行必须与 A0 真值位置、range bin 相同，否则停止性能评价。
- A0 production 对照：R8 `production/A0_Ideal_ON` 的 R7 Production period 1；输出目标 cell 为 `(48,2154)`。R11 manifest 将保存该参考 CSV/CSI 图的路径及身份。
- Native CUDA 二进制：`build_r7/GMTI_pipe_core`，冻结 SHA-256 `de3026ed4f1263be4143dcbcd10ccbb5e53b4be357e21906e1bf19ea0ec29d00`。`USE_COREX=OFF`。
- Production XML 模板：A0 ON `temp_config_stage2_period_0000.xml`，与 R8/R9 生产分支相同；每分支只改输出路径和启用 CSI tap。R11 单文件输入使用 `--local-test 2=<校正输入>`：输入文件内部 period index 保持 0，forced result id 2 由生产代码映射为 `stage2_period_id=1`。
- 四通道协议：float32 IQ，4 通道，pulse length 11820，130 packets，采样率 60 MHz；校正模型与 R9 一致：Stage2 零延拓线性分数延迟逆算子 `x[n]=(y[n]-d*x[n-1])/(1-d)`、`x[0]=0`、`d=τ·fs`。
- 每个校正量独立启动 Production，避免跨条件 TrackManager 状态串扰。单 period 不形成 confirmed track；只评价实际 detection、CFAR、生产 CTDR/几何结果和 lineage，不以航迹确认作为成功条件。

## 预注册量化指标与门控

### 输入和处理链正确性

1. 每个校正文件必须有 130 个完整协议 packet，文件大小与源文件相同；packet header、通道1/3/4 必须逐字节不变；只有通道2 IQ 可变；校正输出全部有限。
2. 保存每个 corrected input 的 SHA-256、校正量、算法、源文件身份、packet 数和校正函数返回记录。4 ns 输出应与 R8 Known Correction seed303 ON period 1 的 prepared input SHA 相同；若该历史输入不可读，则将此项记为未评估，不改用其他已知量替代。
3. 以 A0 period 1 原始协议 IQ 为参考，计算物理 ch2、F1、F2 的复数相对 RMS：`sqrt(mean(|candidate-reference|²)/mean(|reference|²))`，同时报告复数最小二乘增益和相位。F1 是未修改通道组的控制量；F2 是受 ch2 校正影响的主量。沿用 R9 既有函数的 `1e-3` 相对 RMS，要求 4 ns Known 的 F1 与 F2 均闭合。已有 R8 Known 输入审计值为 F1 `0`、F2 `9.0822e-5`，此处仅用于预先论证门槛，不是 R11 结果。其他五点用于曲线，不按此 Known 门槛作通过/失败判定。
4. 校正量、XML 语义配置、beam 50、处理 stage、二进制 hash 和调度必须相同；唯一的科学变量为校正后的协议 IQ。不得更改质量门槛或关联设置。

### CUDA Production 和结果完整性

每一分支须留存实际命令、runtime XML/config dump、完整 log、CSI tap、CFAR geometry/numerical diagnostics、cluster/detection lineage、detection CSV、TrackManager debug 和源码/二进制身份。有效 Production 证据需同时满足：进程退出码 0、runtime XML 与 config dump 存在、日志有 CUDA new-protocol preprocess 记录、CSI cancellation/CFAR/timing stage 记录、非空 CSI summary、CFAR geometry 中至少一个可评估且 valid CUT 数大于 0 的分支。否则停止该点之后的算法解释，先标记并定位生产路径失败。

### 误差传递指标

- 物理输入：ch2、F1、F2 对 A0 的相对 RMS、复数增益幅相；校正量对数值闭合的排序趋势。
- CSI：从 Production `csi_after_real/imag` tap 读取固定 cell `(48,2154)` 复数值；相对 A0 报告复数比值、幅度比、相位差。若数组形状/坐标与注册 cell 不符，标为不可评估，不猜测索引。
- Detection：按真实 Production 输出检查固定 cell `(48,2154)` 是否出现；记录实际候选 cell、CFAR margin、cluster/detection lineage 和完整 detection CSV。若候选变化，不强制关联；候选变化时只把实际输出分类记录。固定 cell CSI 是只读诊断，不替代 Production detection。
- CTDR/几何：对 Production 实际输出的注册 cell detection，读取该行 `ctdr_raw_phase_rad`、`phase_rad`、`range_phase_correction_rad`、`af_used_hz`、生产 Jacobian、`theta_used_deg` 和 EN 位置。相位差统一使用 wrap 到 `[-π,π)`。若冻结一对一真值评价器将目标匹配到另一个 cell，注册 cell 的参数只标为 `FIXED_CELL_DIAGNOSTIC_ONLY`，实际匹配 detection 另外保留。
- 角度与位置误差：依 `evaluate_real_parameter_localization.py` 的既有定义，以 platform EN 到 target EN 与 platform EN 到估计位置的方位差计算 180° 周期角误差；同时计算 EN 2D error、truth-LOS cross-range 和 range error。只有冻结评价器实际匹配的 detection 可作为 target-performance 数值；未匹配的 `(48,2154)` 生产 detection 可以相对 truth 作诊断，但不得记作正确目标检测或用于替代匹配结果。
- 局部 Jacobian 闭合：以 A0 的 `loc_jacobian_crossrange_m_per_phase_rad` 预测 `Δcross-range = J_A0·wrap(φ_i−φ_A0)`，与同一个实际 Production cell EN 位置直接投影得到的差比较。预注册闭合容差为绝对值 1.0 m（R10 历史同 cell 残差最大 0.35 m，留出数值与 CSV 舍入余量）。cell 未通过真值匹配时，此项明确为固定 cell 诊断，不是 target-performance 证据；此量不独立证明延迟是唯一相位来源。
- Known 对照：4 ns 与 A0 的固定 cell raw CTDR 相位差容差 0.001 rad，EN 2D 位置差容差 1.0 m，测角误差差容差 0.001 deg；容差根据 R10 历史 A0/Known 的近机器精度闭合向上留余量。若目标 cell 未在 4 ns Production 真实输出中出现，不进行该门判定。

校正量对 F2 closure、CSI 复响应、raw phase、固定 cell 几何诊断、评价器实际匹配 detection 的目标误差均分别报告。曲线趋势是本次单 period 可行性结果，不作统计显著性或泛化声明。若 4 ns Known 输入不闭合、F2 不闭合或生产链不可比，停止因果性能解读。评价器更换候选时，分别保留两类输出，不将固定 cell 诊断冒充真实任务结果。

## 误差传播和报告约定

报告的主要方向是校正量趋近 4 ns 时，物理 ch2/F2 残差、CSI target-cell 复响应、CTDR raw phase、角度误差和 EN 位置误差是否按预期趋向 A0。对每一级同时报告观测量与模型预测，不将解析响应、输入 closure、CSI response、production phase 或几何 Jacobian 相互冒充独立验证。

图表只呈现这六个注册点及 A0/A1/R8 已有参考；每张图标注单一 seed/period、点数、测量来源和候选是否保持。不得报告置信区间、显著性、Pd/Pfa 改善、跨 seed 泛化或 AI 优势。

## 输出和保留

本机输出根目录：`outputs/phase_r11_local_delay_scan_20261024/`。保留六个 corrected input、每个分支的配置/日志/CSV/CFAR/TrackManager 记录、manifest、压缩后的固定 cell 复数响应、汇总表、曲线图和失败现场。全图 CSI `.npy` 只作为本次抽取固定 cell response 的临时 Production tap；成功抽取并记录每个全图 tap 的大小与 SHA 后清除这些派生全图文件，保留紧凑 target-cell response 和 tap summary。失败分支的原始 maps 保留用于定位，绝不清理唯一失败证据。

本阶段完成即停止 R11，不自动启动 R12、确定性估计器开发或 AI 训练。

## Production 入口修订记录

初次 CUDA 启动实际记录了 `[ERR] 新协议回波不足以读取 beam=50 total_prt=130 need_end_prt=260`，发生在正式处理 stage 前。根因是 R11 单周期文件只有 130 个 PRT，而首版命令 `2@1=<file>` 同时把文件读取 offset 设为 1，生产 reader 因而试图跨到第二个周期。这是 runner 入口映射错误，不是时延、CUDA kernel、CFAR 或目标性能结果。失败的首版输入、XML、runtime dump、日志和 manifest 保存在 `conditions/delay_3.009965ns/`。

修订只改 local-test selector 为 `2=<file>`：`new_protocol_file_period_index` 留在 XML 的 0 以读取单文件；R7 `MainCtrl.cpp` 在未指定 forced period index、但 result id 为 2 时，将 `stage2_period_id` 设为 `forcedResultId-1=1`。校正点、输入、生产 binary、XML 语义设置、阈值、指标和容差全部不变。修订后的六点运行另存于 `attempt_02/`，旧失败现场保留。
