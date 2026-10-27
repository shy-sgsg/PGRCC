import json
import math
from pathlib import Path

import pytest

from scripts.phase_r import run_r22_cuda_pilot as r22


def test_r22_case_identifier_encodes_physical_factors_and_sample_scale():
    value = r22.case_identifier("confirmatory", 222001, 0.5, 20.0, 4.0, 20, 10)

    assert value == (
        "confirmatory__seed_222001__vr_0d5__snr_20__delay_4ns__targets_20__periods_10"
    )


def test_run_case_refuses_to_overwrite_existing_output(tmp_path: Path):
    root = tmp_path / "existing"
    root.mkdir()
    (root / "keep.txt").write_text("existing", encoding="utf-8")

    with pytest.raises(ValueError, match="refusing to overwrite"):
        r22.run_case(
            root,
            phase="engineering",
            seed=220001,
            speed_mps=2.5,
            snr_db=20.0,
            delay_ns=4.0,
            target_count=20,
            period_count=5,
            first_beam=50,
            beam_count=1,
            stage2_binary=tmp_path / "missing_stage2",
            production_binary=tmp_path / "missing_production",
        )
    assert (root / "keep.txt").read_text(encoding="utf-8") == "existing"


def test_r22_json_manifest_normalizes_nonfinite_values_and_paths(tmp_path: Path):
    destination = tmp_path / "manifest.json"

    r22.write_json(destination, {"missing": math.nan, "path": tmp_path})
    loaded = json.loads(destination.read_text(encoding="utf-8"))

    assert loaded == {"missing": None, "path": str(tmp_path)}


def test_r22_attaches_production_evidence_gates_before_aggregating_branch_status(monkeypatch):
    def audit(record):
        return {"status": "passed", "branch": record["method_id"]}

    monkeypatch.setattr(r22.stage1, "audit_production_path_evidence", audit)
    branches = [
        {"method_id": "C0", "role": "ON", "status": "passed", "production_returncode": 0},
        {"method_id": "Known", "role": "ALL", "status": "NOT_AVAILABLE"},
    ]

    audited = r22._audit_production_branches(branches)

    assert audited[0]["production_path_gate"] == {"status": "passed", "branch": "C0"}
    assert audited[1] == branches[1]
    assert r22._all_production_branches_passed(audited)


def test_r22_rejects_any_production_branch_with_failed_evidence_gate(monkeypatch):
    def audit(record):
        return {"status": "failed", "reasons": ["runtime_xml_missing"]}

    monkeypatch.setattr(r22.stage1, "audit_production_path_evidence", audit)
    branches = [{"method_id": "C0", "role": "ON", "status": "passed", "production_returncode": 0}]

    audited = r22._audit_production_branches(branches)

    assert not r22._all_production_branches_passed(audited)
