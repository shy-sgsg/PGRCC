# R18 pilot failed attempt — preserved

- Attempt: seed 101, 6.7 m/s, 30 dB, delay 0 ns, 3 periods; methods C0–C3, Mode-A and Mode-B.
- Execution source commit: `4c6d4d303621324f5e174db0ba1811c5691df2e2`.
- Native binaries: `/tmp/pgrcc-r18-native-release`; Production SHA-256 `3328d7c61bc36b6d3b906692e355c46c328150c0399fa457e6f871bcb79abaf0`; Stage2 SHA-256 `5dd0ac4e2536b8bcf2508cc53db8a30780eba75f40c025e13ffaa930f8ba7b9b`.
- Exit: 1. This attempt is not an evaluable Production result and is excluded from performance analysis.
- Failure: after the first C0 Production branch returned, the runner referenced undefined `RESEARCH_METHOD_IDS` while deciding whether to audit adapter taps.
- Root-cause fix: commit `c57c6e5` derives the audit decision from the method contract’s `research_calibration_enable` field; regression test first failed, then passed.
- Failed raw run directory retained at `.scratch/r18_gate_pilot_seed101_20261025/` for traceback/input/config review. No files from this attempt have been cleaned.

The fix does not alter any signal-processing or detector parameter. A fresh seed101 pilot is being run from the corrected runner commit and will have a separate output root.
