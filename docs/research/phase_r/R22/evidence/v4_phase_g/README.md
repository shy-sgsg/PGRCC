# R22-V4 Phase G 机器证据

本目录保存 Phase G 统计契约、候选 seed 与磁盘资源的精简审计记录。证据均为本地 CPU 统计/文档审计；没有运行 Stage2、Native CUDA、Production 或确认性矩阵。

| 文件 | 内容 |
|---|---|
| `statistical_contract_audit.json` | bootstrap-t 定义、零方差处理、共同支持门槛候选、主/次终点、H0 单位及尚待用户决定的字段 |
| `seed_registry_audit.json` | 当前 GitHub 分支身份、已有本地历史扫描范围、V3.3 历史支持量分析及外部注册表限制 |
| `resource_constraint_audit.json` | 项目实际磁盘占用、P0 与 compact 大小、四类 schema 未测量项和20 GB最终门 |
| `validation_summary.json` | 本阶段定向测试、JSON 检查和未执行的真实 CUDA 项 |

候选统计界值仍未获用户批准；因此设计保持 `DRAFT_NOT_FROZEN`、确认实验授权保持关闭。V3.3 支持分析使用历史 batch estimator，仅用于说明候选支持下限 1/5/10 在该历史切片上没有区分度，不用于选择阈值。
