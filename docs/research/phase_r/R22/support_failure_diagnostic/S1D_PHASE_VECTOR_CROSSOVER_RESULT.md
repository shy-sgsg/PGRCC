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

## CSI 向量变化与目标真值距离格的事后交叉

为区分“support 数过门”和“支持来自哪些距离格”，将 S1d 七分支 selected-cell 保留集合与 S1c 已核验的 GMTI05 / beam 50 目标真值距离 bin 做 evaluator-only 交叉。真值未进入 Production 或 support 估计。CSI phase sidecar 与各分支 `csi_post_range_phase_pre_c4_adapter` trace 在选定133格逐格相符，七分支最大绝对差为 `4.903e-9 rad`。

| CSI phase 向量对比 | 全133格平均绝对 wrapped 差 | 11个目标真值距离格 | 其余122格 | 向量差绝对值>0.35 rad格数 |
|---|---:|---:|---:|---:|
| SNR20 ON vs SNR30 ON | 0.141472 rad | 0.125783 rad | 0.142887 rad | 0 |
| SNR20 ON vs paired OFF | 1.775982 rad | 1.642302 rad | 1.788035 rad | 133 |
| SNR30 ON vs paired OFF | 1.824229 rad | 1.727056 rad | 1.832990 rad | 133 |

SNR20 与 SNR30 的 ON CSI 向量差异在目标真值距离格上并未更大；按该组的事后空间标签，差异也没有集中到目标格。这不排除目标通过估计器或处理链的全局作用影响向量，但不能据此把变化归因于直接的目标距离格污染。表中的 `>0.35 rad` 仅统计两向量逐格差值；C4 的±0.35 rad质量门实际作用于单元相对组相位中心的残差，二者不能混同。

| 条件 | support | 保留的目标真值距离格 | 其他保留格 |
|---|---:|---:|---:|
| SNR20 native ON | 25 | 3 | 22 |
| SNR20 ON 输入 / SNR30 CSI 向量 | 25 | 3 | 22 |
| SNR20 ON 输入 / OFF CSI 向量 | 21 | 3 | 18 |
| SNR30 native ON | 7 | 0 | 7 |
| SNR30 ON 输入 / SNR20 CSI 向量 | 11 | 0 | 11 |
| SNR30 ON 输入 / OFF CSI 向量 | 22 | 4 | 18 |
| target-free OFF 自身拟合 | 55 | 3 | 52 |

这里“目标真值距离格”只表示 ON 场景目标真值映射到的距离坐标；在 OFF 行中这些格并没有目标信号。对 SNR30 ON，SNR20 ON 交叉向量使 support 达11，且11格都不在目标真值距离坐标；OFF 交叉向量使 support 达22，其中4格落在目标真值距离坐标。两个干预得到的有效支持不等价，OFF 的较大 support 不能单独作为更好的参考或部署收益证据。

逐格相位差、真值格标签和七分支成员集合见 [`s1d_phase_vector_target_overlap.csv`](evidence/s1d/s1d_phase_vector_target_overlap.csv)；汇总及输入 SHA 见 [`s1d_phase_vector_target_overlap_summary.json`](evidence/s1d/s1d_phase_vector_target_overlap_summary.json)，生成脚本为 [`analyze_r22_s1d_vector_target_overlap.py`](evidence/s1d/analyze_r22_s1d_vector_target_overlap.py)。

## S1b 同场景 ON/OFF/TO 分量上下文

为检查目标能量是否确实落在目标真值格，并观察该输入变化与相位向量差的空间关系，对已有 S1b `SNR30 / GMTI05 / beam 50 / azimuth 52 / range 1065–1197` 的 ON、target-free OFF、target-only TO 阶段 trace 做了离线逐格审计。没有重跑 Stage2 或 Production。S1b 的 period-4 ON/OFF 输入 SHA 分别与 S1d 的 SNR30 ON/OFF 输入 SHA 一致；TO 输入 SHA 也由 S1b manifest 和实际文件逐字节复核。

在 `raw_dbs_pre_range_phase`，ON−OFF−TO 的复数闭合相对 L2 为 F1 `0.009367`、F2 `0.018056`；`csi_dbs_pre_range_phase` 分别为 `0.010316`、`0.018056`。TO-only 的平均幅度在11个真值距离格上明显高于其余122格：F1 为 `100.889` 对 `11.519`（8.76倍），F2 为 `89.161` 对 `9.452`（9.43倍）；约71%的 TO 分量能量位于这11格。由此可确认目标分量在选定行内具有明显空间局部性。

但 ON 与 OFF 的相位向量变化没有在这些目标格上变大：raw phase 的平均绝对 wrapped 差为目标格 `0.713787 rad`、其他格 `0.770182 rad`；CSI phase 为 `1.824464 rad`、`1.941023 rad`。逐格 TO 幅度与 ON/OFF 相位差的 Pearson 相关在约 `−0.18` 至 `−0.21`，仅作单场景描述，不作统计推断。它不支持“目标真值格处的直接能量越强，相位向量局部变化越大”这一简单解释；不能据此排除目标对 CSI 估计器的全局非线性影响或距离/方位扩展。

必须保留实现边界：这组上下文来自 S1b 的 Mode-A reference-assisted 诊断，执行源码 commit `1ed2a2e67a88a918858784be81794238ef112c7d` 的工作树当时为 dirty，Production binary SHA-256 为 `16cb0f2d35596ff98ab8bdcbc9571b972b99e217053cf0b1b7f6b49168c8c998`；它不等同于 S1d 的 Mode-B binary/source，不能替代 Mode-B 结论。另在 CSI range-phase 后，F2 的 ON−OFF−TO 相对 L2 增至 `0.787703`，显示逐分支校正后不再满足线性分量闭合，不能把 TO-only 校正结果当成混合 ON 的部署反事实。

逐格复数值、phase、目标标签与 closure residual 见 [`s1b_target_component_phase_context.csv`](evidence/s1d/s1b_target_component_phase_context.csv)；输入身份和分阶段汇总见 [`s1b_target_component_phase_context_summary.json`](evidence/s1d/s1b_target_component_phase_context_summary.json)，生成器为 [`analyze_s1b_target_component_phase_context.py`](evidence/s1d/analyze_s1b_target_component_phase_context.py)。原始 Stage2 输入仍只在本机 `outputs/`，GitHub 快照提供 SHA 和紧凑派生表，不包含原始二进制。

## 解释边界

可支持的结论是：**在这一固定 Production 输入和失败组上，CSI range-phase 向量来源对 robust-phase 筛选成员及 support count 有因果影响。** 对 SNR30 输入，SNR20 ON 向量让支持数从7增至11，OFF 向量让它增至22。

补充 S1b 提供了目标分量在真值距离格局部集中的证据，但同场景 ON/OFF 相位差并未集中在这些格上；它削弱了直接局部目标格污染解释，仍未区分全局 CSI 响应、范围/方位扩展和实现差异。S1b Mode-A 与 S1d Mode-B 的执行版本不同。配对 OFF 仍是 privileged 诊断向量，且其 support 集合与 ON 交叉结果并不相同。没有比较目标幅相保持、杂波残余、SCNR、Detection、Pd/Pfa、径向速度或方位误差，也没有独立 seed 重复。

因此，不能把支持数恢复称为 C4 方法修复或任务性能改善；不能据此恢复 R22 Screening，不应降低 support 门槛、删除失败组、回退 Current，也没有 AI 必要性证据。

## 交付状态与证据

- `IMPLEMENTED`：period-correct 2×3 runner 与独立 support membership 重算。
- `EXECUTED`：7个本机 Native CUDA Production 分支，均返回0；Stage2 未运行。
- `EVALUABLE`：该固定组的相位来源干预、support count、成员集合和生产记录可交叉核验；更广的物理根因不可唯一评估。
- `PERFORMANCE_VERIFIED=false`：没有 GMTI 任务级性能对照或统计验证。
- 精简审查证据：[S1d evidence](evidence/s1d/)，128个登记文件，约3.2 MB，逐文件大小和 SHA-256 已复核；包含原 S1d 七分支 evidence、S1d 目标格交叉及本次 S1b ON/OFF/TO 逐格上下文。S1b 派生审计所需的3份 trace 和6个 phase-vector sidecar 以原始字节放入紧凑包，并保留源路径/大小/SHA。原始 Stage2 `.bin` 仍未上传；本机原始运行输出没有改动。
- **复现限制：** GitHub 精简包含选定组逐格 F1/F2、运行 XML/日志和身份 manifest，但没有原始 Stage2 `.bin` IQ；可独立重算选定组 support，不能从该 GitHub 快照端到端重放 Production 或独立重算输入文件内容。
- 本地完整运行目录：`outputs/r22_s1d_period_corrected_crossover_20261031/`，约8.2 MB；紧凑 Git 证据约2.6 MB。高价值审计证据已提交后，本地完整输出及两次无效试跑在 ChatGPT 审查完成后按清理台账移除。
- 矩阵恢复：[`r22_experiment_matrix.py`](../../../../../scripts/phase_r/r22_experiment_matrix.py) 对 `GATE_FAILED` manifest 显式拒绝 `--resume`。因此没有尝试重启矩阵；S1d 是单独批准的诊断实验，不改变该矩阵状态。
- 执行命令：

```bash
python3 scripts/phase_r/run_r22_s1d_phase_vector_crossover.py \
  --source-root outputs/r22_s1_c4_support_replay_20261029 \
  --output-root outputs/r22_s1d_period_corrected_crossover_20261031 \
  --pipe-binary /tmp/pgrcc_r22_s1d_cuda_20261031/GMTI_pipe_core \
  --simulator-binary build/simulate_stage2_statistical

python3 docs/research/phase_r/R22/support_failure_diagnostic/evidence/s1d/analyze_s1b_target_component_phase_context.py \
  --s1b-root outputs/r22_s1b_phase_boundary_20261029
```

审查完成前停止在这里。是否补做 C4 新版本或恢复 Screening，应由独立审查证据和用户决定。
