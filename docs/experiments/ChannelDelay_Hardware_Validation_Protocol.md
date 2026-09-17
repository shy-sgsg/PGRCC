# Channel-delay hardware validation protocol

**状态：** protocol only；不是硬件实验结果。

**版本日期：** 2026-09-17
**适用链路：** `4ch protocol IQ → F1/F2 → 2ch CSI → production GO-CFAR → clustering/positioning → TrackManager/PIPE`

## 1. 目的与当前证据边界

本协议用于验证四通道接收链中的相对 fractional time delay、相位漂移和包/脉冲边界行为，并把测量不确定度传递到 C0–C4、CSI、CFAR、检测和航迹指标。它不改变生产链、不启用 native four-channel STAP/JDL，也不引入 AI 或 Router。

截至本协议版本，仓库已有证据是仿真加生产软件诊断；没有 RF/IF 硬件测量结果、硬件实测 Pd/Pfa 或硬件 TrackManager 结果。任何硬件字段在设备、校准和原始捕获未具备前都必须写 `NOT_EVALUABLE`，不能用 CPU smoke、编译成功或仿真数字代替。

## 2. 可复核拓扑

```text
common RF/IF source or recorded 4ch stimulus
  ├─ path 1: programmable delay/phase/attenuation → ADC/IQ capture C1
  ├─ path 2: programmable delay/phase/attenuation → ADC/IQ capture C2
  ├─ path 3: programmable delay/phase/attenuation → ADC/IQ capture C3
  └─ path 4: programmable delay/phase/attenuation → ADC/IQ capture C4

common 10 MHz/reference clock + common trigger/PRI marker
                         ↓
             capture point before F1/F2 fusion
                         ↓
       protocol parser → F1=(C1+C3)/2, F2=(C2+C4)/2 → production chain
```

最小可用设备/连接关系：

1. 四条相互独立但由同一源驱动的 RF/IF 路径；每条路径要能单独记录增益、固定相位和可编程相对时延。
2. 共用参考时钟和共用触发；记录参考源型号、锁定状态、触发抖动规格、PRI/pulse marker 和 ADC sample clock。
3. 可编程 delay emulator，至少支持 `0`、正/负小 fractional delay、正/负 stress delay，以及跨 packet/pulse boundary 的 delay 状态切换。若仪器只能实现非负物理线延迟，负值必须用共同参考路径与数字/基带相对延迟实现，并单独记录该实现的频响。
4. 可控 phase/drift source 或等效相位调制路径，用于静态相位、慢漂移、脉冲间跳变和 packet 间跳变；延迟和相位不能只在软件 manifest 中伪造而不进入捕获样本。
5. 捕获点必须位于融合前，能够导出原始四通道 protocol IQ、逐样本 timestamp、packet/pulse 序号和触发标记。融合后的 F1/F2 只能作为派生审计输入，不能替代四通道原始捕获。
6. 独立功率计/频谱仪/矢量网络分析仪或等效可追溯仪器，用于校准每条路径的幅度、相位、群时延、带宽、噪声底和连接器/线缆变化。

## 3. 仪器校准与真值记录

在每个测试批次开始和结束时执行 reference-through、path-swap 和 zero-delay capture。校准记录至少包含：

- 每条路径的群时延曲线、幅频/相频曲线、噪声底、有效带宽、仪器量程和校准证书/到期日；
- delay emulator 的设定值、实测值、分辨率、残差和不确定度预算；
- phase/drift source 的设定值、实测相位、时间基准和漂移曲线；
- 共钟/触发锁定状态、采样率、ADC 满量程、中心频率、脉冲宽度、PRI、packet 长度；
- 温度、湿度、供电电压/电流、设备 warm-up 时间、机箱/线缆布置和连接器状态；
- 校准前后原始 reference capture 的路径与 SHA-256。

“truth”在本协议中指经独立仪器测量的相对路径参数，不指 emulator 的软件设定值。若实测 truth 不确定度大于该测试点要求的误差分辨率，参数估计和 recovery 指标写 `NOT_EVALUABLE`，同时保留捕获和不确定度预算以便复核。

## 4. 测试矩阵与重复设计

每个 operating point 至少固定一套 nominal、低信噪比、高速/宽 Doppler 或等效带宽、不同纹理/几何的输入；具体范围必须在预约设备后写入不可变 config，不能事后根据结果改范围。每个点包含：

| 维度 | 必测组合 |
|---|---|
| 相对时延 | `0`；正/负 small fractional delay；正/负 stress delay；正负相同幅度成对点 |
| 时间边界 | pulse 内；pulse 起始/结束；packet 起始/结束；跨 packet 状态切换；丢失/重复 marker 的拒绝路径 |
| 相位 | 固定相位；慢速漂移；pulse-to-pulse；packet-to-packet；与 delay 同时变化 |
| 幅度/频响 | nominal；受控幅度失配；可测的带内群时延/频响变化 |
| 环境 | 至少两个温度点或明确的环境窗口；每点记录供电和 warm-up；改变连接/重插后重复 zero-delay |
| 重复 | 每个点先做独立 warm-up/reference，再做预注册的重复次数；重复数写入 config，不能因失败后临时减少 |

每个 delay 点至少保留同一 stimulus 的 `OFF = C+N`、`ON = S+C+N` 和 `TO = S` 或等价可分离记录。若硬件不能安全产生目标分量，仍可完成 OFF/closure 子协议，但 ON、Pd、tracking 相关行必须 `NOT_EVALUABLE`。

## 5. C0–C4 与下游指标

### 5.1 参数/闭环层

- **C0 nominal identity：** zero-delay、zero-drift reference 的四通道协议解析、F1/F2 映射、timestamp 和复数布局正确。
- **C1 injection fidelity：** 由独立仪器测得的相对 delay/phase 与捕获信号中估计的注入值一致；报告 bias、repeatability 和 truth uncertainty。
- **C2 estimator：** 在 target-free OFF 输入上运行 D1/D2/D3 和注册的传统 baseline；estimator 不读取 delay truth，只在 evaluation 阶段使用 truth。
- **C3 physical inverse correction：** 使用估计值进行物理 fractional-delay correction；分别报告正、负和边界样本的残余 phase/coherence/时延；记录 correction source、fallback 和 packet/pulse mask。
- **C4 production closure：** 将 corrected F1/F2 送入真实生产链，逐字段保存 CSI、CFAR valid CUT、detection、TrackManager 关联和 PIPE reverse audit。没有真实 production input、有效 CUT 分母或本周期 confirmed match 时，明确写 `NOT_EVALUABLE`，不回退为 Current。

### 5.2 系统层指标

同一 paired stimulus 下分开报告：

1. delay bias/RMSE/STD/重复性和 residual uncertainty；
2. CSI coherence/residual 与 correction recovery；
3. CFAR valid CUT 数、false-hit 数和分母定义；理论 Pfa 与 empirical structured-clutter false alarm 不混写；
4. target detection Pd、target-period hit Pd、track Pd，分母和确认条件分别列出；
5. position/angle/velocity RMSE；
6. false detections、false clusters、false tracks、ID switches，以及每个 switch 的 TrackManager candidate/gate/lifecycle/detection source 归因。

所有 higher-is-better/lower-is-better 方向、单位、有效样本规则和 zero-denominator 规则沿用 `configs/research/channel_delay_stage1_materiality.json`。硬件阶段的阈值只有在来源属于 `system_requirement`、`engineering_judgment`、`historical_production_variability` 或 `literature-derived` 且在运行前冻结后，才允许成为 pass/fail；其余为 `exploratory_pending`。

## 6. 判定规则

### 6.1 设备与数据完整性 gate

以下是硬 gate，不是从数据倒推的性能阈值：

- 共钟/触发锁定、采样率、packet/pulse marker 和四通道 capture 均可追溯；
- 校准前后 reference-through、path-swap、zero-delay capture 完成且不确定度预算可审查；
- delay/phase 的实测真值和每次捕获的温度、供电、配置、版本、时间戳齐全；
- 正负 fractional delay、边界切换和异常 marker 至少各有一个有效捕获或有明确的设备限制记录；
- manifest 中 source commit、config/template/build hash、命令、attempt/resume、设备状态、输入/输出 hash 和 dirty 状态完整；
- 任意中断、过载、丢包、失锁、饱和、校准过期或不确定度超限都不得被标成通过。

### 6.2 科学性能判定

性能判定按预注册 materiality config 与 paired A0/A1/A2/A3 规则执行：`A2-A1` 是 known-error correction upper bound，`A3-A1` 是 blind estimated correction；恢复空间为零、指标缺失、valid CUT 分母缺失或无法配对时均为 `NOT_EVALUABLE`。本协议不预先发明一个 ns、dB、Pd、Pfa 或 ID-switch 阈值，也不允许用一次成功捕获推导全局硬件结论。

若硬件 C0–C3 通过但 C4 生产诊断缺失，只能报告“参数/闭环层通过、下游层未评估”；若 C4 有输出但 TrackManager 不能反查到当前周期 `Confirmed + matched_this_frame` detection，则航迹/PIPE 层失败或 `NOT_EVALUABLE`，不能用原始 detection CSV 代替。

## 7. 数据保留和清理

必须保留：root manifest、不可变 config、校准证书/不确定度预算、设备 snapshot、命令和日志、reference capture、每个测试点的 compact summary、失败/中断 manifest、代表性原始四通道捕获、CFAR/TrackManager/PIPE 审计和 cleanup ledger。完成 compact 校验前不得删除原始捕获。

compact evidence 经 hash 和行数复核后，可删除可重建的中间 FFT、重复的派生图、临时转换文件和未被 manifest 引用的 smoke 产物；删除前必须记录明确路径、大小、理由和删除时间。不得覆盖 v1/v2 或唯一失败现场。

## 8. 当前状态与最短复现入口

当前没有硬件运行结果，故本协议不能给出硬件通过结论。软件侧可复核入口仍是：

```bash
cd /home/shy/AIR/SAR+AI/project/PGRCC/.worktrees/unknown-system-error-next-stage
python3 -m pytest -q tests/test_fractional_delay_inverse_closure.py \
  tests/test_delay_stage1_hierarchical_stats.py \
  tests/test_channel_delay_materiality.py
```

硬件实验启动前必须新建独立输出根（不得复用 Formal-v1/v2），先运行一次代表性 zero-delay/reference case，再按本协议登记矩阵；任何设备、权限或额度不足只影响对应硬件 gate，不得将软件证据重命名为硬件结果。
