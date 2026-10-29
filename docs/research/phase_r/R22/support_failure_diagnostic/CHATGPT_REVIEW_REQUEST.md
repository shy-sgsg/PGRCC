# PGRCC R22-S1 CUDA 局部因果诊断：ChatGPT 独立审查请求

## 先阅读的固定快照

**请先读取下面审查快照中的 `docs/research/RESEARCH_MASTER_PLAN.md`，完整理解 PGRCC 的 GMTI 研究主题、总体目标、核心科学问题、研究内容、总体研究思路和长期技术路线。之后再读取动态研究状态、决策日志和审查协议。不能只评价阶段任务是否完成，还要判断本阶段是否推进总体目标、研究方向是否仍合理。**

- 仓库：<https://github.com/shy-sgsg/PGRCC>
- 分支：`codex/unknown-system-error-next-stage`
- **审查快照 commit：`0c259ea359169e7338f4e427a57d8170fd0dbf59`**
- 总纲 blob：`e637e3769bde7814298ff012d0f42301884e2985`
- 本次实验执行时源代码 HEAD：`dc39afcbfea060777a4a7b247aeb3d4e16fe6d0c`；诊断 tap 当时在工作树中，现已随审查快照提交。
- Stage2 binary SHA-256：`47fa5ec34ca15cdc4edeb35226eefa941a78ee344aa2311e5e7984c811dcb40f`
- 原 Production binary SHA-256：`d9e4d2e0b8429c5415e94be1af0230f3c0148455c394dc6d661e7e6c13a4ad1e`
- 诊断 Production binary SHA-256：`480db5484a705634d7b467dcdec4b0c77cf8f212489071f8350cb2e0a195b83a`

请在该 commit 依次读取：

1. `docs/research/RESEARCH_MASTER_PLAN.md`
2. `docs/research/RESEARCH_STATUS.md`
3. `docs/research/RESEARCH_DECISION_LOG.md`
4. `docs/research/CHATGPT_REVIEW_PROTOCOL.md`
5. `AGENTS.md`
6. 本目录的 `README.md`、`evidence/evidence_manifest.json` 与 `evidence/trace_phase_audit_summary.json`
7. `evidence/trace_phase_audit_cells.csv`、`evidence/mode_comparison/`、`evidence/phase_residuals.png`
8. `evidence/runtime/`、`evidence/stage2/`、`evidence/run/`、`evidence/baseline/`
9. `src/runtime_diagnostics.cpp`、`src/gpu/gpu_kernels.cu`、`tests/production_calibration_config_selftest.cpp`、本目录的 `analyze_selected_cells.py`
10. `docs/research/phase_r/R22/SCREENING_GATE_REVIEW.md` 及本次使用的 R22 方法配置。

如果不能读取总纲、动态状态或特定证据，列出缺失文件，并将相关方向判断标记 `NOT_VERIFIED`。GitHub 上没有提交大型 Stage2 `.bin`；它们留在执行机的忽略目录，证据包内有 manifest 身份与选定 Production F1/F2 trace。不要假称已独立访问原始 `.bin`。

## 任务与实验概况

- 总体目标：公平刻画传统 GMTI 方法能力边界，找到可证伪的任务瓶颈。
- 科学问题：C4 Robust DDC-RB 在 `GMTI05 / beam 50 / az 52 / range 1065–1197` 的支持数从25降到7，目标污染是否为原因，还是存在处理链/背景相位等竞争解释？
- 本次获批准的范围：固定位置旁路 tap、seed1932001、2.5 m/s、+4 ns、20 targets、10 periods、beam 50，只做 SNR20 与 SNR30 配对重放和一个失败周期的原 binary 对照。
- 本地 Native CUDA；运行前设备记录为 NVIDIA GeForce RTX 3050 Laptop GPU；Stage2 area clutter 两个条件均为1300 packets、6,508,782 samples、平均功率0.0221596696662、距离窗84500–85500 m。
- C4 冻结门槛仍为 `min_support=8`、相位残差±0.35 rad；没有关闭错误门或回退失败结果。

## 关键观测（请独立核对证据）

1. 在处理后的133个选定 range cells 中，SNR20 ON 的直接拟合 support=25、coherence=0.2028995670；SNR30 ON support=7、coherence=0.1172000323，Production 行为 `NOT_EVALUABLE`。离线计算先将 CSV 数值还原到 float32，再用冻结规则拟合，精确重现 Production 结果。
2. SNR20/SNR30 的 A1_OFF 十个 Stage2 周期逐字节相同；OFF 两条件的 F1/F2 选定值266个复通道值完全相同。target generator manifest 除 target SNR 外一致。
3. Mode-A/Mode-B 的 F1/F2 输入逐值一致：SNR20 OFF/ON/TO，以及 SNR30 ON。SNR30 Mode-B OFF/TO 因 ON reference 失败而未执行。
4. ON20 与 ON30 的保留 support 集合仅交叠1个单元；ON20独有24个、ON30独有6个，拟合相位中心移动−0.7775734 rad。
5. Stage2 period 的索引是零基，Production `GMTI05` 对应 Stage2 `period_0004.bin`。该时刻有5个可见目标落在失败 range group。range-bin 重合没有证明二维响应覆盖 az row 52，更不能把全部目标 TO 响应归给单个目标。
6. 处理后的 ON/OFF/TO 未满足线性闭合：SNR30 的 F1、F2、互谱相对 L2 残差分别约0.00948、0.74374、0.64460。SNR20 同样未闭合。目标污染解释因此尚非唯一。
7. 诊断 tap 与原 binary 对齐的失败周期输出：GMTI05.bin SHA 完全一致；156条 Gamma 组记录的键与结果字段完全一致；adapter summary 对齐字段相同；period4/beam50 的两条 CFAR geometry 行相同。Detection/track 文件因运行周期历史不同未作字节比较。
8. 当前范围没有独立 seed、H0/Pfa 试验、Pd/目标保持评价或统计验证。

## 请完成两层审查

### 第一层：实现与因果证据是否正确

- 审查 tap 是否确实只记录指定范围，是否覆盖选定通过组与失败组，未把真值输入 estimator。
- 核验 Mode-A/Mode-B 比较是否在 F1/F2 形成阶段之前/之后，是否足以证明两种算子输入一致；指出没有执行的配对角色。
- 核验 `GMTI05` 与 Stage2 `period_0004.bin` 的索引解释及目标真值映射。
- 评估 OFF raw 周期逐字节相同、OFF F1/F2 相同、Mode-A/B 输入相同、ON20/30 支持变化分别能排除什么混杂，不能排除什么。
- 根据 F1/F2、互谱闭合残差和 P38/range-phase 处理链，判断目标污染是否可识别；不要把距离 bin 重合或 group coherence 当作因果证明。
- 检查原/诊断二进制对照是否足以支持 tap 非侵入性，以及哪些输出因运行历史不同不能外推。
- 检查实现、失败关闭和自检是否覆盖新增 tap 的关键边界。发现可复现缺陷时给出文件/行和最小修复建议。

### 第二层：总体科研价值与方向

- 本阶段是否推进研究总纲的 GMTI 目标，价值属于机制证据、工程基础还是任务性能证据？
- `INCONCLUSIVE / STOP_AT_GATE` 是否恰当？是否有证据足以确认传统 C4 的一般性能力缺陷？
- 当前结果是否支持低速目标、复杂杂波、未知系统失配或 AI 方向调整？请分别说明证据边界。
- 是否存在比 AI 更简单的下一步确定性实验/分析？只提出一个优先任务，列明最小输入、观察量、进入条件、停止条件；不要自动启动。
- 是否需要修改研究总纲？若需要，只提出具体修改建议及证据、影响，不得把建议当成用户批准。

## 输出格式

1. `IMPLEMENTED / EXECUTED / EVALUABLE / PERFORMANCE_VERIFIED` 状态表。
2. 阶段任务裁决：`PASS / PASS_WITH_LIMITATIONS / FAIL`，与整体科研状态分开。
3. 直接观测、支持的推断、仍未验证的解释。
4. 对总研究目标的贡献与 `CONTINUE / STOP / PIVOT / INCONCLUSIVE` 建议。
5. 唯一下一步任务及明确进入/停止条件。
6. 列出审查不到的文件/数据，禁止将单 seed 写成统计显著性或任务性能改善。

Stage test/Production 运行成功不等于科研假设成立；support 恢复也不等于检测或参数估计性能改善。未完成独立 seed 与任务级评价前，不得通过 AI 进入门。
