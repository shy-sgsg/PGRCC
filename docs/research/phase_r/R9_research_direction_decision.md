# R9 研究方向裁决

日期：2026-10-23。只裁决 R9 已完成的 D1/D2/D3 时延标定及两组 R8 留出 seed 的 CUDA Production 回放；不自动启动后续实验。

## 阶段状态

| 标签 | 判定 |
|---|---|
| `IMPLEMENTED` | 是：D1、D2、D3 均已有确定性实现。 |
| `PREVIOUSLY_EXECUTED` | 是：R3 seed101 target-free D3 已执行；R9 在 seed202/303 ON 也执行 D1/D2/D3。 |
| `EVALUABLE` | 是：两 seed 的估计、物理闭合、Production、检测/association、H0 observed counts 和 lineage 均可评估。Pfa、radial velocity、校准置信区间不可评估。 |
| `QUALIFIED_FOR_CURRENT_PRODUCTION` | 仅限 R8 seed202/303、R7 固定二进制和当前 ON/OFF/TO 配置的受控回放。 |
| `ONLINE_DEPLOYABLE` | 否：完整周期批量估计后回放同批数据，不是实时在线处理；没有漂移、延迟和不确定度门控验证。 |
| `PERFORMANCE_VERIFIED` | 不作广义声明。精确范围内观察到 detection 计数恢复；两 seed × 三期不足以声称统计显著或普遍性能提升。 |

## Q1–Q10

### Q1：现有 D1/D2/D3 是否正确实现预期的物理时延估计模型？

**纯时延线性相位模型的符号、单位和频率轴正确；与本次生产失配的物理映射并不严格匹配。** `C12=ΣFFT(F1)conj(FFT(F2))` 对纯正时延产生正相位斜率，`τ=slope/(2π)`。合成 +4 ns 和常相位检查均通过。R9 使用 raw fast-time FFT 轴，不是 Doppler 轴。

限制是生产 F2 将受扰通道2与未受扰通道4融合，估计后却只校正物理通道2；Stage2 分数延迟又是线性插值而不是理想相位坡道。因此拟合值是融合信号上的有效斜率，不能无条件视为无偏的物理通道2时延。拟合截距吸收常相位，不能分离随频率变化的几何相位。

### Q2：方法是否能从实际可获得的数据辨识 +4 ns？

**能在无真值 ON 数据上得到正的有限斜率，但本轮估计存在明显低估，不能称为精确辨识。** D3 为 seed202 `3.3212 ns`、seed303 `2.9880 ns`，误差 `−0.6788/−1.0120 ns`。生产任务恢复到 `5/6` detection，证明该估计量在当前受控场景有任务价值；物理闭合仍未达到 Known。真实估计方差和置信区间未校准。

### Q3：seed101 独立标定能否在其他 seed 恢复检测？

**可以恢复这两组留出场景中的观察计数。** 固定 seed101 D3=`3.409965 ns`，应用于 seed202/303 后检测 `5/6`，与 A0/C Known 的 `5/6` 相同；正确 track association 也同为 `3/6`。但 F2 相对 RMS 仍为 `0.061–0.084`，远高于 Known 的约 `1e-4`；参数 RMSE 和 H0 虚假输出也没有逐项匹配 Known。

### Q4：ON 数据估计是否可行？

**可行到受控 batch 估计和 Production 重放这一步；实时部署未证明。** 估计函数未读取 target truth、A0、OFF、TO 或任务结果；但它读取完整三周期 H1 数据，再用于同批数据的回放，所以不能称为实时单周期盲校准器。

### Q5：D3 与 Known Correction 的任务差距是多少？

检测和正确 track association 计数差为 `0/6`：固定 D3/在线 D3/A0/Known 在 seed202、303 分别为 `2/3、3/3` detection。参数层仍有差距且样本很少：seed202 固定 D3 angle/position RMSE `0.1069°/164.1 m`，Known `0.0400°/61.4 m`；seed303 固定 `0.0901°/138.2 m`，Known `0.0177°/27.3 m`。地速仅有 1–2 个有效误差样本，seed303 数值方向不同，不能据其排名。

### Q6：残余误差是否足以解释目标漏检？

**不能解释全部漏检。** 固定标定低估 `0.590 ns`，在线候选低估 `0.679/1.012 ns`，而两类校正都恢复 `5/6` detection。seed202 period1 的同一漏检也存在于 A0 和 Known；邻域 hits 有，但候选未过冻结 Doppler 门。当前没有证据把这个失败归因于残余通道时延。

### Q7：是否证明固定离线标定不足？

**没有。** 在两个独立留出 seed 上，固定目标关闭标定已把检测计数恢复到 Known/A0 的观察水平。因此现阶段不能以 R8 的检测损失主张必须在线估计。固定标定是否能跨时间、温漂、重构或不同场景保持精度仍未评估。

### Q8：有无证据需要更复杂在线优化或学习方法？

**没有。** D3 已存在且无 oracle 的 ON 候选可用；固定标定亦恢复本轮检测计数。在线候选相对固定值没有任务检测计数收益，也未显示稳定的参数优势。物理模型误差、融合可辨识性、检测门和小样本问题应先于 AI 讨论。

### Q9：未知系统失配下一步应研究哪种方向？

优先审查固定标定在时间/温度/场景变化下的稳定性和单通道可观测性；同时基于现有失败 lineage 审查 CFAR cluster、冻结 Doppler gate 与目标保持。只有发现固定参数会实际漂移且对可观测量可追踪时，再定义确定性时变估计；如果参数残差已小仍存在目标损失，应转向检测/关联对残余失配的稳健性，而不是单纯压低时延 RMSE。此为建议，未启动下一轮实验。

### Q10：是否有足够证据证明 AI 必要？

**没有。`AI_NOT_JUSTIFIED`。** 本轮没有证明固定标定不足，传统确定性估计已恢复本场景 detection 计数，也没有任何 AI 对照。本阶段停止，不训练 AI、不扩展六参数、不适配 CoreX。

## 证据边界与停止

- R8 Stage2 area clutter 与 seed202/303 原始 IQ 直接复用；没有重生成场景。
- 12 个新 Production 分支和 8 个 CFAR 数值诊断都是真实本机 Native CUDA；CFAR 数值、branch path、runtime XML 指纹和日志均有记录。
- 两个 test seed 的 `5/6` 是有限 scene-period 观察，不报告统计显著性 Pd/Pfa。H0 false detections、clusters、confirmed tracks 是 observed counts，不是校准 Pfa。
- 径向速度为 `NOT_EVALUABLE`；D1/D2/D3 无经校准标准误/置信区间。
- 合成公式检查是在 CUDA 回放后补做；文档和机器证据如实记录，不倒写成 Production 前的验证。

完成 R9 后停止。主结果见 [检测恢复](R9_detection_recovery_results.md)、[固定标定](R9_static_calibration_baseline.md)、[ON 盲估计](R9_online_blind_estimation.md) 与 [估计器审计](R9_deterministic_delay_estimator_audit.md)。紧凑 manifest 和机器记录见 [R9 evidence](evidence/r9_deterministic_delay_estimator_20261023/)。
