# R20 研究机会与阶段方向裁决

## 与总纲目标的关系

R20 用同一物理 Doppler 映射和冻结任务门，检查未知系统时延是否沿“通道失配→Production target detection / parameter / track outcome”形成可追溯任务损失，以及 Known 与现有确定性校正能否回收。R20 不改变研究总纲的科学目标，不以本阶段结果自动把静态时延提升为长期创新主线。

## 五个决策问题

1. **未知通道误差是否降低 GMTI 性能？** 在 +4 ns、6.7 m/s、30 dB 的历史受控场景中，R8 seed202/303 与 R12 seed404 三个可评价 seed 均见 B=0/3；对应 A0 分别为 2/3、3/3、2/3。R8 seed101 因 axis 缺失被排除。观察支持在这些可评价 case 中存在 detection loss；样本规模不支持统计显著性或总体 Pd 声明。
2. **Known physical correction 恢复多少？** 两个可评价 R8 seeds 中 Known 分别 2/3、3/3，回到相同 seed 的 A0 水平，比 B 分别多 2 和 3 个机会；R12 Known 2/3，比 B 多 2 个，回到 A0。Known F2 closure 数值来自留存 manifest（R8 202/303 ON `8.68e-5` / `9.12e-5`；R12 ON `8.73e-5`），不是 R20 raw-IQ 重算。
3. **传统确定性校正离 Known 多远？** 仅 R12 seed404 可评价 M1/D3：二者检测各 3/3，Known 2/3；M1 delay residual `−0.24064 ns`，R9 fixed D3 residual `−0.59003 ns`。但 M1 F2 residual `2.6840%`、D3 `6.3719%`，Known `0.00873%`，均为历史保存 closure。共同成功机会上 M1 与 D3 的角度/位置误差均大于 Known；地速也只 n=2，M1 `11.03 m/s`、D3 `8.06 m/s`。所以“比 Known 多一 hit”不等于物理/多参数性能达到 Known。一个 seed 无法给出可推广差距。
4. **是否需要 AI？** 否。已观察到的是单一静态时延误差，R12 有一个确定性物理匹配估计器；其输入闭合残差和任务/参数折衷尚需独立重复。当前证据支持继续验证可观测性/传统估计边界，不满足“确定性方法在可观测物理状态上稳定失败”的 AI 入口。
5. **是否转向低速目标或复杂杂波？** 没有。R20 的非零误差场景只覆盖 6.7 m/s；不是低速 sweep。实验虽有面积杂波，但没有冻结复杂杂波类型和因果变量，也没有可评价的 H0 false-hit/valid CUT 统计。因此无法以 R20 排低速保护或复杂杂波为主线。

## 方向裁决

**`CONTINUE`，但仅继续一项有边界的 Unknown System Error Characterization 复验；不恢复静态时延估计为主要创新方向。** 相比 R19，R20 统一复评补上两个 R8 seeds 的严格因果结果，并发现 seed404 的确定性方法只在一个 seed 中多一项 detection opportunity、参数误差仍高于 Known。这足以维持未知失配任务影响的研究价值，但不足以推翻 R12 对狭窄静态估计方向的限制。

建议用户审阅后再决定是否立项：冻结 +4 ns 与相同 6.7 m/s / 30 dB 场景，加入至少两个独立 seed 的 A0 / Current / Known / M1 配对对照；每条件保留实际 Doppler axis、输入与 binary/config SHA、完整 ON/OFF/TO role 以及 TrackManager/Detection lineage。主指标为逐 seed detection hits 与 Known−Current recoverable space、M1−Current actual recovered；共同机会参数 RMSE、F2 closure 和 H0 evaluability 为共同门。不得把三期当独立 seed，也不得在完成结果前扩大到 AI 或多参数模型。

**停止条件：** 若当前失配在独立 seeds 不重现，或 Known 物理闭合失败，停止该 error→task 因果主张并先修实验身份/物理模型；若 M1 的检测收益不重现、参数/输入 closure 持续落后于 Known，则把 M1 留作 baseline，回到 R12 的传统 GMTI Baseline 主线；若继续研究未发现确定性估计的可重复任务残差，则不引入 AI。H0/Pfa 只有在有效独立 H0 分母和不确定性可评时才作统计判断。

本建议没有启动 CUDA 或其他实验。R20 到此停止，等待 ChatGPT 独立审查和用户决定。
