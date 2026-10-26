# R19：研究机会与总体方向评估

## 与长期目标的关系

R19 服务于研究总纲定义的 airborne GMTI 目标：用可追溯的物理/算法对照判断哪些处理因素会影响目标检测、参数估计和航迹质量。它提供更严格的历史 Detection / TrackManager 评价口径，并说明清理过的中间数组限制了机制定位。它没有测试非零未知误差，也没有测试新的校正方法。

## 四个决策问题

### 未知通道误差是否会降低 GMTI 性能？

R19 本身不能回答：12 个 R18 场景通道时延均为 0 ns。已有历史受控实验提示正时延可能降低特定任务检测：R8 的 +4 ns B 条件在三个 seed 共 0/9，A0/Known 为 8/9；R12 seed404 中 B 为 0/3，A0/Known 为 2/3。但这些旧结果未按 R19 冻结的 strict matching contract 统一重算，故应视作有依据的历史信号，而非本次正式重新验证或统计性结论。

### 已知物理校正恢复多少？

按相同历史计数，R8 Known 相对 B 恢复了 8/9 target-period hits；R12 Known 与 A0 均比 B 多 2/3。恢复量目前只覆盖所报场景中的检测机会，不覆盖一般参数误差、统计 Pd 或 Pfa。R19 没有重新计算这些差值。

### 传统盲估计距离已知校正还有多少差距？

R19 对 Mode-B 的分析是零时延 C2/C3 Gamma 参考差，不能解释为对 +4 ns 盲校正能力的验证。历史 R12 单 seed 中，确定性估计器获得 3/3 目标检测，而 Known 为 2/3；但角度/位置/速度等参数 RMSE 互有胜负，样本仅一个 seed，且评价口径尚未统一。因此目前无法给出可信、可推广的性能差距。

### 是否需要 AI，或转向低速目标/复杂杂波？

没有。R19 展示的零时延 Mode-A/B 差异不能说明需要 AI；历史物理误差证据尚需 harmonized re-evaluation，确定性估计的可恢复空间与任务损失也未在同一冻结口径下闭合。低速 target hits 在 R18 多数条件均为 9/9；复杂杂波也不是 R19 操纵变量。两者均未因本阶段升为主线。

## 独立研究判断

R19 值得作为评价与审计基础继续保留，但其结果不能改变长期研究目标。R18 的旧 target counter 曾给出每层 9/9；R19 strict one-to-one 重评在高速度下得到不同命中数。新增候选审计显示，seed606 6.7 m/s、30 dB 下，C0/C3 Mode-B 的 315/226 条 Detection 中分别有 24/23 条通过旧 range/position 门，而 strict gate 只保留 2/1 条候选边；因此旧、新命中差异首先是匹配定义差异，不能当成通道误差或算法效应。

R8 先前三 seed 计数是 A0/Current/Known=`8/9、0/9、8/9`；R12 seed404 是 A0/B/Known=`2/3、0/3、2/3`，同时 D3/M1 为 `3/3`。这些历史方向值尚未统一使用 R19 contract 重算；R12 的单 seed 命中优势也伴随参数精度与 Known 输入闭合差距，不能单独说明盲估计整体优于 Known。R19 本轮没有重评 R8/R12。

**方向建议：CONTINUE，仅继续一项边界明确的离线证据统一复评。** 先按同一 strict opportunity、字段、身份与信息隔离规则复核既有 R8 三 seed及 R12 单 seed，不运行 CUDA。当前主机的旧 manifest 引用 R8 54 份逐期 Detection 与 12 份 truth 文件、R12 45 份逐期 Detection 与 10 份 truth 文件路径均可读；但 R8 原始逐期文件没有进入 Git evidence 包，远端审查者不能仅凭当前 commit 复现这一复评。若逐条件 identity/standalone truth/Production axis 不能核验，停止指标对比并提交最小缺失证据及 replay 方案。

现有证据不支持直接进入 AI，也不支持宣布低速保护或复杂杂波优先。低速 R18 命中为 9/9；R18/R8/R12 没有把复杂杂波类型作为冻结因果变量。

## 研究状态标签

- `IMPLEMENTED`：R19 离线 evaluator、契约、结果与审计报告。
- `EXECUTED`：R18 snapshot 的 12-case 离线复评；本阶段没有新的 CUDA。
- `EVALUABLE`：R18 零时延任务分层描述、保存的 Gamma/CFAR/TrackManager/参数数据中字段完整部分。
- `PERFORMANCE_VERIFIED`：未知误差因果损失、Known correction 恢复率和盲估计相对差距均未由 R19 验证；统计 Pd/Pfa 也未验证。
