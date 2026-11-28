from __future__ import annotations

import csv
import importlib.util
import json
from pathlib import Path

import pytest


ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts" / "collect_formal_evidence.py"
SPEC = importlib.util.spec_from_file_location("collect_formal_evidence", SCRIPT)
if SPEC is None or SPEC.loader is None:
    raise RuntimeError(f"cannot load {SCRIPT}")
COLLECTOR = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(COLLECTOR)


def _write_csv(path: Path, rows: list[dict[str, object]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8", newline="") as stream:
        writer = csv.DictWriter(stream, fieldnames=list(rows[0]))
        writer.writeheader()
        writer.writerows(rows)


def _source_roots(tmp_path: Path) -> dict[str, Path]:
    roots = {
        "geometry": tmp_path / "geometry",
        "geometry_nuisance": tmp_path / "geometry_nuisance",
        "e2e": tmp_path / "e2e",
        "deadband": tmp_path / "deadband",
        "target_transfer": tmp_path / "target_transfer",
        "pfa": tmp_path / "pfa",
        "servo": tmp_path / "servo",
        "information_matched": tmp_path / "information_matched",
        "velocity": tmp_path / "velocity",
        "attitude": tmp_path / "attitude",
        "track": tmp_path / "track",
    }
    _write_csv(
        roots["geometry"] / "matrix_summary.csv",
        [{"condition": "zero", "estimate_m": "0.0"}],
    )
    _write_csv(
        roots["geometry_nuisance"] / "matrix_rows.csv",
        [{"profile": "range_9000", "estimate_m": "0.0"}],
    )
    _write_csv(
        roots["e2e"] / "production_metrics.csv",
        [{"branch": "Current", "pd": "0.0"}],
    )
    _write_csv(
        roots["e2e"] / "recovery_metrics.csv",
        [{"branch": "Known", "recovery": "0.0"}],
    )
    _write_csv(
        roots["deadband"] / "servo_decisions.csv",
        [{"label": "zero", "status": "NO_CORRECTION_NEEDED"}],
    )
    _write_csv(
        roots["target_transfer"] / "target_only_transfer.csv",
        [{"branch": "Current", "pd": "0.0"}],
    )
    _write_csv(
        roots["pfa"] / "pfa_summary.csv",
        [{"hypothesis": "H0", "empirical_cell_pfa": "0.0"}],
    )
    _write_csv(
        roots["servo"] / "servo_estimates.csv",
        [{"branch": "S0", "estimate_deg": "0.0"}],
    )
    _write_csv(
        roots["servo"] / "servo_decisions.csv",
        [{"branch": "S0", "action": "KEEP_CURRENT"}],
    )
    _write_csv(
        roots["information_matched"] / "information_matched_pairwise_aggregate.csv",
        [{"pair": "J4-J2", "delta": "0.0"}],
    )
    _write_csv(
        roots["velocity"] / "velocity_estimates.csv",
        [{"branch": "V1", "status": "FALLBACK"}],
    )
    _write_csv(
        roots["velocity"] / "velocity_decisions.csv",
        [{"branch": "V1", "decision": "KEEP_CURRENT"}],
    )
    _write_csv(
        roots["attitude"] / "attitude_estimates.csv",
        [{"branch": "A1", "status": "FALLBACK"}],
    )
    _write_csv(
        roots["attitude"] / "attitude_decisions.csv",
        [{"branch": "A1", "decision": "KEEP_CURRENT"}],
    )
    _write_csv(
        roots["track"] / "track_branch_metrics.csv",
        [{"branch": "Current", "track_pd_all_visible": "1.0"}],
    )
    _write_csv(
        roots["track"] / "track_protocol_payload_audit.csv",
        [{"branch": "Current", "eligible": "1", "matched_truth": "1"}],
    )
    (roots["track"] / "manifest.json").write_text(
        json.dumps({
            "status": "completed",
            "input_mode": "shm",
            "period_count": 3,
            "seeds": [101],
            "input_rate_bytes_per_sec": 1000000,
            "ai_training": False,
            "router_enabled": False,
            "branch_contract": {"branches": ["Current"]},
            "cases": [{
                "seed": 101,
                "branches": [{
                    "branch": "Current",
                    "production_status": "passed",
                    "target_status": "positive",
                    "metrics": {
                        "track_audit_status": "pass",
                        "track_audit_total_violations": 0,
                    },
                    "pipe_runtime_metrics": {"status": "passed"},
                }],
            }],
        }),
        encoding="utf-8",
    )
    (roots["servo"] / "raw_payload.bin").write_bytes(b"raw evidence must not be copied")
    return roots


def test_collect_formal_evidence_writes_exact_required_files(tmp_path: Path) -> None:
    result = COLLECTOR.collect_formal_evidence(
        repository_root=ROOT,
        output_root=tmp_path / "formal_evidence",
        source_roots=_source_roots(tmp_path),
    )

    assert result["manifest"]["raw_artifacts_excluded"] is True
    assert result["manifest"]["source_commit"]
    assert sorted(path.name for path in (tmp_path / "formal_evidence").iterdir()) == [
        "attitude_decisions.csv",
        "attitude_estimates.csv",
        "correction_decisions.csv",
        "information_matched_pairwise_aggregate.csv",
        "manifest.json",
        "pfa_summary.csv",
        "servo_decisions.csv",
        "servo_estimates.csv",
        "summary.csv",
        "target_transfer.csv",
        "track_branch_metrics.csv",
        "track_contract.json",
        "track_protocol_payload_audit.csv",
        "velocity_decisions.csv",
        "velocity_estimates.csv",
    ]
    assert not (tmp_path / "formal_evidence" / "raw_payload.bin").exists()
    manifest = json.loads(
        (tmp_path / "formal_evidence" / "manifest.json").read_text(encoding="utf-8")
    )
    assert manifest["schema"] == "unknown_system_error_formal_evidence_v1"
    assert manifest["artifact_count"] == 15


def test_collect_formal_evidence_rejects_unmapped_or_missing_source(tmp_path: Path) -> None:
    with pytest.raises(ValueError, match="explicit source mapping"):
        COLLECTOR.collect_formal_evidence(
            repository_root=ROOT,
            output_root=tmp_path / "formal_evidence",
            source_roots={"unknown": tmp_path / "does_not_exist"},
        )


def test_collect_formal_evidence_refuses_overwrite(tmp_path: Path) -> None:
    roots = _source_roots(tmp_path)
    output = tmp_path / "formal_evidence"
    output.mkdir()
    (output / "existing.txt").write_text("preserve", encoding="utf-8")
    with pytest.raises(FileExistsError):
        COLLECTOR.collect_formal_evidence(
            repository_root=ROOT,
            output_root=output,
            source_roots=roots,
        )
