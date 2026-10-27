#!/usr/bin/env python3
"""Execute a frozen R22 case matrix sequentially with resumable audit state."""

from __future__ import annotations

import argparse
import hashlib
import itertools
import json
import os
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Mapping, Sequence

from scripts.phase_r import run_r22_cuda_pilot as r22


ROOT = Path(__file__).resolve().parents[2]
DEFAULT_DESIGN = ROOT / "docs/research/phase_r/R22/config/r22_extension_experiment_design.json"


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _write_json_atomic(path: Path, value: Mapping[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(path.name + ".tmp")
    temporary.write_text(
        json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True, allow_nan=False) + "\n",
        encoding="utf-8",
    )
    os.replace(temporary, path)


def _design_digest(design: Mapping[str, Any]) -> str:
    payload = json.dumps(design, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def load_design(path: Path) -> dict[str, Any]:
    value = json.loads(Path(path).read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise ValueError("R22 design must be a JSON object")
    validate_design(value)
    return value


def validate_design(design: Mapping[str, Any]) -> None:
    if design.get("schema_version") != 1 or not design.get("design_id"):
        raise ValueError("R22 design requires schema_version=1 and design_id")
    seed_sets = design.get("seed_sets")
    if not isinstance(seed_sets, Mapping):
        raise ValueError("R22 design seed_sets must be an object")
    independent_names = ("engineering", "screening", "confirmatory", "h0")
    normalized: dict[str, set[int]] = {}
    for name in independent_names:
        values = seed_sets.get(name)
        if not isinstance(values, list) or not values:
            raise ValueError(f"independent seed set {name} must be a non-empty list")
        parsed: list[int] = []
        for value in values:
            if isinstance(value, bool) or not isinstance(value, int) or value < 0:
                raise ValueError(f"seed set {name} contains a non-integer seed")
            parsed.append(value)
        if len(parsed) != len(set(parsed)):
            raise ValueError(f"seed set {name} contains duplicate seeds")
        normalized[name] = set(parsed)
    for index, left in enumerate(independent_names):
        for right in independent_names[index + 1:]:
            overlap = normalized[left] & normalized[right]
            if overlap:
                raise ValueError(
                    f"independent seed sets overlap: {left} and {right}: {sorted(overlap)}"
                )

    methods = design.get("common", {}).get("method_ids", design.get("method_ids"))
    modes = design.get("common", {}).get("calibration_modes", design.get("calibration_modes"))
    if not isinstance(methods, list) or not methods or not all(isinstance(x, str) and x for x in methods):
        raise ValueError("R22 design requires non-empty method_ids")
    if not isinstance(modes, list) or not modes or not all(isinstance(x, str) and x for x in modes):
        raise ValueError("R22 design requires non-empty calibration_modes")

    phases = design.get("phases")
    if not isinstance(phases, Mapping) or not phases:
        raise ValueError("R22 design phases must be a non-empty object")
    target_counts = design.get("target_counts", {})
    common_period_count = design.get("period_count")
    for phase, phase_spec in phases.items():
        if not isinstance(phase_spec, Mapping):
            raise ValueError(f"phase {phase} must be an object")
        seed_set = phase_spec.get("seed_set")
        if seed_set not in seed_sets:
            raise ValueError(f"phase {phase} references unknown seed set {seed_set}")
        target_count = phase_spec.get("target_count", target_counts.get(phase))
        if isinstance(target_count, bool) or not isinstance(target_count, int):
            raise ValueError(f"phase {phase} requires integer target_count")
        if (phase == "h0" and target_count != 0) or (phase != "h0" and target_count <= 0):
            raise ValueError(f"phase {phase} has invalid target_count={target_count}")
        period_count = phase_spec.get("period_count", common_period_count)
        if isinstance(period_count, bool) or not isinstance(period_count, int) or period_count <= 0:
            raise ValueError(f"phase {phase} requires positive period_count")
        if "conditions" not in phase_spec and "factorial" not in phase_spec:
            raise ValueError(f"phase {phase} requires conditions or factorial factors")


def _conditions(phase_spec: Mapping[str, Any]) -> list[dict[str, Any]]:
    direct = phase_spec.get("conditions")
    if direct is not None:
        if not isinstance(direct, list) or not direct:
            raise ValueError("conditions must be a non-empty list")
        result = [dict(item) for item in direct]
    else:
        factorial = phase_spec.get("factorial")
        if not isinstance(factorial, Mapping) or not factorial:
            raise ValueError("factorial must be a non-empty object")
        keys = list(factorial)
        values = [factorial[key] for key in keys]
        if any(not isinstance(items, list) or not items for items in values):
            raise ValueError("every factorial factor must be a non-empty list")
        result = []
        for combination in itertools.product(*values):
            item = dict(zip(keys, combination))
            item["condition_id"] = (
                f"v{item['radial_speed_mps']:g}_snr{item['target_snr_db']:g}_"
                f"d{item['delay_error_ns']:g}"
            )
            result.append(item)
    required = ("radial_speed_mps", "target_snr_db", "delay_error_ns")
    ids: set[str] = set()
    for item in result:
        if any(key not in item for key in required):
            raise ValueError(f"condition missing one of {required}")
        condition_id = str(item.get("condition_id", ""))
        if not condition_id or condition_id in ids:
            raise ValueError("each condition requires a unique non-empty condition_id")
        ids.add(condition_id)
        for key in required:
            if isinstance(item[key], bool) or not isinstance(item[key], (int, float)):
                raise ValueError(f"condition {condition_id} has non-numeric {key}")
    return result


def build_case_specs(design: Mapping[str, Any], phase: str) -> list[dict[str, Any]]:
    validate_design(design)
    phases = design["phases"]
    if phase not in phases:
        raise ValueError(f"phase is not in frozen design: {phase}")
    phase_spec = phases[phase]
    common = design.get("common", {})
    target_count = phase_spec.get("target_count", design.get("target_counts", {}).get(phase))
    period_count = phase_spec.get("period_count", design.get("period_count"))
    cases: list[dict[str, Any]] = []
    for seed in design["seed_sets"][phase_spec["seed_set"]]:
        for condition in _conditions(phase_spec):
            cases.append({
                "phase": phase,
                "seed": seed,
                "condition_id": condition["condition_id"],
                "speed_mps": float(condition["radial_speed_mps"]),
                "snr_db": float(condition["target_snr_db"]),
                "delay_ns": float(condition["delay_error_ns"]),
                "target_count": target_count,
                "period_count": period_count,
                "first_beam": common.get("first_beam", 50),
                "beam_count": common.get("beam_count", 1),
                "area": dict(common.get("area", r22.DEFAULT_AREA)),
                "method_ids": list(common.get("method_ids", design.get("method_ids", []))),
                "calibration_modes": list(common.get("calibration_modes", design.get("calibration_modes", []))),
                "scenario_template": common.get("scenario_template"),
                "method_config": common.get("method_config"),
            })
    identifiers = [
        r22.case_identifier(
            phase=item["phase"], seed=item["seed"], speed_mps=item["speed_mps"],
            snr_db=item["snr_db"], delay_ns=item["delay_ns"],
            target_count=item["target_count"], period_count=item["period_count"],
        ) for item in cases
    ]
    if len(identifiers) != len(set(identifiers)):
        raise ValueError(f"phase {phase} produces duplicate case identifiers")
    return cases


def _case_state(phase: str, result: Mapping[str, Any]) -> str:
    labels = result.get("status_labels", {})
    if phase == "h0":
        summary = result.get("evaluation_summary", {})
        if labels.get("EVALUABLE") and int(summary.get("independent_h0_seed_rows", 0)) > 0:
            return "PASSED" if result.get("implementation_result") == "passed" else "H0_EVALUABLE_WITH_GAPS"
        return "GATE_FAILED"
    if (result.get("implementation_result") == "passed"
            and labels.get("EVALUABLE")
            and result.get("all_production_branches_passed") is True):
        return "PASSED"
    return "GATE_FAILED"


def _relative_path(path_value: str | None) -> Path | None:
    if not path_value:
        return None
    path = Path(path_value).expanduser()
    return path if path.is_absolute() else (ROOT / path).resolve()


def run_matrix(
    design: Mapping[str, Any],
    *,
    phase: str,
    output_root: Path,
    stage2_binary: Path | None = None,
    production_binary: Path | None = None,
    template: Path | None = None,
    method_config: Path | None = None,
    max_cases: int | None = None,
    resume: bool = False,
    run_case_fn: Callable[..., Mapping[str, Any]] | None = None,
) -> dict[str, Any]:
    validate_design(design)
    specs = build_case_specs(design, phase)
    if max_cases is not None:
        if isinstance(max_cases, bool) or max_cases <= 0:
            raise ValueError("max_cases must be positive")
        specs = specs[:max_cases]
    output_root = Path(output_root).expanduser().resolve()
    output_root.mkdir(parents=True, exist_ok=True)
    manifest_path = output_root / "matrix_manifest.json"
    digest = _design_digest(design)
    if manifest_path.is_file():
        if not resume:
            raise FileExistsError(f"matrix output already exists; pass --resume: {output_root}")
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        if manifest.get("design_id") != design.get("design_id") or manifest.get("design_sha256") != digest:
            raise ValueError("cannot resume with a different frozen design")
        if manifest.get("status") == "GATE_FAILED":
            raise RuntimeError("phase previously failed its gate; use a new evidence root after review")
    else:
        if any(output_root.iterdir()):
            raise FileExistsError(f"refuse to use non-empty matrix output root: {output_root}")
        manifest = {
            "schema_version": 1,
            "design_id": design["design_id"],
            "design_sha256": digest,
            "phase": phase,
            "status": "RUNNING",
            "planned_case_count": len(build_case_specs(design, phase)),
            "cases": [],
            "started_at_utc": _utc_now(),
        }
    if manifest.get("phase") != phase:
        raise ValueError("matrix output root belongs to a different phase")

    existing = {row["case_id"]: row for row in manifest.get("cases", []) if "case_id" in row}
    completed_now = skipped_now = 0
    case_runner = run_case_fn or r22.run_case
    common = design.get("common", {})
    for spec in specs:
        case_id = r22.case_identifier(
            phase=phase, seed=spec["seed"], speed_mps=spec["speed_mps"],
            snr_db=spec["snr_db"], delay_ns=spec["delay_ns"],
            target_count=spec["target_count"], period_count=spec["period_count"],
        )
        previous = existing.get(case_id)
        if previous and previous.get("status") in {"PASSED", "H0_EVALUABLE_WITH_GAPS"}:
            skipped_now += 1
            continue
        if previous:
            manifest["status"] = "GATE_FAILED"
            _write_json_atomic(manifest_path, manifest)
            break
        attempts_root = output_root / phase / case_id
        attempts_root.mkdir(parents=True, exist_ok=True)
        attempt = 1
        while (attempts_root / f"attempt_{attempt:03d}").exists():
            attempt += 1
        case_root = attempts_root / f"attempt_{attempt:03d}"
        print(f"[{phase}] START {case_id} seed={spec['seed']} output={case_root}", flush=True)
        started = _utc_now()
        try:
            result = dict(case_runner(
                case_root,
                phase=phase,
                seed=spec["seed"],
                speed_mps=spec["speed_mps"],
                snr_db=spec["snr_db"],
                delay_ns=spec["delay_ns"],
                target_count=spec["target_count"],
                period_count=spec["period_count"],
                first_beam=spec["first_beam"],
                beam_count=spec["beam_count"],
                stage2_binary=Path(stage2_binary or r22.DEFAULT_STAGE2),
                production_binary=Path(production_binary or r22.DEFAULT_PRODUCTION),
                template=_relative_path(str(template) if template else spec.get("scenario_template")) or r22.DEFAULT_TEMPLATE,
                method_config_path=_relative_path(str(method_config) if method_config else spec.get("method_config")) or r22.DEFAULT_METHOD_CONFIG,
                method_ids=tuple(spec["method_ids"]),
                calibration_modes=tuple(spec["calibration_modes"]),
                area=spec["area"],
            ))
            state = _case_state(phase, result)
            record = {
                "case_id": case_id,
                "condition_id": spec["condition_id"],
                "seed": spec["seed"],
                "attempt": attempt,
                "status": state,
                "output_root": str(case_root),
                "started_at_utc": started,
                "finished_at_utc": _utc_now(),
                "implementation_result": result.get("implementation_result"),
                "status_labels": result.get("status_labels"),
                "evaluation_summary": result.get("evaluation_summary"),
                "all_production_branches_passed": result.get("all_production_branches_passed"),
            }
        except Exception as exc:
            state = "GATE_FAILED"
            record = {
                "case_id": case_id,
                "condition_id": spec["condition_id"],
                "seed": spec["seed"],
                "attempt": attempt,
                "status": state,
                "output_root": str(case_root),
                "started_at_utc": started,
                "finished_at_utc": _utc_now(),
                "failure": {"type": type(exc).__name__, "message": str(exc)},
            }
        manifest.setdefault("cases", []).append(record)
        existing[case_id] = record
        completed_now += 1
        passed_states = {"PASSED", "H0_EVALUABLE_WITH_GAPS"}
        manifest["status"] = "RUNNING" if state in passed_states else "GATE_FAILED"
        manifest["finished_at_utc"] = _utc_now()
        _write_json_atomic(manifest_path, manifest)
        print(f"[{phase}] {state} {case_id}", flush=True)
        if state == "GATE_FAILED":
            break

    terminal_ids = {
        row["case_id"] for row in manifest.get("cases", [])
        if row.get("status") in {"PASSED", "H0_EVALUABLE_WITH_GAPS"}
    }
    if manifest.get("status") != "GATE_FAILED":
        full_case_count = len(build_case_specs(design, phase))
        manifest["status"] = "COMPLETED" if len(terminal_ids) == full_case_count else "PARTIAL_BOUNDED_RUN"
        manifest["finished_at_utc"] = _utc_now()
        _write_json_atomic(manifest_path, manifest)
    return {
        **manifest,
        "completed_cases": completed_now,
        "skipped_cases": skipped_now,
    }


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--design", type=Path, default=DEFAULT_DESIGN)
    parser.add_argument("--phase", required=True,
                        choices=("engineering", "screening", "confirmatory", "dense", "bridge", "h0"))
    parser.add_argument("--output-root", type=Path, required=True)
    parser.add_argument("--stage2-binary", type=Path, default=r22.DEFAULT_STAGE2)
    parser.add_argument("--production-binary", type=Path, default=r22.DEFAULT_PRODUCTION)
    parser.add_argument("--template", type=Path)
    parser.add_argument("--method-config", type=Path)
    parser.add_argument("--max-cases", type=int)
    parser.add_argument("--resume", action="store_true")
    args = parser.parse_args(argv)
    try:
        design = load_design(args.design)
        result = run_matrix(
            design,
            phase=args.phase,
            output_root=args.output_root,
            stage2_binary=args.stage2_binary,
            production_binary=args.production_binary,
            template=args.template,
            method_config=args.method_config,
            max_cases=args.max_cases,
            resume=args.resume,
        )
    except Exception as exc:
        print(f"[r22_experiment_matrix][ERROR] {type(exc).__name__}: {exc}")
        return 2
    print(json.dumps({
        "phase": args.phase,
        "status": result.get("status"),
        "completed_cases": result.get("completed_cases"),
        "skipped_cases": result.get("skipped_cases"),
        "planned_case_count": result.get("planned_case_count"),
        "manifest": str(args.output_root.expanduser().resolve() / "matrix_manifest.json"),
    }, ensure_ascii=False, sort_keys=True))
    return 2 if result.get("status") == "GATE_FAILED" else 0


if __name__ == "__main__":
    raise SystemExit(main())
