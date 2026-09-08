# 第一阶段：当前 CSI 杂波对消的数学模型

## 结论

当前工程的“四通道”是输入协议层的四路 IQ；在 CSI 对消层，通道 1/3 和
通道 2/4 先分别做几何相位补偿、残余相位估计和相干平均，最终仍形成两路
等效通道 `F1`、`F2`。当前 CSI 不是四路自适应滤波器。

本文件只描述副本中已经存在的实现，不引入 AI。主要依据是
[`config_structs.hpp`](../include/config_structs.hpp)（通道、校准、CSI 配置）、
[`NewProtocolReader.cpp`](../src/dbs/NewProtocolReader.cpp) 与
[`gpu_kernels.cu`](../src/gpu/gpu_kernels.cu)（四通道融合和 GPU 对消）、
[`processOnePeriod.cpp`](../src/processOnePeriod.cpp)（调用顺序）、
[`clutter_CSI.cpp`](../src/clutter_CSI.cpp)（P38 CPU 参考）以及
[`ctdr_phase_model.hpp`](../include/ctdr_phase_model.hpp)（等效相位中心模型）。

## 1. 记号和数据形状

令 `q` 为协议通道号，`p` 为慢时间脉冲号，`n` 为快时间采样号：

\[
z_q(p,n)\in\mathbb C,\qquad q\in\{1,2,3,4\}.
\]

本阶段实验是 4 通道、130 个脉冲、4096 个脉压后距离单元；生成 XML 中
`enable_four_channel_fusion=1`，读取通道为 1、2，融合通道为 3、4。
后续二维矩阵以多普勒行 `m`、距离列 `c` 表示：

\[
F_i(m,c),\quad i\in\{1,2\}.
\]

## 2. 四通道如何形成两路等效 CSI 通道

### 2.1 几何相位补偿

对通道对 `(1,3)`、`(2,4)`，源码根据包头姿态、平台高度、波束角、通道
安装偏移和载频计算每个脉冲、距离采样的几何补偿：

\[
g_{13}(p,n)=\exp\{j\,s_\phi\,2\pi\,[\Delta r_1(p,n)-\Delta r_3(p,n)]/\lambda\},
\]

\[
g_{24}(p,n)=\exp\{j\,s_\phi\,2\pi\,[\Delta r_2(p,n)-\Delta r_4(p,n)]/\lambda\}.
\]

这里 `s_phi` 是配置的载波相位符号，`Delta r` 是相对参考相位中心的稳定
路径差。对应 GPU 代码为
`new_protocol_fusion_factor_kernel` / `new_protocol_decode_fuse_kernel`
（`gpu_kernels.cu:1334-1452`）；CPU 参考在
`NewProtocolReader.cpp:buildFusionCompensationAtRange`。

### 2.2 用整段孔径估计残余相位

几何补偿后，对每个快时间采样 `n` 跨完整孔径累加互相关，并只保留单位模
残余相位：

\[
\rho_{13}(n)=
\frac{\sum_p z_1(p,n)\,\overline{z_3(p,n)g_{13}(p,n)}}
     {\left|\sum_p z_1(p,n)\,\overline{z_3(p,n)g_{13}(p,n)}\right|},
\]

\[
\rho_{24}(n)=
\frac{\sum_p z_2(p,n)\,\overline{z_4(p,n)g_{24}(p,n)}}
     {\left|\sum_p z_2(p,n)\,\overline{z_4(p,n)g_{24}(p,n)}\right|}.
\]

分母为零时源码回退到 1。最终融合为

\[
x_1(p,n)=\frac12\left[z_1(p,n)+z_3(p,n)g_{13}(p,n)\rho_{13}(n)\right],
\]

\[
x_2(p,n)=\frac12\left[z_2(p,n)+z_4(p,n)g_{24}(p,n)\rho_{24}(n)\right].
\]

因此，实际 CSI 输入通道数是 **2**。若 `enable_four_channel_fusion=false`，
则直接取 `x1=z1`、`x2=z2`；不存在四通道直接参与的 CSI 自适应权重。

本阶段的 paired 回放使用 C+N 包估计 `rho13/rho24`，再复用到 S+C+N 包，
使目标注入不会改变融合残余因子。这是实验隔离策略，不改变源码的融合公式。

## 3. 脉压、通道幅度校准和二维变换

对每一路融合数据，源码做零填充距离 FFT、频域 LFM 匹配滤波、逆 FFT 和
连续距离裁剪，可写为

\[
R_i(p,c)=\operatorname{crop}_c\left\{
\operatorname{IFFT}_n\left[\operatorname{FFT}_n(x_i(p,n))H(n)\right]\right\}.
\]

当前校准路径只对通道 2 施加标量系数：

\[
R_1\leftarrow R_1,\qquad R_2\leftarrow\gamma_{\rm calib}R_2.
\]

对应 [`processOnePeriod.cpp`](../src/processOnePeriod.cpp) 中的
`cuda_scale_channel2_async` / `data2[i]*=coef`（约 `2256`、`3792` 行）；本阶段
配置 `gamma_calib=1`，所以不会额外改变幅度。

多普勒中心由相邻慢时间互相关得到：

\[
f_{a,ctr}=\frac{\mathrm{PRF}}{2\pi}
\arg\left(\sum_{p,c}R_1(p,c)\overline{R_1(p-1,c)}\right),
\]

频率轴为

\[
f_a[m]=-\frac{\mathrm{PRF}}2+m\frac{\mathrm{PRF}}{N_a}+f_{a,ctr}.
\]

慢时间 FFT 前的 `(-1)^p` 预乘与中心化 FFT 等价；DBS 再做循环行移位。
对应 [`alignFFTAndDBS.cpp`](../src/alignFFTAndDBS.cpp:22-169)。

## 4. CTDR 延迟和 P38 相位模型

### 4.1 等效延迟

两个接收相位中心物理间距记为 `d`。共发双收模型使用等效双程相位中心间距
`d/2`：

\[
d_{\rm eq}=\frac d2,\qquad
\tau_{\rm eq}=\frac{d}{2V},\qquad
s_{\rm truth}=\tau_{\rm eq}\,\mathrm{PRF}.
\]

当前生产流程将慢时间移位取整：

\[
s_{\rm current}=\operatorname{round}\left(\frac{d\,\mathrm{PRF}}{2V}\right).
\]

当 `s>0` 时，源码把通道 1 的第 `s` 行复制到输出第 0 行，并将尾部置零；
通道 2 不移位。负移位对称处理。对应
`alignFFTAndDBS.cpp:22-67` 和 `ctdr_phase_model.hpp:99-220`。

本阶段 `d=0.17 m`、`V=60 m/s`、`PRF=1300 Hz`，因此

\[
\tau_{\rm eq}=1.4167\text{ ms},\quad
s_{\rm truth}=1.8417\text{ PRT},\quad
s_{\rm current}=2\text{ PRT}.
\]

Oracle delay 仅在离线回放中使用 1.8417 PRT 的线性慢时间重采样，用来量化
整数化误差；生产源码当前使用整数移位。

### 4.2 P38 的实际含义

对每个多普勒行 `m`，在配置距离支撑 `[rg_st,rg_ed]` 上计算

\[
C_m=\sum_{c=rg_{st}}^{rg_{ed}}F_1(m,c)\overline{F_2(m,c)},
\]

并以 `sqrt(E1_m E2_m)` 作为行能量尺度。P38 拟合的是

\[
\arg C_m\approx k f_a[m]+b,
\qquad
\phi_m=k f_a[m]+b.
\]

源码先按能量、相干性筛选，再做相位展开、加权直线拟合、Huber 鲁棒迭代和
内点复拟合；不满足样本数、内点率或 RMSE 条件时回退到理论斜率。P38 不是
幅度权重，也不是目标检测器；它只生成跨通道的多普勒相关相位轨迹。

CPU 参考见 [`clutter_CSI.cpp`](../src/clutter_CSI.cpp:43-132)，参数细节见
[`p38_phase_fit.hpp`](../include/p38_phase_fit.hpp)。生产流程在零移位和最终
CSI 对齐后各拟合一次（`processOnePeriod.cpp:2440-2529`）。

## 5. 距离向相位校正

源码先计算每个距离列的跨行互相关相位：

\[
\phi_{rg}(c)=\arg\left(\sum_m F_1(m,c)\overline{F_2(m,c)}\right),
\]

再用阈值为 `0.1π` 的稳健二次拟合得到连续校正曲线，并施加

\[
F_2(m,c)\leftarrow F_2(m,c)\exp\{j\phi_{rg}(c)\}.
\]

对应 [`rg_correct.cpp`](../src/rg_correct.cpp)；最终 CSI 处理默认再次开启该校正，
由 `csi_range_phase_correction_enable` 控制。

## 6. 当前默认 CSI 对消

当前阶段 XML 使用 `csi_cancellation_mode=legacy_min_magnitude`。对动态杂波
支撑内的每个单元，先用 P38 相位旋转第二路：

\[
B(m,c)=\exp\{j\phi_m\}F_2(m,c).
\]

再做逐单元最小幅度均衡：

\[
q=\min(|F_1|,|B|),\qquad
F_{1,e}=q\frac{F_1}{|F_1|},\qquad
B_e=q\frac{B}{|B|},
\]

最后

\[
Y(m,c)=F_{1,e}(m,c)-B_e(m,c).
\]

动态支撑外直接旁路：`Y=F1`。如果启用 `csi_bypass_enable`，支撑内输出
均衡后的 `F1_e` 而不相减。精确 GPU 实现为
[`clutter_kernel`](../src/gpu/gpu_kernels.cu:728-776)，行复最小二乘等替代模式
在 `gpu_kernels.cu:778-930`。

源码还保留三个逐多普勒行模式：

\[
\alpha_m=\frac{\sum_cF_1(m,c)\overline{F_2(m,c)}}
                  {\sum_c|F_2(m,c)|^2},
\]

`row_complex_ls` 使用 `Y=F1-alpha_m F2`；`row_phase_ls_linear` 只取
`angle(alpha_m)` 做线性相减；`row_phase_ls_min_magnitude` 使用该行相位但保留
最小幅度均衡。它们是实验对照/后续候选，不是本阶段默认 Current。

## 7. 杂波支撑和检测输入

动态支撑由
[`computeDynamicSupportDomain.cpp`](../src/computeDynamicSupportDomain.cpp:57-123)
给出。半支撑角为

\[
\theta_s=\max(2^\circ,\;\mathrm{beamwidth}/2),
\]

\[
B_{az}=\frac{2|V|\sin\theta_s}{\lambda},\qquad
f_{d,st}=f_{a,ctr}-B_{az}-\frac{\Delta f}{2},\quad
f_{d,ed}=f_{a,ctr}+B_{az}+\frac{\Delta f}{2}.
\]

当前检测配置支持：

- `dynamic`：支撑内用 CSI，支撑外用通道 2；
- `full`：所有多普勒行用 CSI；
- `split`：支撑内外分别 CFAR 后合并；
- `union`：动态 CSI 和 full-CSI 两个结果取并集。

本阶段使用默认 `dynamic`。两种实验均得到动态行 `42..88`，目标真值行是
`60`，因此目标位于 CSI 支撑内；目标距离列为 `2200`。

## 8. 本阶段离线 Oracle 的定义边界

Oracle 不是另一个端到端网络，而是把某一类物理参数替换为背景真值估计：

\[
\alpha_m^{CN}=\frac{\sum_cF_{1,CN}(m,c)\overline{F_{2,CN}(m,c)}}
                         {\sum_c|F_{2,CN}(m,c)|^2}.
\]

`alpha_m^{CN}` 只从 paired C+N 计算，随后固定施加到 C+N 和 S+C+N；任何
目标真值、目标 ROI 或目标功率都不参与权重求解。目标真值只在最后计算
`target_loss_dB`、`Pd` 和排除目标 ROI 的虚警数时使用。

这一定义允许 Oracle All 比 Current 差：Current 的非线性最小幅度算子会主动
限制通道噪声/幅度不匹配的放大，而背景-only 线性复权可能降低残余高能点，却
增加整体噪声代价。因此本阶段把 Oracle All 当作“物理参数已知的背景-only
线性复权对照”，不把它宣称为无条件性能上界。
