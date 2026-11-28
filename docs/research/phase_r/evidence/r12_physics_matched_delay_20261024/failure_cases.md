# R12 failure and limitation cases

| Case | Evidence | Interpretation |
|---|---|---|
| Unknown `+4 ns` on seed404 | B/ON: 0/3 correct detections; three frozen target opportunities missed. | Direct feasibility evidence of task degradation for this case only. |
| Known correction closure | Known F2 relative RMS to A0: ON `8.7338e-5`, OFF `9.1865e-5`, TO `7.1439e-8`; all within `1e-3`. | Physical correction closes the input model and returns detection count to A0's 2/3. |
| Residual delay after blind correction | M1 estimate `3.759357 ns` vs injected `4 ns`; error `−0.240643 ns`; corrected F2 relative RMS remains 2.684%. | M1 improves over same-input D3 but does not reach Known closure. |
| Fixed calibration mismatch | R9 fixed D3 is `3.409965 ns`; seed404 evaluator error `−0.590035 ns`; F2 relative RMS 6.372%. | A prior fixed calibration is not an exact per-run correction. |
| Estimator model confound | Synthetic unmodelled pulse-varying geometry phase: estimated `4.468978 ns` for 4 ns with normalized residual `0.005373`. | A low fit residual cannot certify the geometry model; this failure is in-scope and must remain a model gate. |
| False-alarm accounting gap | `unique_union_hit_cells=NOT_EVALUABLE`; H0 rows/tracks are only raw descriptive counts. | No statistical Pfa or pooled false-hit claim can be made. |
| Radial velocity ambiguity | All conditions report radial velocity `NOT_EVALUABLE`. | Do not interpret the other parameter RMSE values as radial velocity validation. |
| Single independent seed | Three target opportunities per condition; no seed-level distribution. | `PERFORMANCE_VERIFIED=false`; no statistical significance or population Pd/Pfa. |
