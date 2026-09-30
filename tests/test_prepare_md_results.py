"""Contract tests for the local miaodong JSONL adapter (synthetic data only)."""

import copy
import importlib.util
import json
import math
import contextlib
import io
from pathlib import Path
import stat
import tempfile
import unittest
from unittest import mock


SCRIPT = Path(__file__).resolve().parents[1] / "skills/test-center-analysis/scripts/prepare_md_results.py"
FIXTURE = Path(__file__).resolve().parents[1] / "examples/synthetic-md-results.jsonl"


def load_rows():
    with FIXTURE.open(encoding="utf-8") as stream:
        return [json.loads(line) for line in stream]


class SyntheticMdFixtureTests(unittest.TestCase):
    def test_fixture_is_five_complete_synthetic_rows(self):
        rows = load_rows()
        self.assertEqual(len(rows), 5)
        fields = {"task", "taskName", "name", "caseId", "execId", "scenario", "user", "expect",
                  "passed", "verdict", "reply", "actions", "cost", "ms", "testExecId", "noop",
                  "notRun", "online"}
        for row in rows:
            self.assertEqual(set(row), fields)
            for key in ("task", "caseId", "execId"):
                self.assertTrue(row[key].startswith("example-"))
        self.assertTrue(all(isinstance(row["actions"], str) for row in rows))
        self.assertTrue(all(isinstance(row["online"], str) for row in rows))
        self.assertIsNone(rows[3]["cost"])


class PrepareMdResultsContractTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        if not SCRIPT.exists():
            raise AssertionError("prepare_md_results.py has not been implemented")
        spec = importlib.util.spec_from_file_location("prepare_md_results_under_test", SCRIPT)
        cls.module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(cls.module)

    def test_normalization_maps_status_and_does_not_mutate_rows(self):
        rows = load_rows()
        before = copy.deepcopy(rows)
        dataset = self.module.normalize_md_rows(rows)
        self.assertEqual(rows, before)
        records = dataset["records"]
        category_records = [records[index] for index in (0, 1, 3, 4)]
        self.assertEqual([r["originalPassed"] for r in category_records], [True, False, None, None])
        self.assertEqual([r["completed"] for r in category_records], [True, True, True, False])
        self.assertEqual([r["status"] for r in category_records], ["completed", "completed", "noop", "not_run"])
        self.assertEqual(dataset["source"]["total"], 5)

    def test_actual_context_handoff_scene_duration_and_synthetic_flags(self):
        records = self.module.normalize_md_rows(load_rows())["records"]
        normal = records[0]
        self.assertEqual(normal["id"], "example-test-exec-1")
        self.assertEqual(normal["caseName"], "正常通过")
        self.assertEqual(normal["input"], "查询示例地址")
        self.assertEqual(normal["actual"], {"reply": "已找到示例地址", "actions": "send-text-message;success"})
        self.assertEqual(normal["context"], {})
        self.assertTrue(normal["completed"])
        self.assertEqual(normal["status"], "completed")
        self.assertEqual(normal["durationMs"], 120)
        self.assertIsNone(normal["handoffEvidence"])
        self.assertIsNone(normal["handoff"])
        self.assertEqual(normal["scene"], "地址查询")
        self.assertEqual(normal["assertions"], [{
            "type": "miaodong-test-result", "actualValue": {"reply": "已找到示例地址", "actions": "send-text-message;success"},
            "expectedDescription": "返回示例地址", "llmReason": "通过", "passed": True,
        }])
        self.assertEqual(normal["attributes"], {
            "task": "example-task", "caseId": "example-case-1", "execId": "example-exec-1",
            "testExecId": "example-test-exec-1", "online": "example-online-1", "cost": 0.01,
        })
        noop = records[3]
        self.assertEqual(noop["actual"], {"reply": "", "actions": ""})
        self.assertEqual(noop["status"], "noop")
        self.assertTrue(noop["completed"])
        self.assertIsNone(noop["assertions"][0]["passed"])

    def test_complete_record_contract_and_unknown_fields(self):
        row = {**load_rows()[0], "unknownExampleField": "retained-or-ignored"}
        dataset = self.module.normalize_md_rows([row])
        self.assertEqual(dataset["schemaVersion"], 1)
        self.assertEqual(set(dataset), {"schemaVersion", "source", "records"})
        self.assertEqual(dataset["source"]["total"], 1)
        self.assertTrue(dataset["source"]["complete"])
        self.assertEqual(dataset["source"]["scope"], "task")
        self.assertTrue(dataset["source"]["scopeVerified"])
        self.assertTrue(dataset["source"]["warnings"])
        warnings = " ".join(dataset["source"]["warnings"])
        self.assertIn("JSONL 不含完整多轮上下文", warnings)
        self.assertIn("context 保持空对象", warnings)
        record = dataset["records"][0]
        for key in ("caseName", "input", "assertions", "handoff", "handoffEvidence", "attributes"):
            self.assertIn(key, record)
        assertion = record["assertions"][0]
        for key in ("type", "actualValue", "expectedDescription", "llmReason", "passed"):
            self.assertIn(key, assertion)
        self.assertEqual(assertion["type"], "miaodong-test-result")
        self.assertEqual(assertion["actualValue"], record["actual"])
        self.assertEqual(record["actual"], {"reply": "已找到示例地址", "actions": "send-text-message;success"})
        for key in ("task", "caseId", "execId", "testExecId", "online", "cost"):
            self.assertIn(key, record["attributes"])

    def test_read_jsonl_preserves_raw_bytes_and_empty_reply_actions(self):
        raw = FIXTURE.read_bytes()
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "rows.jsonl"
            path.write_bytes(raw)
            raw_bytes, rows = self.module.read_md_jsonl(path)
            self.assertEqual(raw_bytes, raw)
            self.assertEqual(path.read_bytes(), raw)
            self.assertEqual(rows[3]["reply"], "")
            self.assertEqual(rows[3]["actions"], "")

    def test_jsonl_reader_preserves_unicode_line_separator_inside_strings(self):
        first = {**load_rows()[0], "user": "第一段\u2028第二段", "reply": "回复甲\u2028回复乙"}
        second = {**load_rows()[1], "testExecId": "example-test-exec-u2028"}
        raw = (json.dumps(first, ensure_ascii=False) + "\r\n" +
               json.dumps(second, ensure_ascii=False) + "\n").encode("utf-8")
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "unicode-separator.jsonl"
            path.write_bytes(raw)
            raw_bytes, rows = self.module.read_md_jsonl(path)
        self.assertEqual(raw_bytes, raw)
        self.assertEqual(len(rows), 2)
        self.assertEqual(rows[0]["user"], "第一段\u2028第二段")
        self.assertEqual(rows[0]["reply"], "回复甲\u2028回复乙")
        dataset = self.module.normalize_md_rows(rows)
        self.assertEqual(dataset["records"][0]["input"], "第一段\u2028第二段")
        self.assertEqual(dataset["records"][0]["actual"]["reply"], "回复甲\u2028回复乙")

    def test_valid_exec_id_with_empty_case_id_is_allowed(self):
        row = {**load_rows()[0], "caseId": "", "testExecId": "example-stable-exec"}
        self.assertEqual(len(self.module.normalize_md_rows([row])["records"]), 1)

    def test_single_task_and_required_types_are_validated(self):
        rows = load_rows()
        missing = {**rows[0]}
        del missing["expect"]
        malformed = [
                    [missing],
                    [{**rows[0], "task": "example-other-task", "testExecId": "example-other-test-exec"},
                     {**rows[1], "task": "example-second-task", "testExecId": "example-second-test-exec"}],
                    [{**rows[0], "caseId": "example-case-duplicate", "testExecId": "example-duplicate"},
                     {**rows[0], "caseId": "example-case-duplicate", "testExecId": "example-duplicate"}],
                    [{key: value for key, value in rows[0].items() if key != "testExecId" and key != "caseId"}],
                    [{**rows[0], "testExecId": "", "caseId": ""}],
                    [{**rows[0], "noop": "false"}],
                    [{**rows[0], "notRun": 0}], [{**rows[0], "passed": "true"}],
                    [{**rows[0], "actions": ["not-a-string"]}], [{**rows[0], "ms": -1}],
                    [{**rows[0], "ms": math.inf}], [{**rows[0], "ms": "120"}],
                    [{**rows[0], "ms": float("nan")}], [{**rows[0], "taskName": 3}],
                    [{**rows[0], "user": 3}], [{**rows[0], "expect": 3}],
                    [{**rows[0], "reply": 3}], [{**rows[0], "verdict": 3}],
                    [{**rows[0], "testExecId": 3}], [{**rows[0], "cost": "0.1"}],
                    [{**rows[0], "ms": True}],
                    ]
        for bad in malformed:
            with self.subTest(bad=bad):
                with self.assertRaises(ValueError):
                    self.module.normalize_md_rows(bad)

    def test_arbitrarily_large_nonnegative_integer_ms_is_preserved(self):
        huge_ms = 10 ** 400
        row = {**load_rows()[0], "ms": huge_ms}
        with tempfile.TemporaryDirectory() as directory:
            root, source, output = Path(directory), Path(directory) / "input.jsonl", Path(directory) / "out"
            source.write_text(json.dumps(row, ensure_ascii=False) + "\n", encoding="utf-8")
            self.assertEqual(self.module.main(["--input", str(source), "--output-dir", str(output)]), 0)
            dataset = json.loads((output / "dataset.json").read_text(encoding="utf-8"))
        self.assertEqual(dataset["records"][0]["durationMs"], huge_ms)

    def test_fallback_id_is_stable_and_unique_for_same_case_multiple_rounds(self):
        rows = load_rows()[:2]
        for row in rows:
            row["testExecId"] = ""
            row["caseId"] = "example-same-case"
            row["execId"] = "example-same-exec"
        first = self.module.normalize_md_rows(rows)["records"]
        second = self.module.normalize_md_rows(rows)["records"]
        self.assertEqual([r["id"] for r in first], [r["id"] for r in second])
        self.assertEqual(len({r["id"] for r in first}), 2)
        self.assertTrue(all("example-task" in r["id"] and "example-same-case" in r["id"] for r in first))

    def test_jsonl_reader_rejects_empty_blank_non_object_duplicate_and_invalid_utf8(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "rows.jsonl"
            valid_rows = FIXTURE.read_bytes().splitlines()
            for content in (b"", valid_rows[0] + b"\n\n" + valid_rows[1] + b"\n", b"[]\n", b'{"a":1,"a":2}\n', b"\xff\n"):
                path.write_bytes(content)
                with self.subTest(content=content):
                    with self.assertRaises(ValueError):
                        self.module.read_md_jsonl(path)

    def test_deep_json_is_a_safe_preparation_error_and_cli_leaves_no_outputs(self):
        marker = "example-deep-sensitive-marker"
        raw = ('{"unknown":' + '[' * 2000 + json.dumps(marker) + ']' * 2000 + '}\n').encode("utf-8")
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source, output = root / "deep.jsonl", root / "out"
            source.write_bytes(raw)
            with self.assertRaises(self.module.MdPreparationError) as caught:
                self.module.read_md_jsonl(source)
            self.assertNotIn(marker, str(caught.exception))
            stdout, stderr = io.StringIO(), io.StringIO()
            with contextlib.redirect_stdout(stdout), contextlib.redirect_stderr(stderr):
                result = self.module.main(["--input", str(source), "--output-dir", str(output)])
            emitted = stdout.getvalue() + stderr.getvalue()
            self.assertNotEqual(result, 0)
            self.assertIn("转换失败", emitted)
            self.assertNotIn("Traceback", emitted)
            self.assertNotIn(marker, emitted)
            self.assertFalse((output / "raw-md-results.jsonl").exists())
            self.assertFalse((output / "dataset.json").exists())

    def test_cli_scans_deep_already_parsed_unknown_fields_without_recursion_error(self):
        marker = "example-deep-unknown-marker"
        nested = marker
        for _ in range(1500):
            nested = [nested]
        row = {**load_rows()[0], "unknownExtra": nested}
        with tempfile.TemporaryDirectory() as directory:
            root, output = Path(directory), Path(directory) / "out"
            stdout, stderr = io.StringIO(), io.StringIO()
            with mock.patch.object(self.module, "read_md_jsonl", return_value=(b"synthetic", [row])):
                with contextlib.redirect_stdout(stdout), contextlib.redirect_stderr(stderr):
                    result = self.module.main(["--input", str(root / "input.jsonl"),
                                               "--output-dir", str(output)])
            emitted = stdout.getvalue() + stderr.getvalue()
            self.assertEqual(result, 0)
            self.assertNotIn("Traceback", emitted)
            self.assertNotIn(marker, emitted)
            self.assertTrue((output / "raw-md-results.jsonl").exists())
            self.assertTrue((output / "dataset.json").exists())

    def test_cli_writes_private_outputs_and_refuses_overwrite_or_symlink(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source = root / "input.jsonl"
            source.write_bytes(FIXTURE.read_bytes())
            output = root / "out"
            self.assertEqual(self.module.main(["--input", str(source), "--output-dir", str(output)]), 0)
            for name in ("raw-md-results.jsonl", "dataset.json"):
                target = output / name
                self.assertTrue(target.exists())
                self.assertEqual(stat.S_IMODE(target.stat().st_mode), 0o600)
            self.assertEqual((output / "raw-md-results.jsonl").read_bytes(), FIXTURE.read_bytes())
            self.assertEqual(json.loads((output / "dataset.json").read_text(encoding="utf-8")),
                             self.module.normalize_md_rows(load_rows()))
            self.assertNotEqual(self.module.main(["--input", str(source), "--output-dir", str(output)]), 0)
            independent = root / "independent-empty"
            independent.mkdir()
            link = root / "linked-out"
            link.symlink_to(independent, target_is_directory=True)
            result = self.module.main(["--input", str(source), "--output-dir", str(link)])
            self.assertNotEqual(result, 0)
            self.assertEqual(list(independent.iterdir()), [])

    def test_cli_failure_leaves_no_partial_outputs(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source = root / "bad.jsonl"
            source.write_text("not-json\n", encoding="utf-8")
            output = root / "out"
            result = self.module.main(["--input", str(source), "--output-dir", str(output)])
            self.assertNotEqual(result, 0)
            self.assertFalse((output / "raw-md-results.jsonl").exists())
            self.assertFalse((output / "dataset.json").exists())

    def test_cli_refuses_preoccupied_targets_without_modifying_contents(self):
        with tempfile.TemporaryDirectory() as directory:
            root, source, output = Path(directory), Path(directory) / "input.jsonl", Path(directory) / "out"
            source.write_bytes(FIXTURE.read_bytes())
            for filename in ("raw-md-results.jsonl", "dataset.json"):
                output = root / (filename + ".out")
                output.mkdir()
                target = output / filename
                target.write_text("sentinel-" + filename, encoding="utf-8")
                before = target.read_bytes()
                self.assertNotEqual(self.module.main(["--input", str(source), "--output-dir", str(output)]), 0)
                self.assertEqual(target.read_bytes(), before)

    def test_cli_rejects_each_output_symlink_without_touching_target(self):
        with tempfile.TemporaryDirectory() as directory:
            root, source, output = Path(directory), Path(directory) / "input.jsonl", Path(directory) / "out"
            source.write_bytes(FIXTURE.read_bytes())
            for filename in ("raw-md-results.jsonl", "dataset.json"):
                output = root / (filename + ".out")
                output.mkdir()
                target = root / (filename + ".target")
                target.write_text("symlink-sentinel", encoding="utf-8")
                link = output / filename
                link.symlink_to(target)
                self.assertNotEqual(self.module.main(["--input", str(source), "--output-dir", str(output)]), 0)
                self.assertEqual(target.read_text(encoding="utf-8"), "symlink-sentinel")

    def test_write_outputs_cleans_first_file_when_second_write_fails(self):
        if not hasattr(self.module, "write_outputs"):
            self.fail("prepare_md_results.py must expose write_outputs for atomic cleanup")
        with tempfile.TemporaryDirectory() as directory:
            output = Path(directory)
            dataset = {"schemaVersion": 1, "source": {"total": 0}, "records": []}
            real_open = self.module.os.open
            calls = [0]
            def open_first_then_fail(path, flags, mode=0o777, *, dir_fd=None):
                calls[0] += 1
                if calls[0] == 3:
                    raise OSError("second target")
                return real_open(path, flags, mode, dir_fd=dir_fd)
            with mock.patch.object(self.module.os, "open", side_effect=open_first_then_fail):
                with self.assertRaises(Exception):
                    self.module.write_outputs(output, b"raw", dataset)
            self.assertEqual(calls[0], 3)
            self.assertFalse((output / "raw-md-results.jsonl").exists())
            self.assertFalse((output / "dataset.json").exists())

    def test_write_outputs_cleans_all_files_when_either_file_close_fails(self):
        dataset = {"schemaVersion": 1, "source": {"total": 0}, "records": []}
        for failing_close in (1, 2, 3):
            with self.subTest(failing_close=failing_close), tempfile.TemporaryDirectory() as directory:
                output = Path(directory)
                real_close = self.module.os.close
                calls = [0]

                def close_then_fail(descriptor):
                    calls[0] += 1
                    real_close(descriptor)
                    if calls[0] == failing_close:
                        raise OSError("synthetic close failure")

                with mock.patch.object(self.module.os, "close", side_effect=close_then_fail):
                    with self.assertRaises(OSError):
                        self.module.write_outputs(output, b"raw", dataset)
                self.assertFalse((output / "raw-md-results.jsonl").exists())
                self.assertFalse((output / "dataset.json").exists())

    def test_write_outputs_refuses_missing_posix_safety_capabilities(self):
        dataset = {"schemaVersion": 1, "source": {"total": 0}, "records": []}
        patches = (
            mock.patch.object(self.module.os, "O_DIRECTORY", None),
            mock.patch.object(self.module.os, "O_NOFOLLOW", None),
            mock.patch.object(self.module.os, "fchmod", None),
            mock.patch.object(self.module.os, "supports_dir_fd", set()),
        )
        for index, capability_patch in enumerate(patches):
            with self.subTest(index=index), tempfile.TemporaryDirectory() as directory:
                output = Path(directory) / "out"
                with capability_patch:
                    with self.assertRaises(self.module.MdPreparationError) as caught:
                        self.module.write_outputs(output, b"raw", dataset)
                self.assertIn("当前环境不支持安全输出", str(caught.exception))
                self.assertFalse((output / "raw-md-results.jsonl").exists())
                self.assertFalse((output / "dataset.json").exists())

    def test_write_outputs_cleans_first_file_and_reraises_second_file_interrupt(self):
        dataset = {"schemaVersion": 1, "source": {"total": 0}, "records": []}
        with tempfile.TemporaryDirectory() as directory:
            output = Path(directory)
            real_write = self.module._write_private_file
            calls = [0]

            def write_first_then_interrupt(dir_fd, name, payload):
                calls[0] += 1
                if calls[0] == 2:
                    raise KeyboardInterrupt("synthetic interrupt")
                return real_write(dir_fd, name, payload)

            with mock.patch.object(self.module, "_write_private_file", side_effect=write_first_then_interrupt):
                with self.assertRaises(KeyboardInterrupt):
                    self.module.write_outputs(output, b"raw", dataset)
            self.assertEqual(calls[0], 2)
            self.assertFalse((output / "raw-md-results.jsonl").exists())
            self.assertFalse((output / "dataset.json").exists())

    def test_credentials_are_rejected_without_echoing_secret(self):
        secrets = {
            "authorization": ("Authorization: example-authorization-secret", "example-authorization-secret"),
            "cookie": ("Cookie: example-cookie-secret", "example-cookie-secret"),
            "jwt": ("eyJhbGciOiJub25lIn0" + ".eyJzdWIiOiJleGFtcGxlLXN1YmplY3QifQ.example-jwt-signature", "example-subject"),
            "password": ("password=example-password-secret", "example-password-secret"),
        }
        for label, (secret, payload) in secrets.items():
            for field in ("reply", "taskName", "unknownExtra"):
                row = load_rows()[0]
                row[field] = secret
                with self.subTest(secret=label, field=field):
                    stdout, stderr = io.StringIO(), io.StringIO()
                    with contextlib.redirect_stdout(stdout), contextlib.redirect_stderr(stderr):
                        with self.assertRaises(ValueError) as caught:
                            self.module.normalize_md_rows([row])
                    emitted = stdout.getvalue() + stderr.getvalue()
                    self.assertNotIn(secret, str(caught.exception))
                    self.assertNotIn(payload, str(caught.exception))
                    self.assertNotIn(secret, emitted)
                    self.assertNotIn(payload, emitted)

    def test_cli_rejects_nested_unknown_common_credential_keys_without_leakage(self):
        keys = ("X-API-Key", "X-Auth-Token", "Auth-Token", "Access-Key", "Secret-Key", "Client-Secret")
        marker = "opaque-example-credential-1234"
        for key in keys:
            with self.subTest(key=key), tempfile.TemporaryDirectory() as directory:
                root, source, output = Path(directory), Path(directory) / "input.jsonl", Path(directory) / "out"
                row = {**load_rows()[0], "unknownExtra": {"nested": {key: marker}}}
                source.write_text(json.dumps(row, ensure_ascii=False) + "\n", encoding="utf-8")
                stdout, stderr = io.StringIO(), io.StringIO()
                with contextlib.redirect_stdout(stdout), contextlib.redirect_stderr(stderr):
                    result = self.module.main(["--input", str(source), "--output-dir", str(output)])
                emitted = stdout.getvalue() + stderr.getvalue()
                self.assertNotEqual(result, 0)
                self.assertNotIn(marker, emitted)
                self.assertNotIn("Traceback", emitted)
                self.assertFalse((output / "raw-md-results.jsonl").exists())
                self.assertFalse((output / "dataset.json").exists())

    def test_cli_invalid_arguments_do_not_echo_credentials(self):
        secrets = [
            ("Authorization: example-authorization-secret", "example-authorization-secret"),
            ("Cookie: example-cookie-secret", "example-cookie-secret"),
            ("eyJhbGciOiJub25lIn0" + ".eyJzdWIiOiJleGFtcGxlLXN1YmplY3QifQ.example-jwt-signature", "example-subject"),
            ("password=example-password-secret", "example-password-secret"),
        ]
        for secret, payload in secrets:
            for argv in (["--input", secret, "--output-dir", "example-out"], ["--unknown", secret]):
                stdout, stderr = io.StringIO(), io.StringIO()
                with contextlib.redirect_stdout(stdout), contextlib.redirect_stderr(stderr):
                    result = self.module.main(argv)
                self.assertNotEqual(result, 0)
                emitted = stdout.getvalue() + stderr.getvalue()
                self.assertNotIn(secret, emitted)
                self.assertNotIn(payload, emitted)

    def test_cli_rejects_credential_in_raw_input_without_creating_outputs(self):
        secret = "Cookie: example-cookie-secret"
        with tempfile.TemporaryDirectory() as directory:
            root, source, output = Path(directory), Path(directory) / "credential.jsonl", Path(directory) / "out"
            row = {**load_rows()[0], "taskName": secret}
            source.write_text(json.dumps(row, ensure_ascii=False) + "\n", encoding="utf-8")
            stdout, stderr = io.StringIO(), io.StringIO()
            with contextlib.redirect_stdout(stdout), contextlib.redirect_stderr(stderr):
                result = self.module.main(["--input", str(source), "--output-dir", str(output)])
            self.assertNotEqual(result, 0)
            emitted = stdout.getvalue() + stderr.getvalue()
            self.assertNotIn(secret, emitted)
            self.assertNotIn("example-cookie-secret", emitted)
            self.assertFalse((output / "raw-md-results.jsonl").exists())
            self.assertFalse((output / "dataset.json").exists())


if __name__ == "__main__":
    unittest.main()
