# R16 实验设计：C1 Gamma=1 传统 Production Baseline

设计状态：冻结后执行；日期：2026-10-25。实验结论和实际执行身份见 [R16 结果报告](R16_minimal_c1_results.md) 与 [R16 证据包](evidence/r16_c1_gamma1_seed505/README.md)。

## 1. 研究定位

R16 补齐普通复数相减 `C1`（固定 `Gamma=1`）的真实 Native CUDA Production 基础能力边界。它用于回答：在不消费额外 OFF 标定参考时，最简单的确定性两通道相减在本项目 GMTI 检测、杂波抑制和参数估计链路中的实际表现如何。

本实验不是未知系统误差因果实验，不比较 AI，不开发新检测、估计或关联算法，也不据单 seed 结果宣称统计显著性、普遍性能优势或论文创新。它只为总纲中的长期研究目标提供一项传统基线证据。

## 2. 运行前代码审查结论

- `src/production_calibration_adapter.cpp` 的 `ordinary_subtraction` 分支将复权固定为 `(1, 0)`，处理公式是 `F2 - F1`，不拟合参数。
- `src/gpu/gpu_kernels.cu` 在 Production 最终 F1/F2 完成 P38 range phase 后记录 adapter tap；该位置是 C1 的 Production 输入边界。
- C1 的 OFF 分支只作为配对 H0 评价数据，不作为 ON 或 TO 的拟合参考。ON 与 TO 不读取 OFF reference 文件。
- runner 原先用同一方法集合表示“需要写研究 tap”与“需要跨角色拟合并应用 reference”，会把 C1 展开成 Mode-A/B；评估器也把方法子集固定为 C0/C2/C3。R16 为这两个控制路径增加了针对性合同和 evaluator 测试，C2/C3 Mode-A 语义保持不变。
- C0 与 C1 使用同一对生产 F1/F2 输入和相同下游 Production 路径。C1 固定 Gamma=1，不使用额外 OFF 信息。

## 3. 冻结场景与条件

| 项目 | 固定值 |
|---|---|
| 场景 | 复用 R14 正常速度 seed505 场景 |
| seed / 目标地速 / SNR / 通道时延 | `505 / 6.7 m/s / 30 dB / 0 ns` |
| 波束 / period | beam 50 / 3 periods |
| 地杂波 | 使用 R14 同一 Stage2 连续面积纹理模型和物理可达距离窗；用 Stage2 报告及逐期输入 SHA 验证，不以开关字段代替有效性证据 |
| 角色 | OFF、ON、TO；与 R14 配对定义一致 |
| 条件 | A0、C0、C1，各执行 OFF/ON/TO，共 9 个 Production branches |
| C1 | `ordinary_subtraction`，固定 `Gamma=1+j0`，无 OFF reference；不调整支持区或参数 |
| Production | `USE_COREX=OFF`、local input、真实 CUDA、既有 GO-CFAR / Detection / 定位 / TrackManager 配置 |

R14 的 C2/C3 不重跑，前提是本次 A0/C0/C1 与 R14 的 OFF/ON/TO 三期 Stage2 文件 SHA 逐期一致。若不一致，则历史结果不作严格配对；是否追加一次最小同输入 C0/C2/C3 重跑，只根据该 SHA 检查结果决定，并记录理由。禁止拼接不同输入来源。

## 4. 评价口径

### 4.1 先验有效性门

1. Stage2 连续面积杂波的 packet/sample 计数均大于零，并记录测得杂波功率、有效距离窗及运行报告身份。
2. A0/C0/C1 每个角色的三期输入和 Production F1/F2 tap 完整；相同角色和 period 的 F1/F2 SHA 跨方法一致。
3. C1 adapter 每期均报告 Production 最终 F1/F2 来源、`ordinary_subtraction`、Gamma 精确为 `1+j0`、有效支持量大于零、无 truth 使用、无 OFF reference 与无 fallback。
4. Production 返回、CSI tap、final-union CFAR geometry/map/log、Detection 与 TrackManager 诊断完整。任何质量门失败均停止对应性能比较，不降低门槛。

### 4.2 报告指标

- H0/OFF CSI 杂波前后功率与残余功率变化；TO 目标-only CSI retention。
- ON 正确目标检测与漏检、非目标 Detection；OFF 错误输出。
- 逐 period 当前周期 `Confirmed + matched_this_frame` 目标关联、错误 Confirmed Track、目标 track identity 连续性。
- 可评价机会上的二维位置、方位角、folded Doppler 与 TrackManager ground speed 误差。
- 各 branch 单次 Production wall time，只作描述性记录。
- C1 与 R14 C2/C3 分层对照时，明确额外信息差异：C1/C0 无需 OFF reference；C2/C3 Mode-A 使用 paired OFF reference，且 OFF/H0 为 reference-in-sample。

经验 Pfa 与未解模糊径向速度如无合格估计器，标为 `NOT_EVALUABLE`。单 seed 中的三个 period 不是独立重复；单次 wall time 不是性能基准。

## 5. 停止与判定规则

- 输入/杂波/adapter/Production/CFAR/诊断有效性门失败：停止受影响结果的算法比较，定位并如实报告，不调整阈值制造通过。
- 输入 SHA 与 R14 不一致：不做跨阶段严格配对；只在确有必要时做同输入最小重跑。
- C1 表现较差：只说明 `Gamma=1` 极简基线的边界，不推断未知误差必须依赖精确 OFF，也不推断需要 AI。
- C1 表现接近或优于其他方法：单 seed 只作可行性观察，不作普遍优势结论。
- 本次可信 C1 Production 对照完成后停止；不扩 seed、不扫描 Gamma、不扩完整 Baseline 矩阵、不启动 AI。

阶段结论分别标注 `IMPLEMENTED`、`EXECUTED`、`EVALUABLE`、`PERFORMANCE_VERIFIED`。本任务预先规定单 seed 不足以将 `PERFORMANCE_VERIFIED` 置为 true。

## 6. 复现命令

从仓库根目录执行。R16 config 与 R14 task-scoped config 内容相同，以保持场景生成设置不变；方法子集由命令显式限定。

```bash
env USE_COREX=OFF GMTI_CFAR_DUMP_BEAM=50 python3 scripts/run_production_hierarchical_calibration_pilot.py \
  --config docs/research/phase_r/evidence/r16_c1_gamma1_seed505/inputs/r16_runner_config.json \
  --mode pilot \
  --output-root .scratch/r16_c1_gamma1_seed505_20261025 \
  --input-mode local \
  --delay-errors-ns 0 \
  --seeds 505 \
  --target-velocities-mps 6.7 \
  --target-snr-db 30 \
  --method-ids C0,C1 \
  --calibration-modes Mode-A \
  --max-cases 1
```

取得唯一 case manifest 后运行：

```bash
python3 scripts/phase_r/evaluate_r13_minimal_baseline_pilot.py \
  --normal-case-manifest .scratch/r16_c1_gamma1_seed505_20261025/cases/base__seed_505__v_6d7__snr_30__delay_0ns/case_manifest.json \
  --normal-scene-label normal_seed505_v6p7 \
  --output-dir .scratch/r16_c1_gamma1_seed505_20261025/r16_analysis
```

## 7. 执行后可比性审计

R16 实际运行后，OFF/ON/TO 九个 Stage2 输入文件与 R14 按 bytes 和 SHA-256 全部一致，所以不追加 C2/C3 重跑。但 R14 与 R16 的 `GMTI_pipe_core` SHA-256 不同；尽管 C0 的 F1/F2 和 CSI tap hashes 在九组均一致，C0 final-union CFAR map 九组均不一致。因此 R14 C2/C3 只保留为历史背景，不与 R16 C1 作为严格同运行因果排名。详细字段见[跨阶段输入身份与 C0 Production 可比性表](evidence/r16_c1_gamma1_seed505/analysis/r14_r16_c0_production_comparability.csv)。
