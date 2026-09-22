# Task 4 fix-round-1 report — production hierarchical calibration delay pilot

## 范围与结论

本轮只收尾 Task 4 fix-round-1，未进入 Task 5/6，未运行 full Cartesian 或
GPU formal。基线为 `HEAD=924e9c4`；保留已有的 15 个 Task4/C++/测试文件改动，
未回退任何用户改动。

本轮修复并验证了以下边界：

- Mode-A 只从 `OFF=C+N` 估计 Gamma，`ON=S+C+N` 与 `TO=S` 只能使用同一
  persisted reference fixed apply；Mode-B 只从 truth-blind `ON` 估计，`TO` 是
  evaluator-only。任何参考缺失或无效都保持 `NOT_EVALUABLE`，不回退 Current。
- method-specific XML overrides 通过 `run_production_branch(..., xml_overrides=...)`
  写入实际分支 `production.xml`，并对最终 XML 做字段审计。
- CFAR cell false-hit 只使用 `hit_cut_count / valid_cut_count`，valid denominator
  非正时为 `NOT_EVALUABLE`；cluster、protocol detection、track 保持独立层。
- formal selection 显式保留 SNR 30 和 35 的 targeted groups，不展开 full Cartesian。
- Python adapter row 现在要求每行 truth marker 存在且明确为 false、status 精确为
  `OK`、support 不低于 min；原始 rows/status/reason/support 保留，失败不替换为
  Current。
- C++ `applyProductionCalibrationReference` 只接受 `Status::kOk` persisted group，
  拒绝 `kPartial`、`kNotEvaluable`、低 support、非有限 Gamma/phase metadata；CSV
  loader 要求 truth 列并只接受明确 false 标记。
- compact 六文件仍严格为 `manifest.json`、`method_contract.csv`、
  `gamma_recovery.csv`、`clutter_metrics.csv`、`decision_matrix.csv`、`report.md`；
  decision matrix 现在也保留 ON−OFF/TO target-protection rule、CFAR denominator
  contract、cluster/protocol/track、association/state/payload 和 ID-switch
  evidence contract。无法从现有 debug 分类时仍为
  `NOT_IDENTIFIABLE_FROM_CURRENT_DEBUG`。

## 本轮 TDD red → green

新增的 adapter fail-closed 测试先得到真实红例：

```text
PYTHONPATH=. pytest -q tests/test_production_hierarchical_calibration_pilot.py -k 'adapter_rows_preserve_provenance_and_fail_closed'
```

退出码 `1`；旧 Python 实现把 `PARTIAL` 报为 `evaluable`。

```text
cmake --build build --target production_calibration_adapter_selftest -j4 && build/production_calibration_adapter_selftest
```

目标构建退出码 `0`，selftest 随后因 persisted `PARTIAL` 断言失败而中止（整体链
退出码 `134`）。

新增 compact decision-matrix 测试也先以 `KeyError: target_protection_rule` 退出码
`1`，确认字段虽被构造却没有写入 CSV。修复后两个新增边界测试均通过。

## 实际 green 验证

Python 相关回归共收集并运行 49 个：

```text
PYTHONPATH=. pytest -q tests/test_production_hierarchical_calibration_pilot.py tests/test_delay_stage1_contract.py tests/test_delay_stage1_provenance.py tests/test_channel_delay_correction.py tests/test_production_calibration_tap.py
```

结果：退出码 `0`，`49 passed in 16.42s`。

Release 配置确认：

```text
cmake -LA -N build | rg '^CMAKE_BUILD_TYPE:'
```

结果：`CMAKE_BUILD_TYPE:STRING=Release`。

受影响目标与 selftest：

```text
cmake --build build --target GMTI_pipe_core production_calibration_adapter_selftest production_calibration_config_selftest -j4
build/production_calibration_adapter_selftest
build/production_calibration_config_selftest gmti.xml
```

结果：目标构建退出码 `0`；adapter selftest PASS；config/runtime selftest PASS。
第一次漏传 `gmti.xml` 的 config selftest 命令按程序用法失败，未被记为代码回归；
补传源 XML 后退出码 `0`。

完整 Release 构建：

```text
cmake --build build -j4
```

结果：退出码 `0`，`GMTI_core`、`GMTI_pipe_core` 和全量 targets 构建完成。构建中
只有既有的初始化顺序、未使用变量及 nvlink 系统库兼容性 warning，没有本轮错误。

CTest：

```text
ctest --test-dir build --output-on-failure
```

结果：退出码 `0`，`23/23` tests passed。

语法、配置和差异检查：

```text
python3 -m py_compile scripts/run_production_hierarchical_calibration_pilot.py scripts/analyze_production_hierarchical_calibration_pilot.py scripts/run_delay_stage1_formal.py scripts/run_track_manager_e2e.py scripts/cfar_geometry_audit.py tests/test_production_hierarchical_calibration_pilot.py
python3 -m json.tool configs/research/production_hierarchical_calibration_delay_pilot.json >/dev/null
git diff --check
```

三项均退出码 `0`。

## GPU 与实验限制

```text
nvidia-smi
```

退出码 `9`：当前环境无法与 NVIDIA driver 通信。因此本轮没有声称真实 CUDA
algorithmic result，也没有运行 GPU formal、full Cartesian 或 Task 5 分析。后续
控制器在真实 CUDA 设备上应使用独立 output root 运行 targeted pilot，并保留 tap、
CFAR geometry、cluster/protocol/track/association/state/payload 和 compact evidence。

## 交付边界

本轮只应提交现有 15 个 Task4/C++/测试改动与本报告。`.scratch/`、`outputs/` 以及
其他未跟踪实验产物保留在工作区，绝不 stage。最短复现入口为：

```text
PYTHONPATH=. pytest -q tests/test_production_hierarchical_calibration_pilot.py tests/test_delay_stage1_contract.py tests/test_delay_stage1_provenance.py tests/test_channel_delay_correction.py tests/test_production_calibration_tap.py
cmake --build build -j4 && ctest --test-dir build --output-on-failure
```

## Task 4 fix-round-2 — strict raw reference groups

本轮基线为 `HEAD=170badd`，只处理 reviewer 指定的两个 Important，未进入
Task 5/6，未触碰 `.scratch/` 或 `outputs/`，也未运行 GPU formal/full Cartesian。

### Red → green

先运行新增 Python group contract 测试：

```text
PYTHONPATH=. pytest -q tests/test_production_hierarchical_calibration_pilot.py -k 'reference_group_rows_reject_empty_identity_and_gamma or reference_artifact_uses_group_rows_not_aggregate_rows'
```

基线退出码 `1`：`validate_reference_gamma_rows` 尚不存在，旧 writer 把 group id
写成 `global`。新增 C++ group coverage 调用随后以退出码 `2` 暴露旧 API 未接收
`range_band_bins` 参数。

修复后定向 contract 测试为 `2 passed`，runtime/loader source contract 为
`2 passed`：

```text
PYTHONPATH=. pytest -q tests/test_production_hierarchical_calibration_pilot.py -k 'reference_group_rows_reject_empty_identity_and_gamma or reference_artifact_uses_group_rows_not_aggregate_rows'
PYTHONPATH=. pytest -q tests/test_production_calibration_tap.py -k 'reference_csv_contract_rejects_wildcards_and_requires_strict_fields or runtime_tap_exports_per_group_reference_csv'
```

### 本轮修复

- `loadProductionCalibrationReference` 对 period/beam/az/range/support 和 Gamma
  使用非空、完整消费、有限/整数解析；不再把缺失字段当 wildcard 或 `0`。固定
  reference 只接受精确 `OK` 与显式 `false`，`result_id`/`group_id` 也必须非空；
  `phase_coherence` 保持可缺省或 `NaN`。
- `ProductionCalibrationTap` 复制 `Result.gamma_summary` 的每个 group，并额外写出
  `production_calibration_reference_gamma.csv`。writer 保留 period/result/beam、
  method/group、坐标、status/support、phase、复 Gamma、truth marker 和 reason；
  aggregate adapter CSV 不再作为 fixed-reference 输入。
- runner 发现并读取 dedicated group CSV，严格检查字段、方法、`OK`、显式 false、
  finite Gamma、support 和 optional phase；artifact 原样保留 group id 与每组 Gamma。
- fixed apply 严格要求 ordinary/SCC 一个 group、DDC/robust DDC 每个 az row、
  robust DDC-RB 每个 az×range band 的精确覆盖，并拒绝缺失、重复、越界、低 support
  或 PARTIAL group；不同 row/band Gamma 分别应用，不合并为全局 Gamma。

### 实际 green 验证

```text
PYTHONPATH=. pytest -q tests/test_production_hierarchical_calibration_pilot.py tests/test_delay_stage1_contract.py tests/test_delay_stage1_provenance.py tests/test_channel_delay_correction.py tests/test_production_calibration_tap.py
```

结果：退出码 `0`，`53 passed in 17.07s`。

```text
cmake --build build --target production_calibration_adapter_selftest production_calibration_config_selftest -j4
build/production_calibration_adapter_selftest
build/production_calibration_config_selftest gmti.xml
```

结果：目标构建退出码 `0`；adapter selftest PASS；config/runtime selftest PASS，
其中验证了 raw group CSV 的严格 header 以及两个 band 的不同 Gamma 保持 distinct。

```text
cmake --build build -j4
ctest --test-dir build --output-on-failure
```

结果：Release 全构建退出码 `0`，`GMTI_core`/`GMTI_pipe_core` 及全量 targets 完成；
CTest 退出码 `0`，`23/23` tests passed。仅有既有初始化顺序、unused variable 和
nvlink 系统库兼容性 warning。

```text
python3 -m py_compile scripts/run_production_hierarchical_calibration_pilot.py scripts/analyze_production_hierarchical_calibration_pilot.py scripts/run_delay_stage1_formal.py scripts/run_track_manager_e2e.py scripts/cfar_geometry_audit.py tests/test_production_hierarchical_calibration_pilot.py tests/test_production_calibration_tap.py
python3 -m json.tool configs/research/production_hierarchical_calibration_delay_pilot.json >/dev/null
git diff --check
```

三项均退出码 `0`。本轮没有声称 GPU algorithmic result；GPU formal、full Cartesian
和 Task 5 均明确未运行。
