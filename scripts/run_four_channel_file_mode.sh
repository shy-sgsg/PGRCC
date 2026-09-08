#!/usr/bin/env bash
set -euo pipefail

if [[ $# -lt 5 || $# -gt 6 ]]; then
  echo "Usage: $0 <GMTI_pipe_core> <base.xml> <echo.bin> <baseline|fusion> <output_dir> [period_count]" >&2
  echo "       GMTI_XML_PROFILE=none|PATH (default: configs/eval/csi_split_repaired_overrides.json)" >&2
  exit 2
fi

binary=$(realpath "$1")
base_xml=$(realpath "$2")
echo_file=$(realpath "$3")
mode=$4
output_dir=$(realpath -m "$5")
requested_period_count=${6:-5}
repo_root=$(cd "$(dirname "$0")/.." && pwd)
xml_profile=${GMTI_XML_PROFILE:-"$repo_root/configs/eval/csi_split_repaired_overrides.json"}

if ! [[ "$requested_period_count" =~ ^[1-9][0-9]*$ ]]; then
  echo "period_count must be a positive integer" >&2
  exit 2
fi

case "$mode" in
  baseline) fusion=0 ;;
  fusion) fusion=1 ;;
  *) echo "mode must be baseline or fusion" >&2; exit 2 ;;
esac

mkdir -p "$output_dir/config" "$output_dir/logs" "$output_dir/results"
effective_xml="$output_dir/config/effective_${mode}.xml"
# The replay payload, not a possibly stale template XML, is authoritative for
# its protocol channel count.  A four-channel fusion run otherwise can be
# rendered unrunnable merely because an older two-channel template was used
# when the Stage2 XML was last generated.
read -r protocol_channel_count available_period_count < <(python3 - "$echo_file" "$base_xml" <<'PY'
import os
import struct
import sys
import xml.etree.ElementTree as ET

echo_path, xml_path = sys.argv[1:]
root = ET.parse(xml_path).getroot()
params = root.find("GMTI_parameter")
if params is None:
    raise SystemExit("missing <GMTI_parameter> in base XML")

def text(name):
    node = params.find(name)
    if node is None or node.text is None:
        raise SystemExit(f"missing <{name}> in base XML")
    return node.text.strip()

pulse_len = int(text("pulse_len"))
pulse_num = int(text("pulse_num"))
iq_type = text("iq_data_type").lower()
iq_bytes = {"float32": 4, "float": 4, "int16": 2}.get(iq_type)
if iq_bytes is None:
    raise SystemExit(f"unsupported iq_data_type for replay preflight: {iq_type}")
with open(echo_path, "rb") as stream:
    header = stream.read(256)
if len(header) != 256:
    raise SystemExit("echo file is shorter than one protocol header")
prt_bytes, = struct.unpack_from("<I", header, 9)
payload_bytes = prt_bytes - 256
sample_bytes = pulse_len * 2 * iq_bytes
if payload_bytes <= 0 or sample_bytes <= 0 or payload_bytes % sample_bytes:
    raise SystemExit(
        f"cannot derive channel count: prt_bytes={prt_bytes}, pulse_len={pulse_len}, "
        f"iq_type={iq_type}")
channels = payload_bytes // sample_bytes
if channels < 2:
    raise SystemExit(f"invalid derived protocol channel count: {channels}")
if pulse_num <= 0:
    raise SystemExit(f"invalid pulse_num: {pulse_num}")
total_prts = os.path.getsize(echo_path) // prt_bytes
if total_prts * prt_bytes != os.path.getsize(echo_path):
    raise SystemExit("echo file size is not an integer number of PRT packets")
prts_per_period = 61 * pulse_num
if total_prts % prts_per_period:
    raise SystemExit(
        f"echo PRT count {total_prts} is not an integer number of 61-beam periods "
        f"({prts_per_period} PRT/period)")
print(channels, total_prts // prts_per_period)
PY
)
if [[ "$mode" == fusion && "$protocol_channel_count" -ne 4 ]]; then
  echo "four-channel fusion requires a 4-channel replay, got $protocol_channel_count" >&2
  exit 2
fi
if (( available_period_count < requested_period_count )); then
  echo "replay requires $requested_period_count period(s), but echo contains only $available_period_count" >&2
  exit 2
fi
profile_args=()
if [[ "$xml_profile" != "none" ]]; then
  if [[ ! -f "$xml_profile" ]]; then
    echo "XML profile not found: $xml_profile" >&2
    exit 2
  fi
  profile_args+=(--set-json "$xml_profile")
fi
python3 "$repo_root/scripts/configure_gmti_xml.py" \
  --input "$base_xml" \
  --output "$effective_xml" \
  "${profile_args[@]}" \
  --set "GMTI_Data_new=$echo_file" \
  --set "result_add=$output_dir/results" \
  --set "new_protocol_channel_count=$protocol_channel_count" \
  --set "enable_four_channel_fusion=$fusion" \
  --set "new_protocol_file_scan_beam_count=61" \
  --set "new_protocol_file_period_index=0" \
  --set "runtime_mode=release" \
  --set "runtime_diagnostics_enabled=false" \
  --set "detection_results_csv_dump=true" \
  --set "track_debug_level=0" \
  --set "track_debug_dump=true" \
  --set "track_debug_dump_level=1" \
  --set "track_debug_frames=0" \
  --set "channel_calibration_enable=false" \
  --set "p38_diagnostics_dump=false" \
  --set "debug_pc_peak=false" \
  --set "motion_comp_debug=false" \
  --set "csi_metrics_enable=false" \
  --set "csi_metrics_dump_power_maps=false"
echo "[FOUR_CHANNEL_FILE] XML profile=${xml_profile}"

args=(
  "$binary" --config "$effective_xml" --runtime-mode release
  --track-debug-dump on --track-debug-dir "$output_dir/results/track_debug_runs"
  --local-test
)
for ((period = 0; period < requested_period_count; ++period)); do
  result_id=$((period + 1))
  args+=("${result_id}@${period}=${echo_file}")
done

printf '%q ' "${args[@]}" >"$output_dir/logs/command.sh"
printf '\n' >>"$output_dir/logs/command.sh"
sha256sum "$echo_file" "$effective_xml" >"$output_dir/logs/input_sha256.txt"
# Keep the complete release log for audit, while displaying each cycle's
# [TIMING][CYCLE]/[FILE][CYCLE] line in the invoking terminal immediately.
"${args[@]}" 2>&1 | tee "$output_dir/logs/gmti_file_mode.log"

grep -E '\[TIMING\]\[CYCLE\]|\[FILE\]\[CYCLE\]|\[GMTI\]\[RESULT\]|\[LOCAL-TEST\]' \
  "$output_dir/logs/gmti_file_mode.log" >"$output_dir/logs/key_metrics.log" || true
echo "[FOUR_CHANNEL_FILE] PASS mode=$mode runtime=release output=$output_dir"
