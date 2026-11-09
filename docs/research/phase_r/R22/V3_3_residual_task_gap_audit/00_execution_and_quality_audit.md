# 00｜执行与质量审计

## 任务定位

本任务服务于研究总纲的“识别并解释关键性能瓶颈”和“公平刻画传统方法能力”目标。具体问题是：在固定仿真中的未知 `+4 ns` 通道误差已经由确定性校正恢复目标检测后，方位、位置、速度和航迹输出还剩多少任务差距？这些差距是否足以改变研究方向？

本阶段仅对 V3.2 本地 compact ZIP 做离线分析。使用冻结的 V3.2 evaluator 语义与生产检测记录，不重新运行 Stage2/Production，不生成 IQ，不训练 AI。

## 固定身份

| 身份项 | 值 |
|---|---|
| 仓库/分支 | `shy-sgsg/PGRCC` / `codex/unknown-system-error-next-stage` |
| 分析开始时 HEAD | `7f8d74f542cb338fcd7244ee956a92db026f4064` |
| V3.2 实验源码 commit | `74bccce61f3ec2f74746512d67271c815ce6a3a8` |
| V3.2 设计 ID | `R22_EXTENSION_20261106_V3_2_IDENTITY_CLOSED_FULL_SCREENING` |
| 设计 JSON 原始 SHA-256 | `9fcc56e27c3a78a96ccfbecdf6ef1b3098fb1298d4bac9886058ea9af6081063` |
| 设计 JSON 规范化 SHA-256 | `ecdb5a3306d27f3298f8c4fd3f199728733ec0f2bae0fd13590e277579b765f5` |
| 方法配置 SHA-256 | `588c36904f62febad8476ddccb789ba67de2e9c23dc2ff12b52418eb06706bbc` |
| Stage2 binary SHA-256（V3.2 历史执行身份） | `47fa5ec34ca15cdc4edeb35226eefa941a78ee344aa2311e5e7984c811dcb40f` |
| Production binary SHA-256（V3.2 历史执行身份） | `d9e4d2e0b8429c5415e94be1af0230f3c0148455c394dc6d661e7e6c13a4ad1e` |
| V3.2 执行设备（历史记录） | Native CUDA，RTX 3050 Laptop，driver `580.178.04`，CoreX=false |
| 研究总纲 blob | `e637e3769bde7814298ff012d0f42301884e2985`，保持不变 |
| 本阶段分析器 | `scripts/phase_r/analyze_r22_v3_3_residual_task_gap.py`；脚本 SHA-256 记录于本目录 `evidence/manifest.json` |
| 2026-11-10 Production 输出补充分析源码 | commit `957782747c9c4886733f70689f0a0d08d25f2da4`；分析器 SHA-256 `60eea069b0d6fbcd53c50e96424a65b0b69e536ce3d565434aa5ac753c772059` |

V3.2 Production 运行身份来自 V3.2 的机器 manifest/compact archive。本阶段没有重新执行或独立重建这些二进制。

## 实际执行和门控

- 从冻结 V3.2 archive index 选取所有 144 个 `delay=+4 ns` case，覆盖 12 个 seed、4 个径向速度、3 个 SNR。
- 每个压缩包按 index 中记录的 SHA-256 复算；144/144 匹配。ZIP 成员通过 `zipfile` 在压缩包中直接读取，没有落地解压。
- 读取 case manifest、Stage2 面积杂波统计、task/truth/association/parameter/track 汇总，以及 13 种方法的逐周期 `cycle_metrics.csv` 和 `cluster_detection_lineage.csv`。对四个核心条件，仅归档实际保留的 period 8 Detection CSV 做逐 Detection lineage 核对。
- 核心检测任务单位是独立 seed；每个 seed 聚合 12 个速度×SNR case、2,400 个有效目标周期机会。
- 逐机会审计表共 115,200 行（4 个核心方法×144 case×200 目标周期机会），压缩存档；其中全十周期位置误差直接取自冻结 evaluator association 记录。
- 对四个核心方法验证 Production branch 状态、task 分子/分母、truth opportunity/sample_key、Detection `period/beam/det_id` lineage，并使用 evaluator 的 `_parameter_errors` 只重算归档实际保留的检测周期。
- 对 13 种方法×144 个 case 核对十周期最终 Detection 数、postfilter cluster 数、selected candidate 数和逐簇 summary；其中 31 个方法×case 的原 task status 不可评价，因此只报告 Production 输出数量，不填目标漏检或未关联检测数。
- `truth_unmatched_final_detection_count` 按冻结 evaluator 语义以最终 Detection 行减去唯一真值关联 `det_id` 计算；不解释为 false cluster 或系统 Pfa。compact ZIP 无完整十周期 cluster-to-truth lineage，非目标 cluster 数保持 `NOT_EVALUABLE`。
- 输出分析表与图约 6.96 MB；18,720 行逐周期输出表 gzip 后约 151 KiB。`evidence/manifest.json` 记录输入/代码身份及每个产物的 SHA-256，包括 576 行位置 evaluator parity 明细、全周期位置共同成功表及 Production 输出/覆盖率表。

未发生 CUDA、Stage2、Production、AI 训练、参数调整或确认性矩阵执行。没有删除任何 V3.2 compact ZIP。分析器输出中的 `PERFORMANCE_VERIFIED=false` 与“统计显著性声明=false”保持不变。

## 状态标签

| 项目 | IMPLEMENTED | EXECUTED | EVALUABLE | PERFORMANCE_VERIFIED |
|---|---:|---:|---:|---:|
| V3.3 离线分析器与固定输入契约 | 是 | 是 | 是 | 不适用 |
| V3.2 +4 ns 目标检测、truth/association | 已由历史生产实现 | V3.2 Native CUDA 历史执行；本轮离线复核 | 是，按归档完整任务记录 | 否 |
| Huber/M1 target-present delay estimate | 已实现于 V3.2 方法链 | V3.2 Production 历史执行；本轮读取归档 | 时延估计残差可评估 | 否 |
| 全十周期位置共同成功 RMSE | 已定义/有 evaluator | 本轮从 `target_associations.csv` 读取冻结 evaluator 的逐机会位置误差 | 是；576/576 个 case×方法 parity 通过 | 否 |
| 方位、斜距、折叠 Doppler 全十周期共同成功 RMSE | 已定义/有 evaluator | 历史生产有 own-success 汇总；其余9周期 Detection lineage 未归档 | `NOT_EVALUABLE` | 否 |
| period 8 共同成功参数 RMSE | 已实现 | 使用 period_id=8 归档 Detection 产品离线重算 | 是，period 8 子集 | 否 |
| TrackManager seed/case 汇总 | 已实现 | V3.2 历史执行且被归档 | 描述性可评估；逐机会地速不可重建 | 否 |
| Production Detection/cluster 周期计数与 evaluator 关联覆盖 | 已实现 | 本轮离线复算 13 方法×144 case | 十周期输出可评价；31 个方法×case 的真值关联不可评价；非目标 cluster truth attribution 不可评价 | 否 |
| 全十周期共同地速、有效径向速度 | 已实现 | TrackManager 历史汇总存在；逐周期 track debug / 有效径向样本未归档 | `NOT_EVALUABLE` | 否 |
| 独立 H0 系统 Pfa | 不在本轮分析输入中 | 未执行/未重算 | 否 | 否 |

## 复现命令

从仓库根目录可重算本目录的核心表与图：

```bash
PYTHONDONTWRITEBYTECODE=1 MPLCONFIGDIR=/tmp/pgrcc-r22-v33-matplotlib \
  python3 scripts/phase_r/analyze_r22_v3_3_residual_task_gap.py
```

本轮实际命令返回码为 0，分析状态为 `COMPLETE_OFFLINE_AUDIT`。命令读取本地 `outputs/r22_v3_2_screening_20261106/archives/screening/`；该 1.5 GiB 归档没有纳入 GitHub。

## 磁盘与清理

分析开始和完成检查时，工作树约 4.8 GiB，文件系统可用空间约 11 GiB，工作树占用明显低于 20 GB 最终上限。144 个所需 ZIP 仍是后续复现与审计输入，因此保留。V3.2 `raw/screening` 下没有普通文件；没有为腾空间删除目录、输入或失败证据。清理本轮生成的 4.2 MiB 未压缩逐周期副本，并在最终执行后清理本轮创建的 `/tmp/pgrcc-r22-v33-matplotlib` 缓存；未清理其它用户数据。
