#!/usr/bin/env bash
set -euo pipefail

if [[ $# -lt 1 ]]; then
  echo "Usage: $0 <echo.bin> [output_root]" >&2
  exit 2
fi

repo_root=$(cd "$(dirname "$0")/.." && pwd)
echo_file=$(realpath "$1")
output_root=${2:-"$repo_root/outputs/shm_phase1_validation/abnormal"}
stamp=$(date +%Y%m%d_%H%M%S)_$$
run_dir="$output_root/run_$stamp"
mkdir -p "$run_dir"

run_recovery_case() {
  local name=$1
  shift
  "$repo_root/build/shm_echo_capture" \
    --shm-name /my_ring_buffer --prt-bytes 189376 --prts-per-cycle 130 \
    --chunk-bytes 1048576 --expect-cycles 1 --min-dropped 1 --timeout-ms 10000 \
    >"$run_dir/${name}_capture.log" 2>&1 &
  local capture_pid=$!
  sleep 0.2
  "$repo_root/build/shm_echo_producer" \
    --input "$echo_file" --shm-name /my_ring_buffer --ring-bytes 67108864 \
    --chunk-bytes 131071 --repeat 1 --rate-bytes-per-sec 50000000 \
    --append-clean --linger-ms 500 "$@" \
    >"$run_dir/${name}_producer.log" 2>&1
  wait "$capture_pid"
}

run_recovery_case drop_prt --drop-prt 10
run_recovery_case duplicate_prt --duplicate-prt 10
run_recovery_case corrupt_header --corrupt-header-prt 10

"$repo_root/build/shm_echo_capture" \
  --shm-name /my_ring_buffer --prt-bytes 189376 --prts-per-cycle 130 \
  --chunk-bytes 1048576 --expect-cycles 0 --min-dropped 1 \
  --cycle-timeout-ms 500 --timeout-ms 3000 \
  >"$run_dir/incomplete_capture.log" 2>&1 &
capture_pid=$!
sleep 0.2
"$repo_root/build/shm_echo_producer" \
  --input "$echo_file" --shm-name /my_ring_buffer --ring-bytes 67108864 \
  --chunk-bytes 131071 --repeat 1 --rate-bytes-per-sec 50000000 \
  --stop-after-bytes 12000000 --linger-ms 3500 \
  >"$run_dir/incomplete_producer.log" 2>&1 &
producer_pid=$!
wait "$capture_pid"
wait "$producer_pid"

grep -H -E '\[CAPTURE\]|\[CAPTURE\]\[METRICS\]' "$run_dir"/*_capture.log \
  >"$run_dir/summary.log"
echo "[ABNORMAL] PASS run_dir=$run_dir"
cat "$run_dir/summary.log"
