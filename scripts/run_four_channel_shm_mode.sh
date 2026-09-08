#!/usr/bin/env bash
set -euo pipefail

if [[ $# -lt 6 ]]; then
  echo "Usage: $0 <GMTI_pipe_core> <client> <producer> <fusion.xml> <echo.bin> <output_dir>" >&2
  exit 2
fi

server_bin=$(realpath "$1")
client_bin=$(realpath "$2")
producer_bin=$(realpath "$3")
config=$(realpath "$4")
echo_file=$(realpath "$5")
output_dir=$(realpath -m "$6")
shm_name="/gmti_four_channel_5period_$$"
pipe_root="$output_dir/pipes"
result_dir="$output_dir/result"
log_dir="$output_dir/logs"
mkdir -p "$pipe_root" "$result_dir" "$log_dir"

server_pid=
client_pid=
producer_pid=
cleanup() {
  set +e
  if [[ -n "$producer_pid" ]]; then
    kill -TERM "$producer_pid" 2>/dev/null
    wait "$producer_pid" 2>/dev/null
  fi
  if [[ -n "$client_pid" ]]; then
    kill -TERM "$client_pid" 2>/dev/null
    wait "$client_pid" 2>/dev/null
  fi
  if [[ -n "$server_pid" ]] && kill -0 "$server_pid" 2>/dev/null; then
    kill -INT "$server_pid"
    wait "$server_pid"
  fi
}
trap cleanup EXIT INT TERM

sha256sum "$echo_file" "$config" >"$log_dir/input_sha256.txt"
# Hashing the 15 GB replay file populates the page cache and can make the
# following 6 GB complete-scan double-buffer allocation fail its MemAvailable
# safety check.  Drop only this file's clean cache pages after hashing; no file
# contents or system-wide caches are modified.
python3 -c 'import os, sys; fd = os.open(sys.argv[1], os.O_RDONLY); os.posix_fadvise(fd, 0, 0, os.POSIX_FADV_DONTNEED); os.close(fd)' "$echo_file"

# The server allocates two complete-scan host buffers and the sender allocates
# the shmdemo ring in tmpfs. Derive the scan size from the current input/config
# and wait for a safe resource window instead of hard-coding this case's shape.
ring_bytes=1073741824
python3 "$(dirname "$0")/wait_shm_memory_preflight.py" \
  --input "$echo_file" --config "$config" --ring-bytes "$ring_bytes" \
  --output "$log_dir/memory_preflight.txt"
printf '%q ' "$server_bin" --config "$config" --echo-input-mode shm \
  --shm-name "$shm_name" --pipe-root "$pipe_root" --result-dir "$result_dir" \
  --runtime-mode debug --track-debug-dump on >"$log_dir/server_command.sh"
printf '\n' >>"$log_dir/server_command.sh"

"$server_bin" --config "$config" --echo-input-mode shm \
  --shm-name "$shm_name" --pipe-root "$pipe_root" --result-dir "$result_dir" \
  --runtime-mode debug --track-debug-dump on \
  >"$log_dir/gmti_pipe_core.log" 2>&1 &
server_pid=$!

for _ in $(seq 1 300); do
  [[ -p "$pipe_root/pipewagmticmd" && -p "$pipe_root/pipewagmtiimage" ]] && break
  if ! kill -0 "$server_pid" 2>/dev/null; then
    tail -100 "$log_dir/gmti_pipe_core.log" >&2
    exit 1
  fi
  sleep 0.1
done
[[ -p "$pipe_root/pipewagmticmd" && -p "$pipe_root/pipewagmtiimage" ]] || {
  echo "FIFO startup timeout" >&2
  exit 1
}

"$client_bin" --pipe-root "$pipe_root" --mode 9 --look-side 255 \
  --result-count 5 --timeout-ms 600000 >"$log_dir/pipe_client.log" 2>&1 &
client_pid=$!

# The receiver intentionally drains stale bytes outside WAGMTI mode. Do not
# start the sender until the command thread has actually applied mode 9;
# a fixed delay races config validation and can discard leading scans.
for _ in $(seq 1 300); do
  if grep -q '\[CMD\] applied .*workMode=9' "$log_dir/gmti_pipe_core.log"; then
    break
  fi
  if ! kill -0 "$server_pid" 2>/dev/null || ! kill -0 "$client_pid" 2>/dev/null; then
    echo "mode-9 command handshake failed" >&2
    exit 1
  fi
  sleep 0.1
done
grep -q '\[CMD\] applied .*workMode=9' "$log_dir/gmti_pipe_core.log" || {
  echo "mode-9 command handshake timeout" >&2
  exit 1
}

"$producer_bin" --input "$echo_file" --shm-name "$shm_name" \
  --ring-bytes 1073741824 --chunk-bytes 1048576 \
  --repeat 1 --linger-ms -1 \
  --skip-input-hash \
  >"$log_dir/producer.log" 2>&1 &
producer_pid=$!

finished_pid=
first_status=0
wait -n -p finished_pid "$server_pid" "$producer_pid" "$client_pid" || first_status=$?
if [[ "$finished_pid" == "$server_pid" ]]; then
  server_pid=
  echo "server exited before all PIPE results: status=$first_status" >&2
  [[ "$first_status" -eq 0 ]] && first_status=1
  exit "$first_status"
elif [[ "$finished_pid" == "$producer_pid" ]]; then
  producer_pid=
  echo "producer exited before all PIPE results: status=$first_status" >&2
  [[ "$first_status" -eq 0 ]] && first_status=1
  exit "$first_status"
fi
client_pid=
if [[ "$first_status" -ne 0 ]]; then
  echo "PIPE client failed: status=$first_status" >&2
  exit "$first_status"
fi
kill -TERM "$producer_pid"
wait "$producer_pid"
producer_pid=
kill -INT "$server_pid"
server_status=0
wait "$server_pid" || server_status=$?
server_pid=
if [[ "$server_status" -ne 0 && "$server_status" -ne 130 ]]; then
  echo "server exit status $server_status" >&2
  exit "$server_status"
fi

grep -E '\[STARTUP\]|\[CMD\]|\[SHM\]\[CYCLE\]|\[SHM\]\[METRICS\]|\[RESULT\]\[METRICS\]|\[GMTI\]\[RESULT\]' \
  "$log_dir/gmti_pipe_core.log" >"$log_dir/key_metrics.log" || true
echo "[FOUR_CHANNEL_SHM] PASS output=$output_dir"
