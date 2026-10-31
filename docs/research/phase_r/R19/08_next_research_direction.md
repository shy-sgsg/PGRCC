# R19：唯一下一步建议与可证伪研究设计

## 裁决

**CONTINUE（范围仅限离线证据统一复评）**：下一项优先任务是用 R19 冻结的 `r19-task-eval-v1` 统一复评 R8 三 seed 的 `A0 / Current+4 ns / Known correction`，并将 R12 seed404 的 `A0 / Current+4 ns / Known / D3 / M1` 作为单独的辅助案例复评。该裁决不是继续把静态单通道时延估计作为长期创新主线，也不授权启动 CUDA、生成新数据或 AI 训练。

R12 已将单一静态时延估计从博士论文主要创新方向 `PIVOT` 到更广的 GMTI Baseline 评价。R19 的 CONTINUE 只回答一个决策前置问题：R8/R12 的历史任务差异在冻结、统一且无真值泄漏的评价下是否仍成立。

## 科学问题与可证伪假设

**总体目标：** 判断物理系统误差是否通过可追溯的处理链降低 GMTI 任务结果，以及物理校正或确定性估计能否恢复该结果。

**H1：** 在 R8 每个独立 seed 的同场景配对数据上，按 R19 strict one-to-one truth opportunity matcher 重评后，`Current+4 ns` 的正确目标周期命中数仍低于 `Known correction`；Known correction 与 A0 的输入闭合及任务结果应相符。三个 seed 的逐 seed 原始计数和差值是主证据，不把 9 个 period 当作 9 个独立重复，也不作显著性声明。

**证伪条件：** 任一 R8 seed 的 Current 未低于 Known，或者 Known 输入未按既有物理闭合容差回到 A0，均表示历史因果结论没有通过统一复核；逐项定位为 matcher 定义、输入/身份不配对、校正不闭合或 Production 下游差异，不通过放宽门限挽救 H1。

R12 只有一个 seed、三个目标机会，并包含额外的 D3/M1 方法。它作为方法与处理链诊断单独报告，不与 R8 合并增加独立样本数。

## 竞争解释

1. **旧 matcher 口径造成计数差异：** R19 已发现零时延 R18 的旧 range/position 门会留下大量候选，而 strict range-bin/Doppler 门可将其排除。R8/R12 的旧命中结果可能部分依赖门控定义；新复评必须逐机会输出候选数、门控失败项及最终匹配。
2. **配对输入或运行身份不一致：** realization、binary、config、truth、Production output 若不一致，差值不能归因于延迟或校正。身份核验失败时拒绝该 paired contrast。
3. **Known correction 没有精确恢复实际物理信号：** 通道延迟注入/反向校正、相位符号或数据路径不闭合，可能导致 Known 与 A0 不一致；先查 F1/F2 或等价保留的校正闭合记录。
4. **差异来自 Detection/CFAR/TrackManager 下游：** 如果输入闭合而最终命中不同，按保留的 stage、Detection、payload lineage 定位可见的首次分歧；缺 power/threshold map 时不得把差异归因到某个 CUT 或阈值尾部。
5. **场景依赖而非普遍未知误差规律：** 当前证据主要是 6.7 m/s、30 dB、单一 `+4 ns` 延迟。即使重评方向一致，也只支持该仿真条件的方向性证据。
6. **盲估计结果来自信息优势或不同定义：** D3/M1 必须审查信息隔离、同输入条件和运行身份；Mode-A paired-OFF 不作为盲估计结果。R12 的单 seed 优势不能外推。

## 现有输入可用性与任务边界

| 证据组 | 本机当前可读记录 | 复评边界 |
|---|---|---|
| R8 seeds 101/202/303 | 旧 evaluator 的 associations、task metrics、CFAR/lineage 摘要在 Git；本机 manifest 引用的 54 份逐期 Detection CSV、12 份独立 truth 文件及身份路径当前均存在于本地 `outputs/phase_r8_unknown_delay_multiseed_20261023/` | 可在本机做离线 strict 重评；这些原始逐期文件没有随 Git evidence 包提交。复评必须保留新 manifest、SHA 与紧凑审计副本，不能假称 GitHub 审查者已读取本地文件。|
| R12 seed404 | compact evidence 包含 45 份逐期 Detection CSV、10 个 truth CSV 引用、lineage、运行 XML/log、branch/source manifests；Production 原始 IQ 不在包内 | 可重评最终 Detection 任务与保存字段；依赖完整 IQ/CSI/power map 的 target transfer、SCNR 或空间 CFAR 机制仍需重放。R12 只能作一个 seed 的独立案例，不与 R8 合并。|
| R18 12 cases | R19 strict 重评结果；全部 delay 为 0 ns | 用于匹配契约和候选定义回归，不作非零误差因果对照。|

开始计算前逐条件核对 standalone truth、逐 period Detection、Production Doppler axis/PRF、配对 realization、实际 binary/config、输入角色身份、执行源码与 evaluator 版本。R8/R12 原始 IQ 缺失不妨碍最终 Detection 表复评，但会限制输入重建、target-only 复响应和新处理链 replay。

## 最小比较条件与传统基线

1. **A0：** 零误差基准。
2. **B / Current+4 ns：** 真实失配处理结果。
3. **C / Known correction：** evaluator-only 真值参数只允许用于离线物理校正参考，报告校正前后输入闭合和 GMTI 任务差值。
4. **D / 可用确定性估计：** 仅纳入同一 case、同一处理链、信息隔离有证据的 D3/M1。R8 若无该方法则标 `NOT_AVAILABLE`；R12 D3 与 M1 作为 seed404 的分开支线，不伪装成 R8 多 seed 盲估计。

Known correction 是物理校正上限；A0 是理想条件；Current 是失配基线；D 是传统确定性估计基线。Mode-A paired-OFF 如被报告，必须独立标为 privileged reference，不能计入盲部署条件。

## 评价指标与统计单位

- **Primary task metric：** standalone truth 可见 target-period opportunity 上的严格一对一命中比例与逐机会结果；同 period/beam，range-bin ±2、range ±150 m、EN 位置 ≤1 km、Production circular Doppler row ±2，按冻结 Hungarian 最大匹配数再最小化代价。任何方法不得单独改变门限。
- **独立单位：** R8 seed；每个 seed 内的三个 periods 是配对重复观测，不增加独立样本数。输出每 seed 的 `B−A0`、`Known−B`、`Known−A0` 及方向一致性，不报伪重复 p 值。
- **Secondary task metrics：** 成功机会上的 angle、position、range、folded Doppler、resolved radial velocity 与 production TrackManager ground-speed RMSE；同时给 own-success/common-success 样本数和漏评原因。Track 指标从 Production 当前周期关联/confirmed payload lineage 计算，不重建 tracker。
- **Detection/CFAR：** 分开列 valid CUT、hit、cluster、final Detection、strict truth match、unmatched Detection、confirmed track 和 payload。只有 valid geometry 下的 CUT 比例可计算；未匹配 Detection 不叫 false CUT。统计 Pfa 只有在独立 H0、合法分母和不确定性评估成立时才报告，否则 `NOT_EVALUABLE`。
- **目标保持/SCNR：** 仅当配对 ON/OFF/TO complex arrays、固定 truth ROI 和输入功率留存时复算。没有完整数组时标 `REPLAY_REQUIRED`，不以 CSI 混合 clutter/noise tap 替代。

## 预注册执行规则

1. 在读取重评数值前冻结 R19 contract、脚本 commit/SHA、truth 字段来源、机会定义、candidate/miss 语义和全部比较条件。
2. 检查每一条件的 branch/input/binary/config/runner identity。无法证明同 seed 配对的 contrast 标 `IDENTITY_MISMATCH` 并停止该 contrast。
3. truth 只取 standalone truth 文件；忽略 Detection 中嵌入的 `truth_*` 字段。盲估计输入只允许该方法原本能看到的 Production 观测；不得读 truth、配对 OFF、TO、Known 参数、其他 seed 或未来周期。
4. 先按无目标/角色判定检查数据可用性和运行闭合，再计算任务指标；不因结果不理想而修改 R19 门限。
5. 逐 seed 公布 counts、missing/invalid row、candidate edges、strict matches、parameter sample counts 和范围；将 R8 三 seed 与 R12 seed404 分层，避免混合平均遮蔽差异。
6. 统计解释限于三个独立 seed 的描述性方向。重新评价相同旧数据不会产生新独立证据，也不能把三 period 当成独立样本。

## 独立验证与停止条件

- 本建议阶段先只做已有 R8/R12 文件的离线 harmonized review；不启动 CUDA。
- 如果 R8/R12 strict 结果对身份、输入或 matcher 无法复核，逐指标列出缺失文件及影响，并只提交最小 Production replay 方案供用户决定。
- 若 R8 三 seed 中 Current 并非均低于 Known，或 Known 输入闭合失败，停止把 `+4 ns` 失配退化当作已经重复验证的任务结论；转为检查实验/评价边界，不扩张 AI 研究。
- 若差异可复现且 Known 恢复而 D 明显落后，下一研究问题只到“传统盲估计残差是否在新的独立 seed/误差状态下稳定且可观测”；需要另行批准增加独立 Production seed。
- 如果 D3/M1 在同一冻结门下与 Known 任务结果相当、或现有差异无法跨 seed 维持，停止拓展这一估计器路线；无需引入 AI。
- 只有在更多独立条件表明确定性物理/统计估计存在稳定任务缺口，且缺口不由不可观测性、校准/同步、配置或常规工程修复解释后，才重新讨论 AI 的研究必要性。

## 结果如何改变研究方向与潜在贡献

- **失配损失和 Known 恢复在 R8 三 seed 严格重评中保持同向：** 支持继续研究未知物理状态与任务误差传播，但仅限下一步制定独立 seed 的有界验证；不把单参数静态 delay 算法自动升为长期创新主线。
- **差异消失、Known 不闭合或身份不配对：** 暂停未知延迟任务收益主张，优先修正证据链/实验有效性，再依据新的真实 GMTI 瓶颈评估传统基线。
- **低速保护：** R18 两档低速 strict hits 均为 9/9；没有低速漏检或专门低速对照，因此目前不转为该主线。
- **复杂杂波：** R18/R8/R12 没有冻结复杂杂波类型作为因果变量；当前没有优先转向证据。
- **AI：** 没有确定性方法的可重复性能上限/残差证据，也未证明可观测状态到任务收益的稳定映射，当前不满足 AI 入口条件。
- **可能的学术贡献：** 若未来独立多 seed 复核一致，潜在贡献是把受控物理通道误差、输入校正闭合与最终 GMTI 检测/参数/航迹任务置于同一信息公平评价链，而不是单独报告校正误差。当前数据规模、仿真单场景、缺失原始 IQ/地图以及有限统计重复不足以宣称论文创新已经成立。

## 授权边界

本设计是 R19 的唯一后续建议，不是已启动的科研阶段。R19 到此停止；下一步是否开展离线统一复评由用户决定。不得由本文件自动触发 CUDA、AI、参数扩展或研究总纲正文修改。
