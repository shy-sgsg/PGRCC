#!/usr/bin/env bash
set -euo pipefail

# 正式三周期批次同时输出三角度实测、目标 Pd 与 TrackManager 审计；不另跑一套
# 简化测角器。调用方须给出 0/30/45° 的正式配对标定 CSV。
script_dir=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)
exec python3 "${script_dir}/09_run_go_formal_batch.py" "$@"
