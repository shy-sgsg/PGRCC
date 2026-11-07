# R22-S1c：共同 OFF 距离相位反事实协议

## 目的

在不改变 C4 Robust DDC-RB 门槛的前提下，判断 SNR20 到 SNR30 的
Mode-B ON 参考支持变化，是否会因使用各自 ON 自适应 CSI 距离相位而明显改变。
本任务只针对 `GMTI05 / beam 50 / azimuth 52 / range 1065–1197`，属于单组机制诊断，
不作 Pd、Pfa 或方法性能排名。

## 2026-10-29 执行修订：单期 GMTI05（v2）

本节优先于下方最初拟定的十期执行步骤。首轮十期试跑已判为
`INVALID_NOT_EVALUABLE`：Production 相位 sidecar 文件名只包含 beam 编号，
多期运行会重复写入同一个 `period050` 文件；因此无法把最终 raw/CSI 向量唯一
绑定到 GMTI05。首轮运行和 CSV 已保存在
`evidence/s1c/attempt1_invalid/` 及本地 `outputs/r22_s1c_shared_off_phase_20261029/`，
不得用作 support 或因果结论。

修订后的 v2 只处理 Stage2 的 `period_0004.bin`：每个分支输入目录将该文件作为
唯一文件提供给 Production。Production 内部结果编号为 1（`GMTI01`）；manifest
显式映射到原始零基索引4（`GMTI05`）。trace selector 为
`1,50,52,1065,1197`。这避免了 sidecar 的跨周期覆盖，同时不改变原始数据字节。

v2 顺序运行五个单期 Native CUDA Production 分支：SNR20 ON baseline、SNR30 ON
baseline、共同 target-free `C4 Mode-A OFF`、SNR20 ON 使用共同 OFF CSI phase、
SNR30 ON 使用共同 OFF CSI phase。两种 SNR 的十期 OFF 输入 SHA 列表必须完全相同；
实际相位向量取自选定 GMTI05 的 OFF Production。两种 ON 条件各自保留同一单期
baseline 产生的 raw phase，仅统一 CSI phase。所有分支继续使用
`min_support=8` 和 `0.35 rad`，不提供 truth，不启用 fallback。

因此 v2 的可解释范围仅为选定 GMTI05 单组的相位与 support 机制诊断。Production
内部 period 1 的编号不能被误读为原始仿真 period 1；任何汇总、trace 和报告都须
同时保留这项映射。v2 不构成 Pd/Pfa、任务性能、统计显著性或目标污染唯一因果归属。

## 冻结条件

- seed：`1932001`
- 径向速度：`2.5 m/s`
- 时延：`+4 ns`
- SNR：`20 dB` 和 `30 dB`
- 目标数：20；连续周期：10；beam：50
- 方法：`C4 / Robust DDC-RB / Mode-B / ON`
- 处理距离：`[666,1197]`
- 选定组：period 5、azimuth row 52、range bins `[1065,1197]`
- range group：133 bins；`min_support=8`；相位残差门限 `0.35 rad`
- 不使用目标真值估计，不使用 Current fallback，不改 CFAR 或扫描质量门

## 配对与反事实

1. 从 seed1932001 的 SNR20、SNR30 case manifest 读取10期 OFF/ON 文件身份。
2. SNR20 与 SNR30 的 OFF 输入需逐期 SHA-256 完全一致；ON 输入应分别对应其 SNR 条件。
3. 采用 SNR20 `C4_Mode-A_OFF` 产生的 CSI range-phase `.f32` 向量作为共同冻结向量，并确认 SNR30 `C4_Mode-A_OFF` 字节完全相同。该 Mode-A OFF 角色的处理结果只用于本机制反事实，不代表在线盲部署条件。
4. 为避免改变更早的 raw 距离相位处理，ON20 和 ON30 各自继续使用原先生产得到的 raw range-phase 向量；仅将 CSI range-phase override 统一到共同 OFF 向量。
5. 两条条件均重放原有10期 ON 输入，经 Native CUDA Production 的 CSI 与 C4 Mode-B 处理链；重新记录各 period 的 CSI tap、Gamma 支持、CFAR 几何、runtime XML、日志和 branch manifest。

## 预期比较

对比原 Production 自适应相位结果和共同 OFF 相位反事实：

- 选定组的有效互谱数、robust support count 和 group status；
- 相位中心、相位 coherence、各单元相位残差；
- 生产相位向量与共同 OFF 向量的差异；
- SNR20 与 SNR30 之间的支持变化是否仍存在。

如果固定相位改变了支持数，只能说明该单组对所选相位向量敏感。它不能证明目标污染是唯一根因，也不能证明新的参考策略有任务收益。

## 停止与解释规则

- 输入身份不配对、override XML 门槛变化、truth-use 非 false、相位文件长度或有限性错误：停止该比较。
- 任一 Production 处理路径失败：保留现场，停止算法解释。
- 不降低 `min_support=8`，不删掉失败组，不自动回退 Current。
- 共同相位若恢复 support≥8，仍须报告目标保持与完整任务输出尚未验证；不能据此称方法修复成功。
- 若支持变化仍无法区分 SNR 与相位影响，则结论为 `INCONCLUSIVE`，停止这条单组归因。

## 执行边界

仅复用既有 Stage2 数据，不重跑 Stage2、不扩展 seed 或场景、不训练 AI。
该反事实读取的条件是冻结的 target-free OFF CSI 相位向量；目标真值只可在估计之外用于解释，
本次脚本不给 Production truth 输入。
