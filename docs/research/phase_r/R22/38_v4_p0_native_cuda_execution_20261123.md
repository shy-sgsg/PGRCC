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

六种方法为 A0、C0、PK、Fixed D3、M1 Causal Prefix、Huber D3 Causal Prefix；分别执行适用的 OFF/ON/TO 角色。PK 的真实参数只用于 evaluator-only 校正；M1 与 Huber 估计器只读取同 case 当前及历史的未知误差 ON C1/C2 输入。两者各有 10/10 个周期估计可用，未发生 fallback，也未将目标真值用于估计或校正。

## 断点续跑与归档恢复

CUDA 计算及 18 个分支已完成后，首次 archive finalize 因 pipeline 内部 case label 与外层 run label 不同而失败；外部场景身份一致。此问题发生在归档身份校验，不能据此重跑实验或跳过校验。

修复 scripts/phase_r/r22_compact_archive.py：优先比较 pipeline manifest 中的 external scene identity 与外层 case identity；只有不存在 external ID 的旧格式才回退比较字面 case label。增加两个回归测试，覆盖 alias 匹配及冲突拒绝。用同一输出根续跑后，runner 将已完成 CUDA case 识别为 skipped=1，仅恢复 archive verification；Stage2 和 Production 没有重跑。

全部 11 个 R22 测试文件共 139 项通过；py_compile、git diff --check 通过。归档包含 4,461 个文件，ZIP 为 13,037,254,578 bytes（约 12.14 GiB），SHA-256 为 0974e1dfd1467cdd2bdced856e8c673d40c8966a53ab05261d0c5db2d99c4b02；CRC、manifest SHA 及 422 个必需 V4 成员验证通过。随后仅按本 case ledger 清理 5,756 个 raw 文件；runner 报告逻辑数据量 25,527,199,869 bytes，此数包含链接/引用数据，不能视为物理磁盘精确回收量。历史结果和其他未跟踪数据未清理。

机器证据和清理理由见 [P0 证据导航](evidence/v4_native_cuda_pilot_20261123/README.md)。压缩包保留在本机 ignored outputs 路径，不随 Git 推送；ChatGPT 只能读取 Git 中的小型审计摘要，不能直接读取该本机 12.14 GiB ZIP。

## 判定

本次单 case 的 Stage2、Production、V4 输出和归档/断点恢复链达到工程门，判为 PILOT_PASS。研究指标标签为 IMPLEMENTED、EXECUTED、EVALUABLE；PERFORMANCE_VERIFIED=false。归档验证并清理后，工作树约 17 GiB，项目目录低于 20 GiB；监测最低可用空间约 25.82 GiB，最终约 41.44 GiB。
