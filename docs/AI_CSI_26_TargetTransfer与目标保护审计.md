# AI-CSI-26：Target Transfer 与目标保护审计

## 目标

V2 的 target-on/target-only peak ratio 不是 primary target-preservation 指标：
target-on 同时包含目标、残余杂波、噪声和 coherent interaction，target-only
还可能被重新估计 calibration。Phase B 改为同场景、同 seed、同 geometry、同
mismatch、同 calibration source 的三角色 paired audit：

    OFF = C + N
    ON  = S + C + N
    TO  = S-only
    DeltaY = Y_ON - Y_OFF

J5/J6 的 primary correction parameters 只从 OFF 估计，然后 freeze 并完全相同地
作用于 OFF/ON/TO；ON-derived calibration 只作为 deployment check，不能进入 primary
transfer 指标。

## 指标

primary：

    L_causal =
    10log10(sum_ROI |DeltaY_candidate|² /
             sum_ROI |DeltaY_Current|²)

secondary：

    L_target_only =
    10log10(sum_ROI |Y_TO_candidate_fixedcal|² /
             sum_ROI |Y_TO_Current_fixedcal|²)

同时保留 integrated ROI energy、peak transfer、range/Doppler centroid shift、
peak-bin shift、ROI spreading，以及每个 target 的 compact complex sums/energy/
peak statistics。不会提交完整 NPY。

paired detection 使用严格定义：

    paired_causal_hit = on_hit AND NOT off_hit

并记录 on/off CFAR margin 与 delta margin；只有这个 paired 指标才能称
paired_causal_pd。

## 运行设计

scripts/audit_target_transfer.py 提供两个模式：

1. snr-screen：12 个 fresh scenes，先找 Current Pd≈0.2–0.8 的 SNR transition；
2. audit：24 个 fresh scenes，覆盖 M1、M2、M1+M2、M1+M3、M2+M3、
   M1+M2+M3，覆盖 D1P0/D0P1/D1P1 的 active candidate cases。

raw runtime 在每个 scene 完成 compact extraction 后删除，只保留 CSV/JSON、manifest
和资源快照。正式结果写入 outputs/target_transfer_audit/，SNR screen 使用独立
输出目录，不与 final Test-V3 混用。

当前阶段仍为 ai_training=false；target-transfer 结果完成前不进入 AI 讨论。
