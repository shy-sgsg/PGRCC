# Phase-I 双通道未知系统误差收束实施计划

> 设计依据：`docs/superpowers/specs/2026-09-15-unknown-system-error-phase-i-convergence-design.md`
>
> 本计划把同一条生产等效 F1/F2 数据链上的可观测性审计、首个
> TrackManager 校准闭环和文档交付串起来；它们共享状态向量、观测字段、四条件
> 命名和审计 manifest，不能拆成相互独立的实验口径。

## 当前实施状态（2026-09-16）

- Task 1：已完成；历史 fixture 缺失时仅 skip，已有 artifact 仍严格断言。
- Task 2：已完成；54/54 simulator-backed F1/F2 case 通过，包含 row-balanced
  rank/condition/cosine、pair-level machine-readable confounding、same-seed zero-control
  delta、source/axis metadata、物理 range/frequency/block coverage 和 seed stability。
- Task 3：已完成；真实 CUDA 4ch protocol TrackManager/PIPE local-input formal 实际施加
  blind/known delay correction 并通过 payload reverse audit。5-period、3-seed、2-velocity、
  2-SNR 共 12/12 case、36/36 branch 完成，所有 runtime/TrackManager/payload audit 通过，
  产物在 `outputs/track_delay_formal_4ch_local_20260916/`。较早的 SHM 尝试仍以 partial
  现场保留在 `/tmp/pgrcc_track_delay_formal_gpu_20260915/`，不与本次 local-input formal
  混算。
- Task 4：已完成；研究框架、当前实证报告、历史 framing note 和 decorrelation contract
  已写入仓库；Phase-II native 4ch STAP/JDL/DOF/CUDA 仍冻结，AI/Router 关闭。
- Task 5：静态、Python、原生 build、CTest、正式 observability 运行、4ch smoke re-audit 和
  12-case TrackManager formal 已完成；decorrelation sweep、target-off fixed-Pfa 和完整目标
  安全统计仍明确未完成。

## 约束和验收口径

- 只推进 Phase-I：四通道六 pair 仅用于调试/交叉验证；不新增 native 4ch STAP、JDL、
  covariance/loading、空间自由度或 CUDA 优化。
- 生产链保持 `4ch protocol IQ → (1,3)/(2,4) → F1/F2 → CTDR/phase/P38 → CSI →
  GO-CFAR → clustering/positioning → TrackManager/PIPE`；`ai_training=false`、
  `router_enabled=false` 写入配置和运行时 manifest。
- 所有 correction 结果必须区分 A0 Ideal、A1 Current + unknown error、A2 Known-error
  correction upper bound、A3 Blind estimated correction；A2 truth 只用于上限评估。
- TrackManager/PIPE 只能沿用生产 `TrackManager`、`track_debug`、同周期
  `Confirmed + matched_this_frame` payload audit。校正未真实施加时写
  `NOT_EVALUABLE`，不得回退为 Current 后宣称收益。
- 保留现有未跟踪 outputs 和用户改动；提交时只 stage 明确的源码、测试、配置、计划和文档。

## Task 1：先固定唯一历史 pytest fixture 失败（已完成）

**测试先行。**

1. 在 `tests/test_background_reuse_validation.py` 为 coherent-gate 历史 compact
   artifact 增加与 `test_valid_mapping_artifact` 一致的缺失文件分支；当两个 gate 的
   `csi_metric_tap_summary.csv` 均不存在时明确 `skipTest`，存在但不完整时仍失败。
2. 定向运行该测试，确认基线的 `StopIteration` 消失且 simulator mapping/impact
   assertions 仍实际执行。

**实现与验证。**

3. 用最小改动实现 fixture 兼容；不生成伪造 summary，不降低 gate 结果断言。
4. 运行 `python3 -m pytest -q tests/test_background_reuse_validation.py`。

## Task 2：建立可测试的 F1/F2 observability core（已完成）

**测试先行。**

1. 新增 `tests/test_two_channel_error_observability.py`，先覆盖：
   - 4 通道 shape/有限值校验和 `(1,3)/(2,4)` 融合顺序；
   - cross-channel phase、coherence、phase-vs-frequency、phase-vs-pulse、
     phase-vs-angle、range-block residual、CSI residual 的确定性合成信号；
   - central-difference 灵敏度、列尺度化 rank/condition/cosine、符号和零点控制；
   - 缺失角度/频率/脉冲、维度错误和非有限输入的明确失败；
   - confounded 列返回 `not independently observable from current two-channel data`，
     并带外部先验建议字段。
2. 先运行定向测试并确认新模块尚不存在导致失败。

**实现。**

3. 新增 `scripts/audit_two_channel_error_observability.py`，把纯函数放在可 import
   的模块级 API 中：
   - `fuse_protocol_channels_to_f1_f2` 只实现生产 pair fusion；2ch 输入仅作为明确的
     F1/F2 调试适配，不参与四通道科学结论；
   - `compute_f1_f2_observables` 输出统一观测字段及 `source_id`、range/angle/pulse
     分组信息；velocity 的 ridge、P38、CTDR、slow-time 候选分列输出；
   - `central_difference_sensitivity`、`classify_observability` 保留 raw/scaled
     matrix，并结合 rank、condition、列余弦、响应符号、显著观测数量和零点控制分类；
   - `build_case_config` 只注入一个状态参数，参数单位来自 CLI；默认扰动量级为 delay
     ns、phase deg/pulse、geometry mm、servo/velocity/yaw 的小扰动，不能把一次运行
     的样本数/阈值固化成算法门槛；
   - CLI 以 Stage2 simulator 和现有 raw packet parser 为数据源，生成 zero/plus/minus
     cases，先 F1/F2 融合再计算观测量；写出 manifest、case manifests、observables
     CSV、raw/scaled sensitivity matrix、stability/classification CSV 和 Markdown。
4. manifest 必须记录 commit、dirty 状态、完整命令、配置/模板 hash、输入身份、设备探测、
   输出文件和 `ai_training/router_enabled`；raw BIN 仅在本次输出目录中生成并在汇总后按
   明确路径清理，不能覆盖已有 output root。
5. 运行定向单元测试、`py_compile` 和一个最小 simulator-backed smoke；失败时按真实
   traceback/输入修复，不用放宽容差掩盖问题。

## Task 3：让首个 TrackManager delay correction 真正进入生产链（已完成）

**测试先行。**

1. 新增 `tests/test_channel_delay_correction.py`，覆盖：
   - 合法 2ch float32 protocol packet 的 header/非目标 channel 保持不变；
   - synthetic fractional delay 的频域校正方向和残余误差；
   - packet size、pulse length、channel count、采样率和目标文件缺失时的明确错误；
   - estimator 输出只来自 calibration raw，不接受 truth 参数。
2. 新增/扩展 `tests/test_track_manager_e2e_contract.py`，先断言 branch contract 要求
   Current/Blind/Known 三个分支、`correction_applied`、source、estimate/known value、
   residual，以及 correction 未施加时 `NOT_EVALUABLE`。
3. 先运行上述测试，确认生产实现尚未满足这些断言。

**实现。**

4. 在 `scripts/estimate_channel_delay.py` 增加带边界校验的 float32 protocol packet rewrite
   helper，复用现有 phase-vs-frequency estimator 和 fractional-delay correction；输出
   correction audit，不改变 TrackManager/CFAR 的算法语义。
5. 扩展 `scripts/run_track_manager_e2e.py`：
   - 同一仿真配置/生产 XML/PIPE 规则生成 T0 Current unknown delay、T1 Blind estimated
     correction、T1K Known-error correction upper bound；
   - calibration raw 为 target-free 或等价独立校准输入，blind 不读取 truth；known 只在
     upper-bound 分支使用 truth；
   - 默认 formal 为 5 periods、多个 seed、至少两种 target velocity 和两种 SCNR，CLI
     可缩短 smoke；
   - T1/T1K 在进入生产 runner 前 assert correction metadata，失败写 `NOT_EVALUABLE`；
   - 生产输入固定为 4ch protocol IQ，先融合为 F1/F2；分支 manifest/CSV 记录 delay truth、
     estimate、applied/residual、packet layout、condition、detection/track/payload audit 路径；统计 cell/cluster/protocol/track false alarm、
     Track Pd、confirmation latency、continuity、ID switch、position/velocity/angle RMSE。
6. 保留旧 formal output 目录，不覆盖；新 run 使用独立含义明确的 output root，raw 在
   汇总并 hash 后清理，compact CSV/JSON/日志/配置保留。
7. 运行纯函数测试、TrackManager contract 测试、一个真实 CUDA 4ch smoke；确认每条 PIPE
   payload 能反查同周期 Confirmed/matched detection，且 T1/T1K correction_applied 为 true。

## Task 4：固定后续 decorrelation 入口和研究文档（已完成）

1. 新增 `configs/research/two_channel_decorrelation_study.json`，只表达统一场景和
   manifest contract：temporal clutter `rho`/internal clutter motion 扫描、Current /
   phase-only / complex LS-Wiener / robust LS / coherence-aware 五种方法，明确不启动
   AI/Router、不扩展四通道 STAP；配置字段有解析/校验/示例说明。
2. 更新 `AGENTS.md`、`README.md`、`docs/AI_CSI_研究进展.md`、
   `docs/AI_CSI_33_真实系统误差参数与可观测性分析.md`，新增
   `docs/AI_CSI_34_双通道系统失配与稳健CSI研究框架.md` 和
   `docs/AI_CSI_35_双通道系统误差可观测性分析.md`：
   - 明确 Phase-I/Phase-II 边界、生产链、状态向量和观测向量；
   - 区分 six raw pair debugging 与 F1/F2 scientific input；
   - 说明 servo/yaw、velocity、geometry、delay/phase drift 的可观测性结论和所需外部先验；
   - 结果章节只引用本轮 manifest/CSV 的实际值，未运行项标成 pending/未验证。
3. 文档中同步当前 TrackManager/PIPE 状态、A0–A3 定义、recovery 公式、metric direction
   和复现命令；不回写历史四通道数字。

## Task 5：实际运行、汇总和质量闸门（已完成，temporal decorrelation 后续）

1. 检查空间、`nvidia-smi`、当前 branch/status；先跑 observability pilot，再跑
   4ch TrackManager CUDA smoke/formal，保留命令、退出码、配置、输入 hash、GPU 状态和产物。
2. 读取实际 CSV/manifest，生成/更新 `AI_CSI_35` 的结果和限制；不把退出码 0 或文件存在
   当作算法通过，逐项检查 rank/condition/confounding、参数误差、phase/coherence、
   CSI/CFAR/Track 指标和 PIPE 反查。
3. 运行最终检查：

   ```bash
   python3 -m pytest -q
   python3 -m py_compile scripts/audit_two_channel_error_observability.py scripts/estimate_channel_delay.py scripts/run_track_manager_e2e.py
   cmake --build build -j4
   ctest --test-dir build --output-on-failure
   git diff --check
   ```

4. 审查 staged path，只提交本阶段明确源码/测试/配置/文档/计划；不纳入已有用户改动、
   未跟踪 raw 输出或敏感信息。完成交付检查后按仓库规则创建普通提交并推送当前远程分支，
   禁止强推/改写历史；若远程目标或权限不明确则停下报告。
