# R20 执行与质量审计

## 执行范围

- 实际执行：只读适配 R18/R8/R12 保存证据；R19 v1 与 R20 v2 R18 回归；Production-axis Doppler truth 映射；R8/R12 逐机会、身份、track、参数、Known closure 记录统一评价。
- 未执行：CUDA/Production、Stage 2、原始 IQ replay、算法/配置改动、AI 训练、R21。
- 历史唯一记录没有删除或覆盖；R19 文档、代码、冻结 CSV 未改。
- 本机工作树占用约 2.0 GB，小于 20 GB 目标。新增结果是紧凑 CSV/JSON/Markdown。

## 命令与实际结果

在交付 commit 中从仓库根目录复现：

```bash
python3 -m pytest tests/test_r20_task_evaluator.py -q
python3 -m pytest tests/test_r19_task_evaluator.py -q
python3 -m py_compile scripts/phase_r/r20_task_evaluator.py scripts/phase_r/r20_historical_evidence_adapter.py scripts/phase_r/r20_harmonized_task_review.py
python3 scripts/phase_r/r20_harmonized_task_review.py --output-root docs/research/phase_r/R20/evidence
git diff --check
```

R20 定向测试实际结果为 `9 passed`；R19 回归测试为 `29 passed`；三个 R20 Python 文件 `py_compile` 通过；离线入口实际生成 210 个 branch 记录、196 个 ON/TO evaluations、16 个 identity groups（全部 `IDENTITY_VALID`）以及 13 个 compact result/manifest 文件；positive control gate=`DOPPLER_MAPPING_VERIFIED`。R20 内容审查快照为 `2f384a38803cfb841d83e8917fddd774ec6bfe9e`；ChatGPT 审查提示词在 follow-up 文档提交中绑定此 SHA。

## 阶段状态

- **IMPLEMENTED：** R20 v2 evaluator、R8/R12 historical adapter、统一离线 review 入口和自动生成 manifest/CSV。
- **EXECUTED：** 本次已读取并重评 saved R18/R8/R12 evidence；执行 Doppler positive control。
- **EVALUABLE：** 12-case R18 metric regression；R8 seed202/303 的 +4 ns detection/track/字段参数；R12 seed404 的五条件 task/track/参数；保存的 Known closure 值（仅作为 manifest 记录）。R8 seed101 task/Doppler、统计 Pfa、ambiguity-unresolved radial velocity 和 raw-IQ closure 重算不具备评价条件。
- **PERFORMANCE_VERIFIED：** 否。没有新 CUDA/新数据；独立可评价 seeds 仍少，H0/Pfa 未闭合；任何差异都是受控历史 case 的描述性证据。

## Git 与治理

研究总纲 `docs/research/RESEARCH_MASTER_PLAN.md` 不在本次修改范围。预期 blob 为 `e637e3769bde7814298ff012d0f42301884e2985`，提交前后核验。ChatGPT review prompt 将在报告与动态状态的主体内容 commit 固定后填写该完整审查 commit SHA；prompt 单独追加提交，以便 ChatGPT 可审查明确的科研证据快照。
