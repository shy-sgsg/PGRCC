# R22 V3.1 设计摘要哈希语义更正

## 更正范围

本说明更正 2026-11-06 的 V3.1 早停审计中关于设计 SHA-256 的解释。原审计将 matrix manifest 的 `design_sha256` 与 JSON 文件原始字节 SHA-256 直接比较，并据此称没有找到对应设计文件、精确设计身份 `NOT_VERIFIED`。该比较忽略了 runner 对 `design_sha256` 的定义。

## 复核方法与结果

当前冻结的 `scripts/phase_r/r22_experiment_matrix.py::_design_digest()` 对解析后的 JSON 使用 `sort_keys=True`、紧凑 separators 序列化，再计算 SHA-256。使用同一算法独立重算已提交的 V3.1 设计文件，并与归档 matrix manifest 核对：

| 身份 | SHA-256 | 结论 |
|---|---|---|
| V3.1 matrix manifest `design_sha256` | `696dce5b18fab9209f57c7d49922ff3c1ae4ea52f0a524c34cede8e8b933e635` | 规范化 JSON 对象哈希 |
| 对已提交 V3.1 JSON 重新计算的规范化哈希 | `696dce5b18fab9209f57c7d49922ff3c1ae4ea52f0a524c34cede8e8b933e635` | 与 manifest 完全一致 |
| 已提交 V3.1 JSON 原始文件字节 SHA-256 | `26e202fca197ae1f62cfad099cdf895de7b834eed1622c553e80b8cd2b94a839` | 不同哈希算法输入，不能与前两者直接比较 |

因此，V3.1 设计的规范化内容身份已通过复算闭合；“没有 JSON 原始字节哈希与 manifest hash 相同”本身不表示设计缺失。原 V3.1 manifest 未单独记录当时解析的设计文件绝对路径和原始文件字节 SHA，故这一层文件来源追溯信息仍未记录。

## 对历史结论的影响

- V3.1 的 `UNKNOWN_STOP`、6/288 case 执行范围、OOM 和其他未知失败状态均不改变。
- V3.1 终态仍不可续跑；V3.2 如执行，必须作为独立新矩阵从队列起点开始，不继承 V3.1 的 case。
- V3.1 的科学因素、质量门、单 seed 限制及“未完成性能验证”的判断均不改变。
- 仅将“V3.1 设计内容身份未闭合”更正为“规范化设计内容身份已验证；原始设计文件路径/字节 SHA 未单独记录”。
- V3.2 继续记录解析路径、原始文件 SHA、规范化设计 SHA 和执行源码身份，以改善文件级审计；该改进不被描述为修复一个不存在的科学设计哈希缺失。

研究总纲未修改；本更正不启动或授权任何额外 CUDA 实验。
