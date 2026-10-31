# R22-S1d：C4 单组 CSI 相位向量交叉诊断

## 阶段裁决

**S1d `EVALUABLE` 的范围仅限于固定失败组的相位向量敏感性。R22 target-present Screening 仍为 `GATE_FAILED / INCOMPLETE`，不得从该状态续跑或跳过失败条件。**

这次在同一 Native CUDA Production binary 下，对同一输入观测交叉应用 SNR20 ON、SNR30 ON 和配对 target-free OFF CSI 相位向量。SNR30 的本地估计支持数从7变为11或22，说明该选定组的冻结支持门对 CSI 相位向量来源敏感。它没有证明目标污染是 ON 相位变化的唯一原因，也没有证明这些反事实能改善部署任务性能。

## 研究问题与固定身份

- **长期目标：** 依据研究总纲识别传统 GMTI 方法的真实能力边界和可恢复任务瓶颈；本诊断不改变研究主题或技术路线。
- **具体问题：** 对 seed1932001、2.5 m/s、+4 ns、SNR20/30 配对输入，在 `GMTI05 / beam 50 / azimuth 52 / range 1065–1197`，固定 ON 观测并更换 CSI range-phase 向量来源时，C4 Robust DDC-RB 的相位筛选支持数如何变化？
- **执行源码 commit：** `461fbd949ea7c024c28f30e7aea36ae9642a4456`。
- **Master Plan blob：** `e637e3769bde7814298ff012d0f42301884e2985`，保持不变。
- **Runner：** [`run_r22_s1d_phase_vector_crossover.py`](../../../../../scripts/phase_r/run_r22_s1d_phase_vector_crossover.py)，SHA-256 `6a5214fa4f127ce1044412abd321ce5bdb36302d038e7fb6455b74229b5327d6`。执行时它是工作树中的未跟踪文件，因此实验身份由该 SHA 与 manifest 中的源码 SHA 列表绑定；本次交付将同一文件原样纳入 Git。
- **Production binary：** Native CUDA Release，CUDA 12.4、架构75、`USE_COREX=OFF`；SHA-256 `c7db0d12f930b297851f1b3b53f3e56de5caf0fa90394595de0b8babf7d7c9de`。
- **GPU：** NVIDIA GeForce RTX 3050 Laptop GPU，4,096 MiB；运行后快照显示显存使用1,418 MiB、温度73°C、GPU利用率100%。任务在外部训练并发期间完成，7个 Production 进程均返回0，无 OOM。该快照不是性能基准。
- **输入：** Stage2 未重跑。使用原配对 Stage2 第零基 period 4 输入（对应 `GMTI05`）；SNR20、SNR30、OFF SHA-256 分别为 `dcd6cbfff80dfd68a24026782d5f8b3cae8074db58df28f340630998fbf4d024`、`d324584380c67b37ea19f3e7efb7ecfdc33ed7287b2e4c97fca5aa98e9d2f831`、`a8b253c035d387da24625f189a576fe41f56789fbc1c2870c652ef7a75e25f48`。
- **冻结门槛：** 分组133 bins、`min_support=8`、相位残差门限±0.35 rad、`fallback_to_current=false`。没有修改算法门槛。

## 实验设计

对 SNR20 和 SNR30 的 ON 输入各运行三种 CSI 相位来源：同一输入自估、另一 SNR 的 ON 盲估计向量、配对 OFF 盲估计向量；另运行一个 OFF 自估分支，共7个 Native CUDA Production 分支。raw phase 按各自 ON 输入估计；交叉只发生在 CSI range-phase 向量。目标真值不参与估计，manifest 中七个分支均记录 `truth_used_in_estimator=false`。OFF 向量只作 privileged 因果诊断，不代表在线可用估计器。

S1d 首轮的两次试跑使用了完整十周期 sidecar，与 GMTI05 所需的 period 4 不匹配，因此其输出**不用于本报告结论**。失败试跑目录保留在本机 `outputs/r22_s1d_phase_vector_crossover_20261031/` 和 `outputs/r22_s1d_phase_vector_self_controls_20261031/`，待独立审查结束后按清理台账删除。本文和 GitHub 证据包仅引用随后完成的 period-correct 结果。

## Native CUDA Production 结果

| ON 输入 | CSI 相位来源 | 支持数 | 相位一致性 | Reference 状态 |
|---|---|---:|---:|---|
| SNR20 | 同一 SNR ON 自估 | 25 | 0.2028995670 | `OK` |
| SNR20 | SNR30 ON 交叉向量 | 25 | 0.2021483383 | `OK` |
| SNR20 | 配对 OFF 向量 | 21 | 0.1855220752 | `OK` |
| SNR30 | 同一 SNR ON 自估 | 7 | 0.1172000323 | `NOT_EVALUABLE`（低于8） |
| SNR30 | SNR20 ON 交叉向量 | 11 | 0.1628735635 | `OK` |
| SNR30 | 配对 OFF 向量 | 22 | 0.0228710435 | `OK` |
| target-free OFF | OFF 自估 | 55 | 0.7418131251 | `OK` |

7个进程均 `production_status=passed`、return code 0；这表示 Production 执行完成，不表示七个 reference 都有效。SNR30 原生 ON reference 仍为 `NOT_EVALUABLE`。从 Production selected-cell F1/F2 独立重算 `F2·conj(F1)`、单位复相位中心和冻结 ±0.35 rad 成员后，7个 support 计数全部与 Production Gamma 记录一致。

支持集合也发生变化：SNR30 原生7格全部包含于 SNR20 ON 向量下的11格，交叉向量额外保留4格；SNR30 原生集合与 OFF 向量下22格没有交集。SNR20 的自身向量与 SNR30 向量有22格交集，各有3格独有；SNR20 自身向量与 OFF 向量有12格交集，各有13格和9格独有。因此 support 数跨过门槛不意味着同一参考单元集合恢复。

## 解释边界

可支持的结论是：**在这一固定 Production 输入和失败组上，CSI range-phase 向量来源对 robust-phase 筛选成员及 support count 有因果影响。** 对 SNR30 输入，SNR20 ON 向量让支持数从7增至11，OFF 向量让它增至22。

本实验没有识别 SNR20 与 SNR30 自估 ON 相位向量差异的物理来源。目标污染、相位估计器对输入变化的响应以及它们的交互仍未被分解；配对 OFF 是 privileged 诊断向量，支持集合与 ON 交叉结果也并不相同。没有比较目标幅相保持、杂波残余、SCNR、Detection、Pd/Pfa、径向速度或方位误差，也没有独立 seed 重复。

因此，不能把支持数恢复称为 C4 方法修复或任务性能改善；不能据此恢复 R22 Screening，不应降低 support 门槛、删除失败组、回退 Current，也没有 AI 必要性证据。

## 交付状态与证据

- `IMPLEMENTED`：period-correct 2×3 runner 与独立 support membership 重算。
- `EXECUTED`：7个本机 Native CUDA Production 分支，均返回0；Stage2 未运行。
- `EVALUABLE`：该固定组的相位来源干预、support count、成员集合和生产记录可交叉核验；更广的物理根因不可唯一评估。
- `PERFORMANCE_VERIFIED=false`：没有 GMTI 任务级性能对照或统计验证。
- 精简审查证据：[S1d evidence](evidence/s1d/)，112个登记文件，逐文件大小和 SHA-256 已复核；入口为 [`s1d_manifest.json`](evidence/s1d/s1d_manifest.json)、[`s1d_analysis.json`](evidence/s1d/s1d_analysis.json)、[`s1d_support_summary.csv`](evidence/s1d/s1d_support_summary.csv) 和 [`evidence_manifest.json`](evidence/s1d/evidence_manifest.json)。为通过 Git whitespace 检查，GitHub 副本的文本日志/CSV 统一 LF 并去掉行尾空格；manifest 同时保留这些文件的原始 source SHA 和字节数。本机原始输出没有改动。
- 本地完整运行目录：`outputs/r22_s1d_period_corrected_crossover_20261031/`，约8.2 MB；紧凑 Git 证据约2.2 MB。高价值审计证据已提交后，本地完整输出及两次无效试跑在 ChatGPT 审查完成后按清理台账移除。
- 矩阵恢复：[`r22_experiment_matrix.py`](../../../../../scripts/phase_r/r22_experiment_matrix.py) 对 `GATE_FAILED` manifest 显式拒绝 `--resume`。因此没有尝试重启矩阵；S1d 是单独批准的诊断实验，不改变该矩阵状态。
- 执行命令：

```bash
python3 scripts/phase_r/run_r22_s1d_phase_vector_crossover.py \
  --source-root outputs/r22_s1_c4_support_replay_20261029 \
  --output-root outputs/r22_s1d_period_corrected_crossover_20261031 \
  --pipe-binary /tmp/pgrcc_r22_s1d_cuda_20261031/GMTI_pipe_core \
  --simulator-binary build/simulate_stage2_statistical
```

审查完成前停止在这里。是否补做 C4 新版本或恢复 Screening，应由独立审查证据和用户决定。
