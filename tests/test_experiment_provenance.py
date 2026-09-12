import hashlib
import tempfile
import unittest
from pathlib import Path

from scripts.experiment_provenance import run_provenance_post, run_provenance_start


class ExperimentProvenanceTests(unittest.TestCase):
    def test_start_records_config_sha_and_separate_post_state(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            config = Path(tmp) / "config.json"
            config.write_text('{"ai_training": false}\n', encoding="utf-8")
            start = run_provenance_start(Path.cwd(), (config,))
            expected = hashlib.sha256(config.read_bytes()).hexdigest()
            self.assertIn(str(config.resolve()), start["config_sha256"])
            self.assertEqual(start["config_sha256"][str(config.resolve())], expected)
            self.assertIn("source_worktree_dirty_before", start)
            post = run_provenance_post(Path.cwd(), start)
            self.assertEqual(post["source_commit_at_start"], start["source_commit"])
            self.assertIn("source_worktree_dirty_after", post)


if __name__ == "__main__":
    unittest.main()
