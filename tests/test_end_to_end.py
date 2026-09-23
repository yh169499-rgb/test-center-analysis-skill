"""Exercise the documented local workflow without network or publication."""

import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest


ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = ROOT / "skills" / "test-center-analysis" / "scripts"


class EndToEndTests(unittest.TestCase):
    def test_documented_example_and_adjustment_keep_raw_evidence(self):
        with tempfile.TemporaryDirectory() as directory:
            base = Path(directory)
            data = base / "task-data"
            prepared = subprocess.run([
                sys.executable, str(SCRIPTS / "prepare_results.py"),
                "--input", str(ROOT / "examples/synthetic-results.json"),
                "--output-dir", str(data)
            ], text=True, capture_output=True)
            self.assertEqual(prepared.returncode, 0, prepared.stderr)
            raw_before = (data / "raw-results.json").read_bytes()
            dataset_before = (data / "dataset.json").read_bytes()
            report = base / "report-v1"
            command = [
                sys.executable, str(SCRIPTS / "build_report.py"),
                "--input", str(data / "dataset.json"),
                "--annotations", str(ROOT / "examples/synthetic-annotations.json"),
                "--config", str(ROOT / "examples/report-config.json"),
                "--output-dir", str(report)
            ]
            rendered = subprocess.run(command, text=True, capture_output=True)
            self.assertEqual(rendered.returncode, 0, rendered.stderr)
            summary = json.loads((report / "report-summary.json").read_text(encoding="utf-8"))
            self.assertEqual((summary["total"], summary["eligible"], summary["passed"], summary["failed"]), (5, 4, 3, 1))
            self.assertEqual((summary["groups"], summary["variants"]), (4, 4))
            self.assertEqual(summary["rate"], 0.75)
            self.assertEqual(summary["handoff"]["known"], 3)
            self.assertEqual(summary["handoff"]["unknown"], 1)
            self.assertAlmostEqual(summary["handoff"]["rate"], 1 / 3)
            self.assertEqual(summary["duration"]["meanMs"], 1400)
            content = (report / "report.html").read_text(encoding="utf-8")
            self.assertNotIn("example-run-", content)
            self.assertNotIn("example-org", content)
            self.assertNotIn("example-task", content)
            adjustment = base / "adjustment.json"
            adjustment.write_text(json.dumps({"adjustments": [{
                "recordId": "example-run-2", "reportPassed": True,
                "reason": "合成示例的预期已确认修正", "evidence": "合成用户确认",
                "confirmedByUser": True
            }]}), encoding="utf-8")
            revised = subprocess.run(command[:-1] + [str(base / "report-v2"), "--adjustments", str(adjustment)], text=True, capture_output=True)
            self.assertEqual(revised.returncode, 0, revised.stderr)
            result = json.loads((base / "report-v2/report-summary.json").read_text(encoding="utf-8"))
            self.assertEqual((result["passed"], result["failed"], result["eligible"]), (4, 0, 4))
            self.assertEqual((data / "raw-results.json").read_bytes(), raw_before)
            self.assertEqual((data / "dataset.json").read_bytes(), dataset_before)


if __name__ == "__main__":
    unittest.main()
