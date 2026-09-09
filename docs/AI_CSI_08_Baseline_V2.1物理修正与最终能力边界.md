# Baseline V2.1 物理修正与最终能力边界

## 目的与结论口径

V2.1 的先决条件是证明四通道目标 steering 与当前 Stage2 真实输出一致，再比较
STAP/CSI 算子。所有性能结论必须来自本次运行的源码提交、配置、输入身份和 compact
CSV；旧 V2 结果不作为 V2.1 最终性能结论。

当前主线只实现“物理基线 + 可审计接口”，不训练最终 AI，也不把目标 truth 输入
自适应权重或未来网络。

## 本次 V2.1 交付结果

所有 final 产物均由源码提交 `47e094ab7e7d519bac756f13277e95f1bbf846fb`
生成，manifest 的 `worktree_dirty=false`。旧 V2/V2.1 目录保留为历史审计，以下
final 目录才是本次最终汇总入口：

| 验证 | 当前证据 | 结果 |
|---|---|---|
| empirical steering sanity | `outputs/ai_csi_baseline_v21_final_screen/baseline_v21_empirical_steering_sanity.json` | 11/11 通过；空间相关性最低 0.999999887，JDL 相关性最低 0.806669，JDL 相位 RMS 最大 0.523 rad，幅度归一化误差最大 4.64e-4 |
| screen | `outputs/ai_csi_baseline_v21_final_screen/` | 90 cases / 1170 method rows，全部 `ok` |
| signed velocity / MDV | `outputs/ai_csi_baseline_v21_final_velocity/baseline_v2_mdv_summary.csv` | 35 cases / 455 rows；approaching、receding、zero 分开保留；所有方法的 abs-speed combined 均在对称性检查后生成，最大逐速度 Pd 差为 0 |
| low-SNR transition | `outputs/ai_csi_baseline_v21_transition/` | 48 cases / 624 rows，全部 `ok`；Corrected JDL 在固定 threshold scale=2.0 下 Pd=19/48=0.3958，Wilson 95% CI=[0.2702, 0.5369]，Pfa=0.001481 |
| ROC | `outputs/ai_csi_baseline_v21_final_roc/` | 5 regenerated seeds × 7 threshold scales，65 rows；Corrected JDL scale=2.0 的高 SNR operating point Pd=1.0，Wilson 95% CI=[0.5655, 1.0]，Pfa=0.001480 |
| formal one-factor matrix | `outputs/ai_csi_baseline_v21_formal/` | 47 factor-level groups × 10 seeds = 470 cases / 6110 rows，全部 `ok`；每个 factor-level 有 130 method rows |
| mixed stress | `outputs/ai_csi_baseline_v21_mixed_stress/` | 确定性 LHS 12 cases / 156 rows，全部 `ok` |

formal 与 mixed 的 feature predictor 输出包含 leave-one-case-out、leave-one-seed-out；
formal 还包含 leave-one-factor-family-out。折内缺失值填补、标准化只使用训练折统计量，
没有把同一 scene 的局部行随机拆到训练和测试两侧。

## Steering 定义

四通道目标 steering 的接口是：

```text
spatial_steering(theta_beam, range_m, geometry)
space_time_steering(theta_beam, fd_row, range_m, geometry)
```

其中 `theta_beam` 来自配置的 beam center、扫描步长和 geometry offset；`range_m`
按当前 range block 的中心推导。Doppler row 只决定 temporal response 的频率，不能
通过 stationary clutter ridge 反解成目标空间角。`clutter_angle_from_doppler()` 仍可
作为诊断特征，但不是目标约束。JDL 是 temporal(fd) 与固定 spatial(theta) 的乘积。

真实 Stage2 sanity 用同 seed、同场景的

```text
S = (S+C+N) - (C+N)
```

构造目标复向量，覆盖固定角度多速度、正负径向速度、目标方位偏移和多个距离。输出
空间/JDL 归一化复相关、逐通道相位误差、幅度归一化误差、速度空间不变性和方位扰动
响应。sanity 未通过时，runner 拒绝 screen、velocity、ROC 和 formal matrix。

## V2.1 对照组

严格 F1/F2 组：Current production replay、Phase-only production replay、scientific
controlled Current、scientific controlled Phase-only、Row complex LS/Wiener，以及
background-only 的 Huber + physics-regularized robust Row-LS/IRLS。四通道 academic
reference 组包括 DL-MVDR、shrinkage MVDR、修正 JDL、MNEC 和明确标注为 approximate
reference 的 SA-MNEC。

所有自适应量从 paired C+N background 估计；目标 truth 只用于评价标签、ROI、Pd 和
oracle label，不进入权重求解。

连续量的 95% CI 使用 seed/trial 层 Student-t（小样本）或相同层级 bootstrap；Pd/Pfa
的 Bernoulli 部分使用 Wilson 区间。Spearman 使用 tie-aware average rank。

## 检测、速度与混合压力

10 dB 饱和 ROC 不能证明检测能力。V2.1 transition sweep 从低 SNR 开始，先用 pilot
找到 Pd 约 0.2--0.8 的区间，再用独立 refined seeds/trials 加密；报告 measured
SCNR--Pd、Pfa、ROC 和 Wilson CI。GO-CFAR 仍是 CPU research proxy，不等同生产检测器。

velocity/MDV 由真实 Stage2 速度重生成得到。approaching/receding 分开汇报；只有在
逐速度 Pd 差异通过配置的 symmetry tolerance 后才生成 abs-speed combined MDV。混合
压力使用确定性 LHS/random 采样联合改变纹理异质性、幅相误差、有效样本率、目标
SNR、径向速度与支撑边缘条件，不把 Cartesian 笛卡尔积误称为 mixed stress。

## Physics Expert Map 接口

机器可读接口由 `physics_expert_map_schema_v1.json` 导出。数据粒度为
`scene × seed × local Doppler row × range block`，禁止随机 local-row split。输入包含
P38 slope/intercept/RMSE/inlier count、coherence、F1/F2 ratio、phase residual/variance、
valid fraction、texture heterogeneity、clutter-ridge/support-edge distance、local
residual tail 和 robust-LS condition/confidence；expert outputs 记录 Current、Row-LS、
robust-LS 的性能。

下一阶段 oracle 只为每个 local region 产生：

```text
delta_log_amplitude, delta_phase, gate_target, oracle_gain_over_current
```

oracle 的 C+N/truth 只用于 label/loss，不能成为推理输入。优先判断 Current 是否已
足够以及 adaptive correction 是否值得；没有证据前不学习 CTDR/P38 residual。

## PGRCC-v1 接口边界

由 `pgrcc_v1_training_schema.json` 导出的小型 MLP 或轻量 1-D CNN 只预测 bounded
residual 和 confidence gate：

```text
alpha_AI = alpha_phy * exp(clamp(delta_log_amplitude))
                    * exp(j * clamp(delta_phase))
Y = (1-g) * Y_Current + g * Y_corrected
```

必须满足 `delta=0, g=0` 精确回到 Current；低置信度、非法特征或物理门控失败回退
Current。V2.1 不训练 Transformer、RD image-to-image、soft support、Deep Unfolding，
也不同时学习 `delta CTDR + delta P38`。

## 可复现入口

```bash
python3 scripts/run_baseline_v2.py --mode sanity \
  --suite configs/research/ai_csi_baseline_v2_suite.json \
  --out outputs/ai_csi_baseline_v21_final_screen
python3 scripts/run_baseline_v2.py --mode screen \
  --suite configs/research/ai_csi_baseline_v2_suite.json \
  --out outputs/ai_csi_baseline_v21_final_screen
python3 scripts/run_baseline_v2.py --mode velocity \
  --suite configs/research/ai_csi_baseline_v2_suite.json \
  --out outputs/ai_csi_baseline_v21_final_velocity
python3 scripts/run_baseline_v2.py --mode transition \
  --suite configs/research/ai_csi_baseline_v2_suite.json \
  --out outputs/ai_csi_baseline_v21_transition
python3 scripts/run_baseline_v2.py --mode mixed_stress \
  --suite configs/research/ai_csi_baseline_v2_suite.json \
  --out outputs/ai_csi_baseline_v21_mixed_stress
python3 scripts/run_baseline_v2.py --mode roc --workers 2 \
  --suite configs/research/ai_csi_baseline_v2_suite.json \
  --out outputs/ai_csi_baseline_v21_final_roc
python3 scripts/run_baseline_v2.py --mode formal --workers 2 \
  --suite configs/research/ai_csi_baseline_v2_suite.json \
  --out outputs/ai_csi_baseline_v21_formal
```

正式提交必须同时检查各目录的 manifest、summary/ROC CSV、sanity CSV/JSON、schema
JSON、输入 SHA-256、source commit 和 `worktree_dirty`。当前环境未能与 NVIDIA driver
通信的普通沙箱探测记录在 manifest 中；受限提权后的 `nvidia-smi` 已确认 RTX 3050
Laptop GPU（4 GiB）。本次 Stage2 statistical simulator 和 Python baseline 仍是 CPU
offline 链路，未运行 CUDA production pipeline，因此以上结果只能称为 CPU research
evidence，不能称为 CUDA production validation。长任务期间主机 available memory 约
9--12 GiB、磁盘剩余约 74--75 GiB；GPU 只作为资源状态记录，未混入这些 CPU 结果。
