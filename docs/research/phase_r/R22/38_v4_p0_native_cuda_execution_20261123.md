# R22-V4 P0：Native CUDA 执行与归档报告

日期：2026-11-23
case：engineering__seed_1937003__vr_2d5__snr_20__delay_4ns__targets_20__periods_10

## 执行结果

在本机 Native CUDA Release 路径完成一次 10 周期、beam 50 的工程 pilot。命令、配置、源码与 binary 身份见机器证据中的 execution_plan.json 和 build_identity.json。实验源码 commit 为 715639c8e9aad7359eb1a5058f9238c0d51f99a1；Stage2 binary SHA-256 为 47fa5ec34ca15cdc4edeb35226eefa941a78ee344aa2311e5e7984c811dcb40f；Production binary SHA-256 为 adef89d1d3776864a5cddf36eacdd852eff495ae33cdf989642eef7eb4d7f78e。构建为 CUDA Release，USE_COREX=OFF。

实验运行身份和源码 commit 相同，执行前工作区源码干净。运行期间 GPU 被外部训练进程持续占用；显存空闲观测约 1800–2023 MiB。未终止、暂停或修改外部进程。GPU 时间只作为受争用条件下的可运行性记录，不作为性能基准。

## 地杂波与通道误差

Stage2 有效面积杂波报告记录连续面积杂波 1,300 packets、6,508,782 samples，测得平均功率 0.0227655060237、lag-1 相关 0.979992768765。六组场景中的 A0_ON 是背景生成角色；其余场景显示新注入计数为零，是因为映射复用了同一组十周期背景。五份 background reuse mapping 均为 10/10 周期匹配。

A1_ON 与 A1_TO 的通道时延为 +4 ns，记录的注入位置是 echo superposition 之后、final protocol write 之前；幅度和固定相位误差为零。此处只有 +4 ns 场景，没有本 pilot 内的 0 ns 配对。

## 生产链与实际耗时

| 项目 | 实际结果 |
|---|---:|
| Stage2 调用 | 6，合计 203.658 s |
| Production 分支 | 18/18 通过，返回码均为 0 |
| Production 合计耗时 | 431.151 s |
| case 总墙钟时间 | 877.273 s（约 14.62 min） |
| 每分支十周期有效 CUT 几何分母 | 5,272,800 |
| CSI_AFTER 复数目标响应 | 90,000/90,000 行有限且完整 |
| 目标真值/ROI 身份审计 | 200 行通过 |

`timing_metrics.csv` 另记录18个分支、180个周期行：Production 周期级 `end_to_end_total` 合计418.801 s，`gmti_processing` 331.541 s，其中 `detection` 284.080 s；CSV 写出47.499 s、`channel_cancellation` 19.934 s、CSI metrics 10.055 s、DBS 12.825 s、pulse compression 5.890 s、读取3.536 s、tracking 6.031 s。`gmti_processing`、`detection` 等范围有嵌套，不能相加；branch manifest 的431.151 s还包括周期计时范围以外的分支耗时。

六种方法为 A0、C0、PK、Fixed D3、M1 Causal Prefix、Huber D3 Causal Prefix；分别执行适用的 OFF/ON/TO 角色。PK 的真实参数只用于 evaluator-only 校正；M1 与 Huber 估计器只读取同 case 当前及历史的未知误差 ON C1/C2 输入。两者各有 10/10 个周期估计可用，未发生 fallback，也未将目标真值用于估计或校正。

估计器的独立墙钟时间没有单独埋点，故不能从 case 总耗时中反推 M1/Huber 估计成本。19.934 s 是所有 Production 周期的 `channel_cancellation` 聚合范围，包含其他方法和常规通道处理，并非单独的校正执行耗时。

## ZIP 内容、磁盘峰值与清理

压缩包中央目录与内嵌 manifest 的分类字节数如下。各行是归档成员大小统计，不代表彼此独立的物理数据量；CSV、JSON、数组及二进制的压缩率不同。

| 归档类别 | 成员数 | 未压缩字节 | ZIP payload 压缩字节 |
|---|---:|---:|---:|
| Stage2 scene IQ bins | 70 | 3,444,313,600 | 2,913,850,661 |
| Prepared input bins | 211 | 6,396,588,160 | 5,375,961,853 |
| CSI/复数处理产物 | 2,880 | 5,369,529,525 | 4,688,368,332 |
| Detection CSV | 234 | 23,810,074 | 5,135,872 |
| Track debug | 126 | 27,063,755 | 6,860,321 |
| 其他 Production 文件 | 816 | 12,493,607 | 2,041,819 |
| Evaluation | 8 | 5,432,013 | 336,263 |
| manifest、日志及支持材料 | 297 | 216,507,976 | 42,776,943 |

ZIP payload 未压缩总量为15,495,738,710 bytes，压缩后13,035,332,064 bytes；ZIP 文件实际大小13,037,254,578 bytes（约12.14 GiB）。内嵌 manifest 记录4,461个纳入文件、180个 symlink 条目及422个必需 V4 成员。归档 SHA-256、manifest SHA-256、CRC 与必需成员检查均通过。详细统计见 [resource_usage.json](evidence/v4_native_cuda_pilot_20261123/resource_usage.json) 和 [archive_validation.json](evidence/v4_native_cuda_pilot_20261123/archive_validation.json)。

运行期间同一文件系统可用空间从57,547,403,264 bytes降至最低27,726,299,136 bytes（约53.61→25.82 GiB），始终高于10 GiB预留；raw清理后记录为44,497,952,768 bytes（约41.44 GiB）。按项目目录整体 `du` 复测为18,675,015,680 bytes（18.68 GB，17.39 GiB），低于20 GB最终上限；归档清理 ledger 指向的 raw 文件当前为0。保留13.04 GB完整ZIP作为唯一高价值运行归档，不再删除支持本结论的证据。

## 断点续跑与归档恢复

CUDA 计算及 18 个分支已完成后，首次 archive finalize 因 pipeline 内部 case label 与外层 run label 不同而失败；外部场景身份一致。此问题发生在归档身份校验，不能据此重跑实验或跳过校验。

修复 scripts/phase_r/r22_compact_archive.py：优先比较 pipeline manifest 中的 external scene identity 与外层 case identity；只有不存在 external ID 的旧格式才回退比较字面 case label。增加两个回归测试，覆盖 alias 匹配及冲突拒绝。用同一输出根续跑后，runner 将已完成 CUDA case 识别为 skipped=1，仅恢复 archive verification；Stage2 和 Production 没有重跑。

归档身份修复最初的定向回归为139项通过。本次补充 H0 ON/OFF 分支隔离、H0 Production 失败 fail-closed 和确认/H0 phase 拒绝覆盖后，重新运行 `tests/test_r12_physics_matched_delay.py`、全部 `tests/test_r21_*.py` 与 `tests/test_r22_*.py`，共 **193 passed**；相关 Python `py_compile`、资源 JSON 解析和 `git diff --check` 通过。归档 manifest 记录4,461个纳入文件，ZIP 实际包含4,642个条目，大小13,037,254,578 bytes（约12.14 GiB），SHA-256为0974e1dfd1467cdd2bdced856e8c673d40c8966a53ab05261d0c5db2d99c4b02；归档 CRC、manifest SHA及422个必需V4成员验证通过。随后仅按本 case ledger 清理5,756个raw文件；runner报告逻辑数据量25,527,199,869 bytes，此数包含链接/引用数据，不能视为物理磁盘精确回收量。历史结果和其他未跟踪数据未清理。

机器证据和清理理由见 [P0 证据导航](evidence/v4_native_cuda_pilot_20261123/README.md)。压缩包保留在本机 ignored outputs 路径，不随 Git 推送；ChatGPT 只能读取 Git 中的小型审计摘要，不能直接读取该本机 12.14 GiB ZIP。

## 判定

本次单 case 的 Stage2、Production、V4 输出和归档/断点恢复链达到工程门，判为 PILOT_PASS。研究指标标签为 IMPLEMENTED、EXECUTED、EVALUABLE；PERFORMANCE_VERIFIED=false。归档验证并清理后，项目目录测得18.68 GB（17.39 GiB），低于20 GB上限；监测最低可用空间约25.82 GiB，清理后约41.44 GiB。估计器和校正器独立耗时未测，不以其他时序值替代。
