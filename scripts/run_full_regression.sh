#!/usr/bin/env bash
set -euo pipefail

repo_root="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$repo_root"

exec python3 scripts/run_gmti_simulation_eval.py \
  --matrix experiments/full_regression.json \
  --report \
  "$@"
