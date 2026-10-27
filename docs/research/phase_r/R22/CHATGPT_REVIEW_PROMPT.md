# PGRCC R22 延续阶段 ChatGPT 独立审查提示词

请先将“最终审查 commit 的完整 40 位 SHA”替换为 Codex 最终交付给出的值。必须从 GitHub 读取指定 SHA 对应文件，不得用最新 HEAD 代替审查快照。

【独立科研审查请求】

请首先读取指定 commit 中的 docs/research/RESEARCH_MASTER_PLAN.md，完整理解研究主题、总体目标、核心科学问题、研究内容、总体研究思路和长期技术路线，再审查本阶段成果。必须进行两层审查：阶段执行与证据是否正确；本阶段是否推动 GMTI 总体研究目标、当前研究方向是否仍值得继续。

仓库：https://github.com/shy-sgsg/PGRCC
分支：codex/unknown-system-error-next-stage
审查 commit：<最终审查 commit 的完整 SHA>
实验执行源码 commit：5c66bb0de3704a3e93f750847cf5b55059da9a9e
研究总纲基线 blob：e637e3769bde7814298ff012d0f42301884e2985

按以下顺序读取：
1. 指定 commit 的研究总纲；
2. RESEARCH_STATUS.md、RESEARCH_DECISION_LOG.md；
3. CHATGPT_REVIEW_PROTOCOL.md、AGENTS.md；
4. R22 README、09_extension_execution_contract.md、10_engineering_gate_failure_20261028.md；
5. evidence/extension_engineering/seed1930101/ 下的 manifest、run/matrix/scenes manifest、raw_cleanup.json；
6. 同一目录的 scenario 配置、Stage2 runtime XML、报告、日志、输入清单；
7. R22 runner、scenario factory、evaluator、Stage1 estimator/correction 实现及相关测试。

如指定 commit 或总纲无法读取，逐项说明并将总体方向判断标记 NOT_VERIFIED。若远端 HEAD 更新，报告两个 SHA，并以指定 commit 为审查快照。

【本阶段事实，请独立核验而非直接采信】

- 冻结工程矩阵计划 12 个 case；只启动 seed1930101 的第 1 个 0 ns case：2.5 m/s、20 dB、20 targets、10 periods、单 beam。矩阵为 GATE_FAILED；筛选、确认、40-target、bridge、独立 H0 均未启动。
- Stage2 已执行。A0_ON 报告记录连续面积杂波 1300 packets / 6,508,782 samples，均值功率 0.0225128995081、CV 1.9925009136、rho(lag1)=0.979999807132、CTDR lag2 rho=0.960397551126；噪声功率 0.00100014187201。
- 0 ns 的 A1_OFF 复用 A0_OFF，后者复用 A0_ON 写出的 A0_background。十周期 A0_OFF/A0_background 文件 SHA 全相同；A0_ON/A1_ON 也全相同。估计器输入 target_free_OFF_C_plus_N.bin 为 1300 packets、492,044,800 bytes，SHA-256 110fac37aecb730fd3e355e0381112afd0797102c29277e126fcde4ac2738462。请核对背景复用，不要仅凭 direct injection=0 判无杂波。
- 零注入时延的 C+N 输入上，D1/D2/D3 分别估计 +0.160424583/+0.172614299/+0.188762554 ns；当前规则选择有限的 D3，拟合 R²=0.012462。D4 为 +0.063558808 ns，三种相关法约 −0.008 至 −0.010 ns，结果不一致。
- 选择的 D3 估计在 60 MHz 下为 0.011325753 个采样点。校正通道首样本为 0.006142768−0.007733644j，幅度 0.009876379；packet RMS 5.206051970，零扩展逆算子的首样本容差 5.20605e−7。严格的零扩展线性插值逆算子拒绝了输入。
- 失败发生在盲校正预处理阶段，Production 未启动。无本 case 的 Production XML、逐 beam 日志、CSI tap、CFAR geometry、Detection/Track 输出或任务指标。GPU 可见、Production binary 已构建，不代表 Production 已执行。
- 因而只能说：Stage2 和配对可审计；盲校正输入前提失败；Production 任务指标不可评价；PERFORMANCE_VERIFIED=false。无 Pd/Pfa、未知时延损失、Known 恢复或盲估计差距结论。配对 OFF 不是独立 H0 seed。
- 现有证据确认估计值为正、输入边界不满足逆算子前提；尚不能区分弱可辨识、噪声/杂波相位斜率、未建模物理残差、D3 选择策略或校正契约问题。原始二进制已清理，SHA 与理由在 raw_cleanup.json 中。

【第一层：阶段正确性】

1. 核实实际执行阶段、case 数、失败位置、源码/Stage2/Production binary 身份，并确认没有把 Stage2 误称为 Production/CUDA 结果。
2. 核验面积杂波是否进入估计器输入、背景复用与逐周期 SHA 是否足以支持配对。
3. 审查 F1/F2 输入、D1-D4 结果与选择顺序、拟合质量、盲估计的信息隔离。
4. 判断首样本门是否符合 Stage2 算子，当前拒绝是正确 fail-closed 还是实现错误。
5. 区分估计器/可辨识性、物理模型、校正契约和普通程序故障；明确现有证据能支持到哪一层。
6. runner 在准备盲校正失败时终止整个 case 是否符合冻结门？是否可将 P1/P2 局部标记 NOT_EVALUABLE 并让无关方法执行？即使如此，engineering gate 是否应保持失败？
7. 给出最小下一项技术动作与验收条件。不得事后增设阈值、强制估计为零、使用真值/参考、换 seed 或放宽质量门。

【第二层：科研价值与方向】

1. 该失败仅表示 pilot 工程链未闭合，还是指出未知误差的可观测性/可辨识性问题？
2. 是否可以继续其余工程 case，还是应先验证 0/+4 ns 方法输入契约？提出一个有限下一步。
3. 是否有证据转向低速目标保护、复杂杂波建模或其他 GMTI 瓶颈？证据不足时请明确指出。
4. AI 进入条件是否成立？本轮没有 Production 任务退化、已知恢复和盲残差证据，不得因确定性估计遇到困难直接建议训练 AI。
5. 如建议修改研究总纲，只提出证据、理由、影响与范围；用户明确批准前不得修改总纲。

【回答格式】

- 总体结论：PASS / PASS_WITH_LIMITATIONS / INCONCLUSIVE / FAIL
- IMPLEMENTED、EXECUTED、EVALUABLE、PERFORMANCE_VERIFIED：逐项定义范围并判定
- 事实核验表：主张｜证据路径/字段｜判断｜限制
- 杂波与配对审查
- 盲估计和反演失败因果链，以及仍未区分的解释
- 可评价/不可评价指标及禁止支持的性能主张
- 研究方向：CONTINUE / STOP / PIVOT；AI：ESTABLISHED / NOT_ESTABLISHED / NOT_VERIFIED
- 唯一下一步：任务、输入、验收门、停止条件和是否需要用户批准

阶段门失败不等于整个研究方向失败，也不授权跳过根因继续矩阵。请以仓库证据和反例为依据，不把计划数当执行数、不把 Stage2 当 Production、不把单 seed 当统计结论。