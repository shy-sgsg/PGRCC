#!/usr/bin/env bash
set -euo pipefail

repo_root=$(cd "$(dirname "$0")/.." && pwd)
if [[ $# -ne 1 ]]; then
  echo "Usage: $0 <new-output-root>" >&2
  echo "The output root must be new or empty; existing formal evidence is never overwritten." >&2
  exit 2
fi

output_root=$(realpath -m "$1")
if [[ -d "$output_root" ]] && find "$output_root" -mindepth 1 -print -quit | grep -q .; then
  echo "Refusing to mix results in non-empty output root: $output_root" >&2
  exit 2
fi

scenario="$repo_root/outputs/four_channel_stage2"
base_xml="$scenario/config/temp_config_stage2_newsystem.xml"
echo_file="$scenario/data/stage2_statistical_newprotocol.bin"
mkdir -p "$output_root/environment" "$output_root/reports"

cmake --build "$repo_root/build" -j4
ctest --test-dir "$repo_root/build" --output-on-failure

nvidia-smi >"$output_root/environment/nvidia_smi_before.txt"
nvidia-smi --query-gpu=timestamp,name,driver_version,pstate,power.draw,power.limit,temperature.gpu,utilization.gpu,clocks.sm,memory.used,memory.total \
  --format=csv >"$output_root/environment/gpu_state_before.csv"
git -C "$repo_root" rev-parse HEAD >"$output_root/environment/git_head.txt"
git -C "$repo_root" status --short --untracked-files=all \
  >"$output_root/environment/git_status.txt"

"$repo_root/scripts/run_four_channel_file_mode.sh" \
  "$repo_root/build/GMTI_pipe_core" "$base_xml" "$echo_file" baseline \
  "$output_root/file_baseline"
"$repo_root/scripts/run_four_channel_file_mode.sh" \
  "$repo_root/build/GMTI_pipe_core" "$base_xml" "$echo_file" fusion \
  "$output_root/file_fusion"

evaluate_run() {
  local mode=$1
  local run_dir=$2
  local input_mode=$3
  set +e
  python3 "$repo_root/scripts/evaluate_fullscan_pipe_e2e.py" \
    --scenario-dir "$scenario" \
    --run-dir "$run_dir" \
    --report-dir "$run_dir/reports" \
    --case-label "four_channel_5period_10db_${mode}" \
    --expected-cycles 5 --input-mode "$input_mode"
  local status=$?
  set -e
  printf '%s\n' "$status" >"$run_dir/reports/evaluator_exit_status.txt"
  [[ -s "$run_dir/reports/fullflow_summary.json" ]] || {
    echo "Evaluator did not create fullflow_summary.json for $mode" >&2
    exit 1
  }
  if [[ "$status" -ne 0 && "$status" -ne 1 ]]; then
    echo "Evaluator crashed for $mode: status=$status" >&2
    exit "$status"
  fi
}

evaluate_run file_baseline "$output_root/file_baseline" file
evaluate_run file_fusion "$output_root/file_fusion" file

# The shared run consumes exactly the effective fusion configuration used by
# file mode.  Only the input source/result paths are changed by command line.
"$repo_root/scripts/run_four_channel_shm_mode.sh" \
  "$repo_root/build/GMTI_pipe_core" \
  "$repo_root/build/gmti_pipe_test_client" \
  "$repo_root/build/shm_echo_producer" \
  "$output_root/file_fusion/config/effective_fusion.xml" \
  "$echo_file" "$output_root/shm_fusion_1g"

evaluate_run shm_fusion_1g "$output_root/shm_fusion_1g" shm

python3 "$repo_root/scripts/compare_file_shm_results.py" \
  --file-report-dir "$output_root/file_fusion/reports" \
  --shm-report-dir "$output_root/shm_fusion_1g/reports" \
  --output-dir "$output_root/comparison" \
  --expected-cycles 5

mapfile -t file_debug_dirs < <(
  find "$output_root/file_fusion/results/track_debug_runs" \
    -type f -name track_frames.csv -printf '%h\n' | sort
)
if [[ ${#file_debug_dirs[@]} -ne 1 ]]; then
  echo "Expected exactly one file-mode TrackManager debug run, found ${#file_debug_dirs[@]}" >&2
  exit 1
fi

python3 "$repo_root/scripts/visualize_track_manager.py" \
  --debug-dir "${file_debug_dirs[0]}" \
  --out-dir "$output_root/file_fusion/reports/track_visualization" \
  --coord en --format png --no-dbs-bg \
  --make-frames --make-gif --make-output-gif --keep-frame-png false

python3 "$repo_root/scripts/visualize_track_manager.py" \
  --debug-dir "$output_root/shm_fusion_1g/result/track_debug" \
  --out-dir "$output_root/shm_fusion_1g/reports/track_visualization" \
  --coord en --format png --no-dbs-bg \
  --make-frames --make-gif --make-output-gif --keep-frame-png false

nvidia-smi >"$output_root/environment/nvidia_smi_after.txt"
nvidia-smi --query-gpu=timestamp,name,driver_version,pstate,power.draw,power.limit,temperature.gpu,utilization.gpu,clocks.sm,memory.used,memory.total \
  --format=csv >"$output_root/environment/gpu_state_after.csv"

echo "[FOUR_CHANNEL_CORRECTNESS_VALIDATION] completed output=$output_root"
echo "[NOTE] evaluator status 1 means recorded absolute algorithm guardrails failed; inspect fullflow_report.md."
