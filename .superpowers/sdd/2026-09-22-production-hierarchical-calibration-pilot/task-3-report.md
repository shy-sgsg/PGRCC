# Task 3 report — production hierarchical calibration pilot

日期：2026-09-22

## 结果

Task 3 已完成：研究配置、运行时 snapshot、共享 CUDA production tap、fail-closed
诊断以及 non-fusion/fusion 两条调用路径已接入。默认生产路径保持关闭；本 Task
没有改动 Task 2 adapter 的实现，也没有进入 Task 4/5/6。

## 改动文件

- `include/config_structs.hpp`：新增研究配置默认值与统一校验；保留
  `csi_cancellation_mode = "legacy_min_magnitude"`。
- `src/loadXML.cpp`：严格解析研究字段，非法值返回解析失败，不静默回退到
  Current。
- `include/runtime_diagnostics.hpp`、`src/runtime_diagnostics.cpp`：运行时 JSON/TXT
  snapshot 记录全部研究字段；新增紧凑的
  `production_calibration_adapter.csv` per-run/beam tap 记录。
- `include/GMTIProcessor.hpp`：共享 CUDA 入口增加诊断 beam 参数，保留默认参数兼容性。
- `src/gpu/gpu_kernels.cu`：在共享
  `clutter_cancel_38_paper_1_cuda` 入口、最终 F1/F2 和既有 P38/range-phase preparation
  之后，按研究开关下载只读 F1/F2、调用 Task 2 host adapter，并只写回现有 CSI device
  buffer；`NOT_EVALUABLE` 和尺寸/上传失败均返回 `false`，不 fallback 到 Current。
- `src/processOnePeriod.cpp`：non-fusion 与 fusion 两个现有调用点均使用同一共享入口，
  没有复制 calibration 实现。
- `CMakeLists.txt`：注册配置/runtime selftest 和 CTest。
- `tests/production_calibration_config_selftest.cpp`：默认值、XML 合法/非法值、旧
  CSI 默认、runtime snapshot 和 tap CSV 回归。
- `tests/test_production_calibration_tap.py`：配置、共享入口、禁用 guard、调用路径和
  range-phase placement 的静态合约测试。

## TDD 记录

以下是本 Task 实际执行的 red → implementation → green 顺序；red 阶段的错误没有被
隐藏。

### Red

1. `cmake --build build --target production_calibration_config_selftest -j4`
   退出码 2：配置 selftest target 尚未注册，CMake 报 `No rule to make target`。
2. 注册测试并先运行测试、尚未实现配置/runtime/CUDA 接口：
   `cmake --build build --target production_calibration_config_selftest -j4`
   退出码 2：编译错误指出缺少 research 配置字段、validator 和 runtime tap API。

### Green

- `cmake -S . -B build -DCMAKE_BUILD_TYPE=Release`：退出码 0，配置和生成成功。
- `cmake --build build --target production_calibration_config_selftest -j4`：退出码 0。
- `./build/production_calibration_config_selftest gmti.xml`：退出码 0，PASS；非法 method
  和非法 bool 的预期解析错误被打印后由 selftest 正确断言。
- `PYTHONPATH=. pytest -q tests/test_production_calibration_tap.py`：退出码 0，`5 passed`。
- `cmake --build build --target GMTI_pipe_core -j4`：退出码 0。

## 最终构建与测试证据

- `cmake --build build -j4`：退出码 0，`[100%] Built target GMTI_core`；构建仅有既有
  warning（初始化顺序、未使用变量和 nvlink 兼容库 warning），无 Task 3 错误。
- `ctest --test-dir build --output-on-failure`：退出码 0，23/23 tests passed，总耗时
  3.65 秒；包含 `production_calibration_adapter_selftest` 和
  `production_calibration_config_selftest`。
- `./build/production_calibration_adapter_selftest && ./build/production_calibration_config_selftest gmti.xml`：退出码 0；adapter selftest passed，配置/runtime selftest PASS。
- `PYTHONPATH=. pytest -q tests/test_production_calibration_tap.py`：退出码 0，`5 passed in 0.02s`。
- `git diff --check`：退出码 0，无 whitespace error。

## 默认路径与边界证明

- `research_calibration_enable` 默认是 `false`。共享 CUDA 入口中 D2H 下载、host
  adapter 调用和结果上传都位于 `if (cfg.research_calibration_enable)` 内；关闭时不
  下载、不调用 adapter，`research_output_ready` 保持 false，既有 CSI 分支继续执行。
- 研究 tap 只使用最终 F1/F2 和生产 support bounds；F1/F2 输入不被覆盖，adapter 结果
  只写入已有 `gpu_ptrs_.csi`。下游 CUDA CSI/CFAR/聚类/TrackManager/PIPE 路径未复制
  或替换。
- adapter 返回 `NOT_EVALUABLE`、输出尺寸错误、下载失败或上传失败时记录诊断并返回
  `false`，不会悄悄降级到 Current。
- `processOnePeriod` 与 `processOnePeriodFusionCache` 的现有两处调用都经过同一共享
  `clutter_cancel_38_paper_1_cuda` 入口；process 文件中没有第二个
  `applyProductionCalibration` 实现。
- runtime JSON/TXT 记录 enable、method、support、range band、robust phase threshold、
  truth-blind 标记和 tap source；CSV 记录 beam、method、status、support/excluded、
  groups、Gamma 摘要、phase coherence、source、reason 和
  `truth_used_in_estimator`。

## 限制

已运行 `nvidia-smi`，退出码 9，真实错误为：`NVIDIA-SMI has failed because it couldn't
communicate with the NVIDIA driver.` 当前环境没有可用 NVIDIA driver，因此本 Task 没有
运行真实 GPU 端到端 production CUDA case；Release nvcc 编译和 CPU/selftest/CTest 不能
替代该运行时证据。待设备可用时应补跑共享入口的 disabled/enabled runtime smoke，并
核对 adapter CSV；本限制不影响本次代码/配置/静态合约和 Release 构建验证结论。

本次没有修改或暂存 `outputs/`，也没有纳入与 Task 3 无关的用户改动。

## Fix round 1 — tap 写入失败必须 fail-closed

Reviewer 指出 `recordProductionCalibrationTap` 原先是 `void`，并且在输出目录、CSV
打开或写入失败时吞掉错误；CUDA 调用点也没有据此停止。该问题在当前代码中复核成立。

### TDD red

先在现有 `production_calibration_config_selftest` 增加 research enabled、`result_add`
指向“普通文件/child”不可创建目录的回归，并运行：

```text
cmake --build build --target production_calibration_config_selftest -j4
```

退出码为 2。真实错误是新增断言需要 bool，但旧 API 返回 `void`：
`could not convert ... recordProductionCalibrationTap(...) from 'void' to 'bool'`。

### Fix

- `recordProductionCalibrationTap` 声明/实现改为 `bool`。
- research enabled 时 `result_add` 为空、目录创建失败、CSV open 失败、header 写入
  失败或 row flush/write 失败均打印明确错误并返回 `false`；disabled 调用为无影响的
  成功 no-op。
- `src/gpu/gpu_kernels.cu` 的全部 7 个 tap 调用点检查 bool 结果；记录失败统一返回
  `false`，成功记录后才继续/标记 research output ready。Task 2 adapter 未修改。

### TDD green 与回归

- `cmake --build build --target production_calibration_config_selftest GMTI_pipe_core -j4`：退出码 0。
- `./build/production_calibration_config_selftest gmti.xml`：退出码 0，PASS；新增 blocker
  场景输出 `cannot create output dir` warning，并断言返回 `false`。
- `PYTHONPATH=. pytest -q tests/test_production_calibration_tap.py`：退出码 0，`5 passed`。
- `cmake -S . -B build -DCMAKE_BUILD_TYPE=Release && cmake --build build -j4`：退出码 0，
  `[100%] Built target GMTI_core`；仅有既有编译/nvlink warnings。
- `ctest --test-dir build --output-on-failure`：退出码 0，23/23 tests passed。
- `git diff --check`：退出码 0。

### GPU 边界

本 fix round 没有运行 GPU smoke，也没有把它写成已完成；由控制器在本 fix review 后
运行。控制器已通过受限权限确认 GPU 可见：RTX 3050、driver 580.173.02、CUDA 13.0。
