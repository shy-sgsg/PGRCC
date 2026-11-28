# R20 执行计划

> 依据：用户给定的 R20 任务说明；研究总纲基线 commit `e637e3769bde7814298ff012d0f42301884e2985`。

## 目标

在不运行 CUDA、不生成新 IQ、不改生产算法/配置、不训练 AI、不启动 R21、且不改写 R19 历史契约与结论的条件下，修正 R19 离线任务评价缺陷，验证 Doppler 真值映射，统一复评已有 R18、R8、R12 证据，并据此给出有边界的研究方向判断。

## 工作包与验收

1. **WP0 证据盘点**：核对 R19 代码/契约/结果、R18 十二场景快照、R8 三 seed +4 ns、R12 seed404 五条件的实际文件、输入身份、源码/runner/build/binary/config 身份及目录空间。缺文件逐项登记；不删除历史证据。
2. **WP1 评价器 v2**：先添加失败测试，再实现独立的 R20 evaluator；修复 TrackManager result ID 到 period ID 的确定映射、机会/漏检分母、参数完整率、身份 schema、非法真值和 range_bin fallback。R19 v1 文件与输出保持原样，逐项量化 v1/v2 差异。
3. **WP2 Doppler 正控**：以物理 truth 分量、PRF folding 和保存的 Production CFAR Doppler 轴独立计算预期行号；用 seed606、6.7 m/s、30 dB 的 TO/A0 及可用的跨 seed/speed 正控制核对。无法独立闭合的 case 标为 `DOPPLER_TRUTH_MAPPING_NOT_VERIFIED`，不扩宽 gate。
4. **WP3 R18 回归**：代表场景通过后，使用冻结 v2 契约复评全部 12 场景，保留逐机会与旧新指标差异、理由和证据引用。
5. **WP4–WP6 历史适配与复评**：建立不改原始 CSV 的 R8/R12 适配层；分别报告 R8 三 seed 和 R12 单 seed，不合并 seeds；验证干预身份、可比性、Known Correction 闭合、目标检测/参数/航迹指标及不可评价项。
6. **WP7 方向判断**：结合总纲和核验后的任务证据，评估未知误差研究必要性及低速目标/复杂杂波替代主线；结论限定于可评估证据，不自动开展新实验。
7. **交付与治理**：生成 R20 报告、审计 CSV/manifest/图表、复现入口和 ChatGPT 审查提示词；只更新动态状态、决策日志与 Phase R 导航，总纲 blob 保持不变；完成必要测试、差异检查、提交并推送当前分支。

## 全局约束

- 严格区分 `IMPLEMENTED`、`EXECUTED`、`EVALUABLE`、`PERFORMANCE_VERIFIED`。
- 历史产物只读；任何唯一失败证据和用户数据均保留。
- CUDA/Production 运行、新 IQ、算法或配置改动、AI 训练和 R21 均不在范围内。
- R8 与 R12 各自的 seed、runner/build/binary/config/input identity 分别审计；不能以原始 IQ SHA 相等作为物理干预配对条件。
- 每条性能结论须指向逐机会数据和适用身份；单 seed 不作统计显著性声明。

## Review Focus

1. result ID/UTC 精度导致首次确认 period 错位：测试应以 period 映射为唯一分母边界，并覆盖 float UTC 不等值。
2. 无效真值和非法 `range_bin` 被误计为漏检或错误 fallback：测试应分开 missing 与 present-invalid。
3. comparison identity 通过可选键交集静默丢字段：测试应覆盖缺 schema 与按角色允许变化的身份字段。
4. Doppler axis 的起点/间隔/行向、PRF 折叠和 truth 频率分量可能不一致：必须由独立实现和物理正控核验。
5. 同一物理配对的输入 SHA 允许因注入变化；共享底噪/目标/几何身份需由独立字段验证，防止错误判为一致或不一致。

## 实施顺序

- [x] WP0：证据/身份/空间盘点，冻结 R20 输入清单。
- [x] WP1：新增 v2 失败测试、观察 RED、实现、观察 GREEN。
- [x] WP2：冻结 Doppler 物理校验结果和门控。
- [x] WP3：先复评代表场景，再复评 12 场景。
- [x] WP4–WP6：适配并评估 R8/R12，输出逐机会结果。
- [x] WP7：完成科研价值与唯一下一步判断。
- [x] 完成交付、ChatGPT 审查提示词和审查快照提交；当前分支推送在最终交付前完成。
