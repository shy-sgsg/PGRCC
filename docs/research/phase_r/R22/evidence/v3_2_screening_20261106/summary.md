# R22 V3.2 Screening archive summary

- Analysis status: COMPLETE_SCREENING
- Matrix status: COMPLETED
- Completed evaluable cases: 288 / 288
- Verified compact archives: 288
- Applicable method units classified: 4320 / 4320
- Case states: {"PASSED": 255, "PASSED_WITH_METHOD_GAPS": 33}
- Independent H0/Pfa: NOT_EVALUABLE_NO_INDEPENDENT_H0_SEED

## Interpretation limits

- Independent inferential unit is the seed; target-period opportunities and CUT counts are not independent seeds.
- This is Screening feasibility and coverage evidence. It does not establish statistical significance or final performance verification.
- A method-level reference gap remains non-evaluable; it is never converted to a miss. Other evaluable methods from that case remain in their own coverage and paired comparisons.
- Recovery quantities use known minus current, estimated minus current, and their ratio only on identical truth-opportunity sets.
- RMSE includes own-success estimates only. Missing estimates are reported as missing, not zero.
- Paired OFF false-cut fractions are descriptive; this target-present matrix has no independent H0 seed, so empirical system Pfa is not evaluable.
- CSI ROI quantities are diagnostics; active-support summaries mix target, clutter, and noise and are not interpreted as target retention.

## Reproduction

python3 scripts/phase_r/summarize_r22_v3_2_screening.py --design docs/research/phase_r/R22/config/r22_extension_experiment_design_v3_2_identity_closed_20261106.json --matrix-manifest outputs/r22_v3_2_screening_20261106/matrix/matrix_manifest.json --archive-root outputs/r22_v3_2_screening_20261106/archives --output-dir outputs/r22_v3_2_screening_20261106/analysis
