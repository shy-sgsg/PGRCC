# R10：测角与位置误差归因

日期：2026-10-23
基线：86574881277692f052fb5470da6c83f3a2da5752
方法：读取当前生产源码，并重算 R8/R9 已有 CUDA 结果中的条件差与生产局部敏感度；没有运行新 GPU 实验。

## 状态与范围

- IMPLEMENTED：生产测角/运动补偿/几何投影和评价公式均在当前源码中。
- EXECUTED：R8/R9 原始处理链已在 Native CUDA 上运行；本 R10 只读取得现存记录。
- EVALUABLE：五个正确检出 period 的角度、位置误差及 phase→cross-range 局部闭合可评估。
- PERFORMANCE_VERIFIED：仅限本次两 seed 的具体处理链。无独立统计推断。

## 实际生产计算链

1. src/rg_correct.cpp 按距离单元计算
   \[
   \phi(r,c)=\arg\left(\sum_{az}F_1(r,c)\overline{F_2(r,c)}\right)
   \]
   并拟合距离相位模型。src/processOnePeriod.cpp 从已选目标 cell 读取该相位、按参考相位展开。
2. motion-compensation 求解器还原 CTDR 原始相位：
   \[
   \phi_{\rm raw}=\operatorname{wrap}(\phi_{\rm corrected}+\phi_{\rm range\ correction})
   \]
   随后通过配置的发射/接收相位中心几何计算静态模型相位，并最小化
   \[
   1-\cos\{\operatorname{wrap}(\phi_{\rm raw}-\phi_{\rm static}(AF_{\rm geo}))\}
   \]
   同时受 Doppler/运动分支约束。此处实际使用的是 CTDR 几何求解，不是理想 ULA 闭式公式。
3. R9 runtime 的波长为 0.018737028625 m、平台速度约 60 m/s、左视、接收基线 0.17 m。生产反演为
   \[
   \sin A=\frac{AF_{\rm geo}\lambda}{2V},\qquad
   R_g=\sqrt{R_s^2-(H-h_0)^2}.
   \]
   computeLookFromSinA 以平台速度方向、左/右视向量和 \(\sin A\) 构造 EN 单位视线；输出位置为
   \[
   (E_t,N_t)=(E_p,N_p)+R_g(\ell_E,\ell_N),
   \]
   方位角用从东向北的 atan2(N_t-N_p,E_t-E_p)。真值角误差同样按平台到目标的 EN 方位计算。二维位置误差是估计 EN 位置与 truth EN 位置的欧氏距离。

对应实现：[相位选择与反演](../../../src/processOnePeriod.cpp#L3496)、[位置投影](../../../src/processOnePeriod.cpp#L1867)、[raw phase 与 CTDR 几何模型](../../../include/motion_comp.hpp#L91)、[生产误差与 Jacobian 输出](../../../src/writeresults.cpp#L911)、[EN 方位及误差定义](../../../src/writeresults.cpp#L1042)、[F1/F2 相位汇聚](../../../src/rg_correct.cpp#L177)。

## seed 级 task RMSE

| Seed | 条件 | Angle RMSE (deg) | Position RMSE (m) | Ground-speed RMSE (m/s) | 正确 detection |
|---:|---|---:|---:|---:|---:|
| 202 | A0 | 0.04000 | 61.396 | 0.0852 | 2/3 |
| 202 | Known | 0.04000 | 61.396 | 0.0852 | 2/3 |
| 202 | 固定 D3 | 0.10693 | 164.058 | 1.8446 | 2/3 |
| 202 | 在线 D3 | 0.11634 | 178.498 | 2.0173 | 2/3 |
| 303 | A0 | 0.01772 | 27.333 | 4.7778 | 3/3 |
| 303 | Known | 0.01772 | 27.333 | 4.7778 | 3/3 |
| 303 | 固定 D3 | 0.09007 | 138.247 | 0.3094 | 3/3 |
| 303 | 在线 D3 | 0.12725 | 195.302 | 1.6082 | 3/3 |

Known 与 A0 的小数尾差仅约 \(10^{-7}\) deg、\(10^{-4}\) m，说明该 evaluator-only 校正在本任务数据上与无新增误差基线闭合。相对固定 D3，位置 RMSE 在 seed202/303 分别约为 A0 的 2.67 倍和 5.06 倍。地速不呈同一方向：固定 D3 在 seed202 变差，在 seed303 反而低于 A0/Known；不要用定位改善替代地速结论。

## 逐期参数误差

单元格格式为 angle error / 2D position error / ground-speed error。period 0 没有有效 ground-speed 误差，记为 —。

| Seed / period | A0 | Known | 固定 D3 | 在线 D3 |
|---|---|---|---|---|
| 202 / 0 | +0.04767° / 73.01 m / — | +0.04767° / 73.01 m / — | +0.12236° / 187.34 m / — | +0.13282° / 203.35 m / — |
| 202 / 2 | +0.03045° / 46.99 m / −0.085 m/s | +0.03045° / 46.99 m / −0.085 m/s | +0.08885° / 136.88 m / +1.845 m/s | +0.09709° / 149.57 m / +2.017 m/s |
| 303 / 0 | −0.01762° / 27.07 m / — | −0.01762° / 27.07 m / — | +0.11901° / 182.21 m / — | +0.16617° / 254.40 m / — |
| 303 / 1 | +0.00692° / 10.76 m / −0.569 m/s | +0.00692° / 10.76 m / −0.569 m/s | +0.02970° / 45.64 m / −0.321 m/s | +0.04476° / 68.75 m / −0.437 m/s |
| 303 / 2 | −0.02415° / 37.32 m / +6.733 m/s | −0.02415° / 37.32 m / +6.733 m/s | +0.09640° / 148.51 m / +0.298 m/s | +0.13769° / 212.09 m / −2.232 m/s |

## 相位到角度/横向位置的局部闭合

对 D3 与 A0 的每个正确目标记录比较目标处生产 CTDR raw phase。已保存 production Jacobian 的 \(\partial AF_{\rm geo}/\partial\phi\) 约为 −90.78 Hz/rad，\(\partial AF_{\rm geo}/\partial f_D=0\)，对应横向位置敏感度约 1956.7–1982.6 m/rad。由真实生产 EN 位置相对真值视线的左法向投影定义带符号 cross-range error。使用 A0 局部 Jacobian 预测条件间横向变化，并与生产 EN 位置直接计算的变化比较：

| Seed / period | 方法 | 同一检测 cell？ | Δraw phase (rad) | 实际 Δangle (deg) | 预测 Δcross-range (m) | 实际 Δcross-range (m) | 闭合残差 (m) |
|---|---|---|---:|---:|---:|---:|---:|
| 202 / 0 | 固定 D3 | 是 | +0.05836 | +0.07469 | +114.29 | +114.35 | 0.06 |
| 202 / 0 | 在线 D3 | 是 | +0.06653 | +0.08515 | +130.28 | +130.36 | 0.08 |
| 202 / 2 | 固定 D3 | 是 | +0.04542 | +0.05840 | +89.91 | +89.95 | 0.04 |
| 202 / 2 | 在线 D3 | 是 | +0.05183 | +0.06665 | +102.60 | +102.65 | 0.05 |
| 303 / 0 | 固定 D3 | 否，row −1 | +0.10681 | +0.13663 | +208.98 | +209.18 | 0.19 |
| 303 / 0 | 在线 D3 | 否，row −1 | +0.14362 | +0.18379 | +281.02 | +281.37 | 0.35 |
| 303 / 1 | 固定 D3 | 是 | +0.01777 | +0.02278 | +34.98 | +34.98 | 0.01 |
| 303 / 1 | 在线 D3 | 是 | +0.02951 | +0.03784 | +58.08 | +58.10 | 0.02 |
| 303 / 2 | 固定 D3 | 否，row −1 | +0.09379 | +0.12055 | +185.52 | +185.68 | 0.15 |
| 303 / 2 | 在线 D3 | 否，row −1 | +0.12587 | +0.16184 | +249.00 | +249.28 | 0.28 |

五个 period 的条件间 angle 变化与 raw-phase 变化对应的斜率约为 1.28 deg/rad。更关键的是，生产求解器的有限差分 Jacobian 对带符号 cross-range 变化的闭合残差不超过 0.35 m。seed202 两期和 seed303 period 1 的 A0/D3 cell 完全一致，所以这三期直接排除了“仅因选中另一个 detection cell”解释其角度/横向位置差异的可能。seed303 period 0、2 的 row 变动仍是混杂因素。

这验证了“所观测的 CTDR 相位变化 → 生产角度/位置几何变化”的局部物理关系。它不单独证明该相位变化只能由物理通道时延造成；插值、F1/F2 融合、杂波相位和候选 cell 改变都可能影响输入相位。沿 truth LOS 的 range error 在这些记录约 1.62–3.00 m，位置差主要投影到 cross-range。

## 数据限制和可复核入口

- RMSE 与 target lineage：evidence/r9_deterministic_delay_estimator_20261023/records/evaluation/task_metrics_comparison.csv、target_lineage_audit.csv。
- 生产 per-target phase、CFAR margin、AF、EN 位置、error 和 finite-difference Jacobian：本机忽略目录 outputs/phase_r_r9_deterministic_delay_20261023/cases/seed{202,303}/production/{R9_Static_R3_D3_ON,R9_Online_D3_ON}/result/detection_results_GMTI*.csv。
- A0/Known 的同类 production 与 TrackManager 记录：本机 outputs/phase_r8_unknown_delay_multiseed_20261023/；R8/R9 的 manifest 和精简摘要位于相邻 compact evidence packages。
- 所有 position projection 与 truth evaluation 都在 EN 平面；本报告没有把经纬度投影误差另行混入。

没有使用 CPU 仿真或新 CUDA 运行替代上述历史 production 证据，也没有给单 seed/period 差值赋予统计显著性。
