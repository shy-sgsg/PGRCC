#!/usr/bin/env python3
"""Run traceable GMTI simulation/evaluation matrices through production code.

The runner deliberately orchestrates the existing Stage2/Stage3 generators,
``GMTI_core`` and metric reducers.  It does not contain an alternate signal
processing implementation.
"""

from __future__ import annotations

import argparse
import concurrent.futures
import csv
import hashlib
import json
import math
import os
from pathlib import Path
import shlex
import shutil
import subprocess
import sys
import threading
import time
import traceback
import xml.etree.ElementTree as ET


REPO_ROOT = Path(__file__).resolve().parents[1]


def read_json(path: Path):
    return json.loads(path.read_text(encoding="utf-8"))


def read_xml_overrides(path: Path) -> dict:
    """Read a reusable XML profile without changing matrix case semantics."""
    payload = read_json(path)
    if isinstance(payload, dict) and "xml_overrides" in payload:
        payload = payload["xml_overrides"]
    if not isinstance(payload, dict):
        raise RuntimeError(f"XML override profile must contain an object: {path}")
    return payload


def write_json(path: Path, value) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def write_csv(path: Path, rows: list[dict], fields: list[str]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(rows)


def first_csv_row(path: Path) -> dict:
    if not path.exists():
        return {}
    with path.open(newline="", encoding="utf-8-sig") as handle:
        return next(csv.DictReader(handle), {})


def sha256_file(path: Path) -> str:
    if not path.exists() or not path.is_file():
        return ""
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(8 * 1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def git_commit() -> str:
    result = subprocess.run(
        ["git", "rev-parse", "--short=12", "HEAD"], cwd=REPO_ROOT,
        text=True, capture_output=True, check=False)
    return result.stdout.strip() if result.returncode == 0 else "unknown"


def git_provenance() -> dict:
    """Identify an uncommitted checkout without treating HEAD as sufficient."""
    status = subprocess.run(
        ["git", "status", "--porcelain=v1", "-z", "--untracked-files=all"],
        cwd=REPO_ROOT, capture_output=True, check=False)
    source_roots = [
        "CMakeLists.txt", "configs", "experiments", "include", "scripts",
        "simulator", "src", "tests", "tools",
    ]
    diff = subprocess.run(
        ["git", "diff", "--binary", "HEAD", "--", *source_roots], cwd=REPO_ROOT,
        capture_output=True, check=False)
    digest = hashlib.sha256(diff.stdout)
    untracked = subprocess.run(
        ["git", "ls-files", "--others", "--exclude-standard", "-z"],
        cwd=REPO_ROOT, capture_output=True, check=False)
    included_roots = {
        "configs", "experiments", "include", "scripts", "simulator",
        "src", "tests", "tools",
    }
    for raw_name in untracked.stdout.split(b"\0"):
        if not raw_name:
            continue
        relative = Path(os.fsdecode(raw_name))
        path = REPO_ROOT / relative
        if (not relative.parts or relative.parts[0] not in included_roots
                or not path.is_file()):
            continue
        digest.update(raw_name)
        digest.update(bytes.fromhex(sha256_file(path)))
    source_dirty = bool(diff.stdout) or any(
        raw_name and Path(os.fsdecode(raw_name)).parts
        and Path(os.fsdecode(raw_name)).parts[0] in included_roots
        for raw_name in untracked.stdout.split(b"\0"))
    return {
        "git_commit": git_commit(),
        "git_dirty": bool(status.stdout),
        "source_dirty": source_dirty,
        "worktree_fingerprint": digest.hexdigest() if source_dirty else "clean",
    }


def deep_set(mapping: dict, dotted_key: str, value) -> None:
    parts = dotted_key.split(".")
    current = mapping
    for index, key in enumerate(parts[:-1]):
        next_is_index = parts[index + 1].isdigit()
        if isinstance(current, list):
            if not key.isdigit():
                raise ValueError(
                    f"list override component must be an index in {dotted_key!r}: {key!r}")
            item_index = int(key)
            if item_index < 0 or item_index >= len(current):
                raise IndexError(f"override index out of range in {dotted_key!r}: {item_index}")
            child = current[item_index]
            expected = list if next_is_index else dict
            if not isinstance(child, expected):
                raise TypeError(
                    f"override path type mismatch in {dotted_key!r} at {key!r}")
            current = child
            continue
        if not isinstance(current, dict):
            raise TypeError(f"override path is not a mapping in {dotted_key!r} at {key!r}")
        child = current.get(key)
        expected = list if next_is_index else dict
        if child is None:
            child = [] if next_is_index else {}
            current[key] = child
        if not isinstance(child, expected):
            raise TypeError(
                f"override path type mismatch in {dotted_key!r} at {key!r}")
        current = child
    last = parts[-1]
    if isinstance(current, list):
        if not last.isdigit():
            raise ValueError(
                f"list override component must be an index in {dotted_key!r}: {last!r}")
        item_index = int(last)
        if item_index < 0 or item_index >= len(current):
            raise IndexError(f"override index out of range in {dotted_key!r}: {item_index}")
        current[item_index] = value
    elif isinstance(current, dict):
        current[last] = value
    else:
        raise TypeError(f"override destination is invalid in {dotted_key!r}")


def safe_remove_tree(path: Path, experiment_root: Path) -> None:
    resolved = path.resolve()
    root = experiment_root.resolve()
    try:
        resolved.relative_to(root)
    except ValueError as exc:
        raise RuntimeError(f"refusing to remove path outside experiment root: {path}") from exc
    if resolved == root:
        raise RuntimeError("refusing to remove experiment root")
    if path.is_symlink() or path.is_file():
        path.unlink()
    elif path.exists():
        shutil.rmtree(path)


def set_xml_values(input_path: Path, output_path: Path, values: dict) -> None:
    tree = ET.parse(input_path)
    root = tree.getroot()
    params = root.find("GMTI_parameter")
    if params is None:
        raise RuntimeError(f"missing <GMTI_parameter> in {input_path}")
    for key, value in values.items():
        node = params.find(key)
        if node is None:
            node = ET.SubElement(params, key)
        if isinstance(value, bool):
            node.text = "1" if value else "0"
        else:
            node.text = str(value)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    ET.indent(tree, space="    ")
    tree.write(output_path, encoding="utf-8", xml_declaration=True)


def normal_run_manifest(path: Path) -> bool:
    try:
        value = read_json(path)
        return bool(value.get("normal_exit")) and int(value.get("exit_code", 1)) == 0
    except (OSError, ValueError, TypeError):
        return False


class MatrixRunner:
    def __init__(self, args):
        self.args = args
        self.matrix_path = args.matrix.resolve()
        self.matrix = read_json(self.matrix_path)
        self.experiment_id = str(self.matrix.get("experiment_id", "")).strip()
        if not self.experiment_id:
            raise RuntimeError("matrix requires experiment_id")
        configured_root = self.matrix.get("output_root", f"outputs/eval/{self.experiment_id}")
        self.output_root = (REPO_ROOT / configured_root).resolve()
        self.command_lock = threading.Lock()
        self.provenance = git_provenance()
        self.commit = self.provenance["git_commit"]
        profile_name = self.matrix.get("xml_override_profile", "")
        self.xml_profile_path = None
        self.xml_profile = {}
        if profile_name:
            self.xml_profile_path = (REPO_ROOT / str(profile_name)).resolve()
            self.xml_profile = read_xml_overrides(self.xml_profile_path)
        cases = self.matrix.get("cases")
        if not isinstance(cases, list) or not cases:
            raise RuntimeError("matrix requires a non-empty cases array")
        ids = [str(item.get("case_id", "")) for item in cases]
        if any(not item for item in ids) or len(set(ids)) != len(ids):
            raise RuntimeError("case_id values must be non-empty and unique")
        requested = set()
        for group in args.case:
            requested.update(part for part in group.split(",") if part)
        missing = requested - set(ids)
        if missing:
            raise RuntimeError(f"unknown --case values: {sorted(missing)}")
        self.case_by_id = {item["case_id"]: item for item in cases}
        if requested:
            # A paired target-on case is not reproducible without its target-off
            # dependency.  Include dependencies automatically for --case runs.
            selected = set(requested)
            pending = list(requested)
            while pending:
                item = self.case_by_id[pending.pop()]
                dependencies = [
                    ("paired_off_case_id", item.get("paired_off_case_id")),
                    ("calibration_reference_from_case_id",
                     item.get("calibration_reference_from_case_id")),
                ]
                for field, dependency in dependencies:
                    if dependency and dependency not in selected:
                        if dependency not in self.case_by_id:
                            raise RuntimeError(
                                f"unknown {field}: {dependency}")
                        selected.add(dependency)
                        pending.append(dependency)
            self.cases = [item for item in cases if item["case_id"] in selected]
        else:
            self.cases = list(cases)
        self.failures: list[dict] = []
        self.states: dict[str, dict] = {}

    def paths(self, case: dict) -> dict[str, Path]:
        case_id = case["case_id"]
        return {
            "work": self.output_root / "case_work" / case_id,
            "config": self.output_root / "configs" / case_id,
            "algorithm": self.output_root / "algorithm_outputs" / case_id,
            "metrics": self.output_root / "metrics" / case_id,
            "plots": self.output_root / "plots" / case_id,
            "reports": self.output_root / "reports" / case_id,
            "logs": self.output_root / "logs" / case_id,
            "failed": self.output_root / "failed_cases" / case_id,
        }

    def create_layout(self) -> None:
        for name in (
                "matrix", "configs", "data", "truth", "algorithm_outputs",
                "metrics", "plots", "reports", "logs", "failed_cases", "case_work"):
            (self.output_root / name).mkdir(parents=True, exist_ok=True)
        shutil.copy2(self.matrix_path, self.output_root / "matrix" / self.matrix_path.name)

    def append_command_record(self, record: dict) -> None:
        path = self.output_root / "commands.jsonl"
        with self.command_lock:
            with path.open("a", encoding="utf-8") as handle:
                handle.write(json.dumps(record, ensure_ascii=False) + "\n")

    def run_command(self, case_id: str, stage: str, cmd: list[str], log_dir: Path) -> dict:
        log_dir.mkdir(parents=True, exist_ok=True)
        command_text = shlex.join(cmd)
        started = time.time()
        record = {
            "case_id": case_id,
            "stage": stage,
            "command": command_text,
            "started_unix": started,
            "timeout_sec": self.args.timeout,
        }
        print(f"[eval][{case_id}][{stage}] {command_text}", flush=True)
        if self.args.dry_run:
            record.update({"status": "dry_run", "return_code": 0, "elapsed_sec": 0.0})
            return record
        try:
            result = subprocess.run(
                cmd, cwd=REPO_ROOT, text=True, capture_output=True,
                timeout=self.args.timeout, check=False)
            elapsed = time.time() - started
            (log_dir / f"{stage}.stdout.log").write_text(result.stdout, encoding="utf-8")
            (log_dir / f"{stage}.stderr.log").write_text(result.stderr, encoding="utf-8")
            record.update({
                "status": "ok" if result.returncode == 0 else "failed",
                "return_code": result.returncode,
                "elapsed_sec": elapsed,
                "stdout_log": str(log_dir / f"{stage}.stdout.log"),
                "stderr_log": str(log_dir / f"{stage}.stderr.log"),
            })
        except subprocess.TimeoutExpired as exc:
            elapsed = time.time() - started
            stdout = exc.stdout.decode() if isinstance(exc.stdout, bytes) else (exc.stdout or "")
            stderr = exc.stderr.decode() if isinstance(exc.stderr, bytes) else (exc.stderr or "")
            (log_dir / f"{stage}.stdout.log").write_text(stdout, encoding="utf-8")
            (log_dir / f"{stage}.stderr.log").write_text(stderr, encoding="utf-8")
            record.update({"status": "timeout", "return_code": 124, "elapsed_sec": elapsed})
        self.append_command_record(record)
        if record["return_code"] != 0:
            raise RuntimeError(
                f"{case_id}: stage {stage} failed with return code {record['return_code']}")
        return record

    def link_case_artifact(self, category: str, case_id: str, target: Path) -> None:
        link = self.output_root / category / case_id
        if link.exists() or link.is_symlink():
            if link.is_symlink() and link.resolve() == target.resolve():
                return
            safe_remove_tree(link, self.output_root)
        link.parent.mkdir(parents=True, exist_ok=True)
        link.symlink_to(os.path.relpath(target, link.parent), target_is_directory=True)

    def clean_case(self, case: dict) -> None:
        if not self.args.force:
            return
        for path in self.paths(case).values():
            if path.exists() or path.is_symlink():
                safe_remove_tree(path, self.output_root)
        for category in ("data", "truth"):
            link = self.output_root / category / case["case_id"]
            if link.exists() or link.is_symlink():
                safe_remove_tree(link, self.output_root)

    def prepare_case(self, case: dict) -> dict:
        case_id = case["case_id"]
        paths = self.paths(case)
        self.clean_case(case)
        for path in paths.values():
            path.mkdir(parents=True, exist_ok=True)
        kind = case.get("kind", "stage2")
        state = {
            "case_id": case_id,
            "experiment_id": self.experiment_id,
            "kind": kind,
            "random_seed": case.get("random_seed", ""),
            "git_commit": self.commit,
            "git_dirty": self.provenance["git_dirty"],
            "source_dirty": self.provenance["source_dirty"],
            "worktree_fingerprint": self.provenance["worktree_fingerprint"],
            "matrix_path": str(self.matrix_path),
            "xml_override_profile": str(self.xml_profile_path or ""),
            "status": "preparing",
            "steps": {},
        }
        if kind == "stage2":
            template = (REPO_ROOT / case["scenario_template"]).resolve()
            scenario = read_json(template)
            scenario["case_id"] = case_id
            scenario["output_dir"] = os.path.relpath(paths["work"], REPO_ROOT)
            if "random_seed" in case:
                deep_set(scenario, "random.random_seed", int(case["random_seed"]))
            for key, value in case.get("scenario_overrides", {}).items():
                deep_set(scenario, key, value)
            scenario_path = paths["config"] / "expanded_scenario.json"
            write_json(scenario_path, scenario)
            data_path = paths["work"] / "data" / "stage2_statistical_newprotocol.bin"
            generated_xml = paths["work"] / "config" / "temp_config_stage2_newsystem.xml"
            truth_dir = paths["work"] / "truth"
            if not (self.args.resume and data_path.exists() and generated_xml.exists()):
                state["steps"]["simulation"] = self.run_command(
                    case_id, "simulate_stage2",
                    ["./build/simulate_stage2_statistical", "--config", str(scenario_path)],
                    paths["logs"])
            else:
                state["steps"]["simulation"] = {"status": "resumed"}
            self.link_case_artifact("data", case_id, paths["work"] / "data")
            self.link_case_artifact("truth", case_id, truth_dir)
            base_xml = generated_xml
        elif kind == "stage3":
            template = (REPO_ROOT / case["scenario_template"]).resolve()
            scenario = read_json(template)
            scenario["case_id"] = case_id
            scenario["output_dir"] = os.path.relpath(paths["work"], REPO_ROOT)
            for key, value in case.get("scenario_overrides", {}).items():
                deep_set(scenario, key, value)

            # Stage3 stores moving-target definitions in a separate JSON file.
            # Copy and expand it per case when requested so every case remains
            # self-contained and target sweeps never mutate the shared template.
            target_template_name = case.get("target_config_template")
            target_overrides = case.get("target_config_overrides", {})
            if target_template_name or target_overrides:
                if not target_template_name:
                    target_template_name = scenario.get("targets", {}).get(
                        "target_config_path", "")
                if not target_template_name:
                    raise RuntimeError(
                        f"{case_id}: target overrides require target_config_template")
                target_template = (REPO_ROOT / target_template_name).resolve()
                target_config = read_json(target_template)
                for key, value in target_overrides.items():
                    deep_set(target_config, key, value)
                target_config_path = paths["config"] / "expanded_targets.json"
                write_json(target_config_path, target_config)
                deep_set(
                    scenario, "targets.target_config_path",
                    os.path.relpath(target_config_path, REPO_ROOT))

            scenario_path = paths["config"] / "expanded_scenario.json"
            write_json(scenario_path, scenario)
            data_path = paths["work"] / "data" / "stage3_sar_scene_newprotocol.bin"
            generated_xml = paths["work"] / "config" / "temp_config_stage3_newsystem.xml"
            truth_dir = paths["work"] / "truth"
            if not (self.args.resume and data_path.exists() and generated_xml.exists()):
                state["steps"]["simulation"] = self.run_command(
                    case_id, "simulate_stage3", [
                        "./build/simulate_stage3_sar_scene",
                        "--config", str(scenario_path),
                        "--output-dir", str(paths["work"]),
                        "--compare-cpu-cuda", "false",
                    ], paths["logs"])
            else:
                state["steps"]["simulation"] = {"status": "resumed"}
            self.link_case_artifact("data", case_id, paths["work"] / "data")
            self.link_case_artifact("truth", case_id, truth_dir)
            base_xml = generated_xml
        elif kind == "existing_data":
            data_path = (REPO_ROOT / case["data_path"]).resolve()
            truth_dir = (REPO_ROOT / case.get("truth_dir", "")).resolve()
            base_xml = (REPO_ROOT / case["base_xml"]).resolve()
            for required in (data_path, base_xml):
                if not required.exists():
                    raise FileNotFoundError(required)
            state["steps"]["simulation"] = {"status": "existing_data"}
        else:
            raise RuntimeError(f"{case_id}: unsupported kind {kind!r}")

        algorithm_dir = paths["algorithm"]
        values = dict(self.xml_profile)
        values.update(self.matrix.get("default_xml_overrides", {}))
        values.update(case.get("xml_overrides", {}))
        values.update({
            "GMTI_data_new": os.path.relpath(data_path, REPO_ROOT),
            "result_add": os.path.relpath(algorithm_dir, REPO_ROOT),
            "runtime_diagnostics_enabled": 1,
        })
        if "p5" in case.get("metrics", []):
            values.setdefault("csi_metrics_enable", 1)
            values.setdefault("csi_metrics_dump_power_maps", 1)
        xml_path = paths["config"] / f"{case_id}.xml"
        set_xml_values(base_xml, xml_path, values)
        state.update({
            "data_path": str(data_path),
            "truth_dir": str(truth_dir),
            "config_path": str(xml_path),
            "algorithm_output_dir": str(algorithm_dir),
            "metrics_dir": str(paths["metrics"]),
            "config_hash": sha256_file(xml_path),
            "data_hash": sha256_file(data_path),
            "status": "prepared",
        })
        write_json(paths["logs"] / "case_state.json", state)
        self.states[case_id] = state
        return state

    def run_algorithm(self, case: dict) -> dict:
        case_id = case["case_id"]
        state = self.states[case_id]
        paths = self.paths(case)
        calibration_reference_case = case.get(
            "calibration_reference_from_case_id")
        if calibration_reference_case:
            source_state = self.states.get(calibration_reference_case)
            if not source_state:
                raise RuntimeError(
                    f"{case_id}: calibration reference case "
                    f"{calibration_reference_case} was not prepared")
            source_manifest = (Path(source_state["algorithm_output_dir"]) /
                               "run_manifest.json")
            if not normal_run_manifest(source_manifest):
                raise RuntimeError(
                    f"{case_id}: calibration reference case "
                    f"{calibration_reference_case} has no successful run")
            diagnostic_paths = sorted(Path(
                source_state["algorithm_output_dir"]).glob(
                    "channel_calibration_beam*.json"))
            if not diagnostic_paths:
                raise RuntimeError(
                    f"{case_id}: calibration reference case has no channel "
                    "calibration diagnostic")
            diagnostic = read_json(diagnostic_paths[0])
            phase = diagnostic.get("phase_bias_rad")
            try:
                phase = float(phase)
            except (TypeError, ValueError) as exc:
                raise RuntimeError(
                    f"{case_id}: calibration reference phase is invalid") from exc
            if not bool(diagnostic.get("valid")) or not math.isfinite(phase):
                raise RuntimeError(
                    f"{case_id}: calibration reference estimate failed "
                    f"quality gate: {diagnostic.get('status', 'unknown')}")
            config_path = Path(state["config_path"])
            set_xml_values(config_path, config_path, {
                "channel_calibration_reference_valid": 1,
                "channel_calibration_reference_phase_rad": phase,
            })
            state["config_hash"] = sha256_file(config_path)
            state["calibration_reference"] = {
                "source_case_id": calibration_reference_case,
                "source_file": str(diagnostic_paths[0]),
                "source_data_hash": source_state.get("data_hash", ""),
                "phase_bias_rad": phase,
                "method": diagnostic.get("method", ""),
                "status": diagnostic.get("status", ""),
            }
            write_json(
                paths["config"] / "calibration_reference_provenance.json",
                state["calibration_reference"])
            write_json(paths["logs"] / "case_state.json", state)
        manifest = Path(state["algorithm_output_dir"]) / "run_manifest.json"
        if (self.args.resume and not calibration_reference_case and
                normal_run_manifest(manifest)):
            state["steps"]["algorithm"] = {"status": "resumed"}
        else:
            state["steps"]["algorithm"] = self.run_command(
                case_id, "gmti_core",
                ["./build/GMTI_core", state["config_path"]], paths["logs"])
        if not normal_run_manifest(manifest):
            raise RuntimeError(f"{case_id}: missing successful run_manifest.json")
        state["status"] = "algorithm_complete"
        write_json(paths["logs"] / "case_state.json", state)
        return state

    def evaluate_case(self, case: dict) -> dict:
        case_id = case["case_id"]
        state = self.states[case_id]
        paths = self.paths(case)
        metrics = case.get("metrics", [])
        if "p4" in metrics:
            report_dir = paths["metrics"] / "p4_truth_eval"
            expected = report_dir / "detection_metrics.csv"
            if not (self.args.resume and expected.exists() and
                    not self.args.force_metrics):
                state["steps"]["p4_truth_eval"] = self.run_command(
                    case_id, "p4_truth_eval", [
                        sys.executable, "scripts/run_p4_truth_eval.py",
                        "--output-dir", state["algorithm_output_dir"],
                        "--truth-dir", state["truth_dir"],
                        "--match-config", case.get(
                            "match_config", self.matrix.get(
                                "match_config", "configs/eval/match_config.json")),
                        "--report-dir", str(report_dir),
                    ], paths["logs"])
            else:
                state["steps"]["p4_truth_eval"] = {"status": "resumed"}
        if "p5" in metrics:
            report_dir = paths["metrics"] / "p5_csi"
            expected = report_dir / "csi_metric_summary.csv"
            if not (self.args.resume and expected.exists() and
                    not self.args.force_metrics):
                cmd = [
                    sys.executable, "scripts/run_p5_csi_eval.py",
                    "--algorithm-output-dir", state["algorithm_output_dir"],
                    "--truth-dir", state["truth_dir"],
                    "--report-dir", str(report_dir),
                    "--experiment-id", self.experiment_id,
                    "--random-seed", str(case.get("random_seed", "")),
                    "--config-path", state["config_path"],
                    "--data-path", state["data_path"],
                ]
                paired_id = case.get("paired_off_case_id")
                if paired_id:
                    paired_state = self.states.get(paired_id)
                    if not paired_state:
                        raise RuntimeError(f"{case_id}: paired case {paired_id} was not selected/prepared")
                    cmd += ["--paired-off-output-dir", paired_state["algorithm_output_dir"]]
                if case.get("plots", False):
                    cmd.append("--plots")
                state["steps"]["p5_csi_eval"] = self.run_command(
                    case_id, "p5_csi_eval", cmd, paths["logs"])
            else:
                state["steps"]["p5_csi_eval"] = {"status": "resumed"}
            p5_summary = first_csv_row(expected)
            p5_status = p5_summary.get("status")
            allow_censored = bool(case.get("p5_allow_censored_target_power", False))
            if p5_status != "ok" and not (allow_censored and p5_status == "incomplete"):
                raise RuntimeError(
                    f"{case_id}: P5 metric quality gate is {p5_status or 'missing'}")
        if "p6" in metrics:
            report_dir = paths["metrics"] / "p6_velocity"
            expected = report_dir / "velocity_ambiguity_summary.csv"
            if not (self.args.resume and expected.exists() and
                    not self.args.force_metrics):
                state["steps"]["p6_velocity_eval"] = self.run_command(
                    case_id, "p6_velocity_eval", [
                        sys.executable, "scripts/evaluate_velocity_ambiguity.py",
                        "--algorithm-output-dir", state["algorithm_output_dir"],
                        "--truth-dir", state["truth_dir"],
                        "--report-dir", str(report_dir),
                        "--match-config", case.get(
                            "match_config", self.matrix.get(
                                "match_config", "configs/eval/match_config.json")),
                    ], paths["logs"])
            else:
                state["steps"]["p6_velocity_eval"] = {"status": "resumed"}
        state["status"] = "complete"
        write_json(paths["logs"] / "case_state.json", state)
        return state

    def capture_failure(self, case: dict, stage: str, exc: BaseException) -> None:
        case_id = case["case_id"]
        paths = self.paths(case)
        failure = {
            "case_id": case_id,
            "stage": stage,
            "error": str(exc),
            "traceback": traceback.format_exc(),
        }
        self.failures.append(failure)
        paths["failed"].mkdir(parents=True, exist_ok=True)
        write_json(paths["failed"] / "failure.json", failure)
        if self.args.dump_failed_case:
            for name in ("config", "logs"):
                source = paths[name]
                target = paths["failed"] / name
                if source.exists():
                    shutil.copytree(source, target, dirs_exist_ok=True)
        print(f"[eval][{case_id}][{stage}][FAIL] {exc}", file=sys.stderr, flush=True)

    def run_phase(self, stage: str, cases: list[dict], method, parallel: bool) -> None:
        if not cases:
            return
        if parallel and self.args.jobs > 1:
            with concurrent.futures.ThreadPoolExecutor(max_workers=self.args.jobs) as pool:
                futures = {pool.submit(method, case): case for case in cases}
                for future in concurrent.futures.as_completed(futures):
                    case = futures[future]
                    try:
                        future.result()
                    except BaseException as exc:  # preserve all case evidence
                        self.capture_failure(case, stage, exc)
                        if self.args.stop_on_failure:
                            raise
        else:
            for case in cases:
                try:
                    method(case)
                except BaseException as exc:
                    self.capture_failure(case, stage, exc)
                    if self.args.stop_on_failure:
                        raise

    def make_reports(self) -> None:
        failed_ids = {item["case_id"] for item in self.failures}
        rows = []
        for case in self.cases:
            case_id = case["case_id"]
            state = self.states.get(case_id, {})
            p4 = first_csv_row(self.paths(case)["metrics"] / "p4_truth_eval" / "detection_metrics.csv")
            p5 = first_csv_row(self.paths(case)["metrics"] / "p5_csi" / "csi_metric_summary.csv")
            p6 = first_csv_row(
                self.paths(case)["metrics"] / "p6_velocity" /
                "velocity_ambiguity_summary.csv")
            rows.append({
                "experiment_id": self.experiment_id,
                "case_id": case_id,
                "kind": case.get("kind", "stage2"),
                "random_seed": case.get("random_seed", ""),
                "status": "failed" if case_id in failed_ids else state.get("status", "not_run"),
                "config_hash": state.get("config_hash", ""),
                "data_hash": state.get("data_hash", ""),
                "git_commit": self.commit,
                "git_dirty": self.provenance["git_dirty"],
                "source_dirty": self.provenance["source_dirty"],
                "worktree_fingerprint": self.provenance["worktree_fingerprint"],
                "truth_count": p4.get("truth_count", ""),
                "detection_count": p4.get("detection_count", ""),
                "matched_count": p4.get("matched_count", ""),
                "false_alarm_count": p4.get("false_alarm_count", ""),
                "mean_position_error_m": p4.get("mean_position_error_m", ""),
                "mean_velocity_error_mps": p4.get("mean_velocity_error_mps", ""),
                "mean_CA_ROI_dB": p5.get("mean_CA_ROI_dB", ""),
                "mean_SCNR_improvement_dB": p5.get("mean_SCNR_improvement_dB", ""),
                "mean_target_loss_dB": p5.get("mean_target_loss_dB", ""),
                "p5_status": p5.get("status", ""),
                "resolved_rate": p6.get("resolved_rate", ""),
                "unresolved_rate": p6.get("unresolved_rate", ""),
                "ambiguity_order_accuracy": p6.get("ambiguity_order_accuracy", ""),
                "wrong_confident_rate": p6.get("wrong_confident_rate", ""),
                "velocity_rmse_mps": p6.get("velocity_rmse_mps", ""),
                "p6_status": p6.get("status", ""),
            })
        fields = list(rows[0]) if rows else ["experiment_id", "case_id", "status"]
        write_csv(self.output_root / "case_manifest.csv", rows, fields)
        summary = {
            "experiment_id": self.experiment_id,
            "matrix_path": str(self.matrix_path),
            "matrix_hash": sha256_file(self.matrix_path),
            "git_commit": self.commit,
            "git_dirty": self.provenance["git_dirty"],
            "source_dirty": self.provenance["source_dirty"],
            "worktree_fingerprint": self.provenance["worktree_fingerprint"],
            "selected_case_count": len(self.cases),
            "complete_case_count": sum(row["status"] == "complete" for row in rows),
            "failed_case_count": len(self.failures),
            "failures": self.failures,
        }
        write_json(self.output_root / "experiment_summary.json", summary)
        if self.args.compare_baseline:
            baseline_rows = {}
            with self.args.compare_baseline.open(newline="", encoding="utf-8-sig") as handle:
                baseline_rows = {row.get("case_id", ""): row for row in csv.DictReader(handle)}
            comparison = []
            for row in rows:
                old = baseline_rows.get(row["case_id"], {})
                comparison.append({
                    "case_id": row["case_id"],
                    "current_status": row["status"],
                    "baseline_status": old.get("status", "missing"),
                    "current_detection_count": row["detection_count"],
                    "baseline_detection_count": old.get("detection_count", ""),
                    "current_mean_CA_ROI_dB": row["mean_CA_ROI_dB"],
                    "baseline_mean_CA_ROI_dB": old.get("mean_CA_ROI_dB", ""),
                })
            write_csv(
                self.output_root / "baseline_comparison.csv", comparison,
                list(comparison[0]) if comparison else ["case_id"])
        if self.args.report:
            lines = [
                f"# {self.experiment_id} 自动评测摘要",
                "",
                f"- 源码提交：`{self.commit}`",
                f"- case 数：{len(rows)}",
                f"- 失败数：{len(self.failures)}",
                "",
                "| case | 状态 | truth/detection/matched | false alarm | CA (dB) | SCNR 改善 (dB) |",
                "|---|---:|---:|---:|---:|---:|",
            ]
            for row in rows:
                counts = "/".join(str(row[key]) for key in
                                  ("truth_count", "detection_count", "matched_count"))
                lines.append(
                    f"| {row['case_id']} | {row['status']} | {counts} | "
                    f"{row['false_alarm_count']} | {row['mean_CA_ROI_dB']} | "
                    f"{row['mean_SCNR_improvement_dB']} |")
            lines.extend([
                "",
                "> 数值来自各 case 的生产 `GMTI_core` 输出；详细配置、hash、日志和指标见同级目录。",
                "",
            ])
            report_path = self.output_root / "reports" / "experiment_report.md"
            report_path.parent.mkdir(parents=True, exist_ok=True)
            report_path.write_text("\n".join(lines), encoding="utf-8")

    def print_dry_run(self) -> int:
        print(json.dumps({
            "experiment_id": self.experiment_id,
            "output_root": str(self.output_root),
            "xml_override_profile": str(self.xml_profile_path or ""),
            "cases": [case["case_id"] for case in self.cases],
            "jobs": self.args.jobs,
            "timeout": self.args.timeout,
            "note": "simulation/evaluation may use --jobs; production CUDA runs are serialized",
        }, ensure_ascii=False, indent=2))
        for case in self.cases:
            kind = case.get("kind", "stage2")
            if kind == "stage2":
                print(shlex.join([
                    "./build/simulate_stage2_statistical", "--config",
                    str(self.paths(case)["config"] / "expanded_scenario.json")]))
            elif kind == "stage3":
                print(shlex.join([
                    "./build/simulate_stage3_sar_scene", "--config",
                    str(self.paths(case)["config"] / "expanded_scenario.json"),
                    "--output-dir", str(self.paths(case)["work"]),
                    "--compare-cpu-cuda", "false"] ))
            print(shlex.join([
                "./build/GMTI_core", str(self.paths(case)["config"] / f"{case['case_id']}.xml")]))
        return 0

    def run(self) -> int:
        if self.args.dry_run:
            return self.print_dry_run()
        self.create_layout()
        self.run_phase("prepare", self.cases, self.prepare_case, parallel=True)
        ready = [case for case in self.cases if case["case_id"] in self.states]
        if self.args.prepare_only:
            self.make_reports()
            print(json.dumps(read_json(self.output_root / "experiment_summary.json"), ensure_ascii=False))
            return 1 if self.failures else 0
        # One production executable owns most device memory; serialize CUDA cases.
        self.run_phase("algorithm", ready, self.run_algorithm, parallel=False)
        algorithm_failed = {item["case_id"] for item in self.failures if item["stage"] == "algorithm"}
        evaluable = [case for case in ready if case["case_id"] not in algorithm_failed]
        self.run_phase("evaluate", evaluable, self.evaluate_case, parallel=True)
        self.make_reports()
        print(json.dumps(read_json(self.output_root / "experiment_summary.json"), ensure_ascii=False))
        return 1 if self.failures else 0


def parse_args():
    parser = argparse.ArgumentParser()
    parser.add_argument("--matrix", required=True, type=Path)
    parser.add_argument("--case", action="append", default=[], help="case id or comma-separated ids")
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument(
        "--prepare-only", action="store_true",
        help="generate expanded configs/data and manifests without running GMTI_core")
    parser.add_argument("--resume", action="store_true")
    parser.add_argument("--force", action="store_true")
    parser.add_argument(
        "--force-metrics", action="store_true",
        help="with --resume, recompute metric/report stages without rerunning simulation or GMTI_core")
    parser.add_argument("--jobs", type=int, default=1)
    parser.add_argument("--timeout", type=float, default=600.0)
    parser.add_argument("--stop-on-failure", action="store_true")
    parser.add_argument("--dump-failed-case", action="store_true")
    parser.add_argument("--report", action="store_true")
    parser.add_argument("--compare-baseline", type=Path)
    args = parser.parse_args()
    if args.jobs < 1:
        parser.error("--jobs must be >= 1")
    if args.timeout <= 0.0:
        parser.error("--timeout must be > 0")
    if args.resume and args.force:
        parser.error("--resume and --force are mutually exclusive")
    if args.force_metrics and not args.resume:
        parser.error("--force-metrics requires --resume")
    return args


def run_tracking_workflow(args) -> int:
    if args.prepare_only:
        raise RuntimeError("tracking workflow does not support --prepare-only; use --dry-run")
    cmd = [
        sys.executable,
        str(REPO_ROOT / "scripts" / "run_tracking_robustness_eval.py"),
        "--matrix", str(args.matrix.resolve()),
        "--timeout", str(args.timeout),
    ]
    for case_group in args.case:
        cmd.extend(["--case", case_group])
    for enabled, flag in (
            (args.dry_run, "--dry-run"),
            (args.force, "--force"),
            (args.resume, "--resume"),
            (args.stop_on_failure, "--stop-on-failure"),
            (args.dump_failed_case, "--dump-failed-case"),
            (args.report, "--report")):
        if enabled:
            cmd.append(flag)
    print(f"[eval][tracking] {shlex.join(cmd)}", flush=True)
    result = subprocess.run(cmd, cwd=REPO_ROOT, check=False)
    return result.returncode


def run_composite_workflow(args, matrix: dict) -> int:
    if args.case:
        raise RuntimeError(
            "composite workflow does not accept unqualified --case; run the child matrix directly")
    matrices = matrix.get("matrices")
    if not isinstance(matrices, list) or not matrices:
        raise RuntimeError("composite matrix requires a non-empty matrices array")
    if args.dry_run:
        overall_rc = 0
        print(json.dumps({
            "workflow": "composite",
            "experiment_id": matrix.get("experiment_id", "full_regression"),
            "matrix": str(args.matrix.resolve()),
            "children": [str(value) for value in matrices],
            "evidence_status": "not_executed_dry_run",
        }, ensure_ascii=False, indent=2))
        for child_name in matrices:
            child_path = (REPO_ROOT / str(child_name)).resolve()
            cmd = [
                sys.executable, str(Path(__file__).resolve()),
                "--matrix", str(child_path), "--jobs", str(args.jobs),
                "--timeout", str(args.timeout), "--dry-run",
            ]
            print(f"[eval][composite][dry-run] {shlex.join(cmd)}", flush=True)
            result = subprocess.run(cmd, cwd=REPO_ROOT, check=False)
            overall_rc = overall_rc or result.returncode
        return overall_rc
    configured_root = matrix.get(
        "output_root", f"outputs/eval/{matrix.get('experiment_id', 'full_regression')}")
    output_root = (REPO_ROOT / configured_root).resolve()
    output_root.mkdir(parents=True, exist_ok=True)
    (output_root / "matrix").mkdir(exist_ok=True)
    shutil.copy2(args.matrix.resolve(), output_root / "matrix" / args.matrix.name)
    provenance = git_provenance()
    records = []
    overall_rc = 0
    for child_name in matrices:
        child_path = (REPO_ROOT / str(child_name)).resolve()
        child = read_json(child_path)
        cmd = [sys.executable, str(Path(__file__).resolve()),
               "--matrix", str(child_path), "--jobs", str(args.jobs),
               "--timeout", str(args.timeout)]
        for enabled, flag in (
                (args.prepare_only, "--prepare-only"),
                (args.force, "--force"),
                (args.resume, "--resume"),
                (args.force_metrics, "--force-metrics"),
                (args.stop_on_failure, "--stop-on-failure"),
                (args.dump_failed_case, "--dump-failed-case"),
                (args.report, "--report")):
            if enabled:
                cmd.append(flag)
        print(f"[eval][composite] {shlex.join(cmd)}", flush=True)
        started = time.time()
        result = subprocess.run(cmd, cwd=REPO_ROOT, check=False)
        child_root = (REPO_ROOT / child.get(
            "output_root", f"outputs/eval/{child.get('experiment_id', child_path.stem)}")).resolve()
        child_summary_path = child_root / "experiment_summary.json"
        child_summary = (read_json(child_summary_path)
                         if child_summary_path.exists() else {})
        selected_count = child_summary.get("selected_case_count", "")
        complete_count = child_summary.get("complete_case_count", "")
        failed_count = child_summary.get("failed_case_count", "")
        if result.returncode != 0:
            evidence_status = "failed"
        elif not child_summary:
            evidence_status = "missing_summary"
        elif selected_count != "" and complete_count == selected_count and not failed_count:
            evidence_status = "complete"
        else:
            evidence_status = "partial"
        records.append({
            "matrix": str(child_path.relative_to(REPO_ROOT)),
            "experiment_id": child.get("experiment_id", child_path.stem),
            "workflow": child.get("workflow", "signal_processing"),
            "return_code": result.returncode,
            "evidence_status": evidence_status,
            "elapsed_sec": time.time() - started,
            "selected_case_count": selected_count,
            "complete_case_count": complete_count,
            "failed_case_count": failed_count,
            "summary_path": str(child_summary_path),
        })
        if result.returncode != 0 or evidence_status == "missing_summary":
            overall_rc = result.returncode or 2
            if args.stop_on_failure:
                break
    write_csv(output_root / "full_regression_summary.csv", records,
              list(records[0]) if records else ["matrix"])
    write_json(output_root / "experiment_summary.json", {
        "experiment_id": matrix.get("experiment_id", "full_regression"),
        "workflow": "composite",
        "matrix_path": str(args.matrix.resolve()),
        "matrix_hash": sha256_file(args.matrix.resolve()),
        **provenance,
        "child_matrix_count": len(records),
        "complete_child_count": sum(row["evidence_status"] == "complete" for row in records),
        "failed_child_count": sum(
            row["evidence_status"] in {"failed", "missing_summary"} for row in records),
        "partial_child_count": sum(row["evidence_status"] == "partial" for row in records),
        "children": records,
    })
    if args.report:
        report_result = subprocess.run([
            sys.executable, str(REPO_ROOT / "scripts" / "generate_eval_report.py"),
            "--matrix", str(args.matrix.resolve()),
        ], cwd=REPO_ROOT, check=False)
        if report_result.returncode != 0 and overall_rc == 0:
            overall_rc = report_result.returncode
    return overall_rc


def main() -> int:
    try:
        args = parse_args()
        matrix = read_json(args.matrix.resolve())
        workflow = matrix.get("workflow", "signal_processing")
        if workflow == "tracking":
            return run_tracking_workflow(args)
        if workflow == "composite":
            return run_composite_workflow(args, matrix)
        return MatrixRunner(args).run()
    except Exception as exc:
        print(f"[eval][FATAL] {exc}", file=sys.stderr)
        traceback.print_exc()
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
