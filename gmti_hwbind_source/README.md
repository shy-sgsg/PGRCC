# GMTI 硬件绑定与七检查点 Gate 源码包

## 1. 用途

本包用于给 Linux C++ GMTI 算法增加与现有 SAR 方案一致的运行期保护：

```text
real hardware binding
+ encoded checkpoint
+ combined / deferred gate
```

七个检查点名称固定为：

```text
initialization
check1
check2
check3
check4
check5
check6
```

行为如下：

- `initialization`：首次采集硬件并完成 SM3 摘要校验；
- `check1`～`check5`：复用运行期缓存并记录流程状态；
- `check6`：强制重新采集硬件并计算摘要；
- `final_decision()`：统一检查七点是否全部出现、全部通过、顺序正确且 `check6` 完成刷新。

本包不修改 GMTI 算法的输入、输出和数学逻辑。

## 2. 支持范围

要求：

- Linux；
- C++17；
- CMake 3.14 或更高；
- 系统提供 `lsblk`；
- 运行账号有权读取 `/proc/cpuinfo` 和 `/etc/machine-id`。

硬件绑定使用三个字段：

```text
CPU Serial    /proc/cpuinfo 中的 Serial
Disk Serial   lsblk -d -n -o SERIAL
Machine ID    /etc/machine-id
```

三者缺少任意一个都会拒绝生成摘要或拒绝授权。当前 CPU 采集规则与 SAR
基线保持一致，主要面向能在 `/proc/cpuinfo` 暴露 `Serial` 的 Linux/ARM
终端；普通 x86 Linux 通常没有该字段，不能未经适配直接使用。

## 3. 目录

```text
CMakeLists.txt
README.md
include/gmti_hwbind/
  binding.h
  binding_config.h       产品版本和目标 SM3 摘要
  hardware.h
  security_gate.h        业务接入公开接口
src/
  binding.cpp
  checkpoint_codec.h
  checkpoint_codec.cpp
  hardware_linux.cpp
  security_gate.cpp
  sm3.h
  sm3.cpp
tools/
  target_keygen.cpp       目标机摘要生成工具
examples/
  integration_example.cpp
tests/
  test_package.cpp
```

业务工程通常只需包含：

```cpp
#include "gmti_hwbind/security_gate.h"
```

## 4. 第一次在目标机生成摘要

### 4.1 编译工具

在最终授权的目标机解压源码包，然后执行：

```bash
cmake -S . -B build -DCMAKE_BUILD_TYPE=Release
cmake --build build --target gmti_target_keygen
```

### 4.2 生成目标摘要

软件版本必须与最终程序配置一致：

```bash
./build/gmti_target_keygen --soft-ver "1.0.0"
```

工具会输出：

```text
software_type
software_version
raw_hardware
canonical_hardware
target_sm3
```

以及可以直接复制进配置文件的三行常量。

工具输出包含目标机硬件标识，应作为交付记录妥善保存，不要写入正式运行日志或
公开发布。

### 4.3 写入配置

编辑：

```text
include/gmti_hwbind/binding_config.h
```

保持产品类型为 `gmti`，写入实际版本和工具输出的摘要：

```cpp
inline constexpr const char* kSoftwareType = "gmti";
inline constexpr const char* kSoftwareVersion = "1.0.0";
inline constexpr const char* kExpectedSm3Hex = "<64位十六进制SM3摘要>";
```

源码包默认把 `kExpectedSm3Hex` 留空。未配置、长度不是 64 或包含非十六进制
字符时，运行期会 fail closed，`checkpoint()` 返回
`GateCode::NotConfigured`，最终判断必定失败。

不要把 SAR 或 MMTI 的摘要复制到这里。即使硬件相同，由于 `software_type`
参与摘要，三个产品的摘要也应不同。

## 5. 编译和测试

写入配置后重新编译：

```bash
cmake -S . -B build -DCMAKE_BUILD_TYPE=Release
cmake --build build
ctest --test-dir build --output-on-failure
```

主要产物：

```text
build/libgmti_hwbind.a
build/gmti_target_keygen
build/gmti_integration_example
build/gmti_hwbind_tests
```

配置正确并且当前机器就是授权目标机时：

```bash
./build/gmti_integration_example
```

应输出：

```text
GMTI authorization passed
```

## 6. 接入 GMTI 工程

### 6.1 CMake 接入

假设本包放在业务工程的 `third_party/gmti_hwbind_source`：

```cmake
add_subdirectory(third_party/gmti_hwbind_source)
target_link_libraries(your_gmti_target PRIVATE gmti_hwbind)
```

`gmti_hwbind` 会公开 `include/` 头文件目录并自动链接线程库，不需要再手工添加
第三方依赖。

如果业务工程不使用 CMake，也可以把以下文件加入原工程：

```text
src/binding.cpp
src/checkpoint_codec.cpp
src/hardware_linux.cpp
src/security_gate.cpp
src/sm3.cpp
```

并把下面两个目录加入头文件搜索路径：

```text
include
src
```

编译选项至少启用 C++17，并链接 pthread。

### 6.2 七点调用

每次开始一条新的 GMTI 处理流程前先清空上一轮 gate 状态：

```cpp
#include "gmti_hwbind/security_gate.h"

gmti::security::reset_gate();
```

在七个真实业务位置分别插入：

```cpp
gmti::security::checkpoint(
    gmti::security::CheckpointId::Initialization);

gmti::security::checkpoint(
    gmti::security::CheckpointId::Check1);

gmti::security::checkpoint(
    gmti::security::CheckpointId::Check2);

gmti::security::checkpoint(
    gmti::security::CheckpointId::Check3);

gmti::security::checkpoint(
    gmti::security::CheckpointId::Check4);

gmti::security::checkpoint(
    gmti::security::CheckpointId::Check5);

gmti::security::checkpoint(
    gmti::security::CheckpointId::Check6);
```

不要把七次调用连续堆在同一个函数中。它们应分布在初始化和六个真实、必经的算法
阶段，调用顺序必须为：

```text
Initialization -> Check1 -> Check2 -> Check3
               -> Check4 -> Check5 -> Check6
```

`check6` 应放在最后一个必经的高价值阶段。如果当前选择的位置不是必经路径，应先
调整位置再交付。

### 6.3 统一判断

在所有受保护结果对外生效前统一判断：

```cpp
if (!gmti::security::final_decision()) {
    return false;
}
```

不要在每个 checkpoint 后都写相同的：

```cpp
if (gmti::security::checkpoint(...) !=
    gmti::security::GateCode::Ok) {
    return false;
}
```

否则会重新形成七组容易识别的 `call-test-branch` 模式，失去 deferred gate
的作用。

完整接法见：

```text
examples/integration_example.cpp
```

## 7. 返回码与排查

`checkpoint()` 返回：

| 返回码 | 含义 |
| --- | --- |
| `GateCode::Ok` | 当前检查点通过 |
| `GateCode::NotConfigured` | 目标摘要未正确配置 |
| `GateCode::HardwareMismatch` | 硬件采集失败或当前摘要与目标摘要不匹配 |
| `GateCode::BadFlow` | 检查点发生逆序 |

可以使用以下接口读取位图状态：

```cpp
const gmti::security::GateSnapshot state =
    gmti::security::gate_snapshot();
```

正式版本建议只记录返回码和状态位，不记录原始硬件标识、规范化绑定串或摘要。

## 8. 交付前验证

至少完成：

1. 目标机七点完整顺序运行可以通过；
2. 非目标机不能通过；
3. 任意必经点缺失时 `final_decision()` 返回 `false`；
4. 检查点逆序时不能通过；
5. `check6` 执行强制刷新；
6. 修改 CPU、磁盘或 machine-id 任一绑定字段后不能通过；
7. GMTI 摘要不能被 SAR/MMTI 配置复用；
8. Release 库中不存在 checkpoint 明文。

最后一项可以检查静态库：

```bash
strings build/libgmti_hwbind.a |
  grep -E 'initialization|check1|check2|check3|check4|check5|check6'
```

预期无输出。测试程序和示例程序为了验证/说明行为可能包含这些文字，因此应检查
`libgmti_hwbind.a`，不要用整个 `build/` 目录作为扫描对象。

## 9. 边界和注意事项

- checkpoint 的 XOR 编码是标识隐藏，不是密码学加密；
- SM3 摘要和多点 gate 提高了直接复制及单点绕过成本，但不能保证本地二进制绝对
  不可逆向或补丁修改；
- 默认 gate 状态属于进程内全局 GMTI 状态，支持多线程安全调用，但不支持多个
  GMTI 任务同时交错执行；并行任务需要改为每任务独立的 gate session；
- `reset_gate()` 只清空七点流程状态，硬件校验缓存仍会保留；每轮 `check6` 都会
  强制刷新缓存；
- 更换 CPU、绑定磁盘、系统 machine-id 或软件版本后，需要重新生成目标摘要并
  重新编译；
- 不要提交 `build/`、目标机硬件采集输出或包含客户摘要的临时记录。

