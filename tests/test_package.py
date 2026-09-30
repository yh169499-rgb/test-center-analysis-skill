"""Public package boundaries; all fixtures are synthetic."""

import json
import re
import subprocess
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SKILL = ROOT / "skills" / "test-center-analysis"
PUBLIC_FILES = [
    ROOT / relative
    for relative in subprocess.check_output(
        ["git", "ls-files", "-z"], cwd=ROOT
    ).decode("utf-8").split("\0")
    if relative
]


def public_file_findings(text):
    """Return high-confidence private-data markers found in public package text."""
    patterns = {
        "production domain": r"(?i)(?:[a-z0-9-]+\.)*(?:juzibot\.com|yanfeibot\.com)",
        "uuid": r"(?i)\b[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}\b",
        "jwt": r"\beyJ[A-Za-z0-9_-]{5,}\.[A-Za-z0-9_-]{5,}\.[A-Za-z0-9_-]{5,}\b",
        "github token": r"\b(?:gh[pousr]_[A-Za-z0-9]{20,}|github_pat_[A-Za-z0-9_]{20,})\b",
        "home path": r"/Users/" r"[^/\s]+/",
    }
    findings = [label for label, pattern in patterns.items() if re.search(pattern, text)]
    header_kinds = r"(?:proxy-authorization|authorization|set-cookie|cookie)"
    quoted = re.compile(
        rf'''(?ix)["'](?P<key>{header_kinds})["']\s*:\s*(?P<quote>["'])(?P<value>.*?)(?P=quote)'''
    )
    plain = re.compile(
        rf'''(?im)(?:^|["'])\s*(?P<key>{header_kinds})\s*:\s*(?P<value>[^"'\r\n]*)'''
    )
    seen_labels = set()
    for match in [*quoted.finditer(text), *plain.finditer(text)]:
        value = match.group("value").strip().lower()
        if not value or value in {"null", "none"}:
            continue
        explicit_synthetic = value.startswith(("example-", "synthetic-", "<redacted>", "[redacted]"))
        real_style = re.search(r"\b(?:basic|bearer|live)\b", value)
        if explicit_synthetic and not real_style:
            continue
        label = "cookie" if "cookie" in match.group("key").lower() else "authorization"
        if label not in seen_labels:
            findings.append(label)
            seen_labels.add(label)
    return findings


class PackageTests(unittest.TestCase):
    def test_reference_links_exist(self):
        for document in [SKILL / "SKILL.md", ROOT / "README.md"]:
            for target in re.findall(r"\]\(([^)]+)\)", document.read_text(encoding="utf-8")):
                if "://" not in target and not target.startswith("#"):
                    self.assertTrue((document.parent / target).is_file(), target)

    def test_public_files_do_not_embed_production_credentials(self):
        self.assertIn(
            ROOT / "docs/superpowers/plans/2026-09-29-miaodong-test-report-integration.md",
            PUBLIC_FILES,
        )
        self.assertIn(
            ROOT / "docs/superpowers/specs/2026-09-29-miaodong-test-report-integration-design.md",
            PUBLIC_FILES,
        )
        self.assertIn(SKILL / "scripts" / "prepare_md_results.py", PUBLIC_FILES)
        self.assertIn(ROOT / "examples" / "synthetic-md-results.jsonl", PUBLIC_FILES)
        for document in PUBLIC_FILES:
            try:
                text = document.read_text(encoding="utf-8")
            except UnicodeDecodeError:
                continue
            self.assertEqual(public_file_findings(text), [], f"unsafe public content in {document}")

    def test_public_file_scan_rejects_private_markers(self):
        unsafe_samples = {
            "production domain": "https://customer-insight." + "juzi" + "bot.com/test/task",
            "uuid": "123e4567" + "-e89b-42d3-a456-426614174000",
            "jwt": "eyJhbGciOiJIUzI1NiJ9" + ".eyJzdWIiOiJsaXZlLXVzZXIifQ.c2lnbmF0dXJlLXZhbHVl",
            "github token": "ghp_" + "ABCDEFGHIJKLMNOPQRSTUVWXYZ1234567890",
            "home path": "/Users/" + "alice/private/results.jsonl",
            "plain auth header": "Authorization" + ": Bearer live-secret-token",
            "plain cookie header": "Cookie" + ": session=live-secret-value",
            "json authorization": '{"' + "Authorization" + '":"' + "Bearer live-secret-token" + '"}',
            "python authorization": "{'" + "Authorization" + "': '" + "Bearer live-secret-token" + "'}",
            "comment bypass": "Authorization" + ": Bearer live-secret-token # example-value",
            "json cookie": '{"' + "Cookie" + '":"' + "session=live-secret-value" + '"}',
        }
        for label, text in unsafe_samples.items():
            with self.subTest(label=label):
                self.assertTrue(public_file_findings(text), label)

    def test_public_file_scan_allows_documentation_terms_and_synthetic_values(self):
        safe_text = """
        Authorization 和 Cookie 是安全术语；JWT 与 GitHub token 不得上传。
        Authorization: example-authorization-value
        Cookie: synthetic-cookie=value
        {"Authorization": "example-json-value", "Cookie": "synthetic-cookie=value"}
        https://console.example.invalid/example-task
        task=example-task caseId=example-case execId=example-online
        """
        self.assertEqual(public_file_findings(safe_text), [])

    def test_no_original_screenshots_or_browser_dumps(self):
        for path in PUBLIC_FILES:
            self.assertNotIn(
                path.suffix.lower(),
                {
                    ".png", ".jpg", ".jpeg", ".gif", ".webp", ".bmp", ".tif", ".tiff",
                    ".svg", ".avif", ".heic", ".heif", ".ico", ".har",
                },
            )

    def test_synthetic_example_is_self_contained(self):
        payload = json.loads((ROOT / "examples" / "synthetic-results.json").read_text(encoding="utf-8"))
        self.assertEqual(payload["page"]["total"], len(payload["data"]))
        ids = {record["testTaskItemId"] for record in payload["data"]}
        self.assertEqual(len(ids), len(payload["data"]))
        self.assertTrue(all(item.startswith("example-") for item in ids))
        annotations = json.loads((ROOT / "examples" / "synthetic-annotations.json").read_text(encoding="utf-8"))
        self.assertTrue(all(item["recordId"] in ids for item in annotations["annotations"]))

        md_rows = [
            json.loads(line)
            for line in (ROOT / "examples" / "synthetic-md-results.jsonl").read_text(encoding="utf-8").splitlines()
        ]
        for row in md_rows:
            for key in ("task", "caseId", "execId"):
                self.assertTrue(row[key].startswith("example-"), (key, row[key]))
            self.assertTrue(not row["testExecId"] or row["testExecId"].startswith("example-"))
            self.assertTrue(row["online"].startswith("example-online-"), row["online"])
        self.assertEqual(len({row["online"] for row in md_rows}), len(md_rows))

    def test_skill_routes_miaodong_test_tasks_to_dedicated_workflow(self):
        skill = (SKILL / "SKILL.md").read_text(encoding="utf-8")
        self.assertIn("references/miaodong-workflow.md", skill)
        self.assertIn("智能体 + 测试集", skill)
        self.assertIn("智能体 + 任务", skill)
        self.assertIn("Request URL + Authorization", skill)
        self.assertIn("完整本地 JSON", skill)

    def test_miaodong_workflow_preserves_cli_safety_boundaries(self):
        workflow = (SKILL / "references" / "miaodong-workflow.md").read_text(encoding="utf-8")
        required = [
            '"<MD_BIN_ABS>" test run',
            '"<MD_BIN_ABS>" test status',
            '"<MD_BIN_ABS>" test results',
            "--deep",
            "--out",
            "prepare_md_results.py",
            '"<MD_BIN_ABS>" auth snippet',
            '"<MD_BIN_ABS>" auth import',
            "退出码 3",
            "退出码 5",
            "--allow-preflight-errors",
            "确认码",
            "空跑",
            "单任务",
            "给客户还是给同事看",
            "login",
            "open",
            "public",
        ]
        for text in required:
            self.assertIn(text, workflow)

        self.assertRegex(workflow, r"超时[\s\S]{0,120}同一任务")
        self.assertRegex(workflow, r"任务对比[\s\S]{0,120}md")
        self.assertRegex(workflow, r"报告[\s\S]{0,80}不等于[\s\S]{0,80}发布")

    def test_miaodong_data_contract_documents_complete_field_mapping(self):
        contract = (SKILL / "references" / "data-contract.md").read_text(encoding="utf-8")
        required = [
            "`task` + `testExecId`",
            "`testExecId` 优先",
            "task + caseId + 同任务稳定序号",
            "`name` → `caseName`",
            "`user` → `input`",
            "`reply` 与 `actions`",
            "`expectedDescription`",
            "`llmReason`",
            "`originalPassed`",
            "`attributes`",
        ]
        for text in required:
            self.assertIn(text, contract)

    def test_miaodong_workflow_resolves_scripts_from_absolute_skill_directory(self):
        workflow = (SKILL / "references" / "miaodong-workflow.md").read_text(encoding="utf-8")
        required = [
            "<SKILL_DIR_ABS>",
            "<RESULTS_DIR_ABS>",
            "<SKILL_DIR_ABS>/scripts/prepare_md_results.py",
            "<SKILL_DIR_ABS>/scripts/build_report.py",
            "绝对路径",
            "不依赖当前工作目录",
        ]
        for text in required:
            self.assertIn(text, workflow)
        self.assertNotIn("python3 scripts/prepare_md_results.py", workflow)
        self.assertNotIn("python3 scripts/build_report.py", workflow)

    def test_miaodong_workflow_resolves_and_uses_literal_md_path_safely(self):
        workflow = (SKILL / "references" / "miaodong-workflow.md").read_text(encoding="utf-8")
        required = [
            "command -v md",
            '"$HOME/.local/bin/md"',
            "MD_BIN",
            '"$MD_BIN" --version',
            "退出码",
            "以 `md ` 开头",
            "已解析的字面绝对路径",
            '"<MD_BIN_ABS>" auth snippet',
            '"<MD_BIN_ABS>" auth import',
            '"<MD_BIN_ABS>" test run',
            '"<MD_BIN_ABS>" test status',
            '"<MD_BIN_ABS>" test results',
        ]
        for text in required:
            self.assertIn(text, workflow)

    def test_miaodong_workflow_commands_do_not_depend_on_previous_shell_state(self):
        workflow = (SKILL / "references" / "miaodong-workflow.md").read_text(encoding="utf-8")
        shell_blocks = re.findall(r"```bash\n(.*?)```", workflow, flags=re.DOTALL)
        self.assertGreaterEqual(len(shell_blocks), 5)

        # The first block may use MD_BIN locally while discovering it. Every later
        # command is an independent invocation and must carry resolved paths as
        # literal placeholders, never assume a previous shell variable survives.
        for block in shell_blocks[1:]:
            self.assertIsNone(
                re.search(r"\$(?:MD_BIN|SKILL_DIR|RESULTS_DIR|TEST_CENTER_ANALYSIS_[A-Z_]+)\b", block),
                block,
            )

        after_discovery = workflow.split("```", 2)[2]
        self.assertNotIn("$MD_BIN", after_discovery)
        self.assertNotIn("$SKILL_DIR", after_discovery)
        self.assertNotIn("$RESULTS_DIR", after_discovery)
        self.assertIn("不依赖跨命令", after_discovery)

    def test_uncertain_task_creation_uses_read_only_status_query(self):
        workflow = (SKILL / "references" / "miaodong-workflow.md").read_text(encoding="utf-8")
        self.assertIn('"<MD_BIN_ABS>" test status --bot <智能体>', workflow)
        self.assertRegex(workflow, r"状态不确定[\s\S]{0,260}不重复执行")

    def test_readme_builds_each_preparation_output_explicitly(self):
        readme = (ROOT / "README.md").read_text(encoding="utf-8")
        required = [
            'REPO_ROOT_ABS="$(pwd -P)"',
            "API_DATA_DIR",
            "MD_DATA_DIR",
            'prepare_results.py" --input "$REPO_ROOT_ABS/examples/synthetic-results.json" --output-dir "$API_DATA_DIR"',
            'prepare_md_results.py" --input "$REPO_ROOT_ABS/examples/synthetic-md-results.jsonl" --output-dir "$MD_DATA_DIR"',
            'build_report.py" --input "$API_DATA_DIR/dataset.json"',
            'build_report.py" --input "$MD_DATA_DIR/dataset.json"',
        ]
        for text in required:
            self.assertIn(text, readme)
        self.assertNotIn("--input examples/", readme)

    def test_openai_interface_covers_scoped_test_run_and_takeover(self):
        metadata = (SKILL / "agents" / "openai.yaml").read_text(encoding="utf-8")
        for text in ["运行或接管", "秒懂测试任务", "生成", "报告", "$test-center-analysis"]:
            self.assertIn(text, metadata)
        self.assertNotIn("任意测试", metadata)

    def test_readme_explains_optional_dependencies_and_safe_authorization(self):
        readme = (ROOT / "README.md").read_text(encoding="utf-8")
        for text in [
            "miaodong-cli",
            "html-gallery",
            "md test run",
            "Authorization",
            "命令历史",
            "给客户还是给同事看",
        ]:
            self.assertIn(text, readme)


if __name__ == "__main__":
    unittest.main()
