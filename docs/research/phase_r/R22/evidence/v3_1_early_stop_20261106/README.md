# R22 V3.1 early-stop evidence

This small Git evidence set preserves the matrix identity, per-case evaluability/area/cleanup summaries, and the complete compact archive for the `UNKNOWN_STOP` case. Reproducible raw IQ and large generated arrays were removed after per-case archive verification.

- `case_summary.json`: 6-case status summary, 87/90 visited method-unit evaluability, clutter gates, cleanup ledger totals, archive hashes, design SHA discrepancy, and GPU observations.
- `matrix_manifest.json`: copied original V3.1 matrix manifest; do not edit to repair the recorded design SHA.
- `failure_case.zip`: full compact archive for seed1932001 / 0.5 m/s / SNR30 dB / +4 ns. The archive contains runtime XML, manifests, logs, CSI/CFAR/task evidence, truth and cleanup ledger, but not reproducible raw IQ.

SHA-256 values are listed below. ZIP CRC validation returned clean for all six local case archives before the failed-case archive was copied here.

| File | SHA-256 |
|---|---|
| `matrix_manifest.json` | `4f48b212575a98797bace5c2e18244fe88a89dfaa49b589c70ca569c546f675d` |
| `case_summary.json` | `deed555649648639e48f050675d3939f9858e8030f3afc5505e5c76ec74171c0` |
| `failure_case.zip` | `dcce39d5aaf17729a6eef0c054201988f3f35e62cc7fd801561a1483f05d8862` |
