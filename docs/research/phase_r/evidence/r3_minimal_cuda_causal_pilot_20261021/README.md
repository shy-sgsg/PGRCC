# R3 CUDA pilot evidence package

This compact package accompanies `05_experiment_design.md`, `06_minimal_cuda_causal_pilot.md`, and `07_preliminary_direction_assessment.md`.

- `r3_minimal_cuda_causal_evaluation.json`: full evaluator output, per seed and per condition, including area-clutter gate, A/C physical closure, CSI output, target transfer, CFAR geometry, detections, tracking, source identity, and labels.
- `r3_cross_seed_condition_metrics.csv` and `r3_cross_seed_comparison.png`: review-friendly table and descriptive figure. The chart is not a significance test.
- `manifests/`: complete run-level and case-level manifests for seed 101, seed 202/303 CUDA reruns, plus the first seed 202 sandbox CUDA failure.
- `production_xmls/`: all 30 actual A/B/C/D (seed 101) and A/B/C (seeds 202/303) Production runtime XML files; the repaired seed101 A0_ON smoke XML is also separately archived under `runtime_smoke/`.
- `runtime_smoke/seed101_A0_ON/`: actual runtime XML, runtime config and run manifests, full log, per-period stage timing, CSI tap summaries/ROI manifests and CFAR geometry diagnostic from the successful CUDA smoke.
- `failures/seed101_initial_cfar_period_identity/`: first smoke attempt, retained to show the duplicate CFAR period identity that motivated the repaired rerun.
- `failures/seed202_sandbox_cuda_unavailable/`: preserved XML, branch manifest and log from the sandbox attempt that reported no CUDA device; the separate restricted-CUDA rerun passed.
- `stage2/`: generated Stage2 runtime XML/scenario snapshots and the three seed reports documenting physically reachable ground range and measured nonzero continuous-area clutter.
- `cleanup/`: per-case `raw_cleanup.json` ledgers listing the exact reproducible `.bin` files removed and byte totals.

The complete local run tree is `outputs/phase_r_minimal_cuda_causal_pilot_20261021/`. Reconstructed protocol `.bin` intermediates are intentionally excluded from this package; per-case `raw_cleanup.json` records their removal while all manifests, configs, logs and derived review data remain.

The two archived `gmt_pipe_core.log` text copies have only trailing horizontal whitespace removed so the evidence diff stays clean; the untouched original logs remain in the local run tree. Log content and numeric records are otherwise unchanged.
