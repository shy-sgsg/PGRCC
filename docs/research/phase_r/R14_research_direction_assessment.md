# R14 研究方向评估

日期：2026-10-25。依据：R13 seed101 与 R14 独立 seed505 的 normal-speed Native CUDA Production 比较；R14 为 delay=0 ns、每方法 3 个 ON target opportunities。该证据只支持有界传统 Baseline 方向判断，不回答未知通道误差的因果效应。

## 阶段裁决

**`CONTINUE` 一次有限的 C2 SCC / C3 Robust DDC 任务级复核。** R13 与 R14 两个独立 seed 均复现 C3 较高的 TO CSI retention（相对 C2 分别约 +7.36 dB、+7.18 dB），也都看到 ON 非目标 detection rows 约下降 30%，且目标检测仍为 3/3。这是可继续验证的 Production 任务差异。

目前不能说 C3 整体胜出：R14 C3 的 ON TrackManager ground-speed RMSE 为 9.259 m/s，C2 为 1.086 m/s（共同机会 n=2）；第 3 period 的目标 cell 相同，但 C3 的目标关联由 track 6 切换到 track 64。C3 相对 C0 Current 的 ON 非目标 detection rows 仍多 162；经验 Pfa 不可评估，且 Mode-A 的 OFF reference 是实验内配对样本，真实系统能否持续提供匹配的 target-free OFF 未验证。

因此 `CONTINUE` 只指继续验证同信息条件下的 C2/C3 任务权衡，**不**表示当前已验证总体性能收益。R14 当前阶段已经结束；本建议不自动运行新 seed、不调整场景、不扩方法矩阵、不训练 AI。长期 `Unknown System Error Characterization` 主线暂不修改：R14 的通道 delay 为 0 ns，不能用它支持或否定未知误差主线。

## 十个问题

1. **R13 的目标保持差异是否复现？** 是。TO CSI retention 的 C3−C2 差在 seed101 为约 +7.358 dB，在 seed505 为 +7.175 dB。两 seed 的 C2/C3 ON 正确检测都是 3/3。

2. **C2/C3 从哪一处理层开始不同？** Production final F1/F2 输入在每个 role×period 的 SHA 审计中相同，配对 OFF 身份也相同。差异从 Mode-A estimator 和 CSI adapter 开始：C2 是全支持 SCC，C3 是逐 Doppler row 的 Robust DDC；随后体现在 H0 residual、CSI target ROI、CFAR hit map、候选数和 TrackManager 关联。C3 ON 目标 CFAR margin 比 C2 高约 4.50–5.21 dB，但目标候选 cell 不变。

3. **差异是否影响实际 ON 任务？** 影响了非目标输出：seed505 C3 比 C2 少 289 条 ON 非目标 detection rows（−29.3%），少 177 条 false Confirmed+matched current-frame associations（−38.5%）。目标正确检测数仍同为 3/3、当前周期正确 confirmed target associations 同为 2/3；位置、方位和 folded Doppler 在共同机会相同。另一方面 C3 的 ON 地速误差显著较大，并出现当前目标 track ID 切换。因此收益是混合的，不构成净任务优势证明。

4. **C3 的 Robust screening 是否稳定、合理？** 本轮固定 0.35 rad 门下，117/117 个 OFF row groups 有效，支持样本保留 93,826/479,115（19.58%），分组支持数 658–943。R13 seed101 的留样比例约 19.53%，量级相近；当前两个 seed 仍不足以验证更广场景的统计稳定性。没有调阈值，也不根据本轮结果调参。

5. **虚警与航迹代价如何？** seed505 OFF 有效 CUT 为 1,581,840：C2/C3 unique union hits 为 79,319/66,503，observed fractions 为 5.014%/4.204%；H0 detection rows 为 376/327，false current-frame confirmed associations 为 155/155 与 140/140（两条有 confirmed associations 的 period frames），unique false track IDs 为 127/118。ON 中 C2/C3 false current-frame associations 为 460/462 与 283/285，false track IDs 为 341/247。全部是场景描述计数：OFF 又是 reference-in-sample，不能换算成 Pfa 或独立背景泛化。

6. **共同正确机会上的参数是否改善？** ON 位置 RMSE 两者均为 28.331 m（n=3），方位均为 0.018365°（n=3），folded Doppler 均为 3.967 Hz（n=3）。TrackManager ground speed 则 C2 1.086 m/s、C3 9.259 m/s（n=2）；差异源于 period 2 的目标 track ID 和速度输出变化。径向速度未解模糊，`NOT_EVALUABLE`。TO 的地速 RMSE 是 C2 3.991、C3 0.903 m/s（n=2），只作 target-only control。

7. **paired OFF 是否能在真实系统中获得？** 本仿真可严格生成并闭合 target-free OFF=C+N；真实运行中是否有时间邻近、波束/平台姿态、通道状态及杂波统计均匹配的 OFF calibration interval 尚未证明。C2/C3 的实际收益依赖此类输入，不能将仿真可得性视为在线可部署性。

8. **传统方法是否已经解决问题？** 不能这样判断。Robust DDC 在两个 seed 中均减少了相对 SCC 的目标外候选，但 seed505 仍有 698 条 ON 非目标 detection rows 和 283 条 false confirmed current-frame associations；相对 C0 Current，C3 ON 非目标 rows 还多 162 条。实测 Pfa 未闭合，也没有验证 paired OFF 的部署可得性。结果支持它是有价值的确定性 Baseline 候选，不支持“传统方法已解决真实 GMTI 杂波/失配问题”。

9. **是否有科学或论文价值？** 有限的、可复核的机制结果有价值：独立 seed 复现了 Robust DDC 的目标保持和候选数差异，且 R14 把 trade-off 追到真实 TrackManager 输出。但要作为性能贡献，仍需多 seed 任务比较、独立 H0/可评估虚警口径、track continuity/地速误差复核和 paired OFF 可部署性分析。目前不得写成显著 Pd/Pfa 改善或全面胜出结论。

10. **是否应修改长期研究方向？** 不应。R14 使用 0 ns channel delay，不测试未知系统失配；它没有证明或推翻 Unknown System Error Characterization，也没有建立 AI 必要性。短期建议只继续有限的传统方法任务验证，重点检查 Robust DDC 造成的非目标候选下降能否跨 seed 保持、以及为何同一目标 cell 会产生地速/track identity 代价。AI 训练和 Router 均无证据门支持。

## 下一步与停止条件

下一步建议（不在本阶段自动启动）：在固定 SCC/DDC 实现和现有质量门下做有限的独立 seed 复核，预先把 ON 非目标候选、false current-frame associations、track identity 连续性与地速误差共同列为任务指标；H0 需明确 reference-in-sample，Pfa 仍只在 truth-filtered false-CUT 分母闭合后报告。继续扩展前先确认 paired OFF 在目标场景中的可用性。

若 C3 的 ON 目标保持/非目标输出差异不再复现，或 ground-speed/identity 代价持续抵消 false-candidate 收益，则停止把 Robust DDC 放在 SCC 前面；若多个独立 seed 均复现任务收益且速度代价可解释、可接受，再进入更广研究价值审查。不得仅凭这两枚 seed 宣称统计显著性，也不得以此启动 AI。
