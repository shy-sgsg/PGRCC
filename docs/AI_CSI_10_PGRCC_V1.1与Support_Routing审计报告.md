# PGRCC V1.1 与 Support Routing Oracle 审计报告

日期：2026-09-09。源码工作树在运行时为 dirty；运行时 manifest 保存了具体状态和
`source_commit`。本报告只记录本次 V1.1 与条件性 Support Routing 审计，不回写或改变
V2.1 冻结 benchmark。

## 结论

V1.1 细网格 bounded complex residual 仍未形成可进入 AI 训练的物理 headroom，正式门禁为：

```text
NO_GO_COMPLEX_WEIGHT_RESIDUAL
```

Support Routing Oracle 随后测试了 Current CSI 与 `F1` bypass 的 hard/soft 行路由，结论为：

```text
NO_GO_SUPPORT_ROUTING
```

因此本次没有生成训练数据，也没有启动 delta-alpha regression、complex-weight NN、Deep
Unfolding、PGRCC 原始网络或支持路由网络训练。Physics-AI 方向应暂缓，保留 Current CSI
作为基线并重新评估更有证据支撑的物理改进方向。

## V1.1 审计范围与方法

- 复用原有 16 个独立 Oracle case，不扩充 case 数。
- 每个 region 的 local screen 使用固定细网格：
  `delta_log_amplitude=[-0.20,-0.10,-0.05,-0.02,0,0.02,0.05,0.10,0.20]`、
  phase `[-15,-10,-5,-2,0,2,5,10,15]` degree、gate `[0,0.25,0.5,0.75,1]`。
- 流程是 local screen → Pareto → per-region top-K → bounded local refinement →
  regional top-K full-map exact GO-CFAR。没有对全部细网格候选逐一运行 full-map exact。
- Current identity 保留为精确 fallback；重复同 seed/同参数的 Current exact rerun 用于估计
  ε95，未人工放宽 non-inferiority 容差。
- inference 只使用物理/background 特征；target truth、paired C+N truth 和未来输出指标
  只用于评价、标签和审计策略选择。

正式结果：16 cases、3472 regions、31280 Pareto 输出、6968 full-map exact 候选（16 个
Current reference 加 6952 个区域候选），0 failures。重复性 CSV 为 112 行，所有 exact
ε95 均为 0。exact positive-headroom case fraction 为 0.6875，但 exact strict 和
non-inferior worthwhile case fraction 均为 0；best exact gain 的 mean/median/P95 为
0.5772/0.5129/1.3369 dB，近零（绝对值 ≤0.05 dB）占 0.3125。严格零退化 control 的
worthwhile region fraction 为 0.001440。

### V1.1 产物

目录：`outputs/pgrcc_oracle_v11/`

- `oracle_v11_region_map.csv`：每个 case/region 的 fine-screen、top-K、refinement 和
  strict/non-inferior 选择结果。
- `oracle_v11_pareto_summary.csv`：最终 regional Pareto 候选。
- `oracle_v11_exact_summary.csv`：regional top-K 的 full-map exact GO-CFAR 结果。
- `oracle_v11_repeatability.csv`：同 seed/同参数 Current rerun 和 ε95。
- `oracle_v11_headroom_summary.csv`：A 物理 headroom 的总体、case 和 factor 汇总。
- `oracle_v11_predictability_summary.csv`：feature correlation 与 grouped predictor，
  仅作可预测性诊断，不是生产模型证明。
- `oracle_v11_manifest.json`：配置、provenance、策略、完整计数和门禁结果。

## Support Routing Oracle

V1.1 no-go 后运行 12 个定向 support stress case，覆盖 dynamic support edge in/out、
split guard、clutter ridge/low speed、±high-speed outside support、nonuniform clutter、
sample loss 和 mixed impairment。四类汇总结果固定包含：Current dynamic、Current split、
best hard binary、best soft blend。

路由严格使用：

\[
Y=p_{clutter}Y_{CSI}+(1-p_{clutter})Y_{bypass},
\]

其中 `Y_bypass=F1`；hard route 的 `p` 只取 0/1，soft route 的 `p` 在 `[0,1]`。路由
特征只来自背景物理量：coherence、robust confidence/condition、support-edge distance、
clutter-ridge distance、background residual ratio 和 valid sample fraction。

正式结果：12 cases、1440 detailed route candidates、48 summary rows、0 failures；summary
中 target support status 为 inside 36 行、outside 12 行。best hard 与 best soft 的
worthwhile fraction 均为 0，正 SCNR gain 的 routed summary fraction 为 0.0833，因此门禁为
`NO_GO_SUPPORT_ROUTING`。所有四类 summary 均保留 SCNR、target loss、Pd、Pfa、CFAR margin、
residual tails、MDV、support-edge loss 和 fallback fraction。

### Support 产物

目录：`outputs/support_routing_oracle/`

- `support_routing_metrics.csv`：全部 hard/soft 候选和 Current 对照。
- `support_routing_summary.csv`：四类要求方法的逐 case 汇总。
- `support_routing_manifest.json`：覆盖场景、特征边界、策略和 no-go 判定。

## 复现入口

长任务前先检查资源：

```bash
nvidia-smi
free -h
df -h . outputs
```

单测和兼容 smoke：

```bash
python3 -m py_compile scripts/build_pgrcc_oracle.py \
  scripts/build_pgrcc_oracle_v11.py scripts/build_support_routing_oracle.py
python3 -m unittest discover -s tests -p 'test_pgrcc_oracle_helpers.py'
python3 scripts/build_pgrcc_oracle.py --case-limit 1 \
  --out outputs/pgrcc_oracle_compat_smoke
```

正式审计入口（使用新输出目录，拒绝覆盖非空目录）：

```bash
python3 scripts/build_pgrcc_oracle_v11.py \
  --config configs/research/pgrcc_oracle_v11.json \
  --out outputs/pgrcc_oracle_v11
python3 scripts/build_support_routing_oracle.py \
  --config configs/research/support_routing_oracle.json \
  --out outputs/support_routing_oracle
```

本次正式 offline Python/Stage2 replay 没有运行 CUDA production pipeline；两个 manifest
保存了完整命令、源码身份、dirty 状态和运行计数。本次长任务前的资源快照为：V1.1
启动前 RTX 3050 Laptop 4 GiB，P8，51°C，737 MiB 显存，系统 available 8.4 GiB、磁盘
可用 74 GiB；Support Routing 启动前同卡处于 P0/71°C/81% 外部负载，显存 1411 MiB，
系统 available 7.9 GiB、磁盘可用 74 GiB。两次运行均未发生 OOM；这些状态不是性能基准。
pilot 输出目录保留用于审计，不与正式统计混合。
