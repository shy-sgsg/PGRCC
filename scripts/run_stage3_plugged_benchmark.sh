#!/usr/bin/env bash
set -euo pipefail

config="${1:-configs/stage3/p45_mirror_cuda_compare_130.json}"
output_root="${2:-outputs/p45_mirror_cuda_plugged_telemetry}"
run_count="${3:-3}"

mkdir -p "${output_root}"
nvidia-smi \
  --query-gpu=timestamp,name,driver_version,pstate,power.draw,power.limit,clocks.current.graphics,clocks.current.memory,temperature.gpu,memory.total \
  --format=csv,noheader > "${output_root}/gpu_static.csv"

nvidia-smi dmon -s pucm -d 1 -c 60 -o DT > "${output_root}/nvidia_smi_dmon.log" &
monitor_pid=$!
cleanup() {
  kill "${monitor_pid}" 2>/dev/null || true
  wait "${monitor_pid}" 2>/dev/null || true
}
trap cleanup EXIT

for ((i=1; i<=run_count; ++i)); do
  run_dir="${output_root}/run${i}"
  mkdir -p "${run_dir}"
  nvidia-smi \
    --query-gpu=timestamp,pstate,power.draw,clocks.current.graphics,clocks.current.memory,temperature.gpu \
    --format=csv,noheader > "${run_dir}/gpu_before.csv"
  /usr/bin/time \
    -o "${run_dir}/wall_time.txt" \
    -f 'elapsed_s=%e,user_s=%U,sys_s=%S,maxrss_kb=%M,exit=%x' \
    ./build/simulate_stage3_sar_scene \
      --config "${config}" \
      --output-dir "${run_dir}" \
      --compare-cpu-cuda false
  nvidia-smi \
    --query-gpu=timestamp,pstate,power.draw,clocks.current.graphics,clocks.current.memory,temperature.gpu \
    --format=csv,noheader > "${run_dir}/gpu_after.csv"
done

cleanup
trap - EXIT
