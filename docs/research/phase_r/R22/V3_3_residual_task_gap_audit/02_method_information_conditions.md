# 02｜方法信息条件与公平比较边界

本轮各方法沿用同一 V3.2 Native CUDA Production binary、冻结任务 evaluator、共用场景/真值组织和同一生产检测链。下表区分估计器实际可读的信息，不把 Known 或 paired-OFF 参考解释为在线盲估计。

| 条件 | 估计/校正信息 | truth 是否进入估计 | 本轮用途与比较边界 |
|---|---|---:|---|
| A0 | 无误差场景参考 | 否 | no-error endpoint；用于特定时延干预的参考条件 |
| C0 | `+4 ns` 未知误差的 Production Current，不作校正 | 否 | 当前失配 endpoint |
| PK | 真实物理时延只供 evaluator/known correction branch | 不进入 estimator | `Known physical correction upper bound`；只作为本误差条件的校正上界参考 |
| Fixed D3 | 历史 R9 冻结的固定校正量 | 否 | 静态确定性 Baseline；不读取本 case truth |
| Huber D3 ON | 同 case、target-present A1/Current 的 raw C1/C2；完整 case 的 10 个周期、1,300 pulses | 否 | 盲确定性估计；本次为全 case batch/offline fit，实时因果可用性和 estimator runtime 未验证 |
| M1 Physics Matched | 同 case、target-present A1/Current 的 raw C1/C2；物理时延模型加常复增益拟合；完整 case 的 10 个周期、1,300 pulses | 否 | 盲确定性估计；全 case batch/offline fit，较早周期处理使用了后续周期观测；实时因果可用性和 estimator runtime 未验证 |
| P1 | 配对 target-free OFF 输入估计物理时延，应用于 ON | 否 | privileged/诊断性信息条件，不能证明在线部署能力 |
| P2 Mode-A | paired-OFF 估计，Mode-A robust reference | 否 | 只与 C4 Mode-A / PKR Mode-A / P2 Mode-A 比较 |
| P2 Mode-B | paired-OFF delay estimate，Mode-B ON reference | 否 | 只与 C4 Mode-B / PKR Mode-B / P2 Mode-B 比较；覆盖不完整 |

V3.2 branch manifest 将 Huber/M1 估计器的输入角色标为 `A1_Current_unknown_error_ON_raw_C1_C2_only`，并记录 `truth_used_in_estimator=false` 与 `truth_used_to_apply_correction=false`。本轮逐 case 核对实际 branch correction 与 qualification 记录中 applied delay 的一致性，并从源码确认估计记录合并完整十周期观测（1,300 pulses）。这证明本次离线估计值使用 ON-only、truth-free 信息；不证明实时处理中可取得未来周期，也没有保存独立 estimator runtime。Known 的真值只作 evaluator-only 物理校正参数。

### 关键比较规则

- `A0 / C0 / PK / Fixed D3 / Huber D3 ON / M1` 是本轮主对照；在相同 28,800 个预定 truth opportunities/seed 下汇总检测结果。
- Recovery 按预先定义的配对公式：`recoverable = Known − Current`，`actual recovered = Estimated − Current`，`recovery ratio = actual/recoverable`。对固定 D3、Huber 和 M1 使用同一 C0/PK 基线。
- P2 Mode-A 使用 `C4-A / PKR-A / P2-A`，P2 Mode-B 使用 `C4-B / PKR-B / P2-B`。不得把 P2 直接拿来和 PK 排名。
- 不同估计器的 own-success RMSE有不同成功机会，不能直接当作同样本算法差。四方法共同成功 RMSE另列，并且只适用于归档保留的 period 8。
- Track false-confirmed counts 和 unmatched payloads 是目标存在场景的 TrackManager 输出摘要，不是独立 H0 false-alarm 率。

处理链可比性不消除信息可用性差异：P1/P2 使用 paired OFF；Huber/M1 使用 target-present ON 数据。两类结果在报告中分别列示，不能把 P1/P2 的近 Known 命中率当作 Huber/M1 的证据。
