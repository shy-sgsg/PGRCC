# R18：Production 基准冻结

状态：`IMPLEMENTED`（配置与契约已冻结）；实际执行判定见 `05_execution_and_quality_audit.md`。

## 固定生产链

- 输入：Stage2 Native 场景生成的四通道协议 IQ；Production 按 `(1,3)` 与 `(2,4)` 分别融合为 F1/F2，再走现有脉压、DBS/P38、两通道 CSI、GO-CFAR、检测、聚类与 TrackManager。
- 通道失配：`delay_error_ns = 0`。R18 研究 C0–C3 传统杂波复权和任务级瓶颈；不将固定时延混入此阶段的算法因果比较。
- 几何：beam 50、expected range bin 2048、地面高程 0 m、平台高度 6,000 m、目标斜距 85 km。连续面积地杂波窗 84.5–85.5 km；area mean power `0.02`、texture sigma `0.25`、temporal rho `0.98`、thermal noise power `0.001`。
- 每场景：3 个连续 period，同一方法之间使用相同场景输入身份；Stage2 报告必须证实连续面积杂波 packet/sample、非零功率、有效距离/Doppler 支持和目标 truth ROI。`area_enabled=true` 不是通过证据。
- CFAR：沿用当前修复后的 Production GO-CFAR、现存配置与门槛。不得修改门限、几何、匹配门或过滤规则以制造成功。
- 可复现性：正式矩阵每个方法/角色记录源码 commit、clean tracked status、simulator/Production executable 的绝对路径与 SHA-256、运行时 XML、输入 SHA、stage logs、CSI tap、CFAR geometry/数值与检测/航迹 lineage。

## 构建身份

唯一验收构建为隔离目录 `/tmp/pgrcc-r18-native-release`，`CMAKE_BUILD_TYPE=Release`、`USE_COREX=OFF`，使用主机 Native CUDA/NVCC 与 RTX 3050 Laptop 对应架构 86。最终实际命令、`CMakeCache.txt`、编译器/NVCC 版本、GPU 状态、库依赖和二进制 SHA 在 `evidence/build/`。本目录不能复用 `build/` 或历史 `build_r7/` 产物。

## 方法定义与角色

| ID/条件 | 估计器与运行数据 | 应用方式 | R18 解释 |
|---|---|---|---|
| C0 | 生产 Current，不拟合复 Gamma | OFF/ON/TO 分别走当前生产路径 | operational anchor |
| C1 | 固定 `Gamma=1`，不拟合 | 同一方法配置处理 OFF/ON/TO | 最简单固定相减基线 |
| C2 Mode-B | 仅用当前 ON 原始可观测 F1/F2 拟合全域复 Gamma | 固定应用到 ON、OFF、TO 的三个记录；OFF 结果不是独立 Pfa 样本 | 可部署信息条件，ON 目标可能污染估计 |
| C3 Mode-B | 仅用当前 ON 原始可观测 F1/F2 按 Doppler 行稳健拟合 | 同上 | 可部署信息条件，需报告逐行支持与目标污染 |
| C2/C3 Mode-A | 由目标自由 paired OFF 拟合固定复 Gamma | 固定应用至 ON/TO | `PRIVILEGED_PAIRED_OFF_REFERENCE`；OFF 拟合样本内评估，不用于 Pfa |
| C2/C3 independent secondary | 单独 seed、同一冻结几何的 target-free C+N 样本估 Gamma | 固定应用至另一 seed 的 OFF/ON/TO | 仅在输入、同质性与 Production adapter 门均通过后评估；不是已证明现实可用的在线传感器参考 |

## pilot 与正式种子

- **执行门 pilot（不计入正式 12 场景）：** seed 101、6.7 m/s、30 dB、delay 0 ns、3 periods。该 seed 已在 R13 方法实验中出现，故只检查执行身份、输入闭合、质量门和方法实际接入，不进入正式统计汇总，也不据此改方法或参数。
- **正式矩阵：** seeds 606、707、808；每 seed 都运行 6.7 与 0.5 m/s、30 与 20 dB 的四种组合，总计 12 个场景。源码与纳入仓库的 R13–R17 清单中未发现这三个 seed 用于方法选择。条件列表在 `04_experiment_matrix.md` 与 `configs/research/phase_r18_native_cuda.json` 固定。
- **0.5 m/s 的定位：** R13 曾在该速度成功形成 3/3 目标机会且可评价 folded-Doppler、位置与方位，但单 seed 下 C0/C2/C3 的这些共同指标相同，未证实它是瓶颈或方法优势。R18 只将其作为低速压力层，避免按预期优胜选择低速。
- **SNR：** 30 dB 用于与已知饱和工作点对照；20 dB 用于降低目标完全饱和的风险。不得看到结果后换挡或删除失败条件。

## 正式工作量

核心矩阵为 12 场景 × `{C0,C1,C2 Mode-B,C3 Mode-B}` × `{OFF,ON,TO}`，最多 144 Production branches。配对 OFF Mode-A 信息参考为 12 × `{C2,C3}` × `{OFF,ON,TO}`，最多另 72 branches；其中 Mode-A OFF 是估计样本，不作为独立 H0。独立 secondary 子矩阵只针对一个预注册场景和 C2/C3，单独计入并明确标记。

`OFF`/`ON`/`TO` 的实际 Stage2 生成角色、输入 SHA、truth 和生产役分开记录。OFF/H0 的 hit ratio 与非目标输出不是经验 Pfa；若没有合法的 truth-filtered false-cell sample unit，Pfa 必须写 `NOT_EVALUABLE`。
