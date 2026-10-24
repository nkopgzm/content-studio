"""Independent CLI regressions for the new-report-only output contract."""

import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest


PLUGIN_ROOT = Path(__file__).resolve().parents[1]
SCRIPT = PLUGIN_ROOT / "skills" / "content-studio" / "scripts" / "audit_content.py"


class OutputSafetyCLI(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="content-output-safety-")
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.input_path = self.root / "input.json"
        self.input_bytes = json.dumps({
            "profile": {"min_visual_changes": 0},
            "posts": [{"id": "new-draft", "body": "A useful draft for a local test."}],
        }, ensure_ascii=False).encode("utf-8")
        self.input_path.write_bytes(self.input_bytes)

    def run_cli(self, output_path):
        return subprocess.run(
            [sys.executable, str(SCRIPT), "--input", str(self.input_path),
             "--output", str(output_path)],
            cwd=str(self.root),
            capture_output=True,
            text=True,
            encoding="utf-8",
            timeout=20,
            check=False,
        )

    def test_existing_file_is_rejected_without_changing_any_bytes(self):
        output_path = self.root / "prior-report.json"
        original = b"original-report\x00\xff\r\nKEEP THIS EVIDENCE\r\n"
        output_path.write_bytes(original)

        result = self.run_cli(output_path)

        self.assertEqual(result.returncode, 2, result.stdout + result.stderr)
        self.assertEqual(output_path.read_bytes(), original)
        self.assertEqual(self.input_path.read_bytes(), self.input_bytes)
        self.assertIn("error", json.loads(result.stdout))
        self.assertEqual(sorted(p.name for p in self.root.iterdir()),
                         ["input.json", "prior-report.json"])

    def test_existing_directory_is_rejected_and_contents_remain(self):
        output_path = self.root / "reports"
        output_path.mkdir()
        marker_path = output_path / "keep.txt"
        marker = b"pre-existing directory contents\r\n"
        marker_path.write_bytes(marker)

        result = self.run_cli(output_path)

        self.assertEqual(result.returncode, 2, result.stdout + result.stderr)
        self.assertTrue(output_path.is_dir())
        self.assertEqual(marker_path.read_bytes(), marker)
        self.assertEqual(self.input_path.read_bytes(), self.input_bytes)
        self.assertIn("error", json.loads(result.stdout))
        self.assertEqual(sorted(p.name for p in self.root.iterdir()),
                         ["input.json", "reports"])

    def test_new_report_is_created_and_matches_stdout(self):
        output_path = self.root / "new-report.json"
        self.assertFalse(output_path.exists())

        result = self.run_cli(output_path)

        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertTrue(output_path.is_file())
        report = json.loads(output_path.read_text(encoding="utf-8"))
        self.assertEqual(report, json.loads(result.stdout))
        self.assertTrue(report["summary"]["passed"])
        self.assertEqual(report["summary"]["error_count"], 0)
        self.assertEqual(self.input_path.read_bytes(), self.input_bytes)
        self.assertEqual(sorted(p.name for p in self.root.iterdir()),
                         ["input.json", "new-report.json"])


if __name__ == "__main__":
    unittest.main()
