# AI-CSI-25：V2 Final-Gate 方法学审计

## 目的与边界

本审计直接读取已保留的 V2 formal compact evidence，不重新运行 CUDA，也不修改
原始 CSV/JSON。V2 的 raw CUDA source commit 为 669df38、clean worktree；后续
metric/gate 修正提交为 6a8f05d、4d777b3、1445f3f。由于 target-preservation
口径是在看到 Test-V2 后才新增/改变，V2 在本报告中降级为 development evidence，
ai_training=false，Final Gate 仍为 UNRESOLVED。

## A1：strict recovery

primary recovery 只使用：

    split == test
    role == target_off
    recoverable_headroom_db >= threshold
    active == true
    finite(recovery_ratio)

validation、target_on 均排除。J5/J6 的结果如下：

| 方法 | headroom | material scenes/rows | active finite | median | mean | p05 | p10 | p90 | minimum | CVaR5 | median bootstrap 95% CI |
|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---|
| J5 Selective | 0.5 dB | 17/17 | 6 | 0.8425 | 0.6590 | 0.0689 | 0.1097 | 1.0248 | 0.0280 | 0.0280 | [0.1097, 1.0248] |
| J6 Joint Surface | 0.5 dB | 17/17 | 6 | 1.0249 | 0.7764 | 0.1275 | 0.1754 | 1.1289 | 0.0797 | 0.0797 | [0.1754, 1.1289] |
| J5 Selective | 1.0 dB | 8/8 | 0 | — | — | — | — | — | — | — | — |
| J6 Joint Surface | 1.0 dB | 8/8 | 0 | — | — | — | — | — | — | — | — |

因此历史 0.8426/1.0249 与 strict 0.5 dB median 接近，但 strict 统计只来自
6 个 active finite cases，1 dB sensitivity 没有 active finite case；不能据此宣称
稳定的 confirmatory held-out recovery。

## A2：strict production CFAR 与 paired bootstrap

Pfa/false-cluster 只使用 split=test AND role=target_off，共 64 个 scene、448
条 J0–J6 target-off rows。每个 candidate 与 Current 按同一 scene 配对，bootstrap
unit 为 scene；同时报告所有 J1–J6，不只报告 J5/J6。

| candidate | metric | candidate - Current mean | paired 95% CI | P(candidate ≤ Current) | 判读 |
|---|---|---:|---:|---:|---|
| J5 | Pfa | +6.59e-6 | [-7.73e-6, +2.29e-5] | 0.6875 | no resolved difference |
| J5 | false clusters | +1.9375 | [+0.7969, +3.3438] | 0.7031 | significant regression |
| J6 | Pfa | -1.27e-5 | [-6.10e-5, +1.75e-5] | 0.7344 | no resolved difference |
| J6 | false clusters | +2.0938 | [-0.4848, +4.7656] | 0.6875 | no resolved difference |

只有 paired CI 不跨零时才使用 significant regression/improvement；否则统一写
no resolved difference。

## A3：Pd 命名修正

V2 target_rows.csv 中的 legacy_hit 只表示 target-on ROI matched detection。
本审计将其重命名为 matched_target_on_pd，并明确：

    causal_pd = not available from V2 target_rows

真正的 causal Pd 必须等待 Phase B 的同一 truth ROI paired OFF/ON detection：
paired_causal_hit = on_hit AND NOT off_hit。

## 证据与复现

结果目录为 outputs/v2_final_gate_reanalysis/：

- recovery_summary_test_only.csv、recovery_stratified_test_only.csv；
- production_pfa_target_off_only.csv、paired_scene_delta_bootstrap.csv；
- target_rows_matched_target_on_pd.csv；
- reanalysis_manifest.json。

复现：

    python3 scripts/reanalyze_v2_final_gate.py \
      --formal-dir outputs/physics_adaptive_selective_v2_formal \
      --output-dir outputs/v2_final_gate_reanalysis

本阶段没有 CUDA 重跑，没有训练 AI；target-transfer 的新场景审计见
AI_CSI_26_TargetTransfer与目标保护审计.md。
