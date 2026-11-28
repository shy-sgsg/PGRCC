#!/usr/bin/env bash
set -euo pipefail

repo_root="$(git rev-parse --show-toplevel)"
cd "$repo_root"

R13_OUT_ROOT="${R13_OUT_ROOT:-outputs/phase_r13_minimal_baseline_cuda_20261024_reproduce}"

env USE_COREX=OFF GMTI_CFAR_DUMP_BEAM=50 python3 scripts/run_production_hierarchical_calibration_pilot.py \
  --mode pilot \
  --output-root "$R13_OUT_ROOT/normal_seed101_v6p7" \
  --input-mode local \
  --delay-errors-ns 0 \
  --seeds 101 \
  --target-velocities-mps 6.7 \
  --target-snr-db 30 \
  --method-ids C0,C2,C3 \
  --calibration-modes Mode-A \
  --max-cases 1

env USE_COREX=OFF GMTI_CFAR_DUMP_BEAM=50 python3 scripts/run_production_hierarchical_calibration_pilot.py \
  --mode e1_pilot \
  --output-root "$R13_OUT_ROOT/low_speed_seed101_v0p5" \
  --input-mode local \
  --delay-errors-ns 0 \
  --seeds 101 \
  --target-velocities-mps 0.5 \
  --target-snr-db 30 \
  --method-ids C0,C2,C3 \
  --calibration-modes Mode-A \
  --max-cases 1

python3 scripts/phase_r/evaluate_r13_minimal_baseline_pilot.py \
  --normal-case-manifest "$R13_OUT_ROOT/normal_seed101_v6p7/cases/base__seed_101__v_6d7__snr_30__delay_0ns/case_manifest.json" \
  --challenge-case-manifest "$R13_OUT_ROOT/low_speed_seed101_v0p5/cases/e1_low_speed__seed_101__v_0d5__snr_30__delay_0ns/case_manifest.json" \
  --output-dir "$R13_OUT_ROOT/analysis"
