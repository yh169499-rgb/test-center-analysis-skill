"""Public package boundaries; all fixtures are synthetic."""

import json
import re
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SKILL = ROOT / "skills" / "test-center-analysis"


class PackageTests(unittest.TestCase):
    def test_reference_links_exist(self):
        for document in [SKILL / "SKILL.md", ROOT / "README.md"]:
            for target in re.findall(r"\]\(([^)]+)\)", document.read_text(encoding="utf-8")):
                if "://" not in target and not target.startswith("#"):
                    self.assertTrue((document.parent / target).is_file(), target)

    def test_public_files_do_not_embed_production_credentials(self):
        patterns = [
            r"eyJ[A-Za-z0-9_-]{24,}\.[A-Za-z0-9_-]{24,}\.[A-Za-z0-9_-]{16,}",
            r"gh[pousr]_[A-Za-z0-9]{30,}",
            r"/Users/[^/\s]+/",
        ]
        candidates = [ROOT / "README.md", *SKILL.rglob("*.md"), *SKILL.rglob("*.yaml"), *SKILL.rglob("*.py"), * (ROOT / "examples").glob("*.json")]
        for document in candidates:
            text = document.read_text(encoding="utf-8")
            for pattern in patterns:
                self.assertIsNone(re.search(pattern, text), f"unsafe public content in {document.name}")

    def test_no_original_screenshots_or_browser_dumps(self):
        for path in ROOT.rglob("*"):
            if ".git" not in path.parts:
                self.assertNotIn(path.suffix.lower(), {".png", ".jpg", ".jpeg", ".har"})

    def test_synthetic_example_is_self_contained(self):
        payload = json.loads((ROOT / "examples" / "synthetic-results.json").read_text(encoding="utf-8"))
        self.assertEqual(payload["page"]["total"], len(payload["data"]))
        ids = {record["testTaskItemId"] for record in payload["data"]}
        self.assertEqual(len(ids), len(payload["data"]))
        self.assertTrue(all(item.startswith("example-") for item in ids))
        annotations = json.loads((ROOT / "examples" / "synthetic-annotations.json").read_text(encoding="utf-8"))
        self.assertTrue(all(item["recordId"] in ids for item in annotations["annotations"]))


if __name__ == "__main__":
    unittest.main()
