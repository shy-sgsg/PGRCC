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
- [x] 在独立 Release build 目录构建；命令、CMake cache、编译器/NVCC、架构/flags、目标文件 SHA 和源码 commit 已记录于 evidence/build/native_release_identity.json。
- [x] CUDA GO-CFAR 数值 selftest 通过；12 场景的 valid-CUT geometry/分母、branch 与 final-union hit 审计均通过。真实经验 Pfa 仍不可评估，见 WP5。
- [x] seed101 pilot 及正式矩阵的 C0/C1/C2/C3 Native CUDA 分支使用固定 Production binary；运行日志、CSI、CFAR、Detection、TrackManager 和方法信息合同已归档。

**停止门：** 构建身份、二进制 hash、CFAR reference、CUT 分母/几何、生产日志或基本处理闭合任一失败，暂停方法比较，先定位根因。

## WP2：一手文献与传统方法能力审查

**文件：** `docs/research/phase_r/R18/01_literature_and_method_review.md`。

- [x] 审查 DPCA/ATI、相位/幅相校准、SCC/DDC 与稳健权重；LS/Wiener/ridge/稳健估计与有限样本；STAP SMI/加载/降秩/JDL 与协方差异质性。
- [x] 每篇材料注明 DOI/原始来源、系统/通道、仿真或实测、输入信息/自由度、Baseline、指标、结果与适用限制；SAR-GMTI、宽幅扫描与 MMTI 分层，不将结果直接迁移到本 Production。
- [x] 对仓库方法逐一标出实际入口、样本来源、估计自由度、输出域、部署信息条件、已有测试和未执行状态；明确并不存在一份“方法名”就等于强 Baseline。
- [x] 给出当前可复现强传统方法能力矩阵及最简单可行替代，不增加未授权算法实现。

## WP3：冻结信息公平性与实验设计

**文件：** `02_production_frozen_baseline.md`、`03_information_fairness_protocol.md`、`04_experiment_matrix.md` 及唯一 R18 runner 配置。

- [x] 核验 C0 Current/C1 Gamma=1/C2 SCC/C3 Robust DDC 的 F1/F2 输入口径和 adapter 实际逻辑。
- [x] 固定 Mode-B 仅以当前 ON 原始可观测 F1/F2 估计 SCC/Robust DDC；truth、目标标签、注入误差、OFF、TO/目标-only、不配对参考全部禁止进入估计器。
- [x] 固定 Mode-A C2/C3 为 `PRIVILEGED_PAIRED_OFF_REFERENCE`，只作为信息上限/科学参考，不作为在线部署性能。
- [x] 审查 secondary data 的模拟可行性与部署限制；预注册独立 seed 909 的 target-free C+N reference，统计同质性尚待执行数据验收。
- [x] 在查看 R18 正式结果前冻结 606/707/808、6.7/0.5 m/s、30/20 dB、零时延、三期/角色、杂波几何/功率、方法和评价分母。
- [ ] target truth ROI 与杂波 Doppler 支持的逐期交叠及其对目标保持的共同机会审计未闭合；valid-CUT geometry/分母已通过，但不能代替 truth-filtered intersection。保留为限制，不事后改评价门。

## WP4：Native CUDA pilot 与冻结矩阵

**文件：** `docs/research/phase_r/R18/evidence/` 下的配置、manifest、实际 XML/log、运行摘要和关键 map/CSV。

- [x] seed101 pilot 与代表性 Production 条件已运行；统一 binary、分支、面积杂波、causal triplet 和 CFAR 分母门均留有证据。目标自消的目标专属幅相闭合未通过本轮数据充分验证。
- [x] 执行冻结 12 场景矩阵（3 seed × 2 speed × 2 SNR）；每 case 21 branches，共 252/252 Production 分支通过执行门。
- [x] Mode-A C2/C3 配对 OFF 特权参考条件与 Mode-B 分层执行；没有把两种信息条件合并排名。
- [x] 各 case 的面积杂波报告、role 输入身份、F1/F2/CSI tap、runtime XML、CFAR 输出和 TrackManager 审计已封存；target truth 与 Doppler support 的逐 CUT 交叠仍未闭合。
- [x] pilot 后完成全部冻结 seed/speed/SNR。阶段执行结束不代表任务性能已验证。

## WP5：任务评价与失效机制

**文件：** `05_execution_and_quality_audit.md`、`06_signal_and_task_results.md`、`07_failure_mechanism_analysis.md`。

- [x] 汇总杂波统计、Gamma 参考 support、CFAR valid unique cells、truth-period Detection、非目标 Detection 行、TrackManager、角度/位置/地速指标；目标专属复增益/相位、SCNR、独立 Pfa、radial velocity 与 PK/PKR residual closure 明确标为 NOT_EVALUABLE。
- [ ] 每种方法可见 truth-period 的描述机会数已报告；独立 truth-filtered H0 false-CUT 分母和共同成功机会分母未闭合。hit-cell fraction 与非目标 Detection 行未当作 Pfa。
- [x] 已分析 area-clutter 几何、零误差范围、ON/paired-OFF 信息差、C3 support 变化及 CFAR→Detection→Track 评价缺口；低速/复杂杂波优先级与目标自消因缺少可识别证据，保持未判定。
- [x] 运行身份与设备状态留存；计时未用于稳定性能排名，未将不同计时口径混算。

## WP6：机会排序、方向裁决与审查交付

**文件：** `08_research_opportunity_ranking.md`、`09_next_research_direction.md`、`10_ChatGPT_review_request.md`、动态状态和追加决策日志。

- [x] 按任务收益证据、残余强度、物理可观测性、传统方法缺口、部署可得性和创新必要性排序候选问题。
- [x] 阶段裁决为 INCONCLUSIVE，并停止本轮方法性能排名；未训练模型、不自动启动下一阶段。
- [x] 更新 RESEARCH_STATUS.md 和决策日志；总纲保持原 blob。
- [x] 生成 ChatGPT 独立审查请求，要求先读指定 commit 的 Master Plan，再做阶段正确性与总体科研价值双层审查。
- [x] 已完成链接、Markdown、测试、差异和证据清单检查；R18 审核交付已提交为 `8eb3d300aff397ede68a21800217077ecfd228e5` 并推送至当前分支。

## 最终进度

- **执行完成：** 12 个冻结 CUDA 场景、252 个 Production 分支均完成；每 case 的连续面积杂波、causal triplet、valid-CUT geometry/分母与 final-union hit 门通过。
- **独立参考次级检查：** seed909 Stage2 和 C2/C3 Gamma Production 检查已运行；预注册 C3 exact-support gate 因 117 组中 116 组 support count 不同而停止评价。没有事后放宽门槛。
- **性能结论：** 所有 case 的经验 Pfa、PK/PKR residual closure 和 waterfall 均 NOT_EVALUABLE；PERFORMANCE_VERIFIED=false。报告仅给描述性目标周期命中与生产参数输出，不作胜负或显著性声明。
- **方向裁决：** INCONCLUSIVE；停止本轮方法排名。零时延矩阵不能回答未知误差损失、Known 恢复量或盲估计差距；不证明低速、复杂杂波或 AI 应成为下一主线。
- **保留证据：** 完整 case snapshot、运行 manifest、XML/log、CSI/CFAR/Detection/TrackManager 审计、summary CSV/图和失败记录均归档于 evidence/。
- **清理状态：** 2026-10-25 实测整个项目根目录约 2.8 GB，低于 20 GB；R18 可重建数组已在审计文件逐项封存并核验后清理。
- **总纲身份：** Git blob e637e3769bde7814298ff012d0f42301884e2985，完全未修改。
