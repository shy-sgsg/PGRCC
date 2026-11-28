# Phase-I 等效复校准与物理状态校准边界设计

## 目标

把 Phase-I 从“逐项识别每一种物理误差”调整为生产等效 F1/F2 双通道上的层次化
校准研究：先恢复必须的物理配准/状态，再用杂波驱动的复系数校准吸收剩余乘性
通道失配，最后以稳健两通道对消和目标/CFAR/TrackManager 安全性判定是否仍有
不可约去相关或需要物理状态恢复的残差。

本轮交付包括：研究边界文档、SCC/DDC/DDC-RB/Robust DDC/Robust DDC-RB 的可测试
传统 baseline、合成 Γ sanity、一个小规模且可复现的机制 pilot/evidence contract，
以及基于实际结果的 decision gate。不会训练 AI、启动 Router 或扩展 native four-channel
STAP。

## 研究不变量

1. 科学输入固定为四路 protocol IQ 经 `(1,3)`、`(2,4)` 融合得到 F1/F2；raw six-pair
   只用于诊断和交叉验证。
2. `Class-E` 只在当前处理域可由 `F2(r,fD) ≈ Γ(r,fD) F1(r,fD)` 表示且不造成独立
   的 range/Doppler/pointing/geolocation/kinematic/track-state 错误时使用。
3. `Class-P` 的 fast-time registration、Doppler registration、物理指向/几何、速度、
   定位和航迹状态问题必须保留 physical estimation、sensor prior 或 model correction
   证据；降低 clutter power 不是充分证据。
4. `Class-D` 的真实失相干使用 coherence limit 和 robust cancellation 解释，不伪装
   成可拟合的系统误差。
5. clutter-derived Γ 是强传统 baseline，不把 complex LS、per-Doppler Γ 或
   range-Doppler Γ 本身写成创新。
6. 估计器只能读取 F1、F2 和 clutter support。target truth、system-error truth、
   known injected error 只能进入 evaluator，不得进入 blind estimator。
7. Target-free Mode-A 的 Γ 从 `OFF=C+N` 估计并固定作用于 `ON=S+C+N`/`TO=S`；
   online Mode-B 的 robust Γ 从 ON 估计但不读 target truth。两种模式分开报告。
8. 支撑不足或零分母返回 `NOT_EVALUABLE` 并进入 evidence；禁止静默回退到 Current。
9. Phase-II 的 native four-channel STAP/JDL/covariance/loading/DOF/CUDA 优化继续冻结；
   `ai_training=false`、`router_enabled=false`。

## 算法接口

`scripts/two_channel_complex_calibration.py` 使用二维复数数组 `F1/F2[range, doppler]`
和同形状布尔 `clutter_support`。估计方向固定为：

```text
Gamma = sum(F2 * conj(F1)) / sum(abs(F1)**2)
Y     = F2 - Gamma * F1
```

模块提供：

```text
estimate_scc(F1, F2, clutter_support, min_support=...)
estimate_ddc(F1, F2, clutter_support, min_support=...)
estimate_ddc_rb(F1, F2, clutter_support, range_band_size, min_support=...)
estimate_robust_ddc(F1, F2, clutter_support, phase_threshold_rad, min_support=...)
estimate_robust_ddc_rb(F1, F2, clutter_support, range_band_size,
                       phase_threshold_rad, min_support=...)
apply_complex_calibration(F1, F2, estimate)
```

每个返回值包含 Gamma、局部 support count、局部 status 和 method metadata。有限支撑、
非有限输入、形状不一致和零 denominator 使用明确异常或 `NOT_EVALUABLE`，不能生成
看似有效的全零/Current 输出。

Robust 方法先在 local clutter support 上用 circular median phase 排除偏离
`phase_threshold_rad` 的点，再用保留点估计复 Gamma；阈值、最小支撑和 range-band
宽度是显式实验参数，不从最终 Pd 反推。

## 试验层次

### 算法 sanity

固定 seed 的 T1–T5：constant Γ、Doppler-dependent Γ、range-Doppler Γ、强 moving
target contamination、以及 coherence `<1` 的 true decorrelation。断言包括：

- SCC 恢复 constant Γ；
- DDC 在 Doppler 变化时优于 SCC；
- DDC-RB 在 range+Doppler 变化时优于 DDC；
- ON-derived robust DDC 相比 ordinary DDC 降低 target contamination；
- 去相关时 residual 受理论 coherence floor 约束，不能靠增加 Γ 自由度无限降低。

### Targeted mechanism pilot

配置 `configs/research/equivalent_vs_physical_calibration_pilot.json` 只列出少量
representative cases：pure equivalent mismatch、fast-time channel delay、servo
pointing、platform velocity 和 decorrelation。runner 先使用受控 F1/F2 mechanism arrays
保证算法比较可复现，再可选读取现有生产 raw-F1/F2 adapter；不把未接入生产 chain 的
downstream 指标填成 proxy pass。证据中必须注明每个字段是 `measured`、`not_evaluable`
还是 `historical_reference`。

### 判定

每种方法同时记录 clutter suppression、target transfer 和 downstream correctness。
若本轮没有 production GO-CFAR/TrackManager tap，`Pd/Pfa/angle/position/velocity/Track`
写 `NOT_EVALUABLE`，并以现有 channel-delay/servo/velocity formal 文档作为带边界的
历史参考，不混入新方法的 measured aggregate。

## Evidence contract

pilot 输出到 `outputs/formal_evidence/equivalent_vs_physical_pilot/`，至少包括：

```text
manifest.json
method_contract.csv
gamma_recovery.csv
clutter_metrics.csv
target_transfer.csv
detection_metrics.csv
track_metrics.csv
decision_matrix.csv
```

manifest 记录命令、退出码、源码 commit/dirty 状态、配置 hash、输入身份、seed、参数、
模式和清理策略。大体积 raw data 不纳入提交；保留能证明结论的 compact CSV/JSON。

## Decision gate

runner/analyzer 只返回以下预注册 label 之一，不能根据旧结论硬编码：

```text
GO_HIERARCHICAL_CALIBRATION
GO_EQUIVALENT_CALIBRATION_ONLY
GO_PHYSICAL_CALIBRATION_ONLY
GO_COUPLED_PHYSICAL_STATE_STUDY
```

`GO_PHYSICS_AI` 不属于本轮可输出的默认分支；只有后续 deterministic coupled baseline
在稳定、可重复且非 implementation bug/ordinary Γ/true information loss 的残差上失败，
并完成 target/CFAR/Track 安全性证据后，才可重新打开 AI gate。
