# R18：信息公平性与独立参考协议

状态：`IMPLEMENTED`（执行前冻结）。任何无法满足的执行契约均作为失败/不可评估报告，不通过放宽信息隔离修复。

## 共同观测与处理链

所有 C0–C3 都从同一 Stage2 四通道 IQ 文件身份进入生产 `(1,3)/(2,4)` F1/F2 融合和相同 CUDA Production 下游。方法条件仅改变 CSI adapter 配置或明确声明的估计信息来源。逐分支记录实际 F1/F2 CSI tap；如果输入 SHA 或前 CSI tap 在方法间变化，则方法比较失去可归因性，先停止解释。

## 核心方法的信息集

| 方法/模式 | 允许的估计输入 | 禁止的输入 | H0/OFF 用法 |
|---|---|---|---|
| C0 Current | 当前分支生产 F1/F2 | 不适用 | 独立处理当前 OFF；观测结果不是经验 Pfa |
| C1 Gamma=1 | 固定常数，无数据拟合 | OFF/ON/TO/真值或已知失配不能参与估计（不设估计器） | 独立处理当前 OFF；无 Pfa 估计 |
| C2/C3 Mode-B | 该 paired case 的当前 ON 原始 F1/F2；只读算法所需生产观测 | 目标 truth、标签、目标位置、注入参数、known correction、TO/目标-only、配对 OFF、其它 seed 或未来帧 | 将 ON 估计出的固定 Gamma 应用于 OFF/TO 时，OFF 只作反事实/观测错误输出，不是独立 H0/Pfa |
| C2/C3 Mode-A | 当前配对 target-free OFF 的生产 F1/F2 | truth、目标标签、注入失配、TO/target-only、known correction | OFF 同时承担拟合和同 realization 评价；标记 `PRIVILEGED_PAIRED_OFF_REFERENCE` 和 reference-in-sample，不称 Pfa |
| C2/C3 independent secondary | 独立生成、target-free、相同冻结几何的 C+N 参考输入；先经同一 Production support 生成并审计 Gamma 文件 | 当前 evaluation ON/OFF/TO、目标真值、配对背景文件、目标位置、注入误差、未来信息 | 应用到新 evaluation case 的三角色；evaluation OFF 与 calibration seed 不同且输入 SHA 独立。即便统计同质，仍只是 Stage2 模型支持下的参考条件，不能自动宣称现实在线可部署 |

## 独立 secondary 的预注册定义

- 参考 RNG seed：909；正式评价 case：seed 606、速度 6.7 m/s、SNR 30 dB。
- 与评价数据使用相同平台/波束/距离/area clutter power、texture、rho、热噪声、period 数、F1/F2 处理 support 和 Production binary；参考 `scene_mode=area_clutter_only`，无目标 truth，不读取主评价数据。
- 来源路径、resolved scenario、实际 Stage2 报告、参考 IQ SHA、Production XML/log、Gamma CSV SHA、support/group coverage、运行二进制 SHA 都单独留存。
- “随机 seed 不同”只证明模拟器 RNG 输入不同，不能单独证明统计同质。必须对照两组 Stage2 的面积杂波实测均功率、功率 CV、lag-1/lag-2 相关、可用 range/Doppler support、训练群组数及 Gamma 复值分布。如果出现系统性不匹配、失败组或 support 不同，则标 `NOT_EVALUABLE`，不得调参数补救。
- 即使可评估，该子矩阵仅给出独立模拟参考相对 Mode-B 的条件差值；需要现实 airborne 数据或合理的在线可获得参考机制，才能支撑部署性主张。

## 可归因比较

1. **同信息算法比较：** Mode-B C2 vs C3；Mode-A C2 vs C3；每个对照使用同一目标、场景输入、生产二进制和下游。
2. **方法/信息联合差异：** Mode-B C2/C3 vs C0/C1，报告时明确算法适配与 ON 拟合信息条件共同改变；不得单独声称为“纯算法收益”。
3. **额外参考的信息收益：** independent-secondary C2/C3 vs Mode-B 同方法/同评价 case；明确比较改变的是标定参考 realization。
4. **参考信息上界：** paired-OFF Mode-A C2/C3 vs Mode-B，仅能描述“paired target-free reference”与“当前 ON 观测”的差值；不能当在线部署结果。

## 禁止的结果表述

- `valid CUT hit fraction`、hit cell、cluster、Detection row 或 track row 都不是 Pfa。
- Mode-A OFF 同样本拟合/评估不作为 H0 虚警概率。
- Mode-B 的 OFF 输出使用了 ON 拟合信息，不能说它是独立无目标试验。
- 同 seed 的 OFF/ON/TO 是因果配对，不是独立重复数；正式 seed 数为 3，周期/像素不能伪装成独立场景数。
- 目标 CSI 改善只有在相同 truth opportunity 的检测/参数估计闭环下才可升级为 GMTI 任务收益。
