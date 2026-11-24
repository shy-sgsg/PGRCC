from __future__ import annotations

import hashlib
import json
import stat
import warnings
import zipfile
from pathlib import Path

import pytest

from scripts.phase_r.r22_v4_p0_compact_archive import (
    build_p0_compact_archive,
    sha256_file,
    verify_p0_compact_archive,
)


CASE_ROOT = "pipeline/cases/case-1"
TEST_METHODS = (
    "A0", "C0", "PK", "R22_R9_Fixed_D3", "HUBER_D3_CAUSAL_PREFIX", "M1_CAUSAL_PREFIX",
)
REQUIRED = [f"{CASE_ROOT}/case_manifest.json", "r22_run_manifest.json"]
for _method in TEST_METHODS:
  for _role in ("ON", "OFF", "TO"):
    _base = f"{CASE_ROOT}/production/{_method}_{_role}/"
    REQUIRED.extend([
        _base + "branch_manifest.json", _base + "production.xml",
        _base + "gmt_pipe_core.log", _base + "result/debug/cfar_geometry_diagnostics.csv",
    ])
    REQUIRED.extend(_base + f"result/detection_results_GMTI{period:02d}.csv" for period in range(1, 11))
    REQUIRED.extend(
        _base + "track_debug_runs/run/" + filename
        for filename in (
            "track_association_accepts.csv", "track_detections.csv", "track_events.csv",
            "track_frames.csv", "track_output_payloads.csv", "track_points.csv",
            "track_results.csv", "track_states.csv",
        )
    )
REQUIRED.extend([
    f"{CASE_ROOT}/scenes/A0_ON/truth/truth_targets_by_beam.csv",
    f"{CASE_ROOT}/r22_v4_output_evidence/target_complex_response.csv",
    f"{CASE_ROOT}/r22_v4_output_evidence/target_complex_response_manifest.json",
    f"{CASE_ROOT}/r22_v4_output_evidence/target_roi_manifest.csv",
    f"{CASE_ROOT}/r22_v4_output_evidence/adaptive_role_difference_diagnostic.csv",
    f"{CASE_ROOT}/r22_v4_output_evidence/truth_mapping_audit.json",
])
EXTRA = [
    "evaluation/doppler_opportunity_audit.csv",
    "evaluation/h0_false_alarm_metrics.csv",
    "evaluation/h0_task_metrics.csv",
    "evaluation/parameter_metrics.csv",
    "evaluation/target_associations.csv",
    "evaluation/target_level_metrics.csv",
    "evaluation/task_metrics.csv",
    "evaluation/track_level_metrics.csv",
]


def _source_archive(path: Path, *, required: list[str] | None = None) -> tuple[str, str]:
    manifest = {
        "archive_profile": "r22_v4_output_complete",
        "required_v4_members": required or REQUIRED,
        "included_files": [],
        "excluded_files": [],
        "symlink_entries": [],
    }
    values = {
        f"{CASE_ROOT}/case_manifest.json": b'{"case_id":"case-1"}\n',
        "r22_run_manifest.json": (
            b'{"case_id":"case-1","phase":"engineering","design":'
            b'{"seed":1,"radial_speed_mps":2.5,"target_snr_db":20,'
            b'"delay_error_ns":4,"target_count":20,"period_count":10}}\n'
        ),
    }
    for name in EXTRA:
        values[name] = b"field,value\nstatus,ok\n"
    values["evaluation/target_associations.csv"] = b"target_id,matched\nt1,true\n"
    for method in TEST_METHODS:
      for role in ("ON", "OFF", "TO"):
        branch = f"{method}_{role}"
        base = f"{CASE_ROOT}/production/{branch}/"
        values[base + "branch_manifest.json"] = b"{}\n"
        values[base + "production.xml"] = b"<runtime/>\n"
        values[base + "gmt_pipe_core.log"] = b"production completed\n"
        values[base + "result/debug/cfar_geometry_diagnostics.csv"] = b"valid_cut_count\n1\n"
        for period in range(1, 11):
            values[base + f"result/detection_results_GMTI{period:02d}.csv"] = b"detection_id\n"
        for filename in (
            "track_association_accepts.csv", "track_detections.csv", "track_events.csv",
            "track_frames.csv", "track_output_payloads.csv", "track_points.csv",
            "track_results.csv", "track_states.csv",
        ):
            values[base + f"track_debug_runs/run/{filename}"] = b"field\n"
    values.update({
        f"{CASE_ROOT}/scenes/A0_ON/truth/truth_targets_by_beam.csv": b"target_id\nt1\n",
        f"{CASE_ROOT}/r22_v4_output_evidence/target_complex_response.csv": b"target_id,real,imag\nt1,1,0\n",
        f"{CASE_ROOT}/r22_v4_output_evidence/target_complex_response_manifest.json": b'{"status":"EVALUABLE"}\n',
        f"{CASE_ROOT}/r22_v4_output_evidence/target_roi_manifest.csv": b"target_id,range_bin\nt1,10\n",
        f"{CASE_ROOT}/r22_v4_output_evidence/adaptive_role_difference_diagnostic.csv": b"target_id,residual\nt1,0.1\n",
        f"{CASE_ROOT}/r22_v4_output_evidence/truth_mapping_audit.json": b'{"status":"passed"}\n',
    })
    with zipfile.ZipFile(path, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        for name, data in values.items():
            archive.writestr(name, data)
        archive.writestr("ARCHIVE_MANIFEST.json", json.dumps(manifest).encode())
    with zipfile.ZipFile(path) as archive:
        source_manifest_sha = hashlib.sha256(archive.read("ARCHIVE_MANIFEST.json")).hexdigest()
    return sha256_file(path), source_manifest_sha


def _build(tmp_path: Path) -> tuple[Path, Path, dict]:
    source = tmp_path / "source.zip"
    compact = tmp_path / "compact.zip"
    source_sha, source_manifest_sha = _source_archive(source)
    result = build_p0_compact_archive(
        source, compact,
        expected_source_sha256=source_sha,
        expected_source_manifest_sha256=source_manifest_sha,
        additional_reanalysis_members=EXTRA,
    )
    return source, compact, result


def test_compact_archive_is_standalone_hashed_and_does_not_change_source(tmp_path: Path):
    source, compact, result = _build(tmp_path)
    source_sha_before = sha256_file(source)
    verified = verify_p0_compact_archive(compact)

    assert source_sha_before == sha256_file(source)
    assert result["required_v4_member_count"] == len(REQUIRED)
    assert result["additional_reanalysis_member_count"] == len(EXTRA)
    assert verified["status"] == "PASS"
    assert verified["source_archive_path_recorded"] is False
    assert verified["zip_crc_verified"] is True
    assert verified["member_sha256_verified"] == len(REQUIRED) + len(EXTRA)
    with zipfile.ZipFile(compact) as archive:
        manifest = json.loads(archive.read("P0_COMPACT_MANIFEST.json"))
        assert manifest["source_archive_identity"]["sha256"] == source_sha_before
        assert set(archive.namelist()) == {*REQUIRED, *EXTRA, "P0_COMPACT_MANIFEST.json"}


def test_builder_refuses_missing_required_members_and_keeps_source(tmp_path: Path):
    source = tmp_path / "source.zip"
    compact = tmp_path / "compact.zip"
    source_sha, source_manifest_sha = _source_archive(source, required=REQUIRED + ["missing.csv"])

    with pytest.raises(ValueError, match="required source members are missing"):
        build_p0_compact_archive(
            source, compact,
            expected_source_sha256=source_sha,
            expected_source_manifest_sha256=source_manifest_sha,
            additional_reanalysis_members=EXTRA,
        )
    assert source.is_file()
    assert not compact.exists()


def test_verifier_detects_truncated_zip_and_missing_member(tmp_path: Path):
    _, compact, _ = _build(tmp_path)
    truncated = tmp_path / "truncated.zip"
    truncated.write_bytes(compact.read_bytes()[:-24])
    with pytest.raises((ValueError, zipfile.BadZipFile), match="compact ZIP|central directory|File is not a zip file"):
        verify_p0_compact_archive(truncated)

    missing = tmp_path / "missing.zip"
    with zipfile.ZipFile(compact) as src, zipfile.ZipFile(missing, "w") as dst:
        for name in src.namelist():
            if name != EXTRA[0]:
                dst.writestr(name, src.read(name))
    with pytest.raises(ValueError, match="member inventory|missing compact members"):
        verify_p0_compact_archive(missing)


def test_verifier_detects_duplicate_members_crc_damage_and_expected_sha_mismatch(tmp_path: Path):
    _, compact, _ = _build(tmp_path)
    with zipfile.ZipFile(compact) as archive:
        payload = archive.read(EXTRA[0])
        all_members = [(name, archive.read(name)) for name in archive.namelist()]

    duplicate = tmp_path / "duplicate.zip"
    with warnings.catch_warnings():
        warnings.simplefilter("ignore", UserWarning)
        with zipfile.ZipFile(duplicate, "w") as archive:
            for name, data in all_members:
                archive.writestr(name, data)
            archive.writestr(EXTRA[0], payload)
    with pytest.raises(ValueError, match="duplicate member"):
        verify_p0_compact_archive(duplicate)

    damaged = tmp_path / "damaged.zip"
    damaged.write_bytes(compact.read_bytes())
    with damaged.open("r+b") as stream:
        with zipfile.ZipFile(compact) as archive:
            info = archive.getinfo(EXTRA[0])
        data_offset = info.header_offset + 30 + len(info.filename.encode()) + len(info.extra)
        stream.seek(data_offset)
        stream.write(bytes([payload[0] ^ 1]))
    with pytest.raises(ValueError, match="CRC|SHA|corrupt|Bad CRC"):
        verify_p0_compact_archive(damaged)

    with pytest.raises(ValueError, match="compact archive SHA-256 mismatch"):
        verify_p0_compact_archive(compact, expected_archive_sha256="0" * 64)


def test_verifier_rejects_unsafe_member_paths_and_symlinks(tmp_path: Path):
    source = tmp_path / "unsafe-source.zip"
    manifest = {
        "archive_profile": "r22_v4_output_complete",
        "required_v4_members": ["../escape.txt"],
        "included_files": [], "excluded_files": [], "symlink_entries": [],
    }
    with zipfile.ZipFile(source, "w") as archive:
        archive.writestr("../escape.txt", b"bad")
        archive.writestr("ARCHIVE_MANIFEST.json", json.dumps(manifest))
    source_sha = sha256_file(source)
    with zipfile.ZipFile(source) as archive:
        source_manifest_sha = hashlib.sha256(archive.read("ARCHIVE_MANIFEST.json")).hexdigest()
    with pytest.raises(ValueError, match="unsafe archive member"):
        build_p0_compact_archive(
            source, tmp_path / "unsafe-compact.zip",
            expected_source_sha256=source_sha,
            expected_source_manifest_sha256=source_manifest_sha,
            additional_reanalysis_members=[],
        )

    symlink = tmp_path / "symlink.zip"
    with zipfile.ZipFile(symlink, "w") as archive:
        info = zipfile.ZipInfo("payload.txt")
        info.create_system = 3
        info.external_attr = (stat.S_IFLNK | 0o777) << 16
        archive.writestr(info, b"target")
    with pytest.raises(ValueError, match="manifest missing|manifest|symlink"):
        verify_p0_compact_archive(symlink)
