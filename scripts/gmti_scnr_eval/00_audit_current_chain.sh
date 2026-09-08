#!/usr/bin/env bash
set -euo pipefail
repo_root=$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)
python3 "$repo_root/scripts/gmti_scnr_eval/00_audit_current_chain.py" "$@"
