#!/usr/bin/env python3
"""Run one R8 independent seed through the frozen Native CUDA A/B/C path."""

from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
import shlex
import sys
from typing import Sequence

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from scripts import run_delay_stage1_formal as stage1
from scripts import run_track_manager_e2e as e2e
from scripts.phase_r import run_r3_minimal_cuda_causal_pilot as r3_runner


DEFAULT_PRODUCTION_BINARY = ROOT / "build_r7/GMTI_pipe_core"
REQUIRED_PRODUCTION_SHA256 = "de3026ed4f1263be4143dcbcd10ccbb5e53b4be357e21906e1bf19ea0ec29d00"
REQUIRED_CONDITIONS = (
    "A0_Ideal",
    "A1_Current_unknown_error",
    "A2_Known_error_correction_upper_bound",
)
REQUIRED_ROLES = ("ON", "OFF", "TO")


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _effective_command(output_root: Path, seed: int, binary: Path) -> dict[str, object]:
    argv = [
        sys.executable,
        str(Path(__file__).resolve()),
        "--output-root",
        str(output_root),
        "--seed",
        str(seed),
        "--production-binary",
        str(binary),
    ]
    return {"argv": argv, "shell": shlex.join(argv)}


def successful_execution(manifest: dict[str, object]) -> bool:
    """Validate completed upstream state and all nine production branch gates."""
    if manifest.get("status") not in {"completed", "production_comparisons_executed"}:
        return False
    if manifest.get("case_status") not in {"completed", "production_comparisons_executed"}:
        return False
    case_result = manifest.get("case_result")
    if not isinstance(case_result, dict) or case_result.get("status") != "completed":
        return False
    conditions = case_result.get("conditions")
    if not isinstance(conditions, dict) or set(REQUIRED_CONDITIONS) - set(conditions):
        return False
    for condition in REQUIRED_CONDITIONS:
        record = conditions.get(condition)
        production = record.get("production") if isinstance(record, dict) else None
        if not isinstance(production, dict) or set(production) != set(REQUIRED_ROLES):
            return False
        for role in REQUIRED_ROLES:
            branch = production.get(role)
            if not isinstance(branch, dict):
                return False
            gate = branch.get("production_path_gate")
            if (
                branch.get("status") != "passed"
                or branch.get("production_status") != "passed"
                or branch.get("production_returncode") != 0
                or not isinstance(gate, dict)
                or gate.get("status") != "passed"
            ):
                return False
    return True


def run_one_seed(
    output_root: Path,
    seed: int,
    production_binary: Path = DEFAULT_PRODUCTION_BINARY,
) -> dict[str, object]:
    """Run one seed after validating the exact R7 executable and output path."""

    if seed not in {202, 303}:
        raise ValueError("seed must be 202 or 303")

    binary = Path(production_binary).expanduser().resolve()
    if not binary.is_file():
        raise ValueError(f"Production binary not found: {binary}")
    binary_sha256 = _sha256(binary)
    if binary_sha256 != REQUIRED_PRODUCTION_SHA256:
        raise ValueError(
            "Production binary SHA-256 mismatch: "
            f"expected={REQUIRED_PRODUCTION_SHA256} actual={binary_sha256} path={binary}"
        )

    output_root = Path(output_root).expanduser().resolve()
    if output_root.exists() and (
        not output_root.is_dir() or any(output_root.iterdir())
    ):
        raise ValueError(f"refusing to overwrite nonempty output root: {output_root}")

    output_root.mkdir(parents=True, exist_ok=True)
    old_stage1_pipe = stage1.PIPE
    old_e2e_pipe = e2e.PIPE
    stage1.PIPE = binary
    e2e.PIPE = binary
    try:
        manifest = r3_runner.run_abc_extension(output_root, seed)
    finally:
        stage1.PIPE = old_stage1_pipe
        e2e.PIPE = old_e2e_pipe

    manifest_path = output_root / "manifest.json"
    if not manifest_path.is_file():
        raise RuntimeError(f"R3 runner did not preserve its run manifest: {manifest_path}")
    saved = json.loads(manifest_path.read_text(encoding="utf-8"))
    if not isinstance(saved, dict):
        raise RuntimeError(f"R3 run manifest is not a JSON object: {manifest_path}")

    saved["experiment"] = "PGRCC Phase R8 independent-seed Native CUDA A/B/C causal pilot"
    saved["command"] = _effective_command(output_root, seed, binary)
    saved["production_binary"] = {
        "path": str(binary),
        "sha256": binary_sha256,
        "backend": "Native CUDA",
        "use_corex": False,
    }
    saved["r8_runner_source"] = {
        "path": str(Path(__file__).resolve()),
        "bytes": Path(__file__).stat().st_size,
        "sha256": _sha256(Path(__file__).resolve()),
    }
    saved["runtime_binary_overrides"] = {
        "stage1_PIPE": str(binary),
        "run_track_manager_e2e_PIPE": str(binary),
    }
    saved["seed"] = seed
    manifest_path.write_text(
        json.dumps(saved, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )
    return saved


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output-root", required=True, type=Path)
    parser.add_argument("--seed", required=True, type=int, choices=(202, 303))
    parser.add_argument(
        "--production-binary", type=Path, default=DEFAULT_PRODUCTION_BINARY
    )
    args = parser.parse_args(argv)
    try:
        manifest = run_one_seed(
            args.output_root, args.seed, args.production_binary
        )
    except (OSError, ValueError, RuntimeError, json.JSONDecodeError) as exc:
        parser.error(str(exc))
    print(
        json.dumps(
            {
                "status": manifest.get("status"),
                "case_status": manifest.get("case_status"),
                "seed": args.seed,
                "output_root": str(args.output_root.expanduser().resolve()),
                "production_binary": manifest.get("production_binary"),
            },
            ensure_ascii=False,
            sort_keys=True,
        )
    )
    return 0 if successful_execution(manifest) else 1


if __name__ == "__main__":
    raise SystemExit(main())
