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
