# R22 V4 集成闭合审查

日期：2026-11-23
分支：`codex/unknown-system-error-next-stage`
实现基线：`ce7d1a96b6902b02b94408e5b2fabf02c275955b`
研究总纲 blob：`e637e3769bde7814298ff012d0f42301884e2985`

## 结论

V4 草案所需的 runner 选择、方法/分支身份、目标真值映射、完整归档及中断续跑路径已完成 CPU 集成闭合。受影响回归 **110 项通过**。这证明软件契约和合成归档生命周期可以按预期工作；本阶段没有执行 Stage2、Native CUDA Production、V4 pilot 或确认性矩阵，不能据此判断物理因果闭合或 GMTI 性能。

状态分别为：

| 状态 | 本阶段结论 |
|---|---|
| `IMPLEMENTED` | 是 |
| `CPU_INTEGRATION_TESTED` | 是，110 passed |
| `STAGE2_EXECUTED` | 否 |
| `NATIVE_CUDA_EXECUTED` | 否 |
| `V4_PILOT_EXECUTED` | 否 |
| `CONFIRMATORY_EXECUTED` | 否 |
| `EVALUABLE`（真实实验） | 否 |
| `PERFORMANCE_VERIFIED` | 否 |
| V4 冻结 | 否，仍为 `DRAFT_NOT_FROZEN` |

## 研究定位

本阶段服务于研究总纲中“建立可信的物理和任务评价基准，并可靠识别传统方法能力边界”的目标。具体问题是：V4 的实验执行器是否严格落实了草案规定的方法子集、因果估计信息边界、真值身份，以及可审计且可断点续跑的结果保留策略？这属于工程/评价基础闭合，不是新算法收益证据，也不证明 V4 设计值得进入确认性实验。

## 完成的实现

- V4 运行路径按时延条件选择方法：0 ns 仅运行 A0 基准；+4 ns 的共享校准 runner 运行 A0、C0、PK，另执行已登记的 Fixed D3、Huber D3 前缀和 M1 前缀。M1 全周期 batch 只作为离线诊断，不进入 Production 排名。未登记或重复的实际分支与预期计划不符时拒绝通过。
- Production 分支身份显式绑定 case、condition、method、Mode-A/B、role、branch name 及源配置身份；运行 XML 中的覆盖参数、源码配置、身份与实际文件内容交叉校验。Mode-A 与 Mode-B 保持分组隔离。
- target-present 情况检查 A0/A1 的 ON/TO 真值来源、seed、target-period-beam-pulse 身份及范围、Doppler、可见性和目标状态一致性。H0 明确不需要目标真值和 target-only TO 响应。
- 完整归档在临时 ZIP 发布前验证成员清单、每个成员的大小和 SHA-256、ZIP CRC 与必需证据；通过后才原子发布。矩阵先记录归档身份和 `PENDING` 清理账本，再删除 raw case，并写入最终账本。续跑时重新验证归档，发现已记录清理的 raw case 再现则 fail closed。
- dense CSI 数组仅在逐数组 SHA 已绑定到 V4 响应 manifest 时排除；未被证明可再生或必要的输入数组仍进入归档。Detection、TrackManager lineage、CFAR 几何和响应审计保持为必需证据。

## 计划范围（静态展开，不是执行结果）

机器设计展开为 180 个 target-present case 和 60 个独立 H0 case，共240个 case。注册的 method/role 槽位为1,470个，其中1,260个要求 Production，210个是 M1 batch 离线诊断槽位。按当前源码调用路径推导，正式计划对应2,520次 Production branch 调用（target-present 1,890，H0 630），另有1,260次内部辅助 Production 调用；Stage2 为1,200–1,440次调用。上述均为设计/源码派生计数，没有实际运行。

### 验收入口

- [CPU 集成验证与失败门审计](34_v4_cpu_integration_validation_20261123.md)
- [CUDA pilot 资源与 readiness](35_v4_cuda_pilot_readiness_20261123.md)
- [机器可读证据包](evidence/v4_integration_closure_20261123/README.md)
- [ChatGPT 独立审查请求](CHATGPT_REVIEW_REQUEST_V4_INTEGRATION_CLOSURE_20261123.md)

## 磁盘与执行门

当前工作树和项目目录的 `du -sh` 占用约4.8G和5.3G，均低于20 GiB最终占用上限。所在卷约8.38 GiB可用。依据协议 packet 布局计算，一个+4 ns case 的六个 Stage2角色及四类三角色校正输入，未压缩数据下界约8.86 GB（8.25 GiB）；比当前空闲空间少约0.13 GiB，尚未计入 CSI/Production 输出、归档验证时 raw 与 ZIP 并存及系统运行余量，不能据此认定空间足够。故本阶段不启动 pilot，也不通过降低归档完整性或覆盖既有结果来腾空间。只清理本次 CPU 测试在 `/tmp` 创建的临时夹具，共2,094个文件、5,283,075逻辑字节；因 `/tmp` 是独立 tmpfs，没有释放项目卷空间。逐路径账目和清理前后空间见[临时文件清理账本](evidence/v4_integration_closure_20261123/temp_cleanup_ledger.json)。历史研究数据和所有可审计失败现场均保留。

V4 的统计 MDE/检测覆盖率非劣界值、最终 seed 注册/碰撞核查、独立源码审查、单 case 尺寸实测及另行 CUDA 授权仍是冻结前门槛。CPU 集成闭合不会自动冻结设计或授权实验。

## 科研判断

本阶段对总体目标的贡献是让后续预注册比较的分支身份、任务输出和 raw 清理更可追溯，并避免某一方法失败时静默混入其他方法结果。现有证据不能回答通道误差是否导致可重复的 GMTI 性能退化、Known correction 能恢复多少、盲估计与 Known 的差距，也不能在未知误差、低速目标保护与复杂杂波建模之间重新排序。AI 必要性没有新证据；不启动 AI 训练或 Router。

下一步为固定本次交付 commit 的 ChatGPT 双层审查，然后根据资源和统计门决定是否另行启动最小尺寸 pilot。若物理卷仍不足，应先获得足够可用空间或提供已授权的独立输出卷，再执行任何 CUDA 工作。
