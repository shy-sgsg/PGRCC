# Task 1 Report — Correct historical semantic/status contracts

## 实现摘要

Task 1 已完成，范围限定在历史 mechanism-only pilot 的语义、状态和 Python 回归。

- 将 M0 从 `Current` 更正为 `uncalibrated_subtraction_proxy`，将 M1 更正为 `ordinary_complex_subtraction`；两者均明确标记为历史 mechanism-only 方法，不代表生产 Current。
- 增加显式物理状态词汇：`RADAR_ESTIMATED`、`SENSOR_PRIOR_ONLY`、`PRIOR_PLUS_RADAR_RESIDUAL`、`KNOWN_TRUTH`、`NOT_EVALUABLE`。
- Servo/velocity 的 P1 物理行现在保持 `SENSOR_PRIOR_ONLY`，不会被提升为雷达估计；delay 的盲估计为 `RADAR_ESTIMATED`，已知物理行使用 `KNOWN_TRUTH`。
- 增加非法/低相干元数据判定为 `NOT_EVALUABLE` 的回归测试；未改变既有 estimator 数学、阈值或 C++ 行为。
- 移除历史决策标签 `GO_COUPLED_PHYSICAL_STATE_STUDY` 的当前输出；mechanism-only pilot 在缺少生产证据时输出 `NEED_MORE_SINGLE_ERROR_PRODUCTION_EVIDENCE`。
- 同步配置、生成的 mechanism report 和相关契约测试。

## 确切文件

实现与配置：

- `scripts/run_equivalent_vs_physical_calibration.py`
- `scripts/analyze_equivalent_vs_physical_calibration.py`
- `configs/research/equivalent_vs_physical_calibration_pilot.json`
- `docs/AI_CSI_40_等效复校准与物理校准定向Pilot报告.md`

测试：

- `tests/test_equivalent_vs_physical_calibration_pilot.py`
- `tests/test_equivalent_calibration_report.py`
- `tests/test_equivalent_calibration_status.py`（新增）

本报告文件：

- `.superpowers/sdd/2026-09-22-production-hierarchical-calibration-pilot/task-1-report.md`

未修改 C++、生产 calibration adapter、生产 runner、非 Task 1 配置或既有 `outputs/` 产物。

## TDD 红灯命令与结果

首先运行包含新状态测试的聚焦集合：

~~~bash
python3 -m pytest -q \
  tests/test_equivalent_vs_physical_calibration_pilot.py \
  tests/test_equivalent_calibration_report.py \
  tests/test_equivalent_calibration_status.py
~~~

结果：退出码 `2`。测试收集阶段因待实现的 `PHYSICAL_STATUS_VOCABULARY` 导入失败。

随后运行不包含新增测试的历史聚焦集合，以确认旧实现的行为性失败：

~~~bash
python3 -m pytest -q \
  tests/test_equivalent_vs_physical_calibration_pilot.py \
  tests/test_equivalent_calibration_report.py
~~~

结果：退出码 `1`，`6 failed, 4 passed`。失败项实际暴露了旧的 M0/M1 名称、`OK` 物理状态、`GO_COUPLED_PHYSICAL_STATE_STUDY` 决策和旧报告文本。

## TDD 绿灯命令与结果

~~~bash
python3 -m pytest -q \
  tests/test_equivalent_vs_physical_calibration_pilot.py \
  tests/test_equivalent_calibration_report.py \
  tests/test_equivalent_calibration_status.py
~~~

结果：退出码 `0`，`14 passed`。

直接运行历史 mechanism pilot 的最终检查也通过：

~~~bash
python3 scripts/run_equivalent_vs_physical_calibration.py \
  --config configs/research/equivalent_vs_physical_calibration_pilot.json \
  --output-root /tmp/task1-final-dsi4lcq3
~~~

结果：退出码 `0`，`pilot_status=passed`，决策为
`NEED_MORE_SINGLE_ERROR_PRODUCTION_EVIDENCE`。输出确认 M0/M1 名称已更正，servo/velocity 状态集合包含 `SENSOR_PRIOR_ONLY`，状态词汇写入 manifest。

## 回归命令与结果

~~~bash
python3 -m pytest -q \
  tests/test_two_channel_complex_calibration.py \
  tests/test_two_channel_complex_calibration_sanity.py \
  tests/test_equivalent_calibration_docs.py \
  tests/test_mechanism_estimators.py
~~~

结果：退出码 `0`，`24 passed`。

~~~bash
python3 -m py_compile \
  scripts/run_equivalent_vs_physical_calibration.py \
  scripts/analyze_equivalent_vs_physical_calibration.py
~~~

结果：退出码 `0`。

~~~bash
python3 -m json.tool \
  configs/research/equivalent_vs_physical_calibration_pilot.json >/dev/null
~~~

结果：退出码 `0`。

~~~bash
git diff --check
~~~

结果：退出码 `0`。

## Fix round 1：review findings

### Finding 1：P2/PK+R clutter rows被旧状态门控丢弃

先添加回归测试并运行：

~~~bash
python3 -m pytest -q tests/test_equivalent_vs_physical_calibration_pilot.py::test_physical_residual_rows_are_kept_for_explicit_status_vocabulary
~~~

旧实现结果：退出码 `1`，`1 failed`，失败为 `KeyError: 'P2'`；原因是 clutter-row append 仍只接受旧的 `{OK, PARTIAL}` 状态。

修复将 append 条件改为接受显式物理状态词汇中除 `NOT_EVALUABLE` 外的状态，因此保留 `RADAR_ESTIMATED`/`KNOWN_TRUTH` 的 P2/PK+R 行，并继续丢弃 `NOT_EVALUABLE` 行。

修复后的定向测试：

~~~bash
python3 -m pytest -q tests/test_equivalent_vs_physical_calibration_pilot.py::test_physical_residual_rows_are_kept_for_explicit_status_vocabulary
~~~

结果：退出码 `0`，`1 passed`。

### Finding 2：低相干/status 回归测试未被纳入版本控制

`tests/test_equivalent_calibration_status.py` 已作为 Task 1 fix commit 的显式暂存文件纳入；它覆盖完整物理状态词汇、非法/低相干 metadata 的 `NOT_EVALUABLE` 和 robust low-phase-coherence metadata。

Fix round 完整聚焦测试：

~~~bash
python3 -m pytest -q \
  tests/test_equivalent_vs_physical_calibration_pilot.py \
  tests/test_equivalent_calibration_report.py \
  tests/test_equivalent_calibration_status.py
~~~

结果：退出码 `0`，`15 passed`。

Fix round 相关回归：

~~~bash
python3 -m pytest -q \
  tests/test_two_channel_complex_calibration.py \
  tests/test_two_channel_complex_calibration_sanity.py \
  tests/test_equivalent_calibration_docs.py \
  tests/test_mechanism_estimators.py
~~~

结果：退出码 `0`，`24 passed`。

Fix round 静态检查：

~~~bash
python3 -m py_compile \
  scripts/run_equivalent_vs_physical_calibration.py \
  scripts/analyze_equivalent_vs_physical_calibration.py
python3 -m json.tool \
  configs/research/equivalent_vs_physical_calibration_pilot.json >/dev/null
git diff --check
~~~

结果：三个命令均退出码 `0`。

## 未完成项与担忧

- 本 Task 不包含生产 F1/F2 calibration adapter、CUDA tap、production delay runner、CFAR/TrackManager/PIPE 证据或正式 GPU pilot；这些属于后续 Task 2–5。
- `outputs/` 中已有的历史/未跟踪产物未被修改。若旧 ignored manifest 仍含废弃决策标签，它只作为历史产物保留，不作为当前源码或当前 report 的决策来源。
- 本 Task 未进行 C++ Release build、CTest 或 CUDA 验证，因为没有 C++ 修改，且 brief 明确禁止扩展到 adapter/runner 范围。
- 工作树未提交、未暂存；用户已有的未跟踪实验输出均保留。
