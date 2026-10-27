# R22 seed900203 CUDA closure-gate failure evidence

This is a compact archive of the R22 one-period, 20-target, +4 ns Native CUDA pilot for seed 900203 (2.5 m/s, 20 dB, beam 50). It is a feasibility and failure-root-cause record, not confirmatory evidence. The stage stopped at the PKR reference/Production gate.

## What is preserved

- Original R22 run manifest, original case manifest, scenario template, target-generation manifest and all evaluator CSVs.
- Six Stage2 role reports/configurations, scenario identity, clutter/reuse mappings, target/channel truth and Stage2 logs. Source IQ `.bin` files are omitted; their sizes and identities remain in the case manifest and period file records.
- All 32 Production execution directories' branch manifest, runtime XML, configure/runtime logs, CSI tap summaries, CFAR geometry/numerical diagnostics, detection and TrackManager CSVs where produced.
- Both PKR failing production logs, calibration adapter outputs and the 26,637-row reference gamma files. Missing CSI/CFAR output is represented as a gate failure rather than synthesized data.
- The existing `cluster_detection_lineage.csv` uses 15-field `beam_summary` rows and 11-field `cluster` rows, distinguished by `record_type`; its original mixed-width records are preserved.
- `posthoc_production_gate_audit.json` and `archive_manifest.json` for record-level status and per-file SHA-256/size verification.

No raw Stage2 IQ, corrected IQ, `.npy`, `.f32` tap arrays or rendered `.png` outputs are in this Git evidence package. It preserves small, decision-relevant configuration, truth, runtime and diagnostic records.

## Root cause and status

The R22 config uses `range_band_bins=6` and `min_support=8`. Each failed PKR reference has 26,637 groups: 26,598 groups have support 6 and 39 tail groups have support 3; all are `NOT_EVALUABLE / insufficient_phase_support`. The adapter has zero valid groups and reports `all_groups_not_evaluable`. Production then fails its existing scan beam quality gate (`valid=0/1 required=1 min_ratio=0.95`) with return code 1. No CSI tap exists for either failed PKR estimator branch, so the A0/PK/PKR residual-closure and dependent task gates remain `NOT_EVALUABLE`.

Do not lower `min_support` or the beam quality gate to make this case pass. A bounded offline audit of the range grouping and support-count contract is the only candidate follow-up, subject to independent review and user decision.

## Reproducibility and archive check

- Native CUDA Production backend, `USE_COREX=OFF`; exact binaries and GPU preflight are in `r22_run_manifest.json`.
- Run source commit and dirty source-file SHA snapshot are in the run manifest; this was not a clean-worktree source execution.
- The archive manifest lists 953 copied/derived source artifacts (39,577,372 bytes payload), including a separate SHA-256 inventory for seven omitted Stage2 raw inputs. All archived artifact sizes and SHA-256 values were verified after copying; see `archive_manifest.json`.
- No estimator used target truth or delay truth; the known physical error is evaluator-only. The exact input-role contract is preserved in the original case manifest.
