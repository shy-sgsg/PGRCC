# R22 V3.1 全量 Screening 早停审计

**状态：`UNKNOWN_STOP / INCOMPLETE`。** V3.1 实际执行6/288个 case 后停止；不得从该终态目录续跑。没有完整 Screening、方法排名或性能验证。

## 1. 固定身份

| 项目 | 身份 |
|---|---|
| 仓库 / 分支 | `shy-sgsg/PGRCC` / `codex/unknown-system-error-next-stage` |
| 实验执行源码 commit | `a2d8e65514847e4cc97e9e78df0c5cbafe528217` |
| 研究总纲 blob | `e637e3769bde7814298ff012d0f42301884e2985`（未修改） |
| 设计 ID | `R22_EXTENSION_20261106_V3_1_POST_REPLAY_FULL_SCREENING` |
| V3.1 仓库设计 JSON SHA-256 | `26e202fca197ae1f62cfad099cdf895de7b834eed1622c553e80b8cd2b94a839` |
| matrix manifest 记录的 design SHA-256 | `696dce5b18fab9209f57c7d49922ff3c1ae4ea52f0a524c34cede8e8b933e635` |
| Native CUDA Production binary SHA-256 | `d9e4d2e0b8429c5415e94be1af0230f3c0148455c394dc6d661e7e6c13a4ad1e` |
| Stage2 binary SHA-256 | `47fa5ec34ca15cdc4edeb35226eefa941a78ee344aa2311e5e7984c811dcb40f` |
| method config SHA-256 | `588c36904f62febad8476ddccb789ba67de2e9c23dc2ff12b52418eb06706bbc` |
| scenario template SHA-256 | `1f4f9c8b9383614601dc019414b8c8a8788b195301526438b95a672b1ee13998` |
| 设备 | NVIDIA GeForce RTX 3050 Laptop GPU，driver `580.178.04` |
| 执行时段 | `2026-11-06T03:31:04Z` 至 `2026-11-06T04:55:09Z` |

### 设计身份审计缺口

matrix manifest 保存的 `design_sha256=696dce…` 与上述仓库冻结 JSON 的 SHA-256 `26e202…` 不同。Production runner 源码会对实际传入的设计文件原始字节计算 SHA。对本机项目内的 JSON 文件及6个 case ZIP 中的 JSON 成员做 SHA 搜索，都没有找到 `696dce…` 对应文件；原始设计文件也没有随运行输出归档。

因此，运行使用的设计 ID 与冻结 V3.1 设计 ID 一致，但**实际设计文件身份没有闭合**。不能声称这6个 case 与 Git 中的 V3.1 设计文件逐字节一致；此项标记 `NOT_VERIFIED`。不改写 matrix manifest，也不以事后重算的摘要替代运行时身份。

## 2. 矩阵执行状态

冻结计划为288个 case、4,320个适用的 ON 方法/模式单元。实际队列只访问同一独立 seed `1932001`、速度 `0.5 m/s` 下的前6个 case：

| 条件 | Case 状态 | 适用单元 | 可评价 | 未知 |
|---|---:|---:|---:|---:|
| SNR 10 dB / 0 ns | `PASSED` | 12 | 12 | 0 |
| SNR 10 dB / +4 ns | `PASSED` | 18 | 18 | 0 |
| SNR 20 dB / 0 ns | `PASSED` | 12 | 12 | 0 |
| SNR 20 dB / +4 ns | `PASSED` | 18 | 18 | 0 |
| SNR 30 dB / 0 ns | `PASSED` | 12 | 12 | 0 |
| SNR 30 dB / +4 ns | `UNKNOWN_STOP` | 18 | 15 | 3 |
| **合计** | **5通过 / 1早停** | **90** | **87** | **3** |

尚有282个 case、4,230个方法/模式单元未访问。唯一独立 seed 只能提供执行可行性和故障定位信息，不支持统计显著性或总体性能推断。matrix manifest 的 `completed_cases` 表示已记录的6个 case，不表示6个都通过。

## 3. 地杂波和评价门

六个 case 的 Stage2 连续面积杂波 gate 均为 `passed`，每 case 记录1,300个连续面积杂波 packet、6,508,782个 sample，实测平均面积功率约 `0.0221596696662`。面积杂波是真实注入且在这六个条件中可比；不能把本次早停解释为“没有杂波”。

前5个完整 case 的 causal triplet、valid-CUT denominator 和 unique final-union hit gates 均通过，case 内全部适用方法单元可评价。第6个 case 的 Production 分支不完整，CUT/unique-hit 总门不可评价；其54条 task rows、6,200条 target-opportunity rows不能补足缺失分支，也不能当作独立统计样本。全矩阵没有独立 H0 rows，本阶段不报告经验 Pfa。

## 4. 早停 case 与直接失败证据

失败条件是 seed1932001、0.5 m/s、SNR 30 dB、+4 ns、20个目标、10个周期。case evaluator 记录18个适用方法单元中15个 `EVALUABLE`、3个 `UNKNOWN_STOP`：

- `C2 / Mode-A`：TO branch 未通过；
- `C2 / Mode-B`：ON branch 未通过；
- `C3 / Mode-A`：OFF branch 未通过。

归档内有50份 branch manifest，47份 Production 通过、3份失败，三条失败的 return code 均为1。`C2 / Mode-B / ON` 的 `gmt_pipe_core.log` 明确记录：

```text
GPU memory allocation failed: out of memory
initcuFFTPlans failed for worker 0
DBS fusion worker prewarm failed
MainCtrl initialization failed; no processing started
exit_code=1
elapsed_sec=0.729
```

执行前 GPU 快照为 RTX 3050 Laptop GPU、P0、61.18 W、78°C、99%利用率；runner 没有记录执行前显存空闲量。早停后查询为4096 MiB总显存、2517 MiB已用、1244 MiB空闲、94%利用率、77°C。该快照不能重建失败瞬间的显存状态。另两条失败分支的 Production 日志没有进入 compact archive，根因均为 `NOT_VERIFIED`。

因此，**至少一个失败由 CUDA OOM 直接触发**；不能据此把另外两条失败也归因于 OOM，也不能将本次早停称作 C2/C3 的算法性能失败。当前资源状态下不得重试终态 V3.1 matrix。

## 5. 产物与磁盘清理

- 全部6个 compact ZIP 的 CRC 校验通过；本机 archive 合计 `29,776,837` bytes。完整 matrix manifest 与失败 case ZIP 已复制进本审查包；失败 ZIP 为 `5,720,517` bytes，SHA-256 `dcce39d5aaf17729a6eef0c054201988f3f35e62cc7fd801561a1483f05d8862`。
- case cleanup ledger 合计记录移除8,826个文件、`47,246,962,032` bytes，全部 `.bin` 清理后计数为0；V3.1 raw 树检查为0个文件，遗留空目录也已移除。
- 上述44.0 GiB是各 case ledger 的逻辑文件大小总和，**不是声称实际释放了44.0 GiB物理空间**。磁盘去重、硬链接和共享文件会令物理回收量不同。
- 本次检查时整个项目工作树约3.4 GiB，低于20 GiB最终上限；所在文件系统约17 GiB可用、97%已用。后者包含同一文件系统的其他数据，不全由PGRCC占用。
- 保留6份小型 compact archive 与关键审计证据；可重建的 raw IQ、`.bin` 和其他大数组已清理。失败归档及摘要见 [`evidence/v3_1_early_stop_20261106/`](evidence/v3_1_early_stop_20261106/)。

## 6. 阶段标签与科研判断

| 对象 | IMPLEMENTED | EXECUTED | EVALUABLE | PERFORMANCE_VERIFIED |
|---|---|---|---|---|
| V3.1 matrix runner / frozen 288-case plan | 是 | 是，记录6个case | 计划本身可审计；执行设计文件哈希不匹配 | 否 |
| 前5个完整 case | 是 | 是 | 是，限单 seed/冻结 case evaluator | 否 |
| 第6个 +4 ns case | 是 | 是，存在3条失败 Production branches | 否；15/18个方法单元可评价，case整体 `UNKNOWN_STOP` | 否 |
| 288-case Screening 汇总 | 是 | 否，6/288 | 否 | 否 |

本阶段没有比较算法收益、Pd/Pfa、目标保持或参数估计改善，也没有形成方向切换证据。它确认了地杂波真实注入，并在全量筛选开端遇到资源相关 Production 失败；研究总纲仍要求用完整、身份闭合的传统方法能力边界证据识别 GMTI 瓶颈。AI 必要性没有新证据，`AI_ENTRY_GATE` 仍为 `FAIL`。

## 7. 下一步与停止条件

1. 保留 V3.1 原 matrix 和所有失败证据，不从 `UNKNOWN_STOP` 根续跑，不降低门槛、不缩减288个 case。
2. 在新版本重新启动前，先闭合实际设计 JSON 路径/SHA 身份，并让 preflight 记录显存总量、已用量和空闲量；不能凭 GPU 利用率猜测显存是否够用。
3. 只有新版本身份冻结、设备条件满足 Production 分配要求后，才可从队列起点重新执行原定288-case/4,320-unit 范围；失败继续按 case gate 停止并归档。任何资源下限应由明确运行证据或代码分配需求确定，不临时编造。
4. 若新版本未能通过身份和资源 preflight，停止启动；若重复出现 OOM，先解决资源调度，不把资源失败当算法差距。

本审计不修改研究总纲、不降低质量门、不开展 AI 训练，也不把本轮结果包装成 Screening 性能结论。
