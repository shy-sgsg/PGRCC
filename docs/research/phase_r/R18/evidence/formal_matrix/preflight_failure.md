# R18 正式矩阵启动失败记录

日期：2026-10-25（UTC）

## 失败内容

首次正式 case 命令遗漏了显式 `--config configs/research/phase_r18_native_cuda.json`，runner 因而使用默认的 `production_hierarchical_calibration_delay_pilot.json`。该默认配置不包含 seed 606 / 6.7 m/s / 30 dB 的显式正式 case，runner 在 case 选择阶段以退出码 2 报错：

```text
[production_hierarchical_calibration_pilot][ERROR] selection overrides removed every explicit case
```

## 影响与处理

- 失败发生在选择阶段，未调用 Stage2 simulator 或 `GMTI_pipe_core`，未启动 CUDA，也未生成实验分支输出。
- 用 `resolve_selection()` 对显式冻结配置及相同 seed/速度/SNR 筛选进行离线核对，case 数为 1，场景身份符合 R18 冻结矩阵。
- 后续所有正式命令显式提供冻结配置路径。
- 紧接着一次重试的 `--evidence-root` 参数在 shell 命令中误插入空格，argparse 退出码 2；同样发生在 runner 启动前，未运行 CUDA。

该记录是命令配置错误，不是算法或生产链实验结果。
