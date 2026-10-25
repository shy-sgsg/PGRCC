# R18 总实施计划

> 本计划分解用户授权的 R18 综合科研任务。依赖 R18 设计与验收要求，按 WP1–WP6 逐项闭环；不启动 R16 以外的新阶段、不训练 AI、不修改研究总纲。

**总体目标：** 在修复且身份冻结的 Native CUDA Production 链路中，识别传统杂波对消方法在不同速度与 SNR 下的任务性能边界，并分离算法差异与参考信息差异。

**科研定位：** 本阶段服务于研究总纲中的 airborne GMTI 杂波抑制、动目标检测和参数估计目标。当前已知风险包括 R14 曾运行 R7 修复前二进制、Mode-A 使用配对 OFF 特权参考、低速失效机制尚未区分物理可分性与处理/评价缺陷。R18 先锁定数值正确性和执行身份，再检验 Current、普通相减、SCC 与 Robust DDC 的端到端任务结果。阶段结果只用于有限场景的可行性与机制判断；是否具有统计功效按独立 seed 和目标机会数实报。

**基线身份：** 分支 `codex/unknown-system-error-next-stage`，任务起始 commit `5ac0f1828218565597533945759c1bdf48ab2e76`。研究总纲 Git blob `e637e3769bde7814298ff012d0f42301884e2985`；正文保持不变。

**方法与平台：** 原生 C++/CUDA Release、CMake、现有 Stage2 仿真器、生产 `GMTI_pipe_core`、Python 评价脚本。生产核心输入为四通道协议 IQ 经 `(1,3)/(2,4)` 融合得到 F1/F2；完整下游沿生产 GO-CFAR、Detection、TrackManager。

## 全局约束

- 使用 `USE_COREX=OFF` 的独立 Release 构建；执行期间所有 Production 分支必须调用同一 `GMTI_pipe_core` SHA-256。
- CMake CUDA 架构按本机 RTX 3050 Laptop 能力显式记录；实验源码 commit、工作树状态、配置、输入和实际二进制身份均进入 manifest。
- GO-CFAR 数值参考、CUT 几何/分母、unique union hits、Production 日志和几何诊断必须闭合；失败即停止性能解释。
- Mode-B 估计不得读取 truth、目标位置标签、注入误差标签、TO 或配对 OFF；ON 数据含目标导致的估计污染与自消须作为待测机制。
- Mode-A 必须标为 `PRIVILEGED_PAIRED_OFF_REFERENCE`；OFF 拟合样本同时用于 OFF/H0 评价时只报告样本内观测，不称为独立 Pfa。
- 所有算法条件保持同一物理输入身份和下游处理契约；Known/Truth/TO/未来数据不得进入估计器。
- 历史实验数据和用户已有未跟踪输出不覆盖、不删除；只清理本轮新建且已由紧凑证据替代的派生产物，并记录清理账本。
- 总纲 `docs/research/RESEARCH_MASTER_PLAN.md` 完全不变；动态结论写入 `RESEARCH_STATUS.md` 与决策日志。

## Review Focus

1. **错用或执行中替换 Production 二进制：** 通过受控二进制路径、逐分支命令身份和 SHA 一致门验证。
2. **GO-CFAR 几何或阈值数值错误：** 通过独立参考数值、valid-CUT 分母、geometry、branch/final-union hit 对照及 CUDA numerical selftest 验证。
3. **Mode-B 真值/配对信息泄漏或目标污染：** 逐角色合同、CSI adapter tap、reference provenance 和 ON 目标 ROI 后验审计。
4. **Mode-A/H0 被误写为可部署虚警率：** 报告标注 reference-in-sample；无独立 truth-filtered false-CUT 估计时 Pfa=`NOT_EVALUABLE`。
5. **把中间残余改善误当成 GMTI 收益：** 同时报 CSI/SCNR、目标保持、检测/错误输出、航迹、角度/位置/速度与有效分母。

---

## WP1：修复后 Production 数值正确性与身份门

**文件范围：** `scripts/run_production_hierarchical_calibration_pilot.py`、`scripts/run_track_manager_e2e.py`、必要时 `scripts/run_delay_stage1_formal.py`、现有 runner contract tests；R18 build/provenance 记录。

- [x] 已审计：主 runner 与 TrackManager runner 硬编码 `build/GMTI_pipe_core`；当前缺少直接选择隔离 Release 二进制的入口。
- [x] 先加失败测试，证明 runner 可选择显式隔离的 simulator/Production binary，并在预期 SHA 与实际 SHA 不一致时 fail closed。
- [x] 用最小向后兼容实现接入独立 build 路径；记录每次运行的 simulator/Production 文件身份，并在分支运行边界检查身份未变。
- [ ] 在新的独立 Release build 目录构建；记录命令、CMake cache、编译器/NVCC、架构/flags、目标文件 SHA 和源码 commit。
- [ ] 运行 CUDA GO-CFAR 数值正确性门：有效 CUT 几何、训练窗背景参考值、门限、GO 方向、fallback/直接重算计数、branch hits、final-union unique hits 与日志 hit counts。
- [ ] 对单个代表场景完成相同二进制的 C0/C1/C2/C3 Native CUDA 检查；所有处理、CSI、CFAR、检测、航迹和信息隔离门通过后才进入系统矩阵。

**停止门：** 构建身份、二进制 hash、CFAR reference、CUT 分母/几何、生产日志或基本处理闭合任一失败，暂停方法比较，先定位根因。

## WP2：一手文献与传统方法能力审查

**文件：** `docs/research/phase_r/R18/01_literature_and_method_review.md`。

- [x] 审查 DPCA/ATI、相位/幅相校准、SCC/DDC 与稳健权重；LS/Wiener/ridge/稳健估计与有限样本；STAP SMI/加载/降秩/JDL 与协方差异质性。
- [x] 每篇材料注明 DOI/原始来源、系统/通道、仿真或实测、输入信息/自由度、Baseline、指标、结果与适用限制；SAR-GMTI、宽幅扫描与 MMTI 分层，不将结果直接迁移到本 Production。
- [x] 对仓库方法逐一标出实际入口、样本来源、估计自由度、输出域、部署信息条件、已有测试和未执行状态；明确并不存在一份“方法名”就等于强 Baseline。
- [ ] 给出当前可复现强传统方法能力矩阵及最简单可行替代，不增加未授权算法实现。

## WP3：冻结信息公平性与实验设计

**文件：** `02_production_frozen_baseline.md`、`03_information_fairness_protocol.md`、`04_experiment_matrix.md` 及唯一 R18 runner 配置。

- [x] 核验 C0 Current/C1 Gamma=1/C2 SCC/C3 Robust DDC 的 F1/F2 输入口径和 adapter 实际逻辑。
- [x] 固定 Mode-B 仅以当前 ON 原始可观测 F1/F2 估计 SCC/Robust DDC；truth、目标标签、注入误差、OFF、TO/目标-only、不配对参考全部禁止进入估计器。
- [x] 固定 Mode-A C2/C3 为 `PRIVILEGED_PAIRED_OFF_REFERENCE`，只作为信息上限/科学参考，不作为在线部署性能。
- [x] 审查 secondary data 的模拟可行性与部署限制；预注册独立 seed 909 的 target-free C+N reference，统计同质性尚待执行数据验收。
- [x] 在查看 R18 正式结果前冻结 606/707/808、6.7/0.5 m/s、30/20 dB、零时延、三期/角色、杂波几何/功率、方法和评价分母。
- [ ] 核实 12 个正式场景的 Stage2 target truth ROI、杂波 Doppler 支持和 CFAR valid-CUT intersection；须以本次实际场景数据为证。

## WP4：Native CUDA pilot 与冻结矩阵

**文件：** `docs/research/phase_r/R18/evidence/` 下的配置、manifest、实际 XML/log、运行摘要和关键 map/CSV。

- [ ] 第一批：一个代表场景、统一 binary、C0/C1/C2/C3、必要的 Mode-A/Mode-B；确认方法真实生效、目标污染可见、无 NaN/Inf、CFAR/Detection/TrackManager 正常。
- [ ] 第二批：通过第一批后执行冻结的 12 场景矩阵（3 seed × 2 speed × 2 SNR）；核心 Mode-B 4 方法 × OFF/ON/TO = 最多 144 Production branches。
- [ ] Mode-A C2/C3 作为单独特权参考子矩阵；按 WP3 预先冻结的场景执行，不把它扩成所有方法/模式笛卡尔积。
- [ ] 每个 case 均检查 Stage2 报告实际连续面积杂波 packets/samples/power、有效 range/Doppler support、role 输入身份、F1/F2 tap、运行时 XML、CSI/CFAR 输出与 track-debug 证据。
- [ ] pilot 通过后完成所有冻结 seed/speed/SNR；不能以 pilot、单 seed 或自测完成 R18。

## WP5：任务评价与失效机制

**文件：** `05_execution_and_quality_audit.md`、`06_signal_and_task_results.md`、`07_failure_mechanism_analysis.md`。

- [ ] 同时报告杂波残余/功率、Gamma 稳定性/有效支持、目标复增益幅相保持、SCNR proxy、CFAR valid unique cells、目标检测/漏检/非目标输出、confirmed matched-track、关联连续性与可评价的角度/位置/速度误差。
- [ ] 固定 truth opportunity 和共同成功机会分母；分别报告每方法自身正确机会和共同机会；不将 hit-cell fraction 或非目标 detection row 当作 Pfa。
- [ ] 分析低速杂波重叠、ON 目标对 Gamma 的污染、自消、支持域/相位异质性、样本数、阈值/聚类/参数估计传播；用实际 CSI、CFAR 和候选 lineage 区分物理/算法/生产/评价根因。
- [ ] 分开 kernel/传输、Production branch wall time、Stage2 与完整 case wall time；若无稳定重复/设备条件不满足，只报告描述计时，不做性能排名。

## WP6：机会排序、方向裁决与审查交付

**文件：** `08_research_opportunity_ranking.md`、`09_next_research_direction.md`、`10_ChatGPT_review_request.md`、动态状态和追加决策日志。

- [ ] 按任务收益证据、残余强度、物理可观测性、传统方法缺口、部署可得性和创新必要性排序候选问题。
- [ ] 以证据给出 `CONTINUE / STOP / PIVOT`，决定是否有必要进入新的确定性物理方法创新；不训练模型、不自动启动下一阶段。
- [ ] 更新 `RESEARCH_STATUS.md` 和决策日志；总纲保持原 blob。
- [ ] 按 ChatGPT review template 生成审查请求，首段要求先读指定 commit 的 Master Plan，再做阶段正确性与总体科研价值双层审查。
- [ ] 完成链接、Markdown、测试、差异与证据清单检查；仅提交 R18 审核后的跟踪文件，普通提交推送至当前分支。

## 当前进度

- **已读：** R18 指令、当前研究总纲、动态状态、决策日志、`AGENTS.md`、README、Phase R 导航、Codex/ChatGPT 审查规范，以及 R7、R12–R17 的关键设计/结果/审计材料。
- **已实现：** runner 具备隔离 simulator/Production executable 入口和 SHA fail-closed 检查；定向 Python contract 测试此前运行 `69 passed`。此改动尚待以 R18 独立 Native Release 构建与实际 CUDA 验收。
- **已完成：** 一手文献/方法能力审查、核心四条件矩阵、独立参考信息边界与正式参数冻结；配置在 `configs/research/phase_r18_native_cuda.json`，冻结 SHA 记录待写入最终 manifest。
- **环境状态：** 提权 `nvidia-smi` 实读 RTX 3050 Laptop、driver 580.178.04、P8、38°C、6.20 W、6% GPU、551 MiB；普通沙箱不具备 GPU 访问，后续 CUDA 构建/运行均需沿用授权提权。磁盘剩余 26 GB；既有 `outputs/` 29 GB 与 `.scratch/` 278 MB 保持不动。
- **当前执行门：** 先提交 runner identity 改动与冻结设计，随后隔离 Native Release 构建、GO-CFAR 数值 gate、seed101 执行 pilot，再运行 12 场景正式矩阵并逐 case 紧凑化本轮证据。
- **未完成：** 新 build、CFAR 数值验收、pilot、正式矩阵、独立 reference 实际同质性、任务结果和方向裁决。
