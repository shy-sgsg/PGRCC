# R18：失败机制与不可评估项

## 主矩阵中的门控缺口

### 1. Pfa 不可评估

所有 case 的 CFAR valid-CUT 几何与分母都通过，但没有可匹配到目标真值并排除目标后的独立 H0 false-CUT hit 数。因此 12 个 decision matrix 均把 empirical cell Pfa 标为 NOT_EVALUABLE。Configured Pfa、hit-cell fraction、未匹配 Detection 行和聚类数都不能填补该分子/分母。

### 2. A0/PK/PKR residual closure 未闭合

现有冻结 Production 矩阵运行 C0/C1/C2/C3 及 A0 输入/参考角色，不包含分析器要求的完整 PK 与 PKR residual 条件。故 residual closure 与 waterfall 标为 NOT_EVALUABLE；这表示比较链缺少所需残差闭合条件，不表示已经观测到校正失败或物理不闭合。

### 3. TrackManager 通过不等于 ID-switch 可归因

生产 TrackManager 一致性审计在已记录分支上通过，未报告结构违规；但独立 ID-switch audit 缺少有效的 track-association-audit-v2 来源记录，case 汇总因此为 NOT_EVALUABLE。不能从 track ID 变化推断目标漏检、误关联或物理参数失效的具体根因。

### 4. 目标保持与参数指标只部分可评估

truth-period 检测、角度/位置/地速有逐分支数值，但没有完成目标专属 CSI 复增益与相位保持的共同机会闭合。地速是 ground-speed 指标，不是径向速度误差。Mode-A 与 Mode-B 信息条件不同，不能简单合并。

## seed909 独立参考停止原因

seed909 参考实验是次级可比性检查，不是独立性能评估：

1. 第一次启动在 Stage2 前因 Python 导入路径失败；失败 manifest/report 已保存在 attempt3 的 preflight_failures 子目录，没有生成实验数据。
2. 第二次启动的 seed909 Stage2 成功，实际产生 390 packets、1,952,655 area samples，平均功率 0.0225120；runner 误读取 seed606 的 A0_OFF 背景复用报告，把其 0 注入计数当作注入 gate，Production 未启动。错误和实际 area owner 均已记录于 [attempt2 报告](evidence/independent_seed909_secondary_attempt2/report.md)。
3. 第三次启动修正为读取 case manifest 指定的 A0_ON 注入 owner。有效面积杂波和预注册统计容差通过，C2 Gamma support 与 seed606 Mode-A OFF 相同；C3 的 117 个 group key 相同且状态均为 OK、truth_used_in_estimator=false，但 116/117 组 support count 不同，总 support 从 95,803 变为 92,859。

attempt3 manifest 记录实际 runner SHA-256 为 e6d0b2c635d838c69868b0ae7719c85614123630f5d35436584ad509d908a956；当前 runner 文件 SHA-256 为 719f86695ba278e2e150f4d9eee93ffeca99ab2f4a0a603f1472acebbd825fe6。执行后对 runner 的调整限于失败 manifest/report bookkeeping；原执行字节快照没有单独归档，因此本次记录保留执行 SHA，但不能从当前文件逐字节重建该版本。

C3 seed909 的 Production Gamma 分支实际返回成功，CSI tap、CFAR valid-CUT 与 TrackManager 生产审计也通过。冻结协议要求每组 support count 完全相等；观察到差异后没有放宽条件，因而未启动 seed606 的六个独立参考任务评价分支。attempt3 状态为 EXECUTED_WITH_GATE_FAILURE / NOT_EVALUABLE；不计算性能差值、不报告 Pfa、不作显著性判断。完整材料见 [attempt3 报告](evidence/independent_seed909_secondary_attempt3/report.md)和 manifest。

## 启动错误与后续处置

正式矩阵之前有两条命令配置错误：漏传冻结配置，及 shell 参数空格错误。两次都在 case selection/runner 启动阶段退出，未调用 CUDA。具体命令与影响见 [preflight failure record](evidence/formal_matrix/preflight_failure.md)。之后正式矩阵使用显式配置完成。

R18 按冻结门停止，不重跑矩阵、不事后修改 support 门、不以输出文件存在或 branch return code 替代任务收益。R18 可重建数组已在保留逐 case 快照和清理台账后删除；项目根目录当前约 2.8 GB，低于 20 GB。
