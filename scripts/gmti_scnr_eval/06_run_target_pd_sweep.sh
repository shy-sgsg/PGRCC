#!/usr/bin/env bash
set -euo pipefail

# 同 05：一次生产三周期扫描产生完整目标级与 TrackManager 航迹级样本，避免重复耗用 GPU。
script_dir=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)
exec python3 "${script_dir}/09_run_go_formal_batch.py" "$@"
