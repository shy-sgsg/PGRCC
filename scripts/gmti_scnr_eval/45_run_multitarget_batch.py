#!/usr/bin/env python3
"""100个固定目标场景、六种生产min_points，按组合断点保存。"""
from __future__ import annotations
import argparse
import fcntl
import hashlib
import json
from pathlib import Path
import subprocess
import sys
import time

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import importlib.util


def load(name, filename):
    spec = importlib.util.spec_from_file_location(name, HERE / filename)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


runner = load("mc125_runner", "43_run_multitarget_mc.py")
scorer = load("mc125_scorer", "44_score_multitarget_mc.py")


def digest(path):
    h = hashlib.sha256()
    with path.open("rb") as f:
        for block in iter(lambda: f.read(1024*1024), b""):
            h.update(block)
    return h.hexdigest()


def execute(args):
    out = args.output.resolve()
    out.mkdir(parents=True, exist_ok=True)
    lock = (out / "batch.lock").open("w")
    fcntl.flock(lock.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
    calibration = json.loads(args.calibration.read_text())
    contract = {"trials": args.trials, "seed_start": args.seed_start,
        "min_points": args.min_points, "calibration": calibration,
        "layout": "compact256", "profile": "hann_kaiser"}
    contract_path = out / "contract.json"
    if contract_path.exists() and json.loads(contract_path.read_text()) != contract:
        raise ValueError("resume contract differs: use a new output directory")
    runner.write_json(contract_path, contract)
    completed = []
    all_trials = []
    for mc_id in range(args.trials):
        case = out / f"mc_{mc_id:03d}"
        todo = []
        for mp in args.min_points:
            result = case / f"mp{mp}_hann_kaiser"
            marker = result / "metrics/complete.json"
            if marker.is_file():
                rows = scorer.read(result / "metrics/trial_results.csv")
                metric_ok = all(
                    r.get("track_metric") == "truth_target_detected_at_least_2_of_3_periods"
                    for r in rows
                )
                if (len(rows) != 25 or not metric_ok or
                        any(int(r["target_total"]) != 15 or int(r["track_total"]) != 5 for r in rows)):
                    # A sealed result from the previous scorer is safe to
                    # rescore offline: the production outputs and truth are
                    # retained, while the primary track metric changed.
                    todo.append(mp)
                    continue
                if marker.read_text(encoding="utf-8").find(
                        "truth_target_detected_at_least_2_of_3_periods") < 0:
                    todo.append(mp)
                    continue
                if len(rows) != 25 or any(int(r["target_total"]) != 15 or int(r["track_total"]) != 5 for r in rows):
                    raise ValueError(f"incomplete sealed trial: {marker}")
                completed.append([mp, mc_id])
                all_trials.extend(rows)
            else:
                todo.append(mp)
        if not todo:
            continue
        if not (case / "scenario.json").exists():
            runner.prepare(case, args.seed_start + mc_id, 0)
            doc = json.loads((case / "scenario.json").read_text())
            design = {r["target_id"]: r for r in json.loads((case / "design.json").read_text())["targets"]}
            for target in doc["targets"]:
                level = design[target["target_id"]]["SCNR_target"]
                target["amplitude"]["snr_db"] = level - calibration["gain_db"]
            runner.write_json(case / "scenario.json", doc)
        for mp in todo:
            result = case / f"mp{mp}_hann_kaiser"
            if result.exists():
                # A process can terminate after scoring but before writing its
                # seal. Re-score only an explicitly successful process output.
                log = case / f"logs/mp{mp}_hann_kaiser.log"
                if not log.is_file() or "exit_code=0" not in log.read_text():
                    raise ValueError(f"unfinished run requires diagnosis: {result}")
            else:
                runner.process(case, args.build_dir.resolve(), mp, "hann_kaiser")
            if not (case / "input_identity.json").exists():
                raw = list((case / "stage2/data").glob("*.bin"))
                runner.write_json(case / "input_identity.json", {"noise_seed": args.seed_start + mc_id,
                    "raw": [{"path": str(p), "bytes": p.stat().st_size, "sha256": digest(p)} for p in raw]})
            trials, targets, audit = scorer.score(case, result, mp, mc_id)
            runner.write_json(result / "metrics/complete.json", {
                "min_points": mp, "mc_id": mc_id, "target_total": 375, "track_total": 125,
                "target_rows": len(targets), "trial_rows": len(trials), "audit_status": audit["status"],
                "track_metric": "truth_target_detected_at_least_2_of_3_periods"})
            all_trials.extend(trials)
            completed.append([mp, mc_id])
            scorer.write(out / "trial_results.csv", all_trials)
            progress = {"completed_combinations": len(completed), "expected_combinations": args.trials*len(args.min_points),
                "completed": completed, "last": {"mc_id": mc_id, "min_points": mp,
                    "target_detected": audit["target_detected"], "track_detected": audit["track_detected"]},
                "updated_local": time.strftime("%Y-%m-%d %H:%M:%S")}
            runner.write_json(out / "progress.json", progress)
            print(json.dumps(progress["last"], ensure_ascii=False), flush=True)
        # The user explicitly requests prompt disposal of this task's generated
        # raw IQ. Preserve truth-by-beam, config, logs, diagnostics and identities.
        removed = []
        for p in list((case / "stage2/data").glob("*.bin")) + [
                case / "stage2/truth/truth_pulse.csv", case / "stage2/truth/moving_target_truth.csv"]:
            if p.is_file():
                removed.append({"path": str(p), "bytes": p.stat().st_size})
                p.unlink()
        runner.write_json(case / "raw_cleanup.json", {"completed_min_points": args.min_points, "removed": removed})
    scorer.write(out / "trial_results.csv", all_trials)
    target_rows = []
    for mp, mc_id in sorted(completed):
        target_rows.extend(scorer.read(out / f"mc_{mc_id:03d}/mp{mp}_hann_kaiser/metrics/target_results.csv"))
    scorer.write(out / "target_results.csv", target_rows)
    runner.write_json(out / "complete.json", {"combinations": len(completed),
        "trial_rows": len(all_trials), "target_rows": len(target_rows), "contract": contract})


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--output", type=Path, required=True)
    p.add_argument("--calibration", type=Path, required=True)
    p.add_argument("--build-dir", type=Path, default=runner.ROOT / "build")
    p.add_argument("--trials", type=int, default=100)
    p.add_argument("--seed-start", type=int, default=2026090600)
    p.add_argument("--min-points", default="3,4,5,6,7,8")
    args = p.parse_args()
    args.min_points = [int(x) for x in args.min_points.split(",")]
    if args.trials < 1 or len(set(args.min_points)) != len(args.min_points) or not set(args.min_points) <= set(range(3,9)):
        p.error("invalid trial count or min_points")
    execute(args)


if __name__ == "__main__":
    main()
