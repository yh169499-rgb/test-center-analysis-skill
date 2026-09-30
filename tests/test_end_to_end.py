"""Exercise the documented local workflow without network or publication."""

import ast
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest


ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = ROOT / "skills" / "test-center-analysis" / "scripts"


class EndToEndTests(unittest.TestCase):
    def test_md_report_pipeline_scripts_have_no_network_dependencies(self):
        forbidden_roots = {"aiohttp", "http", "httpx", "requests", "socket", "urllib"}
        for script_name in ("prepare_md_results.py", "build_report.py"):
            tree = ast.parse((SCRIPTS / script_name).read_text(encoding="utf-8"))
            imported_roots = {
                alias.name.split(".", 1)[0]
                for node in ast.walk(tree)
                if isinstance(node, (ast.Import, ast.ImportFrom))
                for alias in (node.names if isinstance(node, ast.Import) else [ast.alias(name=node.module or "")])
            }
            self.assertTrue(forbidden_roots.isdisjoint(imported_roots), (script_name, imported_roots))

    def test_synthetic_md_results_preserve_outcomes_without_private_ids(self):
        fixture = ROOT / "examples/synthetic-md-results.jsonl"
        source_rows = [json.loads(line) for line in fixture.read_text(encoding="utf-8").splitlines()]
        private_online_values = [row["online"] for row in source_rows]
        self.assertEqual(private_online_values, [f"example-online-{number}" for number in range(1, 6)])

        with tempfile.TemporaryDirectory() as directory:
            base = Path(directory)
            data = base / "md-data"
            report = base / "md-report"
            prepared = subprocess.run([
                sys.executable, str(SCRIPTS / "prepare_md_results.py"),
                "--input", str(fixture),
                "--output-dir", str(data),
            ], text=True, capture_output=True)
            self.assertEqual(prepared.returncode, 0, prepared.stderr)
            rendered = subprocess.run([
                sys.executable, str(SCRIPTS / "build_report.py"),
                "--input", str(data / "dataset.json"),
                "--output-dir", str(report),
            ], text=True, capture_output=True)
            self.assertEqual(rendered.returncode, 0, rendered.stderr)

            summary = json.loads((report / "report-summary.json").read_text(encoding="utf-8"))
            self.assertEqual(
                {key: summary[key] for key in (
                    "total", "eligible", "passed", "failed", "noop", "notRun", "groups", "variants"
                )},
                {
                    "total": 5, "eligible": 3, "passed": 2, "failed": 1,
                    "noop": 1, "notRun": 1, "groups": 3, "variants": 5,
                },
            )

            content = (report / "report.html").read_text(encoding="utf-8")
            for business_text in (
                "已找到两个示例地址",
                "跳过的示例输入",
                "尚未运行的示例输入",
            ):
                self.assertIn(business_text, content)
            for private_id in ("example-test-exec", "example-case"):
                self.assertNotIn(private_id, content)
            for private_online_value in private_online_values:
                self.assertNotIn(private_online_value, content)

    def test_documented_md_prepare_and_build_smoke(self):
        readme_lines = (ROOT / "README.md").read_text(encoding="utf-8").splitlines()
        prefixes = (
            'REPO_ROOT_ABS=',
            'EXAMPLE_ROOT=',
            'MD_DATA_DIR=',
            'MD_REPORT_DIR=',
        )
        commands = [
            line for line in readme_lines
            if line.startswith(prefixes)
            or "scripts/prepare_md_results.py" in line
            or ("scripts/build_report.py" in line and '"$MD_DATA_DIR/dataset.json"' in line)
        ]
        self.assertEqual(sum("prepare_md_results.py" in line for line in commands), 1)
        self.assertEqual(sum("build_report.py" in line for line in commands), 1)

        with tempfile.TemporaryDirectory() as directory:
            environment = os.environ.copy()
            environment["TMPDIR"] = directory + os.sep
            result = subprocess.run(
                ["sh", "-eu", "-c", "\n".join(commands)],
                cwd=ROOT,
                env=environment,
                text=True,
                capture_output=True,
            )
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertEqual(len(list(Path(directory).rglob("raw-md-results.jsonl"))), 1)
            self.assertEqual(len(list(Path(directory).rglob("dataset.json"))), 1)
            self.assertEqual(len(list(Path(directory).rglob("report.html"))), 1)
            self.assertEqual(len(list(Path(directory).rglob("report-summary.json"))), 1)

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
