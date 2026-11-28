# PGRCC R22 V2 六个工程 seed 独立科研审查请求

请将本提示词与 `outputs/r22_chatgpt_review_evidence_v3_six_seed_20261028.zip` 一起提交给 ChatGPT。证据包内含当前审查所需的治理文件、V2 实验设计、12 份运行 manifest、matrix / branch gate manifest、精简 case manifest 审查摘录、task/parameter/H0/track/target-level/association CSV、关键 CUDA 审计产物、源码快照和差异。压缩包不含完整 case manifest、Doppler opportunity 明细、可再生成 `.bin`/`.npy` 大数组或重复长表，因此不能从原始 IQ 完整重放。

## 必须优先读取

请先读取证据包中指定审查快照的：

1. `research_context/docs/research/RESEARCH_MASTER_PLAN.md`
2. `research_context/docs/research/RESEARCH_STATUS.md`
3. `research_context/docs/research/RESEARCH_DECISION_LOG.md`
4. `research_context/docs/research/CHATGPT_REVIEW_PROTOCOL.md`
5. `research_context/AGENTS.md`、`research_context/README.md` 和 Phase R 导航

之后再读 R22 执行契约、V2 设计与配置、六 seed 结果报告、运行及分支 manifest、`case_manifest_review_extract.json`、证据包所含 evaluation CSV、生产诊断、源码和代码差异。不能只根据本提示词摘要代替读取研究总纲。若任何文件无法访问或总纲 blob 不匹配，请列出缺项，并把受影响的整体科研判断标为 `NOT_VERIFIED`。

## 审查快照与执行身份

- GitHub 仓库：<https://github.com/shy-sgsg/PGRCC>
- 分支：`codex/unknown-system-error-next-stage`
- 本轮实验执行时 HEAD：`c1215354b4354fb598bc747eb799ff9995eeee58`
- 研究总纲 blob：`e637e3769bde7814298ff012d0f42301884e2985`
- 实验执行时工作区不是 clean。不要把执行 HEAD 当成完整源身份；以 12 份有效 `r22_run_manifest.json` 的 `source_identity.files`、binary SHA、机器配置及证据包中的源文件 diff 为准。
- Production binary SHA-256：`d9e4d2e0b8429c5415e94be1af0230f3c0148455c394dc6d661e7e6c13a4ad1e`，本机 Native CUDA，RTX 3050 Laptop，`USE_COREX=false`。
- Stage2 binary 版本：seed1930101 的 0/+4 ns 两个 case 使用 `ca8aa9eb8b7d97e5743a27e67c312e66a4efecbf2c76880db581192afd08ce8e`；seed1930102 修复后重跑及 seed1930103–1930106 使用 `47fa5ec34ca15cdc4edeb35226eefa941a78ee344aa2311e5e7984c811dcb40f`。
- 变更原因：Stage2 XML double serialization 增加 `max_digits10`，阻止 Rmin 从 `82802.6768996 m` 被舍入为 `82802.7 m`。seed1930102 的旧 0 ns 失败尝试使用旧 binary，`EVALUABLE=false`，已保留在包中；结果表只使用 `reference_0ns_rmin_precision_fix` 修复重跑。
- 请区分实验执行快照 `c1215354...` 与包含本审查报告/提示词的后续文档提交；如能访问 GitHub，请核对远端分支 HEAD 并分别报告，不能把本地证据包说成已验证远端源码。

## 阶段范围及目前记录

阶段：R22 V2 距离支持域修订后的六 seed 多目标 CUDA Production 工程矩阵。条件为同 seed 配对 0 ns A0 与 +4 ns 条件；每 seed 20 targets、10 periods、beam 50、目标径向速度 2.5 m/s、SNR 20 dB。最终有效矩阵为 12 个 case、216 个 0 ns Production 分支、324 个 +4 ns 分支和 72 个 Stage2 variant；所有最终 case `IMPLEMENTED=true`、`EXECUTED=true`、`EVALUABLE=true`，`PERFORMANCE_VERIFIED=false`。另有 seed1930102 的失败初跑，不能计入最终结果。

六 seed 主要任务命中数（每 seed 每方法 200 个 target-period opportunities；独立重复单位为 6 个 seed）：

| Seed | A0 0 ns | C0 +4 ns Current | PK Known | P1 blind | P2 Mode-A | PKR Mode-A |
|---:|---:|---:|---:|---:|---:|---:|
| 1930101 | 167 | 25 | 167 | 167 | 173 | 175 |
| 1930102 | 151 | 24 | 151 | 145 | 150 | 151 |
| 1930103 | 140 | 22 | 140 | 137 | 140 | 144 |
| 1930104 | 148 | 25 | 148 | 148 | 154 | 152 |
| 1930105 | 153 | 36 | 153 | 156 | 162 | 162 |
| 1930106 | 163 | 34 | 163 | 163 | 169 | 168 |
| 合计 / 比例 | 922/1200 (0.7683) | 166/1200 (0.1383) | 922/1200 (0.7683) | 916/1200 (0.7633) | 948/1200 (0.7900) | 952/1200 (0.7933) |

简要描述统计：`A0−C0=0.6300`；`PK−C0=0.6300` 且 PK 命中数逐 seed 等于 A0；`P1−C0=0.6250`，P1 相对 PK 低 `0.0050`，按命中比例计算的 recoverable-space 比例约 `99.2%`。target-free OFF 的 D3 Huber 时延估计六 seed 均值 `3.1232 ns`（样本 SD `0.1165 ns`，真实误差 4 ns，平均偏差 `−0.8768 ns`）。PK 的真值只在 evaluator-side correction 使用。以上不是确认性统计，也不能把 1,200 个 seed 内机会当作独立样本。

面积杂波证据：每个有效 seed 的 Stage2 报告都有 1,300 continuous-area packets、6,508,782 samples；六 seed 面积功率均值 `0.0226353`，范围 `0.0225109–0.0227849`，lag-1 rho 约 0.98。+4 ns 复用同 seed 的 C+N 背景，逐 period 对应关系见 `background_reuse_mapping.csv`。各 Production case 共用 range window `[666,1197]`；CFAR union valid CUT 目标值 `5,272,800`，应从实际 geometry 核对。

未闭合评价：360 条 OFF H0 记录均标为描述性诊断，不是独立 H0 seed；没有经验 Pfa。所有 `radial_velocity_mps` 有效机会为 0，缺失原因包括 Doppler ambiguity unresolved 和无匹配检测，不能报告径向速度 RMSE。压缩包含 CSI taps、beam stage、CFAR geometry、track / detection / target association / parameter CSV，需独立检查杂波残余、目标保持、SCNR、航迹和可评价参数指标。

资源边界：当前完整 runner 的留存体量外推到 594-case 设计约 240 GB，串行时间粗估约 141.7 h；这不是实际执行成本。当前整个项目约 8.1 GB，低于用户 20 GB 限制。现行留存策略下资源门 `NOT_CLEARED`；不得因为六 seed 已完成就自动开始 screening。没有开展 AI 训练或 Router 开发。

## 请完成两层独立审查

### 第一层：阶段实验和结果是否可信

1. 对 seed1930101 的旧 Stage2 binary 与后五个 seed 使用的精度修复版做逐字段差异审查。Rmin 改动是否纯粹是序列化精度修复，还是可能改变仿真注入、range-bin mapping、目标机会或背景配对？当前六 seed 能否合并作描述性比较？是否应先用修复 binary 重跑 seed1930101 的两种 delay 条件？给出证据和影响范围。
2. 检查 seed1930102 失败初跑和修复重跑的根因证据，确认失败记录没有混入六 seed 汇总；核查修复测试是否覆盖实际 Rmin 序列化与后续 truth/range-bin 语义。
3. 从 Stage2 report、scenario、背景复用映射、输入身份和 per-case manifest 审查面积杂波真实存在、0/+4 配对、杂波功率/统计可比性及所有因果 triplet；不要仅看 `area_enabled`。
4. 审查统一 `[666,1197]` 范围的物理推导与所有 Production 分支实际 XML/runtime config；检查 0 ns / +4 ns 与不同方法的算子、样本、range support、quality gate 是否公平。核对 Production branch 状态、beam stage、CSI tap、CFAR valid CUT、运行日志、目标真值 lineage 及 input/source/binary SHA。
5. 独立重算按 seed 配对的 A0、C0、PK、P1、P2、PKR、R9 D3、Huber D3、M1 和 C1–C4 的主要检测指标。报告每 seed 差值和适当的描述统计；明确小样本限制，不把 1,200 个目标周期机会当独立 seed，不声称统计显著性或普适 Pd 改善。
6. 审查 Known correction 的 truth 是否严格限制在 evaluator-only apply，盲估计是否只使用规定的 target-free OFF 或允许的通道观测，是否读取 A0/Known/目标 truth/误差真值/未来信息。核实 P1 的 D3 估计记录、应用参数与 branch source 能否逐案闭合；核实其他确定性方法的 correction source 与估计真值隔离。
7. 从 CSI/检测/航迹/参数明细检查 A0 与 PK 的物理残余闭合、杂波残余功率、目标幅相保持、Output SCNR、检测任务收益和跨周期航迹。不要将混合噪声的 scalar CSI tap 当成 clutter-only residual。
8. 说明 paired OFF 的 360 条诊断记录为何不是独立 H0；评估虚警统计是否可用。核实径向速度 0 valid 的原因，并列出可评价/不可评价的参数与缺失机制。
9. 说明证据包省略的 raw `.bin`、`.npy`、重复 pulse-level truth 或其他大文件分别限制了哪些核验；不要声称可由精简包完整重放原始 IQ。

### 第二层：整个 GMTI 研究方向是否有价值

1. 当前证据支持“未知 +4 ns 通道时延会损害这个特定任务点”的程度是什么？考虑 Stage2 版本边界后，结论应为 `CONFIRMED`、`PLAUSIBLE` 还是 `NOT_VERIFIED`？
2. Known correction 恢复了多少任务损失？P1 的传统盲估计距离 Known 上限多远，跨 seed 的可重复性证据有多强？P2/PKR 的小幅超过 A0/PK 是否真实、值得确认，还是工程噪声/门限差异待排除？
3. 六 seed 的确定性盲校正接近 Known，是否表明未知物理状态估计仍值得研究？目前是否存在任何必要性证据支持 AI？明确区分“当前 AI 不必要”与“永远不需要 AI”。
4. 当前最重要的未闭合任务瓶颈是跨版本复核、独立 H0/Pfa、径向速度歧义、跨任务场景泛化还是杂波建模？是否有比继续扩展六种传统方法矩阵更简单的实验？
5. 当前资源门未清。请只推荐一个有明确决策价值的下一步，给出进入条件、停止条件和所需证据；不要自动启动 screening、AI 训练或其他实验。

## 输出格式

- 第一段先给总体结论，再给阶段裁决：执行正确性 `PASS / PASS_WITH_LIMITATIONS / INCONCLUSIVE / FAIL`；研究方向 `CONTINUE / STOP / PIVOT / INCONCLUSIVE`。
- 对关键事实标注 `CONFIRMED / PLAUSIBLE / NOT_VERIFIED`，引用证据包内的相对文件路径、manifest 字段、CSV 列、源码行或 binary SHA。
- 严格区分 `IMPLEMENTED`、`EXECUTED`、`EVALUABLE`、`PERFORMANCE_VERIFIED`。
- 分开报告实验执行 HEAD、dirty 源码 SHA、Stage2 / Production binary SHA、审查分支 HEAD 和远端 HEAD。
- 明确回答：未知通道时延在当前设置是否降低 GMTI 任务表现；Known 校正恢复多少；传统盲法差距多少；是否仍需研究未知物理状态估计；资源门能否支持下一阶段；AI 是否有证据必要。
- 总纲如需修订，只能给出证据支持的建议供用户讨论；未经用户明确批准，不得直接改写或把方向建议视为已批准。
