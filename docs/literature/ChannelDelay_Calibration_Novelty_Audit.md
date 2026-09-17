# Channel-delay calibration novelty audit

**检索日期：** 2026-09-17

**审计范围：** 经典多通道校准、multichannel SAR / SAR-GMTI、airborne GMTI/STAP，以及约近 5–10 年的通道不一致/幅相/时延校准工作。
**证据规则：** 技术判断只依据论文正文、论文摘要页、DOI 页面或出版方/期刊页面；下表只做方法边界和证据边界的转述，不复制长段原文。检索结果不是系统综述，也不构成“未发现即不存在”。

本阶段固定开关：`ai_training=false`、`router_enabled=false`、`native_four_channel_stap=false`。

## 当前 Paper-1 的比较对象

本仓库的待审计方案是固定的生产等效链路：

```text
4ch protocol IQ → F1=(C1+C3)/2, F2=(C2+C4)/2
→ 2ch CSI → production GO-CFAR → clustering/positioning
→ TrackManager/PIPE
```

当前阶段把 channel delay 作为可控的多通道误差，使用明确的正延迟符号约定、target-free 观测估计候选、inverse-closure C0–C4、A0/A1/A2/A3 条件和 physical-scene-block 层级统计。现有仓库证据仍是仿真与生产软件诊断；尚无硬件测量结果。以下“与本工作的差异”只描述证据口径差异，不宣称优先权。

## 文献对照表

| 论文（DOI/直接链接） | 平台 | 误差类型 | 校准目标/来源 | 是否 target-free | 方法 | fractional-delay 处理 | real-data 状态 | 下游 Pd/Pfa/tracking 证据 | 与本工作的差异 |
|---|---|---|---|---|---|---|---|---|---|
| [Airborne GMTI experiment based on multi-channel synthetic aperture radar using STAP](https://doi.org/10.1016/j.ast.2011.07.007) | airborne multichannel SAR-GMTI | 波束/阵列流形、通道不平衡、杂波异质性 | 杂波功率/强目标相位等阵列校准判据 | 未声明为 target-free；摘要和方法包含杂波与目标判据 | 自适应阵列流形校准、功率/相位准则 | 未见显式 fractional-delay 逆闭环 | 有 airborne 实验 | 有检测/定位实验语境；未见当前口径的 paired Pd/Pfa/TrackManager 审计 | 关注 STAP/GMTI 阵列校准；没有本工作所需的 F1/F2 固定协议、时延符号/闭环和全链路航迹归因 |
| [Channel balancing algorithm in multichannel wide-area surveillance systems](https://doi.org/10.1049/iet-rsn.2013.0032) | X-band airborne 四通道 WAS-GMTI | 系统性方位相位、幅度、内校准范围/方位不平衡 | 实际雷达回波、内校准和通道间干涉相位；实验含合作车辆 | 否/未明确；论文真实场景含移动车辆，且专门讨论移动目标影响 | 干涉相位排序/平滑区估计，配合幅度和 range 内校准；与 MC 对比 | 相位随 Doppler/range 处理，但未报告本工作式 fractional-delay 注入—逆校正 C0–C4 | 有 X-band 四通道真实数据与 MC | 讨论误警概率风险并展示通道平衡；未提供本工作定义的固定有效 CUT 分母、Pd/Pfa/track/ID-switch 瀑布 | 最接近“真实 airborne 多通道 + 下游 GMTI”背景，但误差参数与证据链不同；本工作还要求 target-free、生产 CFAR tap 和 TrackManager 关联审计 |
| [Two-stage channel calibration technique for multichannel SAR-GMTI systems](https://doi.org/10.1049/iet-rsn.2014.0038) | multichannel SAR-GMTI，airborne raw data | 通道卷积/频域不一致与图像域乘性误差，幅相/时频失配 | 两阶段数据/回波校准；论文使用真实 SAR 原始数据 | 未证明 target-free；校准来源和目标选择需按正文实验解释 | 频域卷积误差校正 + 图像域乘性校正 | 包含与频率相关的误差建模，但未报告独立 fractional-delay inverse-closure 和 packet/pulse 边界矩阵 | 有真实 airborne raw-data 验证 | 有 GMTI/CFAR/ATI 处理语境；未见完整 paired Pd/Pfa、valid-CUT 分母和航迹 ID-switch 归因 | 比较对象覆盖 SAR-GMTI 校准，但不是本工作的“固定 4ch→F1/F2→2ch CSI”协议，也没有当前审计要求的闭环/层级/生产诊断组合 |
| [Improved channel mismatch estimation for multi-channel HRWS SAR based on azimuth cross-correlation](https://doi.org/10.1049/el.2017.4085) | airborne HRWS multichannel SAR | range sampling-time error、常数相位/通道失配 | 通道间 azimuth cross-correlation / SCCC；依赖数据相关结构 | 未明确为 target-free | 相位解缠与 weighted least squares 等失配估计 | 明确涉及 sampling-time/range-time 误差；未见本工作式正负 fractional-delay 注入、逆校正和 C0–C4 报告 | 有 airborne real-data 语境 | 主要是估计/成像质量；未见直接 Pd/Pfa/tracking 证据 | 是“时间采样误差”相关先例，不能据此声称本工作首先处理 channel delay；本工作差异在生产下游和可审计闭环，而非仅估计公式 |
| [Measurement and Calibration of Amplitude-phase Errors in Wideband Multi-channel SAR](https://doi.org/10.3724/SP.J.1146.2012.01064) | airborne wideband multichannel SAR | 宽带幅相误差、频率偏置等 | 闭环空间辐射/内校准与频率偏置修正，属于系统校准链 | 否/未建立 target-free 结论；使用系统校准源/测量链 | 测量驱动的幅相校准与频率偏置修正 | 未见独立 fractional-delay inverse-closure | 有 airborne 实验 | 重点是成像/带宽合成；无当前口径 Pd/Pfa/tracking 审计 | 说明宽带通道校准已有工程先例；本工作不能把“宽带/通道校准”本身作为新颖性，而应限定为当前可复核的延迟观测与下游证据组合 |
| [Correction of Channel Imbalance for MIMO SAR Using Stepped-Frequency Chirps](https://doi.org/10.1155/2014/161294) | stepped-frequency MIMO SAR | 通道幅度/相位不平衡 | 强点目标外部校准 | 否；明确使用强点目标，不能作为 target-free 证据 | 由点目标估计并补偿通道不平衡 | 未见 fractional-delay 逆闭环 | 有仿真和 real raw-data 语境 | 主要报告成像/聚焦，不是 GMTI 的 Pd/Pfa/航迹指标 | 直接反例说明“通道自校准”可能依赖外部强目标；本工作把 target-free 作为待验证条件，不能将两者混写 |
| [Channel Phase Calibration for High-Resolution and Wide-Swath SAR Imaging with Doppler Spectrum Sharpness Optimization](https://pmc.ncbi.nlm.nih.gov/articles/PMC8914916/) | high-resolution wide-swath multichannel SAR | 通道相位不一致 | Doppler spectrum sharpness / 成像数据目标函数 | 未明确为 target-free；优化的是成像观测目标函数 | 以 Doppler 频谱尖锐度为目标的相位校准 | 研究相位不一致，不等同于显式 fractional-delay 注入与逆恢复 | 含仿真/实验性成像验证；需按论文实验段落区分数据来源 | 成像模糊/旁瓣/ambiguity 类指标；无直接 GMTI Pd/Pfa/tracking | 代表近年数据驱动的相位校准路线；与本工作在任务目标、下游生产链和 closure/scene-block 统计上不同 |
| [A Unified Algorithm for Channel Imbalance and Antenna Phase Center Position Calibration of a Single-Pass Multi-Baseline TomoSAR System](https://doi.org/10.3390/rs10030456) | single-pass multi-baseline TomoSAR | 通道不平衡与天线相位中心位置误差 | 阵列/成像数据，联合模型分离 channel 与 APC 误差 | 未声明 target-free | Fresnel/几何模型联合校准，分离通道与 APC | 几何位置误差不是本工作定义的 fractional-delay closure；未见 C0–C4 | 有真实阵列 InSAR 数据语境 | 成像/层析质量；无 GMTI Pd/Pfa/tracking | 说明“通道误差与几何误差耦合”已有邻近研究；本工作需把差异限定在当前固定协议、可逆时延实验和 production audit |
| [A Novel Channel Inconsistency Calibration Algorithm for Azimuth Multichannel SAR Based on Fourth-Order Cumulant](https://doi.org/10.1109/JSTARS.2023.3285083) | azimuth multichannel SAR，含 GF-3 与 airborne 四通道数据 | 相位不一致与 along-track position coupling | 高阶累积量和实测多通道数据 | 未证明 target-free；使用成像/回波统计结构 | fourth-order cumulant 与迭代校准 | 讨论相位/位置耦合；未见显式 fractional-delay 正负注入及逆闭环 | 有实测空间/airborne 数据 | 主要是图像重建、假目标/成像伪影；无完整 Pd/Pfa/TrackManager 证据 | 是较新的通道不一致先例；不能据此否定本工作的工程审计组合，但也不能宣称“首次通道不一致校准” |

## 结果和可安全使用的表述

1. **不作 first/novel claim。** 本审计足以证明“多通道幅相/时间采样/通道不一致校准”“airborne SAR-GMTI 通道平衡”“用真实回波或成像统计量做校准”均已有文献先例。因此 Paper-1 在没有更广泛、可复核的检索和硬件证据前，不得写“首次”“首个”“从未有人处理”。
2. **可写成 potential contributions，且必须绑定当前证据。** 候选表述应限定为：
   - “在固定的 4ch protocol IQ→F1/F2→2ch CSI 生产链上，评估 target-free channel-delay 估计的可行性，并用明确正负号、包/脉冲边界和 inverse-closure C0–C4 审计可逆性”；
   - “把 channel-delay 误差的 A0/A1/A2/A3 恢复空间与 production GO-CFAR、TrackManager 关联和 ID-switch 归因放进同一可追溯证据链”；
   - “以物理 scene block 而非独立 delay 行作为层级统计单位，并显式保留 missing/duplicate/NOT_EVALUABLE 状态”。
   这些是**潜在贡献/工程证据组合**，不是已验证的新颖性结论；最终是否构成研究贡献要等 Formal-v2、硬件验证和同行对比完成。
3. **不要把 estimator-only 结果升级成系统结论。** 现有文献常在成像质量、通道平衡或估计误差层面验证；当前仓库的材料性判断还要求有效 CUT、Pd/Pfa、TrackManager/PIPE 和硬件证据。缺失项应保持 `NOT_EVALUABLE`，不能用文件存在、退出码或 estimator RMSE 单独替代。
4. **关于 fractional delay 的措辞要收窄。** 邻近论文对 sampling-time/range-time 或频率相关误差已有处理，但本审计未核实有论文同时报告当前定义的正负 fractional-delay 注入、逆校正、packet/pulse 边界和全链路 closure。因此只能说“当前证据组合尚未在已审来源中得到核实”，不能写成“文献中没有”。

## 来源与复现记录

- 搜索入口和正文页均为上述 DOI/出版方链接；最后检索日为 2026-09-17。
- 复核时应优先打开 DOI 对应的出版方页面，检查版本、实验数据来源和指标原文；本表的“未见/未明确”是审计记录，不是对论文全文的否定性证明。
- 本文件不改变 Paper-1 结果、不修改 Formal-v1/v2 产物，也不引入 AI/Router/STAP 实现。
