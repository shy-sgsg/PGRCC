# R17：Production Baseline 可比性契约

本契约用于之后的 PGRCC Production 方法比较。R14/R16 audit 显示，相同源码提交范围、相同 Stage2 输入和相同 runtime XML 参数仍不能弥补 Production executable 跨版本不一致；R14 实际 binary 与 R7 pre-fix executable 完全相同。

## 方法比较前的必要身份

| 比较对象 | 每个 branch / period 必须记录 | 失败时的处理 |
|---|---|---|
| 源码 | 完整 commit SHA、tracked/untracked dirty 状态、生产源文件 patch 或受控源码快照 | dirty 生产代码未封存，停止方法排名。 |
| Production executable | 实际运行文件路径、size、SHA-256；构建命令、CMake generator/cache、Release/Debug、CUDA/NVCC、C++ 编译器、关键 flags 与架构 | 不同 binary 不能直接混为同一处理链。若跨 binary 比较是研究目标，需同输入成对运行并单独验证实现差异。 |
| Stage2 输入 | Role、period、byte size、SHA-256、场景配置与生成器 binary 身份 | 输入 hash 或 role/period 配对未闭合，停止因果比较。 |
| 生产前级处理 | F1/F2 tap 的定义、尺寸、来源与 hash；校正 adapter / 模式及实际生效值 | 只凭实验设计配置、不凭 runtime 记录，不算运行时身份闭合。 |
| 实际 CFAR 输入 | 每个 CFAR branch 的 complex detector-input（含来源模式/频带选择）、power map、threshold map、hit map 或其可复算 hash；至少保存有效 CUT mask/geometry 与必要数值探针 | 仅 CSI 输出 tap 相同不足以证明 CFAR 输入相同；CFAR 中间量缺失时把分歧标在“last known equal”与“first known different”之间。 |
| CFAR 语义与几何 | runtime XML / runtime dump 中的 CFAR 类型、Pfa、alpha、guard/background、循环边界、cut band、valid CUT denominator、源码定义版本 | 任何实际参数或分母不同，分层报告，不能合并 hit 数。 |
| Detection 与任务评价 | evaluator commit/SHA、目标机会和 truth association 规则、Detection/Confirmed+matched 分母、truth 和坐标版本、下游聚类/过滤配置 | 统计定义变化时不得把输出数量差当算法效果。 |
| 信息条件 | OFF/ON/TO 的构造与输入身份；参考数据来源及是否 in-sample；真值、标签、配对参考能否进入 estimator | 信息条件不同需独立分层，不能声称等信息比较。 |

## 比较级别

1. **同 binary、同输入：** 可用于方法间的首选因果比较。方法所需的合法信息差异需预先声明，其他生产设置、评价器与分母冻结。
2. **同源码、不同 binary：** 不足以直接比较。即使提交和配置相同，也必须报告 binary/build 变化，并在同一输入上做 paired implementation audit；不能仅用 source commit 相同作等价证明。
3. **不同源码、不同 binary：** 只能作为不同处理链的历史结果。输入和配置对齐可帮助定位，但不提供直接算法排名。
4. **关键中间数组已清理：** 只能用留存 manifests/hash 表定位最早可见差异，并明确证据不可独立重算；不能把推断写成逐点复现结果。

## R17 发现对应的最小差异定位顺序

后续若经用户授权进行 Production 复验，按实际数据流逐级比对：

```text
Stage2 protocol IQ
  → F1/F2
  → CSI output
  → dynamic/full branch 的 complex detector input
  → power map
  → threshold map + valid CUT mask
  → hit map / unique union
  → cluster / Detection
  → TrackManager 当前周期关联与参数
```

每一级记录尺寸、数值 dtype、单位/语义及 SHA-256。若二进制不同，先用同一输入在两 binary 下比较这条链；若 binary 相同，再从第一个不同中间数组定位代码/状态。对于 GO-CFAR，至少分别保留 dynamic/full branch maps，不能只保存 union 合成图；union OR 会掩盖 branch 差异。

## 指标解释约束

- `hit_cut_count / valid_cut_count` 只是该输入图上的 observed hit fraction；没有 H0 truth filtering 和适当统计设计时，不称 empirical Pfa。
- CFAR branch hit、final-union unique hit、cluster、Detection row、truth-matched detection、confirmed current-frame target association 是不同计数单位，不可互相替代。
- 输入/地图 hash 一致是字节身份证据；summary 指标相同、文件名相同、runtime XML 一样或程序退出码为 0，都不等于中间数据一致。
- `IMPLEMENTED`、`EXECUTED`、`EVALUABLE` 与 `PERFORMANCE_VERIFIED` 分别报告。正确性/实现审计不自动构成任务性能或统计显著性证明。
