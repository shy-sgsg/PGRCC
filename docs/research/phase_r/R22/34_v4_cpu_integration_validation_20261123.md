# R22 V4 CPU 集成验证与归档中断审计

日期：2026-11-23
实现基线：`ce7d1a96b6902b02b94408e5b2fabf02c275955b`
研究总纲 blob：`e637e3769bde7814298ff012d0f42301884e2985`

## 验证结论

受影响的 V4、归档、矩阵恢复以及相邻 V3/R12/R21 CPU 回归为 **110 passed、0 failed**。完整命令与机器状态见[CPU 测试摘要](evidence/v4_integration_closure_20261123/cpu_integration_test_summary.json)。此结论仅覆盖软件集成路径，不是 CUDA/算法/实验结果。

## 具体检查

### 1. V4 方法范围和分支身份

- 使用独立执行计划解析器从当前机器设计和 runner 方法契约展开240个 case 与1,470个注册方法/角色槽位，并校验 seed 范围、case 唯一性、target-present/H0 分离及已登记分支集合。
- 0 ns runner 只选择 A0；+4 ns 基础校准 runner 只选择 C0 和 PK，并始终包含生产 A0 基准。Fixed D3、Huber D3 前缀、M1 前缀由 V4 专用校正路径增加；M1 batch 仅作诊断。
- actual-method-record 校验拒绝漏分支、重复分支和未注册分支。角色、方法、condition、mode、branch name 和 case identity 不再仅依赖分支目录名推断。
- runtime XML 的路径、字节数、SHA-256、声明覆盖参数、方法配置文件及 override digest 都须与 branch manifest 身份相符。方法标识包含 Mode-A/B，防止两个模式被合并。

### 2. 真值、响应和因果信息边界

- target-present 抽取前核对 A0_ON、A1_ON、A0_TO、A1_TO 的场景 seed、Stage1 seed、目标身份、period、beam、pulse、range、Doppler、可见性和目标状态；任何映射冲突都会在写响应证据前失败。
- 目标复数响应以共享 CSI_AFTER truth-centered ROI 逐目标/周期/方法/模式/角色记录，并保留重叠单元语义；角色相关校正后的 ON−OFF−TO 只作为描述性诊断，不假定线性闭合。
- H0 允许没有目标真值及 target-only TO 分支，输出明确的 not-applicable 状态。
- Huber/M1 因果前缀使用同一 A1_ON C1/C2 信息，估计截至周期 k 的前缀，只应用于当前周期；未来周期扰动测试验证改变 k 后数据不改变 k 之前估计。Truth 不进入盲估计，PK 的真实误差只作 evaluator-only correction。

### 3. 归档、清理与断点恢复

完整 V4 ZIP 的必需成员、目标真值来源、场景 seed、Production runtime XML、分支集合、逐期 Detection、TrackManager 输出、CFAR geometry 和 target-response evidence 在删除 raw 前验证。可排除的 dense CSI 数组须逐一与响应 manifest 的路径、字节数和 SHA-256 对应。

ZIP 先写临时文件；在发布前校验 CRC、成员清单、归档 manifest 及全部成员内容 SHA-256。随后原子 hard-link 发布。若已有 ZIP，则仅在内容及身份完全相同时复用。矩阵写入归档身份和清理 `PENDING` ledger 后才删除 raw；清理中断可从已验证归档恢复，若清理完成后 raw case 意外再现则拒绝继续删除。

相关中断测试覆盖 ZIP 发布后、矩阵 checkpoint 前，以及归档 checkpoint 后、raw 清理前两处停止点；另覆盖缺失 Detection period、H0 无真值/无 TO、损坏或错误 inventory、Mode-A/B 分组隔离和逐文件哈希保留。完整结果见[archive lifecycle audit](evidence/v4_integration_closure_20261123/archive_lifecycle_audit.json)。

## 本次实际运行身份

- Python 语法：对本次变更的 Python 模块 AST 解析；未生成 compile cache。
- 配置 JSON：可解析，且执行计划仍为 `execution_authorized=false`、`cuda_authorized=false`。
- CPU 回归：110 passed。
- `git diff --check`：交付前执行。
- 研究总纲：工作区 diff 为空；起始 blob 和交付 blob 应均为 `e637e3769bde7814298ff012d0f42301884e2985`。

机器身份及源码 SHA-256 见[integration validation manifest](evidence/v4_integration_closure_20261123/integration_validation_manifest.json)。此处的 branch/run/archive 结论由合成测试和静态代码路径支持；本阶段没有生成真实 V4 case，也没有读取真实 target truth、Production XML 或 CUDA 归档。

临时测试夹具已按逐路径账本清理：[清理账本](evidence/v4_integration_closure_20261123/temp_cleanup_ledger.json)记录文件数、字节数和 tmpfs/项目卷容量变化；未触碰历史研究数据。
