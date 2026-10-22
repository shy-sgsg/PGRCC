"""Tests for provenance validation of reused R7 Production runs."""

from __future__ import annotations

import json
import hashlib
import tempfile
import unittest
from pathlib import Path

from scripts.phase_r.run_r7_minimal_production_matrix import (
    validated_existing_run_record,
)


class TestExistingProductionRunManifest(unittest.TestCase):
    def _fixture(self, root: Path, *, exit_code: int = 0, git_commit: str = "f50ce8a"):
        case = root / "case"
        result = case / "result"
        result.mkdir(parents=True)
        config = root / "runtime.xml"
        config.write_text("<runtime/>", encoding="utf-8")
        input_path = root / "period.bin"
        input_path.write_bytes(b"stage2-input")
        generated = {}
        for key, filename in (
            ("gmti_bin", "GMTI01.bin"),
            ("detection_results_csv", "detection_results.csv"),
            ("timing_metrics_csv", "timing_metrics.csv"),
        ):
            output_path = result / filename
            output_path.write_bytes(b"output")
            generated[key] = str(output_path)
        run_manifest = {
            "git_commit": git_commit,
            "build_type": "Release",
            "executable": "GMTI_pipe_core",
            "config_path": str(config),
            "input_data_path": str(input_path),
            "output_dir": str(result),
            "normal_exit": exit_code == 0,
            "exit_code": exit_code,
            "generated_files": generated,
        }
        manifest_path = result / "run_manifest.json"
        manifest_path.write_text(json.dumps(run_manifest), encoding="utf-8")
        return manifest_path, config, input_path, result

    def test_missing_historical_binary_hash_is_not_inferred_from_current_binary(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            manifest_path, config, input_path, result_dir = self._fixture(root)

            record = validated_existing_run_record(
                manifest_path,
                config,
                input_path,
                result_dir,
                "f50ce8a1ceb5ecbe15e903fd4eb946d4b1c1af86",
                root,
            )

        self.assertEqual(record["status"], "EXECUTED")
        self.assertEqual(record["binary_identity_status"], "NOT_RECORDED_IN_RUN_MANIFEST")
        self.assertIsNone(record["binary_sha256"])
        self.assertEqual(record["input_sha256"], hashlib.sha256(b"stage2-input").hexdigest())

    def test_failed_run_manifest_is_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            manifest_path, config, input_path, result_dir = self._fixture(root, exit_code=1)

            with self.assertRaisesRegex(ValueError, "did not exit cleanly"):
                validated_existing_run_record(
                    manifest_path, config, input_path, result_dir,
                    "f50ce8a1ceb5ecbe15e903fd4eb946d4b1c1af86", root,
                )

    def test_wrong_commit_is_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            manifest_path, config, input_path, result_dir = self._fixture(root, git_commit="deadbeef")

            with self.assertRaisesRegex(ValueError, "source revision differs"):
                validated_existing_run_record(
                    manifest_path, config, input_path, result_dir,
                    "f50ce8a1ceb5ecbe15e903fd4eb946d4b1c1af86", root,
                )


if __name__ == "__main__":
    unittest.main()
