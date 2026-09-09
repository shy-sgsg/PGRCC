# PGRCC-v1 Oracle Headroom Audit 与数据集门禁报告

## 结论

本阶段停在 Oracle Headroom Audit，未进入数据集构造和 AI 训练。

在新的、未复用 V2.1 frozen benchmark 的 16 个 scene/seed、3472 个
`scene × seed × Doppler local region × range block` region 上，bounded complex
residual + confidence gate 没有形成稳定的多指标 headroom：

| 项目 | 实际结果 |
|---|---:|
| 完成 case / 失败 case | 16 / 0 |
| region 数 | 3472 |
| Pareto 候选行 | 7409 |
| strict worthwhile region | 0 / 3472 = 0% |
| Current 保持 Pareto-optimal | 74.42396% |
| 诊断 max-SCNR gain > 0 | 0.23041% |
| 诊断 max-SCNR gain ≥ 0.25 dB | 0.11521% |
| 诊断 max-SCNR gain 均值 / 中位数 / P95 | -0.00392 / 0 / 0 dB |

因此 manifest 的 Go/No-Go 结论为 `NO_GO_ORACLE_NOT_SUFFICIENT`。按照任务门禁，
没有生成 `pgrcc_v1_dataset.json`，没有把 formal 470-case matrix 当训练数据，
也没有训练或评价 PGRCC-v1；这不是遗漏，而是 Oracle 未通过后的必要停止。

## 审计范围与接口

实现和复现入口为：

- 配置：[pgrcc_oracle_audit.json](../configs/research/pgrcc_oracle_audit.json)
- 脚本：[build_pgrcc_oracle.py](../scripts/build_pgrcc_oracle.py)
- 源码提交：`c9cfcc4`
- 最终 manifest 记录的 source commit 与运行时 `worktree_dirty=false`

16 个新 seed 为 `20261201`–`20261216`，包含 9 个 dedicated factor-family case
和 7 个 deterministic LHS mixed case。通道 impairment 场景使用同 seed 的独立
background 生成；无 impairment 场景使用 Stage2 paired background。V2.1 final
screen、formal、velocity、ROC、transition、mixed stress 均未作为训练输入。

每个 region 搜索三个物理 base expert：Current P38、Row complex LS、Robust
Row-LS/IRLS，并搜索：

```text
alpha_AI = alpha_phy * exp(delta_log_amplitude) * exp(j * delta_phase)
Y = (1-g) * Y_Current + g * Y_corrected
```

初始边界为 `delta_log_amplitude ∈ [-0.5, 0.5]`、
`delta_phase ∈ [-π/4, π/4]`，gate 为 `{0.5, 1.0}`，另保留精确 Current
`g=0` fallback。幅度和相位最优解均未达到扩展触发比例，因此没有无依据地扩大
搜索边界。

推理特征只来自物理观测：P38、coherence、F1/F2 能量比、phase residual、有效
样本率、texture heterogeneity、clutter-ridge/support-edge 距离、Current tail、
Robust-LS condition/confidence。target truth、paired C+N truth、未来输出指标、
Oracle candidate 指标和 scene mapping 均未进入 inference feature。

## 多指标判定

worthwhile policy 在运行前固定为：SCNR 至少提升 `0.25 dB`，target preservation
不得低于 `-0.25 dB`，P95/CVaR95 tail 和 background Pfa 不得恶化，CFAR margin
不得下降超过 `0.25 dB`。搜索先构造 Pareto front，再在满足政策的候选中用非加权
字典序选择代表解；没有用 `argmax SCNR` 代替多指标 Oracle。

诊断视角与 strict Oracle 分开保存。max-SCNR 诊断候选的 local screen 均值为：

- SCNR gain：`-0.00392 dB`
- target preservation：`+0.00273 dB`
- residual P95：`-0.29859 dB`
- residual CVaR95：`-0.38473 dB`
- background Pfa：`+8.53e-05`
- target CFAR margin：`+0.00321 dB`

这些数字说明少数区域存在互相冲突的局部 trade-off，但没有形成稳定的 bounded
complex residual headroom。每个 case 另外对 Current、max-SCNR、min-tail、min-Pfa
和 max-margin 代表候选执行了完整全图 GO-CFAR 重算，共 80 条 exact 记录。max-SCNR
代表的 exact SCNR gain 均值为 `0.16187 dB`，但 exact residual P95/CVaR95 均值
分别为 `+0.00151/+0.01984 dB`，exact background Pfa 均值增加 `6.52e-06`；
其改善不稳定，不能作为可学习 headroom 证明。Current exact identity 的所有
delta 均为 0。

物理特征对诊断 max-SCNR gain 的 grouped ridge 只作探索性分析：

- leave-one-seed-out：RMSE `0.16629 dB`，R² `-0.00709`
- leave-one-factor-family-out：RMSE `0.77086 dB`，R² `-20.64199`

这不支持用当前特征稳定预测 Oracle gain。strict label 全部为 Current fallback，
其 grouped R² 为 NaN 是常数标签的正确结果，不应被解释成训练通过。

## 产物

最终产物位于 [`outputs/pgrcc_oracle/`](../outputs/pgrcc_oracle/)：

- [oracle_audit_manifest.json](../outputs/pgrcc_oracle/oracle_audit_manifest.json)
- [oracle_region_map.csv](../outputs/pgrcc_oracle/oracle_region_map.csv)
- [oracle_pareto_summary.csv](../outputs/pgrcc_oracle/oracle_pareto_summary.csv)
- [oracle_headroom_summary.csv](../outputs/pgrcc_oracle/oracle_headroom_summary.csv)
- [oracle_bound_saturation.csv](../outputs/pgrcc_oracle/oracle_bound_saturation.csv)
- [oracle_exact_representatives.csv](../outputs/pgrcc_oracle/oracle_exact_representatives.csv)
- [oracle_feature_correlations.csv](../outputs/pgrcc_oracle/oracle_feature_correlations.csv)
- [oracle_feature_predictor.csv](../outputs/pgrcc_oracle/oracle_feature_predictor.csv)
- `oracle_headroom_scatter.png`、`oracle_gain_by_family.png`、
  `oracle_feature_correlations.png`

manifest 明确区分：Pareto screening 的 local fixed-current-threshold Pfa/margin，
以及代表候选的 full-map exact GO-CFAR Pfa/margin。此前 paired impairment 配置
失败的运行现场保存在 `outputs/pgrcc_oracle_failed_paired_impairments/`；修正后正式
16-case 运行无失败。

## 资源与复现

正式运行前使用提权只读检查：RTX 3050 Laptop GPU，4 GiB 显存，最终运行前空闲约
3.01 GiB；主机可用内存约 8.5 GiB，swap 可用约 2.3 GiB，磁盘剩余约 74 GiB。
本审计实际调用的是 CPU `simulate_stage2_statistical` 和 Python 离线算子，没有
运行 CUDA production pipeline，因此结果不宣称 CUDA 生产验证。

最短复现入口：

```bash
cd /home/shy/AIR/SAR+AI/project/PGRCC
nvidia-smi
python3 -m py_compile scripts/build_pgrcc_oracle.py
python3 scripts/build_pgrcc_oracle.py \
  --config configs/research/pgrcc_oracle_audit.json \
  --out outputs/pgrcc_oracle_reproduce
```

下一阶段只有在新的物理路线（例如 support probability 或 expert selection）
重新产生稳定 Oracle headroom 后，才应创建 `pgrcc_v1_dataset.json`、训练集和
PGRCC-v1 模型；当前不应为了延续既定方案而训练无意义的 Δalpha 网络。
