#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
OUT_DIR="$ROOT_DIR/outputs/gmti_scnr_eval/output_scnr_monte_carlo_$(date +%Y%m%d)"
ANGLES="0,30,45"
OUTPUT_GRID="6,8,10,12,14,16,18,20,22,24"
CAL_GRID="-4,0,4,8,12,16"
TRIALS=100
# 6 calibration levels × 6 groups × 3 screens = 108 independent C+N-only
# periods per angle.  The wider grid avoids extrapolating the 6–24 dB formal
# output range from only three control points, while still keeping the
# calibration run bounded.
CAL_TRIALS=6
# Keep one chunk by default.  Raw input BIN is removed after each audited
# pipeline, while splitting into many chunks would repeat the expensive
# N-only/S-only calibration for every chunk.
CHUNK_TRIALS=100
SEED=20260826
BUILD_DIR="$ROOT_DIR/build"
MIN_POINTS=6
KEEP_DEBUG=0
CALIBRATION_DIR=""

while (($#)); do
    case "$1" in
        --angles=*) ANGLES="${1#*=}"; shift ;;
        --angles) ANGLES="$2"; shift 2 ;;
        --output-scnr-grid=*) OUTPUT_GRID="${1#*=}"; shift ;;
        --output-scnr-grid) OUTPUT_GRID="$2"; shift 2 ;;
        --calibration-grid=*) CAL_GRID="${1#*=}"; shift ;;
        --calibration-grid) CAL_GRID="$2"; shift 2 ;;
        --trials-per-level=*|--trials=*) TRIALS="${1#*=}"; shift ;;
        --trials-per-level|--trials) TRIALS="$2"; shift 2 ;;
        --calibration-trials=*) CAL_TRIALS="${1#*=}"; shift ;;
        --calibration-trials) CAL_TRIALS="$2"; shift 2 ;;
        --chunk-trials=*) CHUNK_TRIALS="${1#*=}"; shift ;;
        --chunk-trials) CHUNK_TRIALS="$2"; shift 2 ;;
        --seed=*) SEED="${1#*=}"; shift ;;
        --seed) SEED="$2"; shift 2 ;;
        --output-dir=*) OUT_DIR="${1#*=}"; shift ;;
        --output-dir) OUT_DIR="$2"; shift 2 ;;
        --build-dir=*) BUILD_DIR="${1#*=}"; shift ;;
        --build-dir) BUILD_DIR="$2"; shift 2 ;;
        --min-points=*) MIN_POINTS="${1#*=}"; shift ;;
        --min-points) MIN_POINTS="$2"; shift 2 ;;
        --keep-debug-maps) KEEP_DEBUG=1; shift ;;
        --calibration-dir=*) CALIBRATION_DIR="${1#*=}"; shift ;;
        --calibration-dir) CALIBRATION_DIR="$2"; shift 2 ;;
        *) echo "未知参数: $1" >&2; exit 2 ;;
    esac
done

mkdir -p "$OUT_DIR"
if command -v nvidia-smi >/dev/null 2>&1; then
    nvidia-smi -q > "$OUT_DIR/nvidia-smi-q.txt" || true
    nvidia-smi > "$OUT_DIR/nvidia-smi.txt" || true
fi

if [[ -n "$CALIBRATION_DIR" ]]; then
    CAL_DIR="$(cd "$CALIBRATION_DIR" && pwd)"
    echo "[INFO] 复用已验证 output-SCNR 校准: $CAL_DIR"
else
    CAL_DIR="$OUT_DIR/calibration"
    python3 "$ROOT_DIR/scripts/gmti_scnr_eval/15_calibrate_output_scnr.py" \
        --angles "$ANGLES" --calibration-grid="$CAL_GRID" --trials-per-level "$CAL_TRIALS" \
        --seed "$SEED" --output-dir "$CAL_DIR" --build-dir "$BUILD_DIR"
fi
python3 "$ROOT_DIR/scripts/gmti_scnr_eval/16_validate_output_scnr_transfer.py" \
    --input-dir "$CAL_DIR"

# Run the requested number of groups in small independent chunks.  Each chunk
# deletes its raw BIN after the production audit, keeping the disk peak bounded.
FORMAL_DIR="$OUT_DIR/formal_mc"
CHUNK_ROOT="$FORMAL_DIR/chunks"
mkdir -p "$CHUNK_ROOT"
if (( CHUNK_TRIALS < 1 )); then echo "--chunk-trials 必须为正" >&2; exit 2; fi
remaining="$TRIALS"
chunk_index=0
CHUNK_DIRS=()
while (( remaining > 0 )); do
    this_chunk="$CHUNK_TRIALS"
    if (( this_chunk > remaining )); then this_chunk="$remaining"; fi
    chunk_dir="$CHUNK_ROOT/chunk_$(printf '%03d' "$chunk_index")"
    chunk_seed=$((SEED + chunk_index * 10000))
    MC_ARGS=(--calibration-dir "$CAL_DIR" --angles "$ANGLES" \
        --output-scnr-grid "$OUTPUT_GRID" --trials-per-level "$this_chunk" \
        --seed "$chunk_seed" --output-dir "$chunk_dir" --build-dir "$BUILD_DIR" \
        --min-points "$MIN_POINTS")
    if [[ "$KEEP_DEBUG" == 1 ]]; then MC_ARGS+=(--keep-debug-maps); fi
    python3 "$ROOT_DIR/scripts/gmti_scnr_eval/17_run_center_angle_monte_carlo.py" "${MC_ARGS[@]}"
    CHUNK_DIRS+=("$chunk_dir")
    remaining=$((remaining - this_chunk))
    chunk_index=$((chunk_index + 1))
done

# Keep one chunk's GO theory/summary paths under the merged root for the plot
# and report scripts; the combined instance/curve CSV remains authoritative.
mkdir -p "$FORMAL_DIR/runs"
cp -a "${CHUNK_DIRS[0]}/runs/." "$FORMAL_DIR/runs/"
MERGE_ARGS=(--output-dir "$FORMAL_DIR")
for chunk_dir in "${CHUNK_DIRS[@]}"; do MERGE_ARGS+=(--run-dir "$chunk_dir"); done
python3 "$ROOT_DIR/scripts/gmti_scnr_eval/22_merge_mc_chunks.py" "${MERGE_ARGS[@]}"
python3 "$ROOT_DIR/scripts/gmti_scnr_eval/19_plot_final_four_curves.py" \
    --run-dir "$FORMAL_DIR" --metrics-dir "$FORMAL_DIR"
python3 "$ROOT_DIR/scripts/gmti_scnr_eval/20_build_joint_threshold.py" \
    --run-dir "$FORMAL_DIR" --metrics-dir "$FORMAL_DIR"
python3 "$ROOT_DIR/scripts/gmti_scnr_eval/21_build_output_scnr_report.py" \
    --mc-dir "$FORMAL_DIR" \
    --output-md "$OUT_DIR/output_scnr_monte_carlo_report.md" \
    --output-pdf "$ROOT_DIR/docs/GMTI_输出SCNR_测角精度与检测概率_理论建模与MonteCarlo闭环报告_20260827.pdf" \
    --output-html "$ROOT_DIR/docs/GMTI_输出SCNR_测角精度与检测概率_理论建模与MonteCarlo闭环报告_20260827.html"

echo "[PASS] 输出 SCNR Monte Carlo 全流程完成: $OUT_DIR"
