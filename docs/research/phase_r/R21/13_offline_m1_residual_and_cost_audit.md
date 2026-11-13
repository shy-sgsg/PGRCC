# R21：M1 离线残差与计算代价审计

## 目的与范围

本补充审计只复用 R21 已保留的 seed1001/1002 输入，检查 M1 与 Known 的 F2 复数差距来自哪里、M1 数值优化是否收敛到正确的局部最优，以及现有确定性估计的离线计算代价。没有运行 CUDA、生成新 seed、改实验输入、训练 AI 或修改算法。

可复现脚本为 [`r21_offline_m1_diagnostic.py`](../../../../scripts/phase_r/r21_offline_m1_diagnostic.py)，逐 seed 机器结果为 [`seed1001 JSON`](evidence/results/r21_m1_offline_diagnostic_seed1001.json) 和 [`seed1002 JSON`](evidence/results/r21_m1_offline_diagnostic_seed1002.json)。脚本先按 [`retained binary manifest`](evidence/inputs/r21_retained_binary_manifest.json) 核对输入 SHA-256 与字节数；机器结果还记录了 runtime XML、实验 manifest、分析源码身份和执行环境。

## 信息隔离与计算方法

M1 仍只读取同 seed 的 B/ON 原始 C1/C2 数据及该分支 runtime XML。脚本检查估计器明确返回 `truth_used_in_estimator=false`。注入的 +4 ns 只在估计结束后用于标记预期剩余时延和计算 evaluator-only 目标函数分数，不参与候选时延选择。

脚本每个 seed 对完整 M1 路径做一次 warm-up 和三次计时。完整路径包含三段原始数据读取、协议解码、几何处理、FFT 和时延拟合，不含 Python 启动与输入 SHA 核对。另单独计时 Huber 加权相位斜率拟合；该子项不含共享的数据读取、几何和 FFT。

残差审计从各 Production `input_merged.bin` 按四通道协议重建 C1–C4、F1=`(C1+C3)/2` 与 F2=`(C2+C4)/2`，并与保存的 `r21_f2_physical_closure.csv` 逐行复算。复数频谱斜率按正、负频率分开拟合，作为描述性指标。优化检查记录 M1 原 401 点剖面、区间内局部极小值，并在已选局部括区做 1001 点密网格扫描，再与现有 golden-section 结果比较。

## 主要结果

| Seed | M1 delay | 相对 +4 ns 的剩余时延 | M1 F2 相对 RMS | Known F2 相对 RMS | M1 归一化模型残差 | M1 全路径中位耗时 |
|---|---:|---:|---:|---:|---:|---:|
| 1001 | 3.89065 ns | 0.10935 ns | 0.0123170 | 0.00008760 | 0.04581 | 2.373 s |
| 1002 | 3.63541 ns | 0.36459 ns | 0.0401842 | 0.00008818 | 0.04453 | 2.192 s |

### 剩余 F2 差距来自 C2 残差

- 两 seed、所有处理条件的 F1 相对 RMS 均为 0；C4 相对 A0 的 RMS 均为 0，且 C4 数组逐位相同。
- F2 误差功率分解与 `F2=(C2+C4)/2` 闭合：C4 项和 C2/C4 交叉项均为 0，F2 误差功率全部来自 C2，恒等式的绝对误差不超过 `2.8e-20`。所有非 A0 条件重新计算的 F2 RMS 与保存 CSV 的差为 0；A0 为零残差自参考。
- M1 校正后的 C2 正/负频率相位斜率分别为 seed1001 `0.11028/0.11033 ns`、seed1002 `0.36569/0.36606 ns`；与 evaluator-only 预期剩余 `0.10935/0.36459 ns` 接近。它们说明本次 M1–Known 物理闭合差距与单一 C2 剩余时延一致，不需要假设 C4 或 F1 同时退化。
- 融合 F2 的正/负频率斜率不一致：M1 为 seed1001 `0.09338/0.01678 ns`、seed1002 `0.34008/0.05468 ns`。这是 F2 中残余 C2 与未变化 C4 的复数混合量，不能把它直接解释成 C2 的物理通道时延，也不能据此宣称存在稳定的频率依赖通道误差。

### 数值优化未解释与 Known 的差距

两个 seed 的 401 点粗剖面在 `[-0.49, 0.49]` samples 内都只有一个内部局部极小值。seed1001 的 golden 结果与 1001 点密网格最优点相差 `−1.94e-6` samples；seed1002 相差 `+1.13e-6` samples。以真实 +4 ns（只作 evaluator-only 评分）计算的目标函数值分别比网格最优值高 `8.69e-6` 和 `1.2343e-4`；该分数差不是置信区间或时延误差估计。

因此，当前证据不支持把 M1 与 Known 的差距归咎于局部优化器没有找到既定目标函数的最优点。M1 估计误差与 C2 剩余时延相符；估计器的 `constant_complex_gain` 模型仍留下约 `0.041–0.044 rad` 相位 RMSE，但本审计没有改变模型或扰动几何，不能仅据此确认更高维模型失配或广义系统状态可辨识性。

## 计算代价与解释边界

每次估计读取 390 个脉冲、每脉冲 11,820 个样本，输入总量为 147,613,440 bytes。当前离线环境中，完整 M1 估计中位耗时为 2.373 s 与 2.192 s；Huber 稳健拟合子项中位耗时为 1.553 ms 与 1.511 ms。进程级峰值 RSS 在估计后约为 1.16 GiB，在完成诊断后约为 1.18 GiB。这些是当前 CPU 主机上的单次离线计时和 Python 进程高水位，不是 CUDA 或生产吞吐基准。

两 seed 的残差方向可用于定位本冻结数据中的 M1 误差来源，但它们仍是两个场景实例的描述性证据。每次三次计时不构成独立统计样本；没有测试几何扰动、跨 SNR/杂波条件或更一般的状态模型，也没有新增端到端任务性能评估。

## 状态与复现

- `IMPLEMENTED`: 离线审计脚本已实现，并记录输入、源码、runtime XML 和 manifest 身份。
- `EXECUTED`: seed1001、seed1002 均已对保留输入实际运行。
- `EVALUABLE`: 输入身份、F2 复数分解、局部优化数值检查和 CPU 计时在本审计定义下可复核。
- `PERFORMANCE_VERIFIED`: 否；本审计不构成新的 CUDA Production 或统计性能验证。

复现命令：

```bash
python3 scripts/phase_r/r21_offline_m1_diagnostic.py --seed 1001 --repeats 3
python3 scripts/phase_r/r21_offline_m1_diagnostic.py --seed 1002 --repeats 3
```

运行环境记录在 JSON 中；本次记录为 Python 3.13.5、NumPy 2.1.3、Linux、12 个逻辑 CPU。脚本要求本机保留 manifest 中列出的原始二进制输入。
