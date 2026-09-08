#!/usr/bin/env bash
# 四通道三目标五周期：速度门、相邻波位重复抑制和航迹回归。
set -euo pipefail

repo_dir=$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)
bin_path=${BIN:-"$repo_dir/build-native-release/GMTI_pipe_core"}
xml_path=${XML:-"$repo_dir/temp_config_four_channel.xml"}
data_dir=${DATA_DIR:-"$repo_dir/outputs/stage2_four_channel_three_targets_5period_20260723/data"}
result_dir=${RESULT_DIR:-"$repo_dir/outputs/manual_run/three_target_5period_regression_$(date +%Y%m%d_%H%M%S)"}

if [[ ! -x "$bin_path" ]]; then
    echo "missing executable: $bin_path; build it with: cmake --build build-native-release --target GMTI_pipe_core -j4" >&2
    exit 2
fi
if [[ ! -f "$xml_path" ]]; then
    echo "missing XML: $xml_path" >&2
    exit 2
fi
for period in 0000 0001 0002 0003 0004; do
    input="$data_dir/stage2_statistical_newprotocol_period_${period}.bin"
    if [[ ! -f "$input" ]]; then
        echo "missing input: $input" >&2
        exit 2
    fi
done
if [[ -e "$result_dir" ]]; then
    echo "refuse to overwrite existing result directory: $result_dir" >&2
    exit 2
fi

"$bin_path" \
    --config "$xml_path" \
    --result-dir "$result_dir" \
    --runtime-mode release \
    --runtime-diagnostics=off \
    --local-test \
    1="$data_dir/stage2_statistical_newprotocol_period_0000.bin" \
    2="$data_dir/stage2_statistical_newprotocol_period_0001.bin" \
    3="$data_dir/stage2_statistical_newprotocol_period_0002.bin" \
    4="$data_dir/stage2_statistical_newprotocol_period_0003.bin" \
    5="$data_dir/stage2_statistical_newprotocol_period_0004.bin"

debug_dir=$(find "$result_dir/track_debug_runs" -mindepth 1 -maxdepth 1 -type d | sort | tail -n 1)
if [[ -z "$debug_dir" ]]; then
    echo "missing TrackManager debug directory under: $result_dir" >&2
    exit 1
fi
python3 "$repo_dir/scripts/audit_track_manager_run.py" \
    --debug-dir "$debug_dir" \
    --output "$result_dir/track_audit.json"

echo "result_dir=$result_dir"
echo "debug_dir=$debug_dir"
