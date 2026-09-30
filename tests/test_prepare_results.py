"""Synthetic-only tests for acquisition and normalization."""

import contextlib
import copy
import importlib.util
import io
import json
import os
from pathlib import Path
import stat
import tempfile
import unittest
from unittest import mock
from urllib.error import HTTPError, URLError
from urllib.parse import parse_qs, urlsplit
from urllib.request import Request


SCRIPT = Path(__file__).resolve().parents[1] / "skills/test-center-analysis/scripts/prepare_results.py"
URL = "https://example.invalid/api/test-center/test-task-item/list?testTaskId=synthetic-task&orgId=synthetic-org"


def item(identifier="synthetic-execution", **fields):
    record = {"testTaskItemId": identifier, "testTaskId": "synthetic-task", "orgId": "synthetic-org",
              "testCaseName": "合成测试", "status": "success", "passed": True,
              "triggerContent": {"content": {"text": "完整输入\n第二行"}},
              "canvasActionOutputAssertionResult": [{"type": "send-text-message", "actualValue": "完整输出",
                                                      "expectedDescription": "示例预期", "passed": True}]}
    record.update(fields)
    return record


def payload(records=None, total=None, current=1):
    records = [item()] if records is None else records
    return {"code": 0, "data": records, "page": {"total": len(records) if total is None else total,
                                                  "current": current, "pageSize": 200}}


class PrepareResultsTests(unittest.TestCase):
    def setUp(self):
        self.assertTrue(SCRIPT.exists(), "prepare_results.py has not been implemented")
        spec = importlib.util.spec_from_file_location("prepare_results_under_test", SCRIPT)
        self.module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(self.module)

    def normalize(self, record=None, source_url=URL):
        return self.module.normalize_payload(payload([record or item()]), source_url=source_url)

    def test_text_input_and_single_actual_preserve_original_values(self):
        value = {"content": "unchanged", "attachments": [{"type": "file", "name": "sample.pdf"}]}
        source = item(canvasActionOutputAssertionResult=[{"type": "send-combination-message", "actualValue": value}])
        result = self.normalize(source)
        self.assertEqual(result["schemaVersion"], 1)
        self.assertEqual(result["records"][0]["input"], "完整输入\n第二行")
        self.assertEqual(result["records"][0]["actual"], value)
        self.assertTrue(result["source"]["complete"])
        self.assertTrue(result["source"]["scopeVerified"])

    def test_all_assertions_and_ordered_actions_are_preserved(self):
        assertions = [{"type": "send-text-message", "actualValue": "first", "llmReason": "reason"},
                      {"type": "send-file-message", "actualValue": {"file": "example.pdf"}},
                      {"type": "handoff", "expectedDescription": "预期不等于实际"}]
        result = self.normalize(item(canvasActionOutputAssertionResult=assertions))["records"][0]
        self.assertEqual(result["assertions"], assertions)
        self.assertEqual(result["actual"], [{"type": "send-text-message", "actualValue": "first"},
                                            {"type": "send-file-message", "actualValue": {"file": "example.pdf"}},
                                            {"type": "handoff", "actualValue": None}])
        self.assertIsNone(result["handoff"])

    def test_non_text_and_multi_input_content_are_not_flattened(self):
        for content in ({"text": "caption", "file": "example.pdf"}, [{"text": "a"}, {"image": "sample.png"}],
                        {"type": "image", "text": "caption", "url": "https://example.invalid/sample.png"}):
            with self.subTest(content=content):
                source = item(triggerContent={"content": content})
                self.assertEqual(self.normalize(source)["records"][0]["input"], content)
        inputs = [{"type": "text", "text": "one"}, {"type": "file", "name": "example.pdf"}]
        source = item(triggerInputs=inputs)
        del source["triggerContent"]
        self.assertEqual(self.normalize(source)["records"][0]["input"], inputs)

    def test_both_input_fields_and_multi_turn_context_are_retained(self):
        source = item(triggerInputs={"customer": "synthetic"}, sessionMemoryCustomData={"previous": ["turn 1"]},
                      history=[{"role": "user", "content": "earlier"}], businessContext={"channel": "demo"},
                      context={"explicit": "context"})
        result = self.normalize(source)["records"][0]
        self.assertEqual(result["input"], {"triggerContent": "完整输入\n第二行", "triggerInputs": {"customer": "synthetic"}})
        for key in ("sessionMemoryCustomData", "history", "businessContext", "context"):
            self.assertEqual(result["context"][key], source[key])

    def test_null_and_non_boolean_verdicts_never_become_false(self):
        for value in (None, "false", "true", 0, 1, [], {}):
            with self.subTest(value=value):
                self.assertIsNone(self.normalize(item(passed=value))["records"][0]["originalPassed"])
        self.assertIs(self.normalize(item(passed=False))["records"][0]["originalPassed"], False)

    def test_completion_is_conservative_and_reports_unknown_status(self):
        for status in ("success", "completed", "finished", "done", "error", "failed", "timeout"):
            with self.subTest(status=status):
                self.assertIs(self.normalize(item(status=status))["records"][0]["completed"], True)
        for status in ("pending", "running", "queued"):
            with self.subTest(status=status):
                self.assertIs(self.normalize(item(status=status))["records"][0]["completed"], False)
        result = self.normalize(item(status="unexpected", passed=True))
        self.assertIsNone(result["records"][0]["completed"])
        self.assertTrue(result["source"]["warnings"])
        source = item()
        del source["status"]
        self.assertIs(self.normalize(source)["records"][0]["completed"], True)
        source["passed"] = None
        self.assertIsNone(self.normalize(source)["records"][0]["completed"])

    def test_no_assertion_uses_only_explicit_text_fallback(self):
        self.assertEqual(self.normalize(item(canvasActionOutputAssertionResult=[], text="原始回复"))["records"][0]["actual"], "原始回复")
        self.assertIsNone(self.normalize(item(canvasActionOutputAssertionResult=[]))["records"][0]["actual"])

    def test_scene_duration_and_default_attributes_do_not_invent_evidence(self):
        source = item(scenarioName="演示场景", duration=3.2, passed=False, handoff=True, text="转人工")
        record = self.normalize(source)["records"][0]
        self.assertEqual(record["scene"], "演示场景")
        self.assertIsNone(record["handoff"])
        self.assertIsNone(record["handoffEvidence"])
        self.assertIsNone(record["durationMs"])
        self.assertEqual(record["attributes"], {})
        for duration in (0, 12.5):
            self.assertEqual(self.normalize(item(durationMs=duration))["records"][0]["durationMs"], duration)
        for duration in (-1, True, "25", float("inf")):
                self.assertIsNone(self.normalize(item(durationMs=duration))["records"][0]["durationMs"])

    def test_known_scenario_path_is_preserved(self):
        record = self.normalize(item(scenarioPath="业务 / 示例场景"))["records"][0]
        self.assertEqual(record["scene"], "业务 / 示例场景")

    def test_nonempty_whitespace_filter_is_not_silently_dropped(self):
        with self.assertRaisesRegex(ValueError, "清.*筛选"):
            self.module.validate_source_url(URL + "&keyword=%20")

    def test_missing_remote_current_cannot_verify_pagination(self):
        response = payload()
        del response["page"]["current"]
        with self.assertRaises(ValueError):
            self.module.fetch_all(URL, "Bearer synthetic-token", request_json=lambda request: response)

    def test_synthetic_example_remains_complete_with_all_scenes(self):
        example = SCRIPT.parents[3] / "examples/synthetic-results.json"
        with example.open(encoding="utf-8") as stream:
            result = self.module.normalize_payload(json.load(stream))
        self.assertEqual(len(result["records"]), 5)
        self.assertEqual({record["scene"] for record in result["records"]}, {"地址查询", "人工处理", "其他"})
        self.assertEqual(sum(record["completed"] is True for record in result["records"]), 4)
        self.assertEqual(set(result), {"schemaVersion", "source", "records"})

    def test_normalization_does_not_mutate_input_or_expose_whole_metadata(self):
        original = payload([item(privateMetadata={"internal": "private"})])
        before = copy.deepcopy(original)
        result = self.module.normalize_payload(original, source_url=URL)
        self.assertEqual(original, before)
        self.assertNotIn("privateMetadata", result["records"][0])
        self.assertNotIn("raw", result["records"][0])

    def test_local_complete_payload_requires_exact_total_unique_nonempty_ids(self):
        bad = [payload(total=2), payload(total=True), payload(total=-1), payload(total="1"),
               payload([item(), item()]), payload([item("")]), payload([item(None)])]
        for candidate in bad:
            with self.subTest(candidate=candidate):
                with self.assertRaises(ValueError):
                    self.module.normalize_payload(candidate)

    def test_payload_structure_and_response_code_are_validated(self):
        for candidate in ([], {}, {"code": 1, "data": [], "page": {"total": 0}},
                          {"code": True, "data": [], "page": {"total": 0}},
                          {"data": [], "page": {"total": 0}}, payload(["not-an-object"])):
            with self.subTest(candidate=candidate):
                with self.assertRaises(ValueError):
                    self.module.normalize_payload(candidate)

    def test_scope_mismatch_and_mixed_scope_are_rejected(self):
        for field in ("orgId", "testTaskId"):
            with self.subTest(field=field):
                with self.assertRaises(ValueError):
                    self.normalize(item(**{field: "another-synthetic-scope"}))
                with self.assertRaises(ValueError):
                    self.module.normalize_payload(payload([item("one"), item("two", **{field: "another-scope"})]))
        candidate = payload()
        candidate["orgId"] = "wrong-envelope-scope"
        with self.assertRaises(ValueError):
            self.module.normalize_payload(candidate, source_url=URL)

    def test_absent_scope_fields_remain_explicitly_unverified(self):
        source = item()
        del source["orgId"]
        result = self.normalize(source)
        self.assertFalse(result["source"]["scopeVerified"])
        self.assertTrue(result["source"]["warnings"])
        self.assertFalse(self.normalize(source_url=None)["source"]["scopeVerified"])

    def test_empty_complete_export_is_valid_but_not_scope_verified(self):
        result = self.module.normalize_payload(payload([]), source_url=URL)
        self.assertEqual(result["records"], [])
        self.assertEqual(result["source"]["total"], 0)
        self.assertFalse(result["source"]["scopeVerified"])

    def test_url_validation_rejects_unsafe_or_ambiguous_targets(self):
        candidates = [URL.replace("https:", "http:"), URL.replace("example.invalid", "user:secret@example.invalid"),
                      URL + "&orgId=second", URL + "&testTaskId=second", URL.replace("orgId=synthetic-org", "orgId="),
                      URL.replace("&orgId=synthetic-org", ""), URL + "#fragment", URL.replace("/list?", "/other?"),
                      URL.replace("example.invalid", "example.invalid:bad"), URL + "\r\n"]
        for candidate in candidates:
            with self.subTest(candidate=candidate):
                with self.assertRaises(ValueError):
                    self.module.validate_source_url(candidate)

    def test_filtered_url_is_refused_with_clear_remedy(self):
        for suffix in ("&passed=false", "&status=failed", "&keyword=test", "&token=do-not-log"):
            with self.subTest(suffix=suffix):
                with self.assertRaisesRegex(ValueError, "清.*筛选"):
                    self.module.validate_source_url(URL + suffix)

    def test_empty_unknown_query_fields_are_removed_and_pagination_is_reset(self):
        requests = []
        def requester(request):
            requests.append(request)
            return payload()
        self.module.fetch_all(URL + "&current=9&pageSize=1&passed=", "Bearer synthetic-token", request_json=requester)
        query = parse_qs(urlsplit(requests[0].full_url).query)
        self.assertEqual(query["current"], ["1"])
        self.assertEqual(query["pageSize"], ["200"])
        self.assertNotIn("passed", query)

    def test_pagination_collects_all_pages_and_preserves_original_responses(self):
        requests = []
        pages = [payload([item("one")], 2, 1), payload([item("two")], 2, 2)]
        def requester(request):
            requests.append(request)
            return pages[len(requests) - 1]
        result = self.module.fetch_all(URL, "Bearer synthetic-token", request_json=requester)
        self.assertEqual([r["testTaskItemId"] for r in result["data"]], ["one", "two"])
        self.assertEqual(result["pages"], pages)
        self.assertEqual(result["page"]["total"], 2)
        self.assertEqual([parse_qs(urlsplit(r.full_url).query)["current"] for r in requests], [["1"], ["2"]])
        self.assertTrue(all(r.get_header("Authorization") == "Bearer synthetic-token" for r in requests))
        self.assertTrue(all(urlsplit(r.full_url).netloc == "example.invalid" for r in requests))

    def test_pagination_rejects_changed_total_early_empty_duplicate_and_wrong_current(self):
        first = payload([item("one")], 2, 1)
        second_pages = [payload([item("two")], 3, 2), payload([], 2, 2), payload([item("one")], 2, 2),
                        payload([item("two")], 2, 1), payload([item("two")], True, 2),
                        payload([item("two"), item("three")], 2, 2)]
        for second in second_pages:
            with self.subTest(second=second):
                iterator = iter([first, second])
                with self.assertRaises(ValueError):
                    self.module.fetch_all(URL, "Bearer synthetic-token", request_json=lambda request: next(iterator))

    def test_scope_conflicts_on_later_pages_are_rejected(self):
        iterator = iter([payload([item("one")], 2, 1), payload([item("two", orgId="another")], 2, 2)])
        with self.assertRaises(ValueError):
            self.module.fetch_all(URL, "Bearer synthetic-token", request_json=lambda request: next(iterator))

    def test_credentials_cannot_be_empty_or_inject_headers(self):
        for value in ("", " ", "Bearer sample\nX-Other: injected", "Bearer sample\rX-Other: injected"):
            with self.subTest(value=value):
                with self.assertRaises(ValueError):
                    self.module.fetch_all(URL, value, request_json=lambda request: self.fail("must not send"))

    def test_transport_uses_timeout_and_disables_all_redirects(self):
        response = mock.MagicMock()
        response.__enter__.return_value = io.StringIO(json.dumps(payload()))
        opener = mock.Mock()
        opener.open.return_value = response
        with mock.patch.object(self.module, "build_opener", return_value=opener) as build:
            self.module._default_request_json(Request(
                URL, headers={"Author" + "ization": "Bearer " + "synthetic-token"}
            ))
        self.assertEqual(opener.open.call_args.kwargs["timeout"], 30)
        handler = build.call_args.args[0]
        handler = handler() if isinstance(handler, type) else handler
        self.assertIsNone(handler.redirect_request(Request(URL), None, 302, "redirect", {}, "https://other.invalid"))

    def test_network_error_and_http_error_never_echo_sensitive_details(self):
        for error in (URLError("Bearer secret URL scope details"),
                      HTTPError(URL, 302, "secret response", {}, None),
                      json.JSONDecodeError("secret response", "secret body", 0)):
            with self.subTest(error=type(error).__name__):
                with self.assertRaises(ValueError) as caught:
                    self.module.fetch_all(URL, "Bearer secret", request_json=mock.Mock(side_effect=error))
                self.assertNotIn("secret", str(caught.exception))
                self.assertNotIn("synthetic-task", str(caught.exception))

    def test_expired_or_forbidden_authorization_has_safe_actionable_message(self):
        for status in (401, 403):
            with self.subTest(status=status):
                error = HTTPError(URL, status, "secret response", {}, None)
                with self.assertRaisesRegex(ValueError, "授权过期或权限不足.*重取当前任务授权") as caught:
                    self.module.fetch_all(URL, "Bearer secret", request_json=mock.Mock(side_effect=error))
                self.assertNotIn("secret", str(caught.exception))

    def test_local_cli_outputs_two_private_files_and_no_scope_identifiers(self):
        with tempfile.TemporaryDirectory() as tmp:
            incoming = Path(tmp) / "input.json"
            incoming.write_text(json.dumps(payload(), ensure_ascii=False), encoding="utf-8")
            output = Path(tmp) / "private-output"
            stdout, stderr = io.StringIO(), io.StringIO()
            with contextlib.redirect_stdout(stdout), contextlib.redirect_stderr(stderr):
                status = self.module.main(["--input", str(incoming), "--source-url", URL, "--output-dir", str(output)])
            self.assertEqual(status, 0, stderr.getvalue())
            self.assertEqual(json.loads((output / "raw-results.json").read_text()), payload())
            self.assertTrue(json.loads((output / "dataset.json").read_text())["source"]["scopeVerified"])
            for name in ("raw-results.json", "dataset.json"):
                self.assertEqual(stat.S_IMODE((output / name).stat().st_mode), 0o600)
            self.assertNotIn(URL, stdout.getvalue() + stderr.getvalue())
            self.assertNotIn("synthetic-task", stdout.getvalue() + stderr.getvalue())

    def test_existing_either_output_refuses_all_writes_and_symlinks(self):
        for name in ("raw-results.json", "dataset.json"):
            with self.subTest(name=name), tempfile.TemporaryDirectory() as tmp:
                incoming = Path(tmp) / "input.json"
                incoming.write_text(json.dumps(payload()), encoding="utf-8")
                output = Path(tmp) / "out"
                output.mkdir()
                occupied = output / name
                occupied.write_text("existing", encoding="utf-8")
                with contextlib.redirect_stderr(io.StringIO()):
                    status = self.module.main(["--input", str(incoming), "--output-dir", str(output)])
                self.assertNotEqual(status, 0)
                self.assertEqual(occupied.read_text(), "existing")
                self.assertEqual(len(list(output.iterdir())), 1)
                occupied.unlink()
                occupied.symlink_to(Path(tmp) / "nonexistent-target")
                with contextlib.redirect_stderr(io.StringIO()):
                    status = self.module.main(["--input", str(incoming), "--output-dir", str(output)])
                self.assertNotEqual(status, 0)
                self.assertTrue(occupied.is_symlink())
                self.assertEqual(len(list(output.iterdir())), 1)

    def test_failed_validation_leaves_no_outputs(self):
        with tempfile.TemporaryDirectory() as tmp:
            incoming = Path(tmp) / "input.json"
            incoming.write_text(json.dumps(payload(total=2)), encoding="utf-8")
            output = Path(tmp) / "out"
            with contextlib.redirect_stderr(io.StringIO()):
                self.assertNotEqual(self.module.main(["--input", str(incoming), "--output-dir", str(output)]), 0)
            self.assertFalse((output / "raw-results.json").exists())
            self.assertFalse((output / "dataset.json").exists())

    def test_authorization_stdin_env_and_tty_are_supported_without_echo(self):
        for mode in ("stdin", "env", "tty"):
            with self.subTest(mode=mode), tempfile.TemporaryDirectory() as tmp:
                stdin = io.StringIO("Bearer synthetic-token\n" if mode == "stdin" else "")
                stdin.isatty = lambda: mode == "tty"
                env = {"TEST_CENTER_AUTHORIZATION": "Bearer synthetic-token"} if mode == "env" else {}
                args = ["--url", URL, "--output-dir", str(Path(tmp) / "out")]
                if mode == "stdin":
                    args.append("--authorization-stdin")
                stdout, stderr = io.StringIO(), io.StringIO()
                with mock.patch.dict(os.environ, env, clear=True), mock.patch.object(self.module.sys, "stdin", stdin), \
                     mock.patch.object(self.module.getpass, "getpass", return_value="Bearer synthetic-token"), \
                     mock.patch.object(self.module, "_default_request_json", return_value=payload()), \
                     contextlib.redirect_stdout(stdout), contextlib.redirect_stderr(stderr):
                    self.assertEqual(self.module.main(args), 0, stderr.getvalue())
                self.assertNotIn("synthetic-token", stdout.getvalue() + stderr.getvalue())

    def test_cli_error_does_not_print_token_response_or_identifiers(self):
        with tempfile.TemporaryDirectory() as tmp, mock.patch.dict(os.environ, {"TEST_CENTER_AUTHORIZATION": "Bearer secret"}, clear=True):
            stdout, stderr = io.StringIO(), io.StringIO()
            with mock.patch.object(self.module, "_default_request_json", side_effect=RuntimeError("secret response synthetic-task")), \
                 contextlib.redirect_stdout(stdout), contextlib.redirect_stderr(stderr):
                status = self.module.main(["--url", URL, "--output-dir", tmp])
            self.assertNotEqual(status, 0)
            self.assertNotIn("secret", stdout.getvalue() + stderr.getvalue())
            self.assertNotIn("synthetic-task", stdout.getvalue() + stderr.getvalue())


if __name__ == "__main__":
    unittest.main()
