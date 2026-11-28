# SDD ledger — plan: docs/superpowers/plans/2026-09-16-channel-delay-stage1.md

## Preflight scan

The isolated worktree is .worktrees/unknown-system-error-next-stage and the spec is docs/superpowers/specs/2026-09-16-channel-delay-stage1-design.md. The following rows record every task's self-consistency and every shared-file/interface edge found before implementation.

| Scope | Shared file/interface | Check | Result and ruling |
|---|---|---|---|
| Task 1 self | configs/research/channel_delay_stage1_formal.json, tests/test_delay_stage1_contract.py | Config keys and test assertions | Consistent: the test loads the exact path and checks all required flags/values. |
| Task 2 self | scripts/delay_stage1_core.py, tests/test_delay_stage1_core.py | Estimator, theory, MC and statistics interfaces | Consistent: tests use only the listed pure APIs; no file side effect is required. |
| Task 3 self | scripts/run_delay_stage1_formal.py, tests/test_delay_stage1_contract.py, tests/test_channel_delay_correction.py | A0–A3 branch contract and additive control | Consistent: tests cover branch metadata and linear float32 triplet before simulator execution. |
| Task 4 self | scripts/run_delay_stage1_formal.py, tests/test_delay_stage1_contract.py | OFF waterfall and production wrapper | Consistent: explicit fixture denominators are required for numeric layers; absent denominators remain NOT_EVALUABLE. |
| Task 5 self | scripts/run_delay_stage1_formal.py, scripts/delay_stage1_core.py, tests/test_delay_stage1_contract.py | CLI, MC manifest and E2E selection | Consistent: parser/manifest tests precede simulator execution and MC remains truth-blind at estimator call time. |
| Task 6 self | scripts/audit_delay_track_id_switch.py, tests/test_delay_track_id_switch.py, scripts/run_delay_stage1_formal.py | Classification and per-branch integration | Consistent: the audit consumes production artifacts and emits one row per actual adjacent-period switch. |
| Task 7 self | scripts/analyze_delay_stage1_formal.py, tests/test_delay_stage1_contract.py | Compact schema and paired statistics | Consistent: exact nine-file output and zero-denominator preservation are tested. |
| Task 8 self | docs/AI_CSI_36_单一系统误差确定性自校准阶段报告.md, docs/papers/Paper1_ChannelDelay_SelfCalibration_Outline.md | Theory/report requirements | Consistent: docs consume generated evidence and explicitly preserve unresolved gaps. |
| Task 9 self | report, compact evidence | Real-run update and final checks | Consistent: report update follows the actual formal manifest; no raw output is staged. |
| Task 1 → Task 3 | config → scene builder | Working point and delay range names | Task 3 consumes the exact config keys from Task 1; no second source of delay values is introduced. |
| Task 1 → Task 5 | config → CLI | pilot/formal selection | Task 5 resolves named working points and formal seeds/velocities/SNR from the config; CLI overrides remain recorded. |
| Task 2 → Task 3 | delay core → A3 correction | D1–D3/baseline row and selected estimate | Task 3 consumes finite target-free rows only; no truth value enters estimator input. |
| Task 2 → Task 5 | MC core → runner | LFM synthesis and aggregate rows | Task 5 uses the pure MC API and reports estimator-only results separately from Level-2 CUDA results. |
| Task 2 → Task 7 | theory/statistics core → analyzer | recovery, McNemar and bootstrap | Task 7 uses zero-denominator status and scene-block IDs as defined by Task 2. |
| Task 2 → Task 8 | theory API → paper outline | Formula and weight definitions | Task 8 documents the implemented WLS variance and approximate cancellation metric, without claiming CRLB/novelty. |
| Task 3 → Task 4 | runner → production | Branch input paths and correction metadata | Task 4 preserves the four condition names and rejects missing correction instead of falling back to Current. |
| Task 3 → Task 5 | runner → CLI | case manifest and selected inputs | Task 5 drives Task 3 case execution and writes the exact resolved command/config metadata. |
| Task 3 → Task 6 | runner → switch audit | debug, detection, payload and truth paths | Task 6 receives production paths from Task 3 and does not reconstruct TrackManager state. |
| Task 4 → Task 5 | waterfall/production → formal runner | OFF/ON/TO branch metrics | Task 5 stores separate target-off and target-on records with identical XML gates. |
| Task 4 → Task 7 | production metrics → analyzer | CFAR/detection/track fields | Task 7 preserves each denominator and status; it does not promote proxy false alarms. |
| Task 5 → Task 7 | formal manifest → compact evidence | source paths, hashes and rows | Task 7 reads the run manifest and writes only the required small evidence files. |
| Task 5 → Task 9 | formal run → real verification | pilot/formal command and GPU manifest | Task 9 uses the registered command, real CUDA state and output hashes from Task 5. |
| Task 6 → Task 7 | ID-switch CSV → statistics | scene block and classification counts | Task 7 aggregates complete case blocks, not periods as independent samples. |
| Task 7 → Task 8 | compact evidence → report | actual results and NOT_EVALUABLE rows | Task 8 cites only generated evidence and retains unavailable denominator reasons. |
| Task 8 → Task 9 | report → formal results | report numeric insertion | Task 9 fills the report only after formal output exists. |
| All tasks | AI/Router/STAP boundary | Global constraint check | No task enables AI, Router, native four-channel STAP/JDL, or gate tuning. |
| All tasks | output safety | Existing untracked outputs | No task deletes or stages old output roots; compact evidence is the only requested output artifact. |

## Rulings

- Ruling: keep AGENTS.md unchanged unless implementation reveals a durable repository-wide rule missing from it — the user's requested experiment is task-specific and the existing instructions already state the AI/Router, protocol, evidence, and push boundaries; changing it now would add permanent scope without reachable need.
- Ruling: use the simulator's existing paired_background_output_dir, background_input_dir, signal_only, and raw_lfm support instead of changing C++ — those fields already provide the requested OFF/ON/TO control, and modifying native simulation would expand the change surface; if the pilot proves the existing additive contract insufficient, stop at the evidence gap and make only the minimum required simulator repair.
- Ruling: execute the plan in the current isolated worktree with task commits, then push the reviewed stage to the configured origin — the repository instructions explicitly enable automatic push after a complete stage; raw untracked outputs remain outside every commit. Cost if wrong: a missing durable simulator capability or remote push must be reported as unresolved rather than silently substituted.

Task 1: fix round 1/5 (I1, I2, M1 addressed; commits ce7e658..e811d22)
Task 1: complete (commits ce7e658..e811d22, review clean)

- Ruling: for Task 2 fix round 1, use a fresh gpt-5.6-luna implementer rather than resuming the original default-model implementer — the user explicitly restricted all subsequent subagents to luna after the original Task 2 dispatch; the brief, report, and review package preserve the needed context. Cost if wrong: some original implementer context is replaced by written artifacts, but quota compliance is preserved.

Task 2: fix round 2/5 (I3, M2, I4, N1, I5 addressed; commit a2b8188..b051981)
Task 2: complete (commits a572445..b051981, review clean)

Task 3: complete (commit 385e5cf; main-thread static review clean; focused tests 23 passed)
Task 4: complete (commit 5e6c078; waterfall schema, production wrapper, and focused tests 19 passed)

Task 5: complete (commit 51374eb; CLI/MC contract 40 passed; v3 Level-2 pilot completed with 3 cases)
Task 6: complete (commit faefab9; ID-switch fixtures and production-artifact audit 25 focused tests passed)

## Post-run update — 2026-09-17

Task 5: formal matrix complete — `outputs/formal_delay_stage1_20260916/manifest.json`
records 135/135 completed cases; MC summary is 135 rows, 5 methods, 100 trials per cell,
status `passed`. The bounded resume batches were kept separate and no incomplete case was
accepted as a completed matrix.

Task 7: compact analysis complete — `outputs/formal_evidence/stage1_delay/` contains exactly
the required nine files; the source manifest hash, row counts and unresolved denominator
statuses are recorded in the compact manifest.

Task 8: report and Paper 1 outline updated from the actual formal CSVs. The report keeps the
positive paired effects, `NOT_EVALUABLE` cell-false-hit denominator, ID-switch count increase
and missing mechanism attribution explicit; no AI/Router/native four-channel STAP scope was
opened.

Task 9: final cleanup and verification completed on 2026-09-17. The formal source manifest is
135/135 completed with MC passed; compact evidence has exactly the nine required files and the
source manifest hash is recorded. Full pytest completed with 258 passed and 18 skipped; changed
Python files passed `py_compile`; Release build and CTest completed with 20/20 tests; `git
diff --check` is clean. Completed-case raw inputs/production/scenes/empty-input trees and
duplicate cleanup JSON were removed. Unreferenced pre/smoke roots were removed, and the 34,830
duplicate per-file cleanup records in standalone case manifests were compacted while the root
manifest kept the canonical path/size/hash/status audit. The cleanup ledger is
`docs/清理记录_2026-09-16.md`; only source/tests/docs/ledger files remain in the commit scope.
