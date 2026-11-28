# PGRCC Phase-I 双通道未知系统误差收束设计

## 目标

本次变更把 PGRCC 当前研究主线收束为：在系统误差、模型失配和真实杂波失相干条件下，研究生产等效 F1/F2 双通道机载 GMTI 的稳健杂波对消，并把参数可观测性、确定性自校准、CSI/CFAR 和 TrackManager/PIPE 串成可复现闭环。

原始四通道最优 STAP、JDL、空间自由度和四通道 CUDA 结果保留为 `Phase-II preliminary evidence / future four-channel study`，在 Phase-I 结题前不再扩展。

## 边界与不变量

1. 生产等效输入链固定为 `4-channel protocol IQ → (1,3)/(2,4) pair fusion → F1/F2 → CTDR/phase calibration/P38 → CSI → GO-CFAR → clustering/positioning → TrackManager/PIPE`。
2. Phase-I 的科学结论必须由 F1/F2 观测支持。原始四通道六 pair 只作为调试或辅助证据；如果参数只能靠六 pair 独立估计，结论必须是 `not independently observable from current two-channel data`，并指出需要的 INS、servo encoder、factory calibration 或 temporal prior。
3. 每个系统误差使用四个信息条件：`A0 Ideal`、`A1 Current + unknown error`、`A2 Known-error correction upper bound`、`A3 Blind estimated correction`。A2 的 truth 只能用于评估上限，不能进入 blind estimator。
4. 方向统一记录：高值优指标的 `recoverable space = Known − Current`、`actual recovered = Estimated − Current`；低值优指标使用等价的正向损失差，并在 CSV/报告中记录 `metric_direction`。
5. `ai_training=false`、`router_enabled=false` 固定保持。只有在确定性估计已经证明存在稳定、可预测且影响 CSI/Pd/Track 的 residual 后，才允许另立 AI 评估任务。
6. PIPE 协议载荷必须继续由同周期 `Confirmed + matched_this_frame` 航迹和关联 detection 触发，并能通过生产 `track_debug` 和 payload audit 反查；原始 detection CSV 只能做离线交叉验证。

## 研究状态参数与观测向量

第一轮状态向量为：

```text
x = [channel_delay_error,
     constant_or_inter-pulse_phase_error,
     baseline_geometry_error,
     servo_angle_error,
     platform_velocity_error,
     yaw_error]
```

第一轮不增加 pitch/roll。实现允许把固定相位与相位漂移拆成两个观测维度，但不把它们误写成两个已独立可辨识状态。

统一 F1/F2 观测包括：

```text
cross-channel phase
phase-vs-frequency slope
phase-vs-pulse slope
phase-vs-beam-angle slope
range-block phase residual
P38 slope/intercept residual
clutter Doppler ridge location
clutter ridge-vs-angle trend
CTDR residual
slow-time phase slope
F1/F2 coherence magnitude
CSI residual power/structure
```

其中 phase-vs-frequency 主要服务 delay，phase-vs-pulse 主要服务 temporal phase drift，phase-vs-angle 需要同时比较 geometry/servo/yaw 的响应。velocity 的 ridge、P38、CTDR、slow-time 四类候选必须分别报告，不得用 robust average 把零误差不闭合或符号错误的候选掩盖。

## 实现结构

### 1. 研究边界文档

更新仓库 `AGENTS.md`、`README.md`、`docs/AI_CSI_研究进展.md` 和 `docs/AI_CSI_33_真实系统误差参数与可观测性分析.md`，新增 `docs/AI_CSI_34_双通道系统失配与稳健CSI研究框架.md` 作为当前阅读入口，并新增 `docs/AI_CSI_35_双通道系统误差可观测性分析.md` 记录本轮矩阵结果。旧四通道章节不删除，只移动到 Phase-II 归档语境。

### 2. 双通道可观测性审计器

新增 `scripts/audit_two_channel_error_observability.py`。审计器以现有 Stage2 simulator 和 raw-IQ/production tap 解析函数为数据源，在同一基础场景下只改变一个扰动参数，生成零点、正扰动、负扰动三类数据，并把原始协议通道先融合成 F1/F2 后再计算观测量。

默认扰动单位和量级为：delay ±ns、phase drift ±deg/pulse、geometry ±0.5 mm、servo ±0.05 deg、velocity ±0.05 m/s、yaw ±0.05 deg；命令行允许显式替换，但不得把某次实验的尺寸硬编码为永久算法门限。

审计器输出 `outputs/two_channel_error_observability/` 下的 manifest、case manifest、逐 case observable CSV、central-difference sensitivity matrix、列尺度/条件数/rank/cosine 汇总、零点/符号/斜率审计和 Markdown 报告。manifest 记录源码身份、dirty 状态、模板/输入 hash、完整命令、Python/平台/GPU 探测、配置和产物路径；raw BIN 在汇总 hash 后清理，不覆盖已有输出目录。

尺度化矩阵用于数值 rank/condition 判断，原始矩阵同时保留。稳定性至少按 range block、beam angle 和 seed 分组；分类不能只依赖单次 rank，而要结合列余弦相似度、响应符号、显著观测数量和零误差控制。

### 3. 首个 TrackManager 校准闭环

第一个正式 TrackManager 误差选 `channel delay`。原因是生产 runner 当前直接消费两通道协议，仓库已有 F1/F2 `phase-vs-frequency` delay estimator 和频域 fractional-delay 基础，能够在不改变 TrackManager/CFAR 逻辑的情况下生成校正前后输入。

扩展 `scripts/run_track_manager_e2e.py`：

- 在同一仿真回波和同一生产 XML/PIPE/TrackManager/CFAR 规则下，形成 `T0_Current_unknown_delay`、`T1_Blind_estimated_delay_correction`、`T1K_Known_delay_correction` 三分支；
- T1/T1K 启动前必须 assert `correction_applied=true`、校正源、估计/已知 delay 和残差；否则该 case 写为 `NOT_EVALUABLE`，禁止退化成 Current 后仍报告收益；
- formal 使用 5 个周期，覆盖多个 seed、至少两个目标速度和两个 SCNR；smoke 可缩短周期但不能改变协议语义；
- 所有分支保留同周期 detection snapshot、TrackManager `track_debug`、accepted-assignment audit 和 PIPE payload audit；统计 cell false-hit、cluster false alarm、协议检测 false alarm、false track、Track Pd、confirmation latency、post-confirmation continuity、ID switch、位置/速度/角度 RMSE；
- 输出 compact manifest/CSV/日志并清理已汇总的 raw 输入，确保可审查且不覆盖已有基准。

### 4. 后续真实失相干入口

新增一个只描述并固定后续实验口径的 research config/manifest contract：在所有可参数化误差完成 A0–A3 后，独立扫描 temporal clutter correlation `rho` 或 internal clutter motion，比较 Current CSI、phase-only、complex LS/Wiener、robust LS、coherence-aware cancellation。该入口不在本轮扩展四通道 STAP，也不启动 AI。

## 验证与交付

实现遵循 TDD：先为可观测性纯函数、delay packet correction、branch contract 和异常输入写失败测试，再实现最小逻辑并逐项回归。最终至少运行：

```bash
python3 -m py_compile scripts/audit_two_channel_error_observability.py scripts/run_track_manager_e2e.py
pytest -q
cmake --build build -j4
ctest --test-dir build --output-on-failure
git diff --check
```

需要 CUDA 证明的 TrackManager smoke/formal 必须在真实 GPU 上运行并记录 `nvidia-smi`；性能或 wall time 不与 CPU/offline 结果混算。最终阶段报告必须回答双通道中真正破坏 CSI 的误差、可独立估计与需外部先验的误差、Known 上限、blind 实际恢复、参数误差清除后的剩余失相干，以及下一步进入 estimator 优化还是 robust CSI。

