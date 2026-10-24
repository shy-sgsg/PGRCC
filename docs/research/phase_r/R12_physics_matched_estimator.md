# R12：物理匹配确定性时延估计器

日期：2026-10-24。适用范围：R8 冻结场景的 4 ns、单一 C2 分数时延。源码基线：`3e23235180f59fbb09b890af000544ae7a7c9fd0`。

## 结论与边界

**M1_Physics_Matched_Delay 已完成受限模型下的 seed404 独立 CUDA pilot。** 本页后附真实 production 结果；它们仍没有证明总体性能或真实设备通道频响适用性。

估计器只接收原始协议 IQ 的物理 C1/C2、逐脉冲头字段中的波束角、生产 XML 中的报告几何及冻结的采样率/载频。它不读取注入时延、A0/Known 配对输入、目标真值、检测结果或校正后参考。相对频响被限定为**一个未知常数复增益**。若相对频响在带内可任意变化，时延不可辨识，M1 必须拒绝；低拟合残差不等于已证明硬件频响平坦。

R8 特定几何条件为平台航向北向、纯沿航向 x 基线、`squint_side=1`、电子扫描、单一 38° 波束以及报告通道位置。不得把当前路径外推到具有横航向基线、变化航向/波束或未校准相位中心的系统。R8 仿真真值中 C2/C4 的 x 位置与报告值相差 2.5 mm；实际设备的通道几何与相对频响需独立标定。

## 冻结观测模型

Stage2 对 C2 执行零扩展线性分数时延。令 (d=\tau f_s\)，在脉冲内部：

\[
H(d,f)=\begin{cases}(1-d)+d e^{-j2\pi f/f_s},&0\le d<1,\\(1-|d|)+|d|e^{+j2\pi f/f_s},&-1<d<0.\end{cases}
\]

对每个脉冲 (p)，从运行 XML 报告位置和协议头波束角计算生产电子扫描几何的 C1→C2 路径差 (\tau_g(p)=(L_2-L_1)/c\)。移除该路径时延和载频相位后，用跨脉冲的物理 C1/C2 交叉谱拟合 (d)：

\[
Y(f)=a^*\,\widehat{X_1G_g}^*(f)\,H^*(d,f)+\epsilon(f),\quad a\in\mathbb C.
\]

每个候选 (d) 上通过加权最小二乘解析剖面化复增益 (a)，再以一维有界搜索最小化归一化复谱平方残差。模型显式纳入 Stage2 有限脉冲边缘项；不把插值器近似为理想纯相位时延。几何补偿使用 Native production 的稳定路径差表达式，距离取 Production XML 的 `Rmin` 工作范围字段，不读取 A1 场景时延参数或目标真值。交叉谱支持按观测幅度第 80 百分位筛选，至少 8 个频点、频率跨度至少 3 MHz；搜索界为 [-0.49, 0.49] 样点。归一化复谱残差大于 0.35 时返回 `model_mismatch`，不输出校正值。

实现位置：[`physics_matched_delay.py`](../../../scripts/phase_r/physics_matched_delay.py)。生产校正仍由 `scripts/estimate_channel_delay.py` 的 Stage2 逆递推执行，只对协议 C2（零基通道索引 1）修改输入；M1 不改变 Native CUDA 融合或 CSI 算子。

## 合成验证

实际命令：

```bash
python3 -m py_compile scripts/phase_r/physics_matched_delay.py
python3 -m pytest tests/test_r12_physics_matched_delay.py tests/test_channel_delay_correction.py tests/test_fractional_delay_inverse_closure.py tests/test_delay_stage1_core.py -q
```

结果：语法检查退出码 0；42 项通过（2026-10-24）。覆盖 0、正/负分数时延、Stage2 正负插值响应、常数复增益和常相位、噪声、支撑不足、任意未知频响拒绝、常量几何群时延偏差及未建模的逐脉冲几何变化。

一项边界结果必须随方法保留：未建模的逐脉冲几何相位变化可使 4.0 ns 真值被估成 4.468978 ns，尽管归一化模型残差仅 0.005373。常量几何差也会按实际减报告值直接偏置估计。因此模型残差门只能筛除部分不匹配，不能证明几何正确；M1 依赖外部几何约束，超出冻结 R8 观测几何必须拒绝或另行审计。

## 开发数据可行性检查（不计独立验证）

使用既有 R8 seed202、seed303 的 A1 ON 原始 C1/C2 协议 IQ（各 3 期、共 390 脉冲），仅用 runtime XML 的报告通道几何和逐脉冲头角计算 (\tau_g)，输入估计器的响应模型固定为 `constant_complex_gain`。真值没有传给估计器；下表“误差”是在估计完成后由 evaluator 用已知 4 ns 计算。两个 seed 已参与前期时延方法开发，不能当作独立结果。

| seed（开发集） | 方法 | 估计 (ns) | 相对 4 ns 误差 (ns) | 归一化模型残差 |
|---:|---|---:|---:|---:|
| 202 | D1 ordinary LS | 3.526422 | -0.473578 | — |
| 202 | D2 weighted LS | 3.536358 | -0.463642 | — |
| 202 | D3 Huber weighted LS | 3.543853 | -0.456147 | — |
| 202 | M1 physics matched | 3.707646 | -0.292354 | 0.043946 |
| 303 | D1 ordinary LS | 3.406403 | -0.593597 | — |
| 303 | D2 weighted LS | 3.408471 | -0.591529 | — |
| 303 | D3 Huber weighted LS | 3.417328 | -0.582672 | — |
| 303 | M1 physics matched | 3.539173 | -0.460827 | 0.046677 |

M1 在两份开发数据上均比同输入 D3 更接近 evaluator 的 4 ns，绝对误差分别少 0.163792 ns、0.121845 ns；两份 M1 仍低估 0.29–0.46 ns。数值由同一冻结估计器读取各 Production XML 的 `Rmin`，结果仅支持进行 seed404 可行性试验，不能宣称性能验证。seed404 是首个独立数据门；估计残差、几何一致性及校正闭合未通过时，不运行该方法的 CUDA 对比。

## 状态标签

- `IMPLEMENTED`：是，物理模型与拒绝状态已编码。
- `EXECUTED`：是；42 项合成/回归测试通过，seed202/303 开发集 CPU 估计已执行，seed404 盲估计和 15 条 Native CUDA Production 分支已完成。
- `EVALUABLE`：是；seed404 盲估计在估计后可由 evaluator 与 4 ns 对照，Known correction 输入闭合、目标匹配、参数误差和 H0 描述计数可评估。
- `PERFORMANCE_VERIFIED`：否；只有一个独立 seed、三个目标机会。CFAR unique-union false hit/Pfa 与径向速度仍不可评估。

## Seed404 独立 CUDA 结果

本次估计输入为 B 条件 A1 ON 的 390 pulses 原始物理 C1/C2 和 Production XML 几何。M1 输出 **3.759357402 ns**，truth 仅在估计之后用于评估，绝对误差 **0.240642598 ns**，相位 RMSE 0.042877 rad、归一化模型残差 0.045710。相同输入的 D1/D2/D3 分别为 3.596081/3.606094/3.588056 ns；seed404 evaluator 的 4 ns 误差绝对值分别为 0.403919/0.393906/0.411944 ns。既有 R9 固定 D3 校正量 3.409965 ns 的误差为 0.590035 ns。

M1 校正后的 F2 相对 A0 输入 RMS 为 2.6840%，固定 D3 为 6.3719%；Known 校正为 0.00873%。M1 与固定 D3 各有 3/3 正确检测机会、2/3 正确 TrackManager 关联，但 M1 的角度/位置 RMSE 较低、地速 RMSE 较高。完整任务级、目标保持、H0 描述计数及逐文件审计见 [R12 Production 任务比较](R12_production_task_comparison.md) 和 [R12 盲估计验证](R12_blind_validation.md)。

目前没有 AI 训练、Router 或多参数联合估计的依据。本报告不支持真实硬件通道频响平坦性结论。
