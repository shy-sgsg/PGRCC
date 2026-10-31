import hashlib
import json
from pathlib import Path

from scripts.phase_r.r20_historical_evidence_adapter import (
    R8_ROOT,
    R8_SEED101_ROOT,
    R12_ROOT,
    discover_historical_cases,
)


def test_historical_adapter_reads_only_the_committed_r20_input_bundle():
    repo = Path(__file__).resolve().parents[1]
    bundle_root = repo / "docs/research/phase_r/R20/evidence/inputs"
    assert R8_ROOT == bundle_root / "r8"
    assert R8_SEED101_ROOT == bundle_root / "r8/seed101"
    assert R12_ROOT == bundle_root / "r12"

    cases = list(discover_historical_cases())
    assert len(cases) == 4
    assert {case["dataset"] for case in cases} == {"R8", "R12"}
    assert {int(case["case_manifest"]["scene_identity"]["seed"]) for case in cases} == {101, 202, 303, 404}
    assert all(bundle_root in Path(case["case_root"]).parents for case in cases)


def test_packed_r8_seed101_remains_axis_unavailable_and_keeps_its_saved_records():
    cases = {case["case_manifest"]["scene_identity"]["seed"]: case
             for case in discover_historical_cases()}
    case = cases[101]
    assert {condition for condition, role in case["branches"] if role == "ON"} == {
        "A0_Ideal", "A1_Current_unknown_error", "A2_Known_error_correction_upper_bound",
    }
    assert all(
        (Path(branch) / "result/debug/cluster_detection_lineage.csv").exists() is False
        for condition, role, branch in [(c, r, b) for (c, r), b in case["branches"].items()]
    )


def test_compact_input_bundle_matches_its_file_hash_manifest():
    repo = Path(__file__).resolve().parents[1]
    bundle_root = repo / "docs/research/phase_r/R20/evidence/inputs"
    manifest = json.loads((bundle_root / "bundle_manifest.json").read_text(encoding="utf-8"))
    assert manifest["raw_iq_included"] is False
    assert manifest["case_count"] == 4
    assert len(manifest["files"]) == 566

    listed_paths = set()
    for record in manifest["files"]:
        relative = Path(record["bundle_path"])
        assert not relative.is_absolute()
        assert ".." not in relative.parts
        path = bundle_root / relative
        listed_paths.add(relative.as_posix())
        assert path.is_file()
        assert path.stat().st_size == record["size_bytes"]
        digest = hashlib.sha256(path.read_bytes()).hexdigest()
        assert digest == record["bundle_sha256"]

    for record in manifest["excluded_exact_duplicates"]:
        duplicate = record["bundle_path"]
        assert duplicate not in listed_paths
        assert record["duplicate_of"] in listed_paths
