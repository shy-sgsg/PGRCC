# R19 执行、身份与质量审计

## 运行边界

- 本阶段只执行 R18 snapshot 的离线评价。R19 未运行 CUDA、未生成 Production IQ、未改算法或仿真器、未改实验参数、未训练 AI。
- R18 原始生产身份：runner `014352ace1c4ad0bade0d759a099a47fe9ecfaa9`；Release build source `4c6d4d303621324f5e174db0ba1811c5691df2e2`；binary SHA-256 `3328d7c61bc36b6d3b906692e355c46c328150c0399fa457e6f871bcb79abaf0`；config SHA-256 `3af60966332877095e5447247ab08cb39a54ef7a15681f37adf33cc251082ae1`。
- 输入快照根目录记于 `evidence/results/formal_matrix/evaluation_manifest.json`。R18 原始数组已依其清理台账移除；manifest 保存路径和历史输入 SHA，但当前不能重新哈希那些缺失数组。
- R19 审查基线 `095d40996bd2a85c5d04b3c8db1b1e9528737b9d` 对应的 Master Plan blob 是 `e637e3769bde7814298ff012d0f42301884e2985`。本阶段仅更新动态状态/决策/阶段报告，不改 Master Plan。

## R19 离线评价 manifest

`evaluation_manifest.json` 记录 12 个 case keys、R18 运行与构建身份、role-level binary/config/input identities、评价契约 SHA、评价器脚本 SHA、输出清单与执行时工作树 dirty 状态。评价时分析工作树是 dirty 的；其脚本 SHA 被单独固定记录。最终交付提交的源码和文档状态由审查 commit 固定，二者不可混称为“实验执行源码 commit”。

R19 R18 snapshot evaluator SHA-256：`d5b9abc68f4e931ee62fa9dd364b15c220b77a589f37057a13a23e3aeed173c9`；任务 evaluator SHA-256：`0eded8bf739e96d6cde57b652908cfd01f9a1bfdcbb805c1ef62a8e69abf1000`；冻结契约 SHA-256：`0326df0f8c3dee08c01f2e56908fd5c4c075f63679e046d41894f1d0dfd15dd4`。快照核对细节见 `evidence/manifests/r19_wp0_snapshot_hash_audit.json` 和 `r19_wp0_key_file_sha256.json`。

清理记录：删除了 R19 图表脚本对应的一个 7,506-byte Python `.pyc` cache 及其空 `__pycache__` 目录；未删除实验输入、失败现场或结果表。

## 已执行的验证

1. **数据/身份盘点：** 12/12 snapshot 的文件数、总字节和扩展名聚合与清理索引一致；可用的旧逐文件 SHA 清单 1,056/1,056 匹配。其余旧快照只有 aggregate index，不能补称逐文件历史哈希审计。
2. **代表性 pilot：** seed606、6.7 m/s、30 dB 离线评价完成；记录位于 `evidence/results/pilot_seed606_v6p7_snr30/`。
3. **正式离线矩阵：** 12/12 case 完成；完整 CSV、manifest 位于 `evidence/results/formal_matrix/`。
4. **Evaluator tests：** `python3 -m pytest tests/test_r19_task_evaluator.py -q`，28 项通过。测试只验证软件语义。
5. **脚本语法检查：** `python3 -m py_compile scripts/phase_r/r19_task_evaluator.py scripts/phase_r/r19_r18_snapshot_evaluation.py docs/research/phase_r/R19/evidence/figures/generate_r19_pipeline_figure.py`。
6. **图表：** 通过 `MPLCONFIGDIR=/tmp/pgrcc-r19-mpl python3 docs/research/phase_r/R19/evidence/figures/generate_r19_pipeline_figure.py` 从汇总表生成；已检查图像内容与标注。
7. **全量离线重算复核：** 使用最终 CSV LF serializer 与冻结契约格式执行完整 12-case 和单 pilot 重算。与先前正式结果的所有 CSV 按 CSV cell 解析逐值一致，旧/新文件除 CRLF→LF 行尾外无字节差异；输出 manifest 更新为最终 evaluator/契约 SHA。命令输出见 [`full_offline_reevaluation.log`](evidence/test_logs/full_offline_reevaluation.log)，逐文件 SHA 和语义比较见 [`csv_reproducibility_check.log`](evidence/test_logs/csv_reproducibility_check.log)。临时重算输出位于 `/tmp/pgrcc-r19-lf-reeval/`。
8. **审查容量：** R19 formal matrix CSV 约 1.3 MB，图约 146 KB。工作树实测约 2.0 GB，低于 20 GB 目标。未为满足容量目标删除数据；保留的历史 `.scratch`/`outputs` 不属于本次清理授权范围且不纳入本次提交。

最终执行日志在 `evidence/test_logs/`；提交前的链接、`git diff --check`、Master Plan blob 和工作区容量记录见 [`delivery_checks.log`](evidence/test_logs/delivery_checks.log)。最终审查 SHA 在交付回复中给出。用户工作区中已有的大量未跟踪 `.scratch`/`outputs` 路径不纳入 R19 提交。

## 证据状态

| 类别 | 状态 | 依据/限制 |
|---|---|---|
| 评价器软件 | `IMPLEMENTED` / `EXECUTED` | 两个 R19 Python 文件与 28 项测试 |
| 12 场景 R18 saved-data 复评 | `EXECUTED` | R19 formal matrix manifest 与 12 个 case key |
| 目标 opportunity / 输出链描述 | 指标级 `EVALUABLE` | 冻结契约、独立 truth 与 Production 输出 |
| 经验 Pfa | `NOT_EVALUABLE` | 缺独立 H0 holdout 与不确定性评估，CUT 相关 |
| target-only complex CSI / SCNR | `REPLAY_REQUIRED` | 源 IQ 与 complex CSI maps 不在保留 snapshot |
| CFAR hit 的空间/功率/阈值归因 | `REPLAY_REQUIRED` | 没有逐 CUT maps |
| radial velocity | `NOT_EVALUABLE` | ambiguity unresolved |
| ID-switch 根因 | `NOT_IDENTIFIABLE_FROM_CURRENT_DEBUG` | 缺 `track_association_audit_v2` |
| 新 GMTI 性能验证 | `PERFORMANCE_VERIFIED=false` | 无新 CUDA；12-case 全是 0 ns delay |

## 复现入口

在输入 snapshot 可访问的 checkout 中，运行：

```bash
python3 scripts/phase_r/r19_r18_snapshot_evaluation.py \
  --input-root docs/research/phase_r/R18/evidence/formal_matrix \
  --output-dir /tmp/pgrcc-r19-reproduced
```

这个入口重算的是保存证据，不会生成新的 Production 数据。输出目录应使用新的隔离路径，不覆盖 R18 snapshot 或本次正式结果。
