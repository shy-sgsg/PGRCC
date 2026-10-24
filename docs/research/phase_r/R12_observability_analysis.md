# R12：物理匹配时延估计的可观测性分析

日期：2026-10-24。源码基线：`3e23235180f59fbb09b890af000544ae7a7c9fd0`。范围仅限 Stage2 通道时延、四通道生产融合及其确定性估计；本文件不使用 evaluator truth 形成估计。

## 判定摘要

- Stage2 的 4 ns 注入是对协议物理通道 C2 的 `0.24` 样点线性分数延迟；C4 不接受该注入。
- Native CUDA 将 C3 几何补偿到 C1、将 C4 补偿到 C2，再分别以等权均值形成 F1/F2。因而融合 F2 的传递响应不是单独 C2 的 (H(d,f))，也不等同于纯线性相位时延。
- 原始 C1/C2 是较直接的物理观测对。已知报告几何后，在相对通道响应只含一个未知常数复增益、且共同散射场假设成立时，可以对 Stage2 的精确 (H(d,f)) 做一维确定性拟合。
- 若允许任意未知复频响，则时延不可辨识：该频响能逐频吸收任意候选 (H(d,f))。此限制不是估计器精度问题。真实设备需要频响标定，或需要有证据支持“带内相对响应为常数复增益”的窄模型。

## 源码中的物理观测链

### Stage2 延迟注入

`simulator/target_injection/channel_impairments.cpp` 从 `new_protocol_read_channel_1/2` 读取通道，保持 C1，按

\[
y_2[n]=x_2(n-d),\qquad d=\texttt{channel\_time\_delay\_ns}\times10^{-9}f_s
\]

对 C2 插值。`interpolate()` 对样点区间线性插值，超出脉冲边界时零扩展。R8 物理配置为 (f_s=60\,\mathrm{MHz})、(\tau=4\,\mathrm{ns})，所以 (d=0.24) sample。忽略边界时，正分数延迟的精确离散频响为

\[
H_+(d,f)=(1-d)+d e^{-j2\pi f/f_s},\quad 0\le d<1.
\]

对 (-1<d<0)，Stage2 的同一插值代码使用未来样点，故

\[
H_-(d,f)=(1-|d|)+|d|e^{+j2\pi f/f_s}.
\]

因此使用 (e^{-j2\pi f\tau}) 的理想纯延迟相位斜率只是近似；它漏掉 (H) 的幅度变化和非线性相位。脉冲首尾还受零扩展影响，拟合窗必须有有效信号支撑，不能把边界效应解释为额外时延。

### 四通道几何与生产融合

R8 输入为 4 通道 float32 协议 IQ，每脉冲 11820 个复样点、每 CPI 130 脉冲。Stage2 的 `channel_geometry` 坐标系为沿航向 (x)、横航向 (y)、向上 (z)：C1/C3 报告 (x=-0.085\,m)，C2/C4 报告 (x=+0.085\,m)；C1/C2 的 (z=+0.085\,m)，C3/C4 的 (z=-0.085\,m)。R8 仿真真值将 C2/C4 的 (x) 设为 (+0.0875\,m)，较报告位置偏 2.5 mm；该几何差属于模型误差，不是 C2 延迟。

`src/gpu/gpu_kernels.cu` 使用运行配置的通道偏置和逐脉冲协议头波束角/平台速度计算相位中心补偿：C3 映射到 C1，C4 映射到 C2；随后

\[
F_1=\tfrac12(C_1+G_{13}C_3),\qquad
F_2=\tfrac12(C_2+G_{24}C_4),
\]

其中 (G_{13},G_{24}) 是生产几何相位补偿（另含由孔径残余相位形成的单位复系数）。C1/C2 是两组融合的参考相位中心；生产没有把 C2 的延迟摊到 C4。于是，即使理想化假设四个通道对同一散射体已配准，F2 相对无延迟 F2 的响应也近似为 \([H(d,f)+1]/2\)，而不是 (H(d,f))。当散射分布、相位中心误差或几何补偿偏差存在时，该近似进一步变差。

几何相位由公共发射相位中心、通道专属接收相位中心和传播路径决定。对窄波束、远场同一散射方向，C1/C2 的名义相对群时延可写成

\[
\tau_g(p)=\bigl(L_2(p)-L_1(p)\bigr)/c,
\]

其中每脉冲 (p) 的角度、航向和通道报告位置来自运行配置/协议头，而不是目标真值。拟合 C1/C2 时，须在跨脉冲累加前去除相应的几何响应；其未知载频常相位也随每脉冲几何变化，不能只减一个基带相位斜率。真实 C2/C4 几何与报告值不同会留下模型偏差；其中等效线性相位项与物理时延同样混淆。

R8 的坐标约定需按当前单一几何核对：仿真把平台局部 (x) 设为北向，通道基线只有 (x) 分量；电子扫描生产补偿直接使用 XML 的偏置分量。仿真 `makeAlgorithmLookVectorEN()` 在北向航迹、`squint_side=1` 下给出北向 LOS 分量 (-\sin\theta)；生产 CUDA 对应地把 (x) 偏置投影到东向 LOS 分量 (-\sin\theta)。所以对 R8 这个纯 (x) 基线，两侧的通道路径差投影相同。该等价依赖 R8 的方向和通道布局；不能推广到含 (y) 偏置或不同航向，M1 配置应拒绝未声明的几何坐标约定。

R9 的 (D1/D2/D3) 输入则是由四通道 IQ 直接按 ((C1+C3)/2,(C2+C4)/2) 形成的融合观测，估计器没有复现 Native CUDA 的逐脉冲几何补偿。因此 R9 的 (3.409965\,ns) 是该特定融合观测上的固定标定值，可作为历史生产分支，不应解释为 C2 物理时延的无偏测量。

### 生产校正

`scripts/estimate_channel_delay.py` 对物理通道 C2 应用与 Stage2 插值匹配的逆递推：

\[
x[0]=0,\qquad x[n]=\frac{y[n]-d x[n-1]}{1-d}.
\]

R8 manifest 记录的 correction channel index 为零基 `[1]`，即物理 C2。该校正只作用于输入 IQ 后再进入同一 Native CUDA 生产融合链；估计与 correction 两侧必须使用相同的采样率、符号和通道索引。

## 可辨识性与 M1 的适用假设

令几何去除后的可观测交叉谱为 (Z(f)=X_1(f)X_2^*(f)/|X_1(f)|^2)。在共同散射场、足够相干的条件下，窄模型为

\[
Z(f)=a^*H^*(d,f)+\epsilon(f),
\]

其中 (a\in\mathbb{C}) 吸收未知常数幅度和常相位，(d=\tau f_s) 是待估 C2 分数样点延迟。足够宽的已占用带宽使 (H(d,f)) 随频率的幅相曲率可用于一维拟合。这个模型允许未知常数复增益，不允许任意频变增益/相位。

若真实观测为 (Z(f)=a^*H^*(d,f)Q^*(f))，而 (Q(f)) 是任意未知复频响，则对任意候选 (d')，都可取

\[
Q'(f)=Q(f)H(d,f)/H(d',f)
\]

使观测完全相同。此时 (d) 不能从 IQ 单独恢复，必须将 (Q(f)) 通过独立 loopback/注入校准测得，或用独立参考通道/标定信号约束。若 (Q) 只未知一个线性相位斜率，它会直接偏置总群时延；仅凭低残差无法区分该项与 C2 延迟。M1 的部署前置条件因此是：相对复频响带内平坦性已标定，或有单独证据排除未知频变项；几何报告值与实际值的误差也需给出上界。

## 证据边界与进入下一门条件

- 已从当前源码确认 Stage2 插值、4 通道布局、生产几何补偿/融合和 C2 逆校正结构。
- R8/R9 有 seed202/303 历史协议 IQ；这些输入已参与方法选择，不能作为独立盲验证。R12 使用冻结的新方法和 seed404 做独立生产验证。
- 当前小实验是模拟器条件下的可行性验证；不能证明真实机载通道的相对频响平坦，也不能消除真实几何标定误差。
- 进入 M1 合成门：正/负分数时延、常数复增益、噪声/支撑不足、报告几何误差、任意未知频响不可辨识反例均有可复核输出。
- 只有合成门通过并冻结公式/参数后，才进入本机 Native CUDA 生产比较；CUDA 运行固定 `USE_COREX=OFF`。

## 源码与数据索引

- Stage2 插值/注入：`simulator/target_injection/channel_impairments.cpp`
- 散射体接收相位中心/传播路径：`simulator/target_injection/lfm_echo_generator.cpp`
- Native CUDA 几何补偿与 F1/F2：`src/gpu/gpu_kernels.cu`
- R9 D1/D2/D3 输入融合与物理逆校正：`scripts/run_delay_stage1_formal.py`、`scripts/delay_stage1_core.py`、`scripts/estimate_channel_delay.py`
- R8 物理位置、4 通道布局及运行输入：`configs/research/unknown_system_error_end_to_end_pilot.json`、`outputs/phase_r8_unknown_delay_multiseed_20261023/seed202/cases/base__seed_202__v_p6d7__snr_p30__delay_p4ns/case_manifest.json`
- R9 固定值与评估证据：`docs/research/phase_r/R9_online_blind_estimation.md`、`docs/research/phase_r/R10_channel_delay_observability.md`

本报告为源码推导；除列明的历史输入与配置外，本文尚未声称 R12 合成门或 CUDA 生产比较已执行。
