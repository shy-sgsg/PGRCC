# SDD ledger — plan: docs/superpowers/plans/2026-09-22-production-hierarchical-calibration-pilot.md

## Preflight scan

| Task pair | Shared files/interfaces | Sequential order | Rationale |
|---|---|---:|---|
| 1 → 2 | Task 1 is Python semantic contracts; Task 2 adds independent C++ adapter API | 1 then 2 | Independent implementation domains; Task 1 establishes naming/status expectations before cross-layer manifests. |
| 2 → 3 | Task 3 consumes `production_calibration_adapter.hpp/.cpp` and adds Config/CUDA call site | 2 then 3 | The CUDA hook must compile against the finalized adapter result/status API. |
| 3 → 4 | Task 4 writes XML fields and expects the runtime adapter diagnostic artifact | 3 then 4 | Runner cannot exercise research modes until parser, runtime snapshot, and CUDA tap exist. |
| 4 → 5 | Task 5 consumes runner manifests, adapter CSV, CFAR geometry, and TrackManager audits | 4 then 5 | Analyzer/report must target the emitted schema and actual production paths. |
| 5 → 6 | Task 6 validates all prior task outputs and the final evidence directory | 5 then 6 | Final review requires the complete artifact and report contract. |
| 1 ↔ 4 | Existing historical pilot tests and new runner both mention method/status labels | sequential | Task 1 owns historical labels; Task 4 must import/validate the new production contract without reusing `C0` as `M0`. |
| 2 ↔ 3 | Adapter summary schema and Config research fields | sequential | Task 2 defines algorithm result fields; Task 3 defines runtime provenance and CSV serialization. |
| 4 ↔ 5 | Branch manifests/evidence CSV schema | sequential | Task 4 defines raw compact rows; Task 5 defines decision interpretation and report. |

## Scope guard

- No AI, router, native four-channel STAP, servo/velocity/decorrelation production experiment, coupled-error study, or speculative dependency work.
- No replacement TrackManager, CFAR implementation, or production Current behavior change.
- Existing user/untracked `outputs/` are out of scope and must never be staged.

## Task ledger

| Task | Status | Implementer | Reviewer | Fix rounds | Rulings |
|---:|---|---|---|---:|---|
| 1 | complete | 01a0c7a0-c8e2-7c72-920d-e6d71cc9056c | 01a0c7b3-d804-78c0-a6e8-f76fa8272caa | 1 | Approved after fix `91257ef`: explicit physical statuses retain P2/PK+R clutter rows and the low-coherence/status regression is committed. |
| 2 | complete | 01a0c7c6-f235-7932-9ad4-71a7cbcf4610 | 01a0c865-f2d7-7560-9f7d-72cc3dbb8f33 | 1 | Approved after fix `2ae1bb7`: fail-closed nonfinite outputs and widened/rejected range-band arithmetic are covered by regression tests. |
| 3 | complete | 01a0c869-9493-72f1-9d30-7edd8af1bac6 | 01a0c882-6c53-73e2-96a7-6a9bd6373708 | 1 | Approved after fix `4e4d195`; report evidence update `832f1c8` records successful disabled/enabled CUDA smoke and fail-closed CSV provenance. |
| 4 | complete | 01a0c91e-4ada-7742-8c99-0cb528e2a6d1 | 01a0c8b7-199d-7400-891a-dc98335b63de | 3 | Approved after `856f57f` (strict per-group reference persistence/apply) and `9be5882` (canonical `result_id`/`group_id` identity checks); relevant Python/C++ tests, Release build, and CTest passed. |
| 5 | complete | codex-main | 01a0e785-b4a7-7d70-96a3-2da7662f1bcf | 6 | Scoped review approved the `expected_method` separation between pilot method IDs and production Gamma method-family fields. Clean CUDA 0/±2 ns path check and formal `±4 ns × 3 seeds × 2 velocities` evidence are recorded; C4/P2/PKR scan-beam quality gates keep the final decision at `NEED_MORE_SINGLE_ERROR_PRODUCTION_EVIDENCE`. |
| 6 | complete | codex-main | 01a0e785-b4a7-7d70-96a3-2da7662f1bcf | 0 | Final six-file compact evidence is force-added despite global CSV ignores; docs, allowlist/schema, full Python tests, Release build, CTest, and diff checks are complete. |
