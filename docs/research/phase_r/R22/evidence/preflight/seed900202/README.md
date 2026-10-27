# R22 seed900202 compact preflight evidence

This archive preserves a one-period Native CUDA multi-target pilot (20 targets, +4 ns, 2.5 m/s, 20 dB). It is a feasibility/reproducibility record, not an inferential performance result. The execution manifest reported `completed_with_gaps` because the original runner summary required a `production_path_gate` field that the base calibration records did not yet carry. Re-auditing the saved runtime XML, logs, CSI taps, and CFAR evidence with the corrected gate yielded the per-branch results in `posthoc_production_gate_audit.json`. No IQ file is retained; exact source input hashes and sizes remain in the manifests.

This seed selected C0/C2/C3 and did not select PK/PKR, so it cannot establish known-correction closure. Use seed900203/seed900204 evidence for that comparison. H0/Pfa and confirmed-track statistics are not evaluable from one paired period.
