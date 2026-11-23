# R22 V4 CUDA pilot readiness 与磁盘评估

日期：2026-11-23
V4 状态：`DRAFT_NOT_FROZEN`
执行/CUDA 授权：`false / false`

## 结论

**当前无法保证 V4 尺寸 pilot 在磁盘上安全完成，本阶段未运行 Stage2、Native CUDA Production、V4 pilot 或确认性矩阵。**所在文件系统约8.38 GiB可用。按固定协议 packet 大小及十周期输入计算，仅一个+4 ns case 的 raw 与校正数据下界约8.856 GB（8.25 GiB），名义余量约0.13 GiB；该估算尚未计生产输出、CSI arrays、日志、ZIP 验证时 raw/归档并存和系统运行余量。由于余量不足以容纳这些尚未测得的产物，不以实际运行探测。

项目工作树和项目目录的 `du -sh` 占用分别约4.8G和5.3G，低于用户要求的最终20 GiB限制。用户允许中间实验数据暂时超过20 GiB，但这不增加当前卷的物理可用空间；pilot开始前仍需提供足以覆盖原始输入、Production/CSI输出、归档并存和运行余量的空间。

## 资源下界的推导

协议 packet bytes 为 `256 + 11,820 × 4 × 2 × 4 = 378,496` bytes。按130 pulses ×10 periods，每个 role 输入为492,044,800 bytes。+4 ns case 预计需要六个 Stage2 roles（约2.952 GB），以及 PK、Fixed D3、Huber 前缀和 M1 前缀四组、每组 ON/OFF/TO 三角色的校正数组（约5.904 GB），合计约8.856 GB，约8.25 GiB。

这是根据格式和设计身份推导的未压缩数据下界，不是运行中实测文件大小。名义余量只有约138.9 MB（约0.13 GiB）；归档压缩率与不同方法是否能复用物理文件尚未通过 pilot 实测，因此不能把该余量当作充分空间保证。

## 静态执行计划（非运行证据）

| 项目 | 计划数 |
|---|---:|
| target-present / H0 cases | 180 / 60 |
| 注册方法×角色槽位 | 1,470 |
| 其中 Production / batch 诊断 | 1,260 / 210 |
| Production branch calls | 2,520（1,890 target-present；630 H0） |
| 内部辅助 Production calls | 1,260 |
| Stage2 calls | 1,200–1,440 |

细分见[case/branch count audit](evidence/v4_integration_closure_20261123/case_branch_count_audit.json)与[完整计划](evidence/v4_integration_closure_20261123/execution_plan.json)。这些是源代码派生和设计展开计数，**不是本次执行数量**。

## Pilot 启动前条件

1. 独立审查并冻结适用的统计 MDE 与检测覆盖率非劣界值。
2. 已完成当前本机 R22 文档、设计、case-audit、matrix manifest、输出路径和 archive 命名范围内的 seed 碰撞核查。候选 pilot seed 为 `1937001`，不属于 V4 target confirmatory `1933001–1933030` 或 H0 `1936001–1936030`。冻结前仍须由完整项目 seed registry/外部归档完成最终复核；审计范围和限制见[V4预检审计](36_v4_preflight_audit_and_cuda_pilot_proposal_20261123.md)。
3. 为首个 +4 ns case 提供足够的可用临时空间或经用户允许的专用输出卷。必须覆盖 raw、Production/CSI 输出、compact ZIP 并存和系统余量；仅达到8.86 GB下界不够。
4. 先运行一个真实 Native CUDA case，实际记录每角色/方法目录增长、峰值空间、归档大小、压缩比和 raw 清理回收量；立即按逐成员验证后的 archive/ledger 清理 raw。
5. pilot 只报告空间、归档、断点恢复和执行身份；不能用 pilot 结果替代统计确认性矩阵，不能宣称性能改善。

如 pilot 中断，按已有 matrix checkpoint 恢复；只有已通过 CRC、archive manifest、members 和 SHA 校验的 compact ZIP 才能作为 raw 删除依据。pilot 结果审查完成并按 ledger 清理可再生数组后，最终项目占用维持低于20 GiB。

## 后续预检裁决

2026-11-23 的补充审计确认：主要比较的定位 MDE 与检测覆盖率非劣界值仍未填写，不能从 V3.3 full-case batch 结果反推；当前卷与 `/tmp` 的可用空间均低于该 pilot 单 case 的原始数据下界，且没有已验证的峰值/归档余量。故状态明确为 **`BLOCKED`**，目前不具备启动 pilot 的空间和统计设计条件。完整 case 参数、输出、停止条件和候选命令见[预检审计与 pilot 提案](36_v4_preflight_audit_and_cuda_pilot_proposal_20261123.md)。

## 本次磁盘清理

仅删除本阶段明确命名的 `/tmp/pgrcc-r22-v4-*` pytest fixtures 和短期验证文件，共2,094个文件、5,283,075逻辑字节。`/tmp` 是独立 tmpfs，回收空间不会增加项目所在卷的可用空间。逐项路径、每个目录的文件数/逻辑字节、清理后 tmpfs 与项目卷可用空间见[临时文件清理账本](evidence/v4_integration_closure_20261123/temp_cleanup_ledger.json)。仓库中的历史 case、`.scratch`、失败归档、原始数据、构建产物及其他未跟踪文件均未删除。
