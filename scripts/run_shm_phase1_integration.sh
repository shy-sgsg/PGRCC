#!/usr/bin/env bash
set -euo pipefail

if [[ $# -lt 2 ]]; then
  echo "Usage: $0 <config.xml> <echo.bin> [output_root] [repeat] [rate_bytes_per_sec] [chunk_bytes] [expected_results] [cycle_timeout_ms] [toggle_point_num] [restore_point_num] [standby_after_ms] [resume_after_ms] [result_timeout_ms]" >&2
  exit 2
fi

repo_root=$(cd "$(dirname "$0")/.." && pwd)
config=$(realpath "$1")
echo_file=$(realpath "$2")
output_root=${3:-"$repo_root/outputs/shm_phase1_validation/integration_runs"}
repeat=${4:-1}
rate=${5:-100000000}
chunk=${6:-262144}
expected_results=${7:-$repeat}
cycle_timeout_ms=${8:-}
toggle_point_num=${9:-}
restore_point_num=${10:-}
standby_after_ms=${11:-}
resume_after_ms=${12:-}
result_timeout_ms=${13:-180000}
runtime_mode=${GMTI_RUNTIME_MODE:-release}
stamp=$(date +%Y%m%d_%H%M%S)_$$
run_dir="$output_root/run_$stamp"
pipe_root="$run_dir/pipes"
result_dir="$run_dir/result"
log_dir="$run_dir/logs"
mkdir -p "$pipe_root" "$result_dir" "$log_dir"

server_pid=
client_pid=
producer_pid=
rss_sampler_pid=
cleanup() {
  set +e
  if [[ -n "$rss_sampler_pid" ]] && kill -0 "$rss_sampler_pid" 2>/dev/null; then kill -TERM "$rss_sampler_pid"; fi
  if [[ -n "$producer_pid" ]] && kill -0 "$producer_pid" 2>/dev/null; then kill -TERM "$producer_pid"; fi
  if [[ -n "$client_pid" ]] && kill -0 "$client_pid" 2>/dev/null; then kill -TERM "$client_pid"; fi
  if [[ -n "$server_pid" ]] && kill -0 "$server_pid" 2>/dev/null; then
    kill -INT "$server_pid"
    wait "$server_pid"
  fi
}
trap cleanup EXIT INT TERM

echo "[INTEGRATION] run_dir=$run_dir"
server_extra=()
if [[ -n "$cycle_timeout_ms" ]]; then
  server_extra+=(--shm-cycle-timeout-ms "$cycle_timeout_ms")
fi
"$repo_root/build/GMTI_pipe_core" \
  --config "$config" \
  --echo-input-mode shm \
  --shm-name /my_ring_buffer \
  --pipe-root "$pipe_root" \
  --result-dir "$result_dir" \
  --runtime-mode "$runtime_mode" \
  "${server_extra[@]}" \
  >"$log_dir/gmti_pipe_core.log" 2>&1 &
server_pid=$!

(
  sample_index=0
  while kill -0 "$server_pid" 2>/dev/null; do
    sample_index=$((sample_index + 1))
    awk -v sample_index="$sample_index" '
      /^VmRSS:/ { print sample_index, $2 }
    ' "/proc/$server_pid/status" 2>/dev/null || true
    sleep 0.2
  done
) >"$log_dir/rss_samples_kib.log" &
rss_sampler_pid=$!

for _ in $(seq 1 300); do
  if [[ -p "$pipe_root/pipewagmticmd" && -p "$pipe_root/pipewagmtiimage" ]]; then break; fi
  if ! kill -0 "$server_pid" 2>/dev/null; then
    echo "[INTEGRATION][ERR] GMTI_pipe_core exited during startup" >&2
    tail -100 "$log_dir/gmti_pipe_core.log" >&2
    exit 1
  fi
  sleep 0.1
done
if [[ ! -p "$pipe_root/pipewagmticmd" || ! -p "$pipe_root/pipewagmtiimage" ]]; then
  echo "[INTEGRATION][ERR] FIFO startup timeout" >&2
  exit 1
fi

client_extra=()
if [[ -n "$toggle_point_num" ]]; then
  client_extra+=(--toggle-point-num "$toggle_point_num" --restore-point-num "$restore_point_num")
fi
if [[ -n "$standby_after_ms" ]]; then
  client_extra+=(--standby-after-ms "$standby_after_ms" --resume-after-ms "$resume_after_ms")
fi
"$repo_root/build/gmti_pipe_test_client" \
  --pipe-root "$pipe_root" \
  --mode 9 \
  --look-side 255 \
  --result-count "$expected_results" \
  --timeout-ms "$result_timeout_ms" \
  "${client_extra[@]}" \
  >"$log_dir/pipe_client.log" 2>&1 &
client_pid=$!
sleep 0.2

"$repo_root/build/shm_echo_producer" \
  --input "$echo_file" \
  --shm-name /my_ring_buffer \
  --ring-bytes 1073741824 --chunk-bytes 1048576 \
  --chunk-bytes "$chunk" \
  --repeat "$repeat" \
  --rate-bytes-per-sec "$rate" \
  --linger-ms 1000 \
  >"$log_dir/producer.log" 2>&1 &
producer_pid=$!

wait "$producer_pid"
producer_pid=
wait "$client_pid"
client_pid=
kill -INT "$server_pid"
wait "$server_pid"
server_pid=
wait "$rss_sampler_pid" || true
rss_sampler_pid=

awk '
  NR == 1 { first = $2; min = $2; max = $2 }
  { last = $2; if ($2 < min) min = $2; if ($2 > max) max = $2; count += 1 }
  END {
    if (count > 0) {
      print "samples=" count, "first_kib=" first, "last_kib=" last,
            "min_kib=" min, "max_kib=" max, "last_minus_first_kib=" last - first
    } else {
      print "samples=0"
    }
  }
' "$log_dir/rss_samples_kib.log" >"$log_dir/rss_summary.log"

sha256sum "$echo_file" >"$log_dir/sha256.txt"
find "$result_dir" -maxdepth 1 -type f -print0 | sort -z | xargs -0 -r sha256sum >>"$log_dir/sha256.txt"

grep -E '\[STARTUP\]|\[CMD\]|\[SHM\]\[CYCLE\]|\[SHM\]\[METRICS\]|\[RESULT\]\[METRICS\]|protocol packet sent' \
  "$log_dir/gmti_pipe_core.log" >"$log_dir/key_metrics.log" || true

echo "[INTEGRATION] PASS"
echo "[INTEGRATION] logs=$log_dir"
echo "[INTEGRATION] results=$result_dir"
