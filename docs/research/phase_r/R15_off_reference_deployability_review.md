# R15 Mode-A OFF reference 的信息边界与部署审查

## R14 中 OFF 的数据身份

R14 的三期配对输入审计都通过：每期 C2/C3 Mode-A 的 `OFF=C+N` input SHA 与 A0_ON background writer SHA 完全相同；ON SHA 与 Stage2 的 `S+C+N` writer 一致。三期 `truth_used_in_estimator=false`。C2/C3 的 OFF gamma 均由生产最终 F1/F2 支持域拟合，之后固定应用到 ON/TO。

同一份 target-free OFF 数据还作为 H0/OFF 检测评价输入。因此它是 **reference-in-sample**：可以报告这批样本上的输出计数，但不能把它当作独立背景泛化测试；unique hit fraction 也不能称为经验 Pfa。证据见 R14 `analysis/paired_input_audit.csv`、`analysis/input_hashes.csv`、`analysis/adapter_records.csv` 和两方法 runtime XML。

## 实际部署中的可用性

本仓库的仿真器可以逐期生成精确配对的 C+N OFF；R15 没有任何实机运行记录证明 GMTI 部署可以获得这种与目标 ON 完全配对、姿态/波束/通道状态/杂波统计匹配的空目标回波。不能把仿真器数据角色直接当成传感器能力。

传统 airborne STAP 的常规训练信息通常来自 CUT 周边的 secondary range cells 或其他候选 clutter-only samples，并需处理杂波非均匀及 moving-target / strong-scatterer contamination；这和另生成一份精确配对 OFF 的信息条件不同。DLR 基于真实四通道 airborne F-SAR 数据比较训练数据选择和更新，明确将 target contamination、训练数据选择及随空间/时间更新列为实际应用问题：[Training Data Selection and Update Strategies for Airborne Post-Doppler STAP](https://elib.dlr.de/126892/)。这提供的是可调查的传统信息获取路线，不证明本项目当前平台已能提供可用参考。

## 本地已存在的不依赖 paired OFF 的确定性方法

| 方法 | paired OFF 要求 | 当前证据状态 |
|---|---|---|
| C0 Production Current | 不读取额外 OFF reference | R13/R14 已执行；是 operational anchor，但与 C2/C3 存在参考信息差别。 |
| C1 ordinary subtraction，`Gamma=1` | 不拟合、无 paired OFF | Production adapter 已实现，selftest 覆盖基础算子；R14 未执行，不能宣称任务效果。 |
| C2 SCC / C3 Robust DDC Mode-A | 使用 OFF 拟合 reference | R13/R14 Production 已执行；当前可部署 OFF 来源未验证。 |
| synthetic/offline Mode-B | 从 ON 估计 | 仓库有确定性 synthetic pilot；不是 R14 Production 证据，ON 可能包含目标，不能作为 target-free reference 的已验证替代。 |

C1 是最直接、最简单且无需 paired OFF 的比较候选。它尚未被 R14 的 Production 数据验证，故当前不能说 C2/C3 的候选缩减必须依赖 OFF，也不能说无需 OFF 的 C1 已经足够。

## 判定

R14 的 H0 结果只描述 reference 拟合所用的相同 OFF 实现，不能支持背景泛化。真实部署可以研究邻近 secondary cells、移动窗口和目标/强散射筛除，但本仓库尚无平台时间、姿态、波束和统计匹配证据。

R15 后续若重开部署对照，优先考察 Production C1 对 C0 的不依赖 paired OFF 能力，并与已有 C2/C3 seed505结果作同一目标/检测/TrackManager 指标比较。先明确 target-free secondary-data 的现实获取方案；不自动扩 seed 或完整 Baseline 矩阵。
