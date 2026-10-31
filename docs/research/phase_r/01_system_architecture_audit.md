# Phase R0：当前系统架构审计

审计日期：2026-10-18；源码基线：`1dcea9250c3cbe8655723a210e29638d11d6c095`（`codex/unknown-system-error-next-stage`）。范围：当前配置、协议输入、生产处理顺序、定位/速度、跟踪输出，以及截至本审计可追溯的校准 pilot。本文是源码与历史证据审计，不是新实验，也不构成算法性能结论。

## R0 结论

1. 当前主口径是四路协议 IQ 先做几何/相位校正，再按 `(1,3)`、`(2,4)` 融合成 F1/F2；下游 CSI 是双通道处理。四个物理接收通道不等于四个生产 CSI 自由度。配置和实现见 `config/gmti.xml:192-214`、`src/dbs/NewProtocolReader.cpp:62-168, 574-619`、`src/gpu/gpu_kernels.cu:1375-1495`。
2. `enable_dbs_fusion` 是扫描波束级 DBS 缓存/成像与质量门，不是上述四路 IQ 融合。当前静态 XML 打开此开关，PIPE 主控因此调用 `processBeamsParallelFusion`；其 `valid=0/1` 首先是扫描处理成功数/质量门，不是检测性能指标。必须结合逐波束失败 stage 追根因，不能单凭该计数归因于 CSI 性能。见 `config/gmti.xml:34-35`、`src/pipe/MainCtrl.cpp:748-770`、`src/processPeriodsParallel.cpp:468-485`。
3. 流程在 CSI 之后还有 CFAR、聚类/筛选、定位与径向速度估计、检测写盘、在线关联和 PIPE 载荷。配置的 `pf` 只是阈值设定；经验 Pfa 必须以有效 CUT 为分母。历史 pilot 尚未闭合有效 CUT、完整虚警瀑布和 ID-switch 归因。见 `src/processOnePeriod.cpp:2615-2660, 3131-3144`、`docs/evidence/equivalent_vs_physical_pilot/report.md:3-18`。
4. pilot 配置中的 `6.7/12 m/s` 是目标 ENU 速度向量的模，不是直接指定的径向速度。场景脚本按固定方向生成东/北分量；仿真真值再按目标—平台 LOS 投影成径向速度/Doppler。任何把这两个标量直接当作生产径向速度的比较都不成立。见 `scripts/run_track_manager_e2e.py:174-177, 497-501`、`simulator/stage2_statistical_sim/stage2_config.cpp:1303-1326`。
5. 现有证据不足以认定 AI 有必要，也不足以认定它不可能有价值。较新的 pilot 明确为 `NEED_MORE_SINGLE_ERROR_PRODUCTION_EVIDENCE`，且其 manifest 源码提交是 `6fa1a5f…`，不同于本次审计的 `1dcea92`；该证据只按其原始范围引用，不回写为当前 HEAD 的运行结果。见下文“历史运行证据”。

## 数据与处理链

```text
4 路协议 IQ + 头部/位置/时间
  → CPU 解码或 packed-GPU 预处理
  → 几何补偿、残余孔径相位对齐：(C1+C3)/2 → F1；(C2+C4)/2 → F2
  → 脉冲压缩 → 方位抽取 → 多普勒中心/动态支撑域
  → 零移位 FFT/DBS → 原始 P38 拟合 → 距离向相位校正 → 第二次 P38
  → 理论整数 PRT 对齐与距离相位校正 → 最终双通道 CSI
  → CFAR → 连通/小簇及目标筛选 → 定位/径向速度 → detection record
  → 扫描后过滤/去重 → 写出本周期检测 → TrackManager 当前命中关联
  → 仅合格的本周期航迹载荷进入 PIPE
```

上述顺序在 `src/processOnePeriod.cpp:2060-2138, 2172-2216, 2360-2436, 2473-2615, 2615-2660, 3123-3144, 3328-3425, 3574-3699`。`enable_dbs_fusion=1` 时，外层扫描路径改走 `processBeamsParallelFusion → processOnePeriodFusionCache → relocateFusionDetections`；失败波束会清空该槽，且需满足 `ceil(min_valid_beam_ratio × beam_count)` 质量门。见 `src/pipe/MainCtrl.cpp:748-770`、`src/processPeriodsParallel.cpp:323-355, 407-449, 468-495, 538-551`。这个扫描级融合与四通道 F1/F2 预处理是两个不同层次。

### 四通道几何与信息边界

当前 `config/gmti.xml` 的通道相位中心为：C1/C3 的沿航向坐标均为 −0.085 m、垂向分别为 +0.085/−0.085 m；C2/C4 的沿航向坐标均为 +0.085 m、垂向分别为 +0.085/−0.085 m。两对各自先做公共发射、接收路径的几何相位补偿，再估计逐距离门的孔径残余相位，最后复数平均；垂向两行被合并，F1/F2 保留约 0.17 m 的沿航向相位中心间距。几何相位系数使用 `2π/λ`，与公共发射、双接收模型一致。配置见 `config/gmti.xml:202-214`；CPU 路径见 `src/dbs/NewProtocolReader.cpp:62-168, 574-619`；GPU 路径见 `src/gpu/gpu_kernels.cu:1375-1495`。

因此当前生产对照应写成“四路输入协议 → 两路融合 CSI”，而不是“四通道 STAP”。四通道 STAP/JDL 是不同信息条件的研究分支，不能拿它与 F1/F2 的差异单独归因于算法优劣；当前方向审查也明确保持 `native_4ch_stap=false`（`docs/AI_CSI_41_Production复校准接入点与信息边界.md:7, 40`）。

### 静态 XML 与 pilot 运行配置不可混称

| 项目 | 仓库静态 `config/gmti.xml` | Stage2 生成的 pilot 运行时 | 对解释的影响 |
|---|---|---|---|
| 载频 | 16.4721130769 GHz（L47） | 场景模板 16.0 GHz（`simulator/scenarios/beam50_one_targets_continuous.run.json:6-14`） | 数值计算必须引用具体运行配置，不能拿静态 XML 替代 pilot。 |
| 协议通道数 | 4（L192） | 场景模板初始值为 2（`simulator/scenarios/beam50_one_targets_continuous.run.json:16-18`），但 pilot 场景构建器会在运行前扩为 4 并写入四路几何（`scripts/run_delay_stage1_formal.py:248-275`）；production pilot runner 调用该构建器（`scripts/run_production_hierarchical_calibration_pilot.py:1528-1534`） | 不能把模板原始值 2 当成历史 pilot 实际输入为两路的证据。该扩展逻辑也存在于历史 pilot 源提交 `6fa1a5f…`；但 compact evidence 未收录逐 case 生成的 scenario JSON/runtime XML，故这里只能确认被绑定源码的配置路径，不能声称已逐产物复核。 |
| 通道 CSI 对齐 | `integer_equivalent_delay`（L214） | 电子扫描写 `integer_equivalent_delay`；机械扫描且相位中心旋转时写 `none`（`stage2_config.cpp:694-700`） | `Config` 结构体默认 `none`（`include/config_structs.hpp:558-563`），默认值、静态配置、生成配置有意分层。 |
| 低径向速度过滤 | 开启，阈值 2.25 m/s（L219-220） | Stage2 writer 关闭（`stage2_config.cpp:704-706`） | pilot 检测数不能按静态 XML 的过滤条件解释。 |
| 速度/定位分支 | 应以生成文件为准 | 开启 motion compensation、将补偿应用于定位、使用 `root1d` 和联合速度模糊搜索，范围 ±50 m/s（`stage2_config.cpp:721-748`） | pilot 的速度估计设置不同于仅看静态 XML 可推断的设置。 |

此外，静态 XML 的 `enable_dbs_fusion=1` 和质量比例 0.95（`config/gmti.xml:34-35`）决定扫描级 DBS 路径；Stage2 生成器是否覆写该门需结合实际生成 XML/日志核验，不能仅凭 `wavepos_parallel` 推断关闭。

通道数疑点的源码复核：历史 formal pilot manifest 绑定的源提交 `6fa1a5f…` 中，`run_production_hierarchical_calibration_pilot.py` 调用 `run_delay_stage1_formal.build_scene_variants()`；后者先用 `_scenario_template()` 将初始模板通道数从 2/4 统一设为 4，并附加 `_FOUR_CHANNEL_GEOMETRY`，Stage2 再从该场景解析通道数并写入运行 XML。`run_track_manager_e2e._protocol_layout_from_scenario()` 同样要求通道数为 4；生产 `NewProtocolReader` 在四通道融合开启但输入不足 4 路时会 fail-closed（`src/dbs/NewProtocolReader.cpp:223-226`）。因此，现有证据不支持“12-case formal pilot 因模板初始为 2 路而绕过四路融合”的推断；仍应把缺少逐 case 生成配置副本列为产物级复核限制。

## CSI、CFAR 与后处理

- CSI 主调用前，代码以零 PRT 移位建立双通道原始 Doppler/P38 参考、校正距离初相；随后才按理论整数对齐并执行最终 CSI。生产路径还保留一次 P38 斜率拟合和幅度均衡复数相减；应以 `src/processOnePeriod.cpp:2420-2436, 2473-2523, 2552-2585` 与 `src/gpu/gpu_kernels.cu:3925-3994` 为序。
- `research_calibration_enable` 为 false 时，研究校准 tap 不进入，默认生产分支保持不变；打开时才校验并选择 `ordinary_subtraction / scc / ddc / robust_ddc / robust_ddc_rb` 等研究方法。见 `src/gpu/gpu_kernels.cu:3987-4055`、`include/config_structs.hpp:626-658`。pilot 方法配置可见 `configs/research/production_hierarchical_calibration_delay_pilot.json:26-145`。
- 静态 XML 配置 `pf=1e-7`、GO-CFAR、guard=4、background=16（`config/gmti.xml:56-60`）。运行时诊断只有在 `valid_cut_count > 0` 时才将 `hit_cut_count / valid_cut_count` 标为可评估；否则 hit rate 为 NA。见 `src/processOnePeriod.cpp:315-355`。configured Pfa 不是实测 Pfa。
- CFAR 后还存在动态/全带/分离带选择、簇过滤与目标选择；具体分支受运行时配置影响。检测 records 含 P38/速度/模糊状态等诊断字段，但“字段被写出”不等于该方法已在真值上闭环。

## 速度、定位与 PIPE 语义

pilot runner 把配置标量 `target_velocity_mps` 转为 `ve=-0.894427191·speed`、`vn=-0.447213595·speed`，所以 `6.7/12` 是 ENU 地速模。仿真器根据 beam/几何生成 LOS 单位向量 `u_r`，再算 `v_r = ve·u_r,e + vn·u_r,n`，真值 Doppler 为 `sign·2v_r/λ`（`stage2_config.cpp:1312-1326`）。生产侧速度补偿又把 motion Doppler 与几何 Doppler 分开，输出 `v_radial = af_motion·λ/(2·sign)`，并记录模糊阶数/候选状态；定位是否应用补偿另受 `motion_comp_apply_to_localization` 控制。见 `include/motion_comp.hpp:1251-1290`、`src/processOnePeriod.cpp:3348-3425, 3582-3629`。具体运行的投影值和 alias 计算放在 R2 统一按场景配置推导。

在线 PIPE 不会把全部原始检测或 coast 预测直接当作当前目标：有效 result id 时，`trackModuleOnline` 读取当前 result id 对应的检测并调用 `TrackManager::updateRawDetections`（`src/trackModule.cpp:600-635`；无效 id 会回退旧路径）。协议输出需满足 `Confirmed + matched_this_frame + last_result_id == 当前 result_id + 当前命中索引有效`。默认 measurement 模式载荷的位置等观测量取该索引对应的本周期检测，speed 保留航迹多周期估计；prediction/filtered 是显式状态源选项。见 `src/TrackManager.cpp:568-645, 3078-3112`。`MainCtrl` 在本周期检测写盘后才调用在线跟踪并组包（`src/pipe/MainCtrl.cpp:872-895`）。

## 方法与当前证据状态

状态定义：`IMPLEMENTED`=源码有路径；`EXECUTED`=有对应实际运行记录；`EVALUABLE`=指定指标与分母/闭环满足判读条件；`PERFORMANCE_VERIFIED`=有匹配条件、设备状态和重复计时可支持性能结论。四者不可互相替代。

| 方法/条件 | IMPLEMENTED | EXECUTED（历史记录） | EVALUABLE（当前证据范围） | PERFORMANCE_VERIFIED |
|---|---|---|---|---|
| C0 Production Current | 是，生产 CSI 默认分支 | 是；历史 pilot 的 12 个 CUDA case 含当前对照 | 部分处理层有记录；端到端虚警/目标/航迹闭环未满足 | 本审计未见可支持方法性能优劣的受控基准 |
| C1 ordinary subtraction / C2 SCC / C3 robust DDC | 是，研究校准开关路径 | 是；历史 pilot 有对应分支 | 共 180 条 Gamma reference rows 可评估；不等于下游检测收益已评估 | 未验证 |
| C4 robust DDC-RB | 是 | 是；历史 pilot 有对应分支 | 72 条 C4/P2/PKR reference rows 因 scan beam quality gate 而 `NOT_EVALUABLE` | 未验证 |
| P1 blind physical delay + Current / P2 blind physical + C4 | 是，pilot 中有相应输入校正/方法定义 | 是；历史产物分别有 P1/P2 分支记录（`docs/evidence/equivalent_vs_physical_pilot/clutter_metrics.csv:132, 145`） | blind recovery 与组合收益 `NOT_EVALUABLE`；不得据此声称物理校准有效或无效 | 未验证 |
| PK/PKR known-error correction upper bound | 仅 evaluator 用真值的上限条件；不作为盲估计器输入 | 是；历史产物分别有 PK/PKR 分支记录（`docs/evidence/equivalent_vs_physical_pilot/clutter_metrics.csv:171, 184`） | A0/PK/PKR residual closure 未闭合，PKR CSI tap 缺失 | 未验证 |
| AI/MLP/Router/native-4ch STAP | 本轮主链无获准训练/选择路径 | 无本轮 AI 运行 | 不适用；AI gate 尚未打开（`docs/AI_CSI_41_Production复校准接入点与信息边界.md:40`） | 未验证 |

历史 pilot 文档记录：12 个 CUDA case，误差 ±4 ns、seed 101/202/303、地速标量 6.7/12 m/s、SNR 30 dB、每 case 5 periods；180 条 C1/C2/C3 Gamma rows evaluable；C4/P2/PKR 共 72 条因日志 `valid=0/1 required=1 min_ratio=0.95` 不可评估。12 个 runner 返回 0，但全部 case 状态仍是 `completed_with_gaps`，`algorithmic_results_claimed=false`。causal triplet gate 通过不弥补 PKR tap、有效 CUT、虚警瀑布和 ID-switch provenance 的缺口。证据：`docs/evidence/equivalent_vs_physical_pilot/report.md:3-18`、`docs/evidence/equivalent_vs_physical_pilot/manifest.json:1-35`；审查快照将运行日期标为 2026-09-29，并明确该包基于 `1dcea92` 打包（`docs/research/phase_r_ai_direction_review_20261018/00_审查任务与当前快照.md:1-3`）。该 pilot manifest 的源码身份是 `6fa1a5f92c210c499a6d0112c05d065bb7f06dc9`（`docs/evidence/equivalent_vs_physical_pilot/manifest.json:8-12`），故不将上述执行状态伪装成当前 HEAD 的复跑。

## 未验证项与 R0 结论边界

- 本文没有运行新的 CUDA 实验，也没有对 CPU/GPU 输出等价性做新测试；当前实现路径的存在不能替代数值等价验证。
- 没有从历史 pilot 的每个生成 XML、完整 CUT 表、真值匹配、TrackManager association 与 PIPE payload audit 逐 case 重算所有指标；历史汇总中的未闭环状态按原报告保留。
- 静态 XML、Stage2 writer 和实际运行日志是不同配置层；后续 R3/R4 必须绑定具体生成 XML、输入、提交和产物。
- R0 只能建立系统和信息边界，不能单凭“研究方法已实现”、局部 Gamma evaluable 或历史 AI 方向报告推导出 AI 必要性。R1–R5 的文献、物理上限、评价有效性和最小可辨识实验仍是后续决策依据。

## R0 完成状态

- [x] 当前协议/配置、四通道融合几何、双通道 CSI、扫描融合分支、CFAR/定位/速度/TrackManager/PIPE 数据流已定位到源码。
- [x] 速度参数语义从 runner 配置追到 simulator LOS truth，再对应到 production radial-velocity 输出。
- [x] 历史 pilot 的执行与可评估范围、源码身份和限制已分开记录。
- [x] 未改动生产源码、未启动新实验、未清理或覆盖历史 scratch/output/审查包。
