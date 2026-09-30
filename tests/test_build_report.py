"""Synthetic-only integration checks for the public report renderer."""
import copy
import html
import importlib.util
import json
from pathlib import Path
import stat
import subprocess
import sys
import tempfile
import unittest


SCRIPT = Path(__file__).resolve().parents[1] / "skills/test-center-analysis/scripts/build_report.py"


def record(number=1, passed=True, **overrides):
    value = {
        "id": f"private-execution-{number}", "caseName": f"案例 {number}",
        "input": "原始输入", "context": {}, "actual": "原始输出",
        "assertions": [{"type": "send-text-message", "actualValue": "原始输出",
                        "expectedDescription": "对应期待", "llmReason": "对应原因", "passed": passed}],
        "originalPassed": passed, "status": "success", "completed": True,
        "scene": "场景 A", "handoff": None, "handoffEvidence": None,
        "durationMs": None, "attributes": {},
    }
    value.update(overrides)
    return value


def dataset(*records):
    return {"schemaVersion": 1, "source": {"complete": True, "total": len(records), "scope": "task"},
            "records": list(records)}


class BuildReportTests(unittest.TestCase):
    def setUp(self):
        self.assertTrue(SCRIPT.exists(), "The report renderer must exist")
        spec = importlib.util.spec_from_file_location("build_report", SCRIPT)
        self.module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(self.module)

    def build(self, data, **kwargs):
        return self.module.build_report(data, **kwargs)

    def rejects(self, data, **kwargs):
        with self.assertRaises(ValueError):
            self.build(data, **kwargs)

    def test_exact_input_and_context_grouping_and_mixed_variants(self):
        data = dataset(record(1), record(2, False), record(3, None),
                       record(4, context={"turn": 1}), record(5, input="原始输入 "),
                       record(6, actual="不同实际输出"))
        content, summary = self.build(data)
        self.assertEqual((summary["total"], summary["eligible"], summary["passed"], summary["failed"]), (6, 5, 4, 1))
        self.assertEqual((summary["groups"], summary["variants"]), (3, 4))
        counts = summary["groupCounts"][0]
        self.assertEqual((counts["executions"], counts["passed"], counts["failed"], counts["excluded"]), (4, 2, 1, 1))
        first = counts["variantCounts"][0]
        self.assertEqual((first["executions"], first["passed"], first["failed"], first["excluded"]), (3, 1, 1, 1))
        self.assertIn("混合判定", content)
        self.assertEqual(sum(g["executions"] for g in summary["groupCounts"]), summary["total"])

    def test_adjustments_500_executions_six_confirmed_changes(self):
        data = dataset(*(record(i, i < 491) for i in range(500)))
        original = copy.deepcopy(data)
        adjustments = {"adjustments": [{"recordId": f"private-execution-{i}", "reportPassed": True,
                                        "reason": "预期需要修正", "evidence": "用户确认规则", "confirmedByUser": True}
                                       for i in range(491, 497)]}
        content, summary = self.build(data, adjustments=adjustments)
        self.assertEqual((summary["total"], summary["eligible"], summary["passed"], summary["failed"]), (500, 500, 497, 3))
        self.assertEqual(summary["rate"], 497 / 500)
        self.assertEqual(summary["original"]["passed"], 491)
        self.assertEqual(summary["adjustmentCount"], 6)
        self.assertEqual(data, original)
        self.assertIn("预期需要修正", content)

    def test_unknown_completion_judgment_and_errors_are_separate(self):
        _, s = self.build(dataset(record(1, False), record(2, None), record(3, False, completed=False),
                                  record(4, False, completed=None), record(5, None, completed=False, status="error")))
        self.assertEqual((s["eligible"], s["failed"], s["excluded"]), (1, 1, 4))
        self.assertEqual((s["incomplete"], s["completionUnknown"], s["judgmentUnknown"], s["errorStatus"]), (2, 1, 2, 1))

    def test_noop_and_not_run_are_disclosed_but_excluded_from_business_rate(self):
        attack = '</script><script>alert("synthetic-noop")</script>'
        data = dataset(
            record(1, True, status="completed", completed=True),
            record(2, False, status="completed", completed=True),
            record(3, False, status="noop", completed=True, actual=attack),
            record(4, None, status="not_run", completed=False),
            record(5, None, status="completed", completed=True),
        )

        content, summary = self.build(data)

        self.assertEqual((summary["eligible"], summary["passed"], summary["failed"]), (2, 1, 1))
        self.assertEqual((summary["noop"], summary["notRun"]), (1, 1))
        self.assertEqual(summary["judgmentUnknown"], 2)
        self.assertIn("otherJudgmentUnknown", summary)
        self.assertIn("otherCompletedJudgmentUnknown", summary)
        self.assertEqual(summary["otherJudgmentUnknown"], 1)
        self.assertEqual(summary["otherCompletedJudgmentUnknown"], 1)
        self.assertNotIn("normalJudgmentUnknown", summary)
        self.assertEqual((summary["original"]["eligible"], summary["original"]["passed"],
                          summary["original"]["failed"]), (2, 1, 1))
        self.assertEqual(sum(group["executions"] for group in summary["groupCounts"]), summary["total"])
        self.assertIn("空跑 1", content)
        self.assertIn("未跑 1", content)
        self.assertIn("非空跑/未跑记录判定缺失", content)
        self.assertIn("未正常执行明细", content)

        failures = content.split('<section id="failures"', 1)[1].split('</section>', 1)[0]
        self.assertIn("案例 2", failures)
        self.assertNotIn("案例 3", failures)
        self.assertNotIn("案例 4", failures)
        self.assertNotIn("案例 5", failures)

        abnormal = content.split('<section id="abnormal-executions"', 1)[1].split('</section>', 1)[0]
        self.assertIn("案例 3", abnormal)
        self.assertIn("案例 4", abnormal)
        self.assertNotIn("案例 2", abnormal)
        self.assertNotIn("案例 5", abnormal)
        self.assertIn("noop", abnormal)
        self.assertIn("not_run", abnormal)
        self.assertIn(html.escape(attack), abnormal)
        self.assertNotIn(attack, content)

        executable = content.rsplit("<script>", 1)[1].split("</script>", 1)[0]
        parsed = subprocess.run(["node", "--check"], input=executable, capture_output=True, text=True)
        self.assertEqual(parsed.returncode, 0, parsed.stderr)

    def test_abnormal_details_use_global_search_filter_and_pagination_contract(self):
        records = [record(i, False, status="noop", completed=True,
                          input=f"空跑输入 {i}", actual=f"空跑输出 {i}") for i in range(1, 27)]

        content, _ = self.build(dataset(*records))

        abnormal = content.split('<section id="abnormal-executions"', 1)[1].split('</section>', 1)[0]
        self.assertTrue(abnormal.startswith(' data-list>'))
        self.assertEqual(abnormal.count('<article class="card" data-failed="0">'), 26)
        self.assertEqual(abnormal.count("data-prev"), 1)
        self.assertEqual(abnormal.count("data-page"), 1)
        self.assertEqual(abnormal.count("data-next"), 1)
        executable = content.rsplit("<script>", 1)[1].split("</script>", 1)[0]
        self.assertIn("document.querySelectorAll('[data-list]')", executable)
        parsed = subprocess.run(["node", "--check"], input=executable, capture_output=True, text=True)
        self.assertEqual(parsed.returncode, 0, parsed.stderr)

    def test_handoff_uses_completed_known_values_and_never_response_text(self):
        data = dataset(record(1, handoff=True, handoffEvidence="已核对成功转接事件"),
                       record(2, False, handoff=False, handoffEvidence="已检查完整轨迹，无转接事件"),
                       record(3, actual="为您转人工"), record(4, completed=False, handoff=True, handoffEvidence="转接事件存在"))
        content, s = self.build(data, config={"includeHandoff": True})
        self.assertEqual(s["handoff"], {"completed": 3, "known": 2, "transferred": 1, "notTransferred": 1, "unknown": 1, "rate": .5, "coverage": 2 / 3})
        self.assertEqual((s["passed"], s["failed"]), (2, 1))
        self.assertIn("识别覆盖率", content)
        default_content, default_summary = self.build(data)
        self.assertNotIn('id="handoff"', default_content)
        self.assertNotIn("handoff", default_summary)

    def test_duration_and_group_breakdowns(self):
        data = dataset(record(1, durationMs=0, attributes={"channel": "网页"}), record(2, durationMs=200),
                       record(3), record(4, completed=False, durationMs=500))
        _, s = self.build(data, config={"includeDuration": True, "groupBy": ["scene", "attributes.channel"]})
        self.assertEqual(s["duration"], {"completed": 3, "samples": 2, "missing": 1, "meanMs": 100, "minMs": 0, "maxMs": 200})
        self.assertEqual(len(s["groupBreakdowns"]), 2)
        self.rejects(dataset(record(1, durationMs=-1)))
        self.rejects(dataset(record(1, durationMs=float("nan"))))
        self.rejects(dataset(record(1, durationMs=True)))

    def test_optional_operational_metrics_only_use_normal_completed_executions(self):
        data = dataset(
            record(1, True, status="completed", completed=True, durationMs=100,
                   handoff=True, handoffEvidence="已记录转接事件"),
            record(2, False, status="noop", completed=True, durationMs=10000,
                   handoff=False, handoffEvidence="空跑无转接"),
            record(3, None, status="not_run", completed=False, durationMs=20000,
                   handoff=True, handoffEvidence="未执行记录"),
            record(4, None, status="completed", completed=True, durationMs=250,
                   handoff=True, handoffEvidence="判定未知"),
            record(5, False, status="error", completed=True, durationMs=40000,
                   handoff=False, handoffEvidence="执行异常"),
            record(6, False, status="completed", completed=False, durationMs=50000,
                   handoff=True, handoffEvidence="未完成"),
        )

        _, summary = self.build(data, config={"includeDuration": True, "includeHandoff": True})

        self.assertEqual(summary["duration"], {
            "completed": 2, "samples": 2, "missing": 0,
            "meanMs": 175, "minMs": 100, "maxMs": 250,
        })
        self.assertEqual(summary["handoff"], {
            "completed": 2, "known": 2, "transferred": 2,
            "notTransferred": 0, "unknown": 0, "rate": 1.0, "coverage": 1.0,
        })

    def test_operational_metrics_are_independent_from_business_judgment(self):
        data = dataset(record(
            1, None, status="completed", completed=True, durationMs=250,
            handoff=True, handoffEvidence="已记录转接事件",
        ))

        _, summary = self.build(data, config={"includeDuration": True, "includeHandoff": True})

        self.assertEqual(summary["duration"], {
            "completed": 1, "samples": 1, "missing": 0,
            "meanMs": 250, "minMs": 250, "maxMs": 250,
        })
        self.assertEqual(summary["handoff"], {
            "completed": 1, "known": 1, "transferred": 1,
            "notTransferred": 0, "unknown": 0, "rate": 1.0, "coverage": 1.0,
        })

    def test_annotations_do_not_mutate_input_or_change_pass_and_require_evidence(self):
        data = dataset(record(1, False))
        original = copy.deepcopy(data)
        annotations = {"annotations": [{"recordId": "private-execution-1", "handoff": False,
                                        "handoffEvidence": "全轨迹已核对", "scene": "补充场景", "durationMs": 12,
                                        "attributes": {"channel": "网页"}}]}
        _, s = self.build(data, annotations=annotations, config={"includeHandoff": True, "includeDuration": True})
        self.assertEqual(s["failed"], 1)
        self.assertEqual(s["handoff"]["notTransferred"], 1)
        self.assertEqual(data, original)
        for bad in [{"handoff": True}, {"handoff": False}, {"actual": "更改"}, {"input": "更改"},
                    {"status": "success"}, {"completed": True}, {"originalPassed": True}]:
            self.rejects(data, annotations={"annotations": [{"recordId": "private-execution-1", **bad}]})
        self.rejects(data, annotations={"annotations": [{"recordId": "missing", "scene": "x"}]})
        self.rejects(data, annotations={"annotations": annotations["annotations"] * 2})

    def test_exact_original_text_is_escaped_and_metadata_is_not_published(self):
        attack = '  文字\n</script><script>alert("x")</script>&<img src=x onerror=alert(1)>  '
        data = dataset(record(1, False, input=attack, actual=attack, context={"history": [attack]}))
        data["source"]["sourceUrl"] = "https://private.example.invalid/internal"
        data["source"]["orgId"] = "private-organization"
        content, summary = self.build(data)
        self.assertIn(html.escape(attack), content)
        self.assertNotIn(attack, content)
        self.assertNotIn("private-execution-1", content)
        self.assertNotIn("private-organization", content)
        self.assertNotIn("private.example.invalid", content)
        self.assertNotIn("private-execution-1", json.dumps(summary))
        self.assertIn("对应期待", content)
        self.assertIn("对应原因", content)
        self.assertIn('type="search"', content)
        self.assertIn("只看未通过", content)
        self.assertIn("下一页", content)
        self.assertIn("window.print()", content)
        self.assertNotIn("<script src=", content)

    def test_embedded_summary_cannot_close_script_through_title(self):
        title = '</script><script>alert("synthetic")</script>'
        content, _ = self.build(dataset(), config={"title": title})
        embedded = content.split('id="report-summary">', 1)[1].split('</script>', 1)[0]
        self.assertNotIn("<", embedded)
        self.assertEqual(json.loads(embedded)["title"], title)

    def test_raw_assertion_extra_fields_are_preserved_but_not_published(self):
        data = dataset(record(passed=False))
        data["records"][0]["assertions"][0].update({"internalActionId": "private-action-marker", "debugMetadata": {"sourceUrl": "https://private.example.invalid/action"}})
        original = copy.deepcopy(data)
        content, _ = self.build(data)
        self.assertEqual(data, original)
        self.assertNotIn("private-action-marker", content)
        self.assertNotIn("private.example.invalid", content)
        data["records"][0]["assertions"][0]["debugMetadata"]["password"] = "synthetic-credential"
        self.rejects(data)

    def test_adjustments_reject_missing_unconfirmed_duplicate_and_incomplete(self):
        adjustment = {"recordId": "private-execution-1", "reportPassed": True, "reason": "原因", "evidence": "依据", "confirmedByUser": True}
        for patch in [{"confirmedByUser": False}, {"recordId": "missing"}, {"reason": " "}, {"evidence": ""}, {"reportPassed": None}, {"input": "更改"}]:
            self.rejects(dataset(record(1, False)), adjustments={"adjustments": [{**adjustment, **patch}]})
        self.rejects(dataset(record(1, False)), adjustments={"adjustments": [adjustment, adjustment]})
        self.rejects(dataset(record(1, False, completed=False)), adjustments={"adjustments": [adjustment]})
        self.rejects(dataset(record(1, False, completed=None)), adjustments={"adjustments": [adjustment]})

    def test_empty_dataset_has_null_rate_and_not_applicable(self):
        content, s = self.build(dataset(), config={"includeHandoff": True, "includeDuration": True})
        self.assertIsNone(s["rate"])
        self.assertIsNone(s["handoff"]["rate"])
        self.assertIsNone(s["handoff"]["coverage"])
        self.assertEqual((s["groups"], s["variants"]), (0, 0))
        self.assertIn("不适用", content)

    def test_optional_null_labels_and_unverified_scope_survive_normalization(self):
        data = dataset(record(caseName=None, status=None, scene=None, input=None, actual=None))
        data["source"]["scopeVerified"] = False
        content, summary = self.build(data)
        self.assertEqual(summary["errorStatus"], 0)
        self.assertIn("来源组织 / 任务范围未核实", content)
        failed_content, _ = self.build(dataset(record(passed=False, caseName=None, status=None)))
        self.assertIn("未命名案例", failed_content)

    def test_leading_newline_is_not_stripped_by_html_pre_element(self):
        content, _ = self.build(dataset(record(input="\n  原文\n")))
        self.assertIn("<pre><span>\n  原文\n</span></pre>", content)

    def test_refuse_incomplete_source_total_mismatch_duplicate_and_bad_schema(self):
        data = dataset(record())
        for patch in [{"complete": False}, {"complete": None}, {"total": 2}, {"total": True}]:
            broken = copy.deepcopy(data)
            broken["source"].update(patch)
            self.rejects(broken)
        self.rejects(dataset(record(), record()))
        self.rejects({**data, "schemaVersion": 2})
        self.rejects(dataset(record(completed="true")))
        self.rejects(dataset(record(originalPassed=1)))
        self.rejects(dataset(record(unknownField="unsupported")))

    def test_extension_validation_and_safe_text_rendering(self):
        data = dataset(record())
        metric = {"label": "自定义 <指标>", "numerator": 1, "denominator": 1, "unit": "%", "definition": "明确口径", "evidence": "本次数据统计规则"}
        section = {"title": "分析 <标题>", "text": "安全 <script> 内容", "columns": ["类别", "次数"], "rows": [["类别 A", 1]]}
        content, _ = self.build(data, config={"metrics": [metric], "sections": [section]})
        self.assertIn("自定义 &lt;指标&gt;", content)
        self.assertIn("安全 &lt;script&gt; 内容", content)
        for config in [{"unknown": True}, {"groupBy": ["input"]}, {"groupBy": ["attributes."]},
                       {"metrics": [{k: v for k, v in metric.items() if k != "definition"}]},
                       {"metrics": [{**metric, "evidence": ""}]}, {"metrics": [{**metric, "expression": "1+1"}]},
                       {"sections": [{**section, "rows": [[1]]}]}, {"includeHandoff": "true"}]:
            self.rejects(data, config=config)

    def test_obvious_credentials_are_refused_with_safe_error(self):
        for credential in ["Bearer abcDEF1234567890", "password=not-a-real-value", "secret: synthetic-key-123",
                           "eyJhbGciOiJub25lIn0" + ".eyJzdWIiOiJzeW50aGV0aWMifQ.signature123"]:
            with self.assertRaises(ValueError) as caught:
                self.build(dataset(record(actual=credential)))
            self.assertNotIn(credential, str(caught.exception))
        self.rejects(dataset(record(context={"password": "synthetic"})))

    def test_http_credential_header_text_is_refused_with_safe_error(self):
        credentials = [
            "Authorization" + ": Basic c3ludGhldGljOnNhbXBsZQ==",
            "Authorization: synthetic-opaque-value",
            "authorization:" + "\tsynthetic-opaque-value",
            "Proxy-Authorization" + ": Basic c3ludGhldGljOnNhbXBsZQ==",
            "Cookie: synthetic-session=value",
            "Set-Cookie: synthetic-session=value; HttpOnly; Secure",
            'request headers: {"Authorization": "synthetic-opaque-value"}',
            "request headers: {'Cookie': 'synthetic-session=value'}",
        ]
        for credential in credentials:
            with self.subTest(credential=credential):
                with self.assertRaises(ValueError) as caught:
                    self.build(dataset(record(actual=credential)))
                self.assertNotIn(credential, str(caught.exception))

    def test_http_credential_header_dictionary_values_are_refused(self):
        for key in ("Cookie", "cookie", "Set-Cookie", "set_cookie", "Proxy-Authorization"):
            for value in ("session=synthetic-session-value", ["session=synthetic-session-value"], {"session": "synthetic-session-value"}):
                with self.subTest(key=key, value=value):
                    self.rejects(dataset(record(context={"headers": {key: value}})))

    def test_empty_http_header_fields_are_not_credentials(self):
        empty_headers = [
            "Authorization:", "Cookie" + ":  \t", "Set-Cookie" + ": \r\nX-Trace: synthetic",
            "Authorization" + ":\nContent-Type: application/json", "Authorization: null",
            '{"Authorization": "", "Cookie": null, "Set-Cookie": "  "}',
            "{'Authorization': '  ', 'Cookie': ''}",
        ]
        for value in empty_headers:
            with self.subTest(value=value):
                _, summary = self.build(dataset(record(actual=value)))
                self.assertEqual(summary["passed"], 1)
        for key in ("Authorization", "Cookie", "Set-Cookie", "Proxy-Authorization"):
            for value in (None, "", " \t\r\n", [], {}):
                with self.subTest(key=key, value=value):
                    _, summary = self.build(dataset(record(context={"headers": {key: value}})))
                    self.assertEqual(summary["passed"], 1)

    def test_cli_permissions_refuses_overwrite_and_duplicate_json_keys(self):
        with tempfile.TemporaryDirectory() as tmp:
            source = Path(tmp) / "dataset.json"
            source.write_text(json.dumps(dataset(record())), encoding="utf-8")
            output = Path(tmp) / "report"
            command = [sys.executable, str(SCRIPT), "--input", str(source), "--output-dir", str(output)]
            first = subprocess.run(command, capture_output=True, text=True)
            self.assertEqual(first.returncode, 0, first.stderr)
            for name in ("report.html", "report-summary.json"):
                self.assertEqual(stat.S_IMODE((output / name).stat().st_mode), 0o600)
            old = (output / "report.html").read_bytes()
            second = subprocess.run(command, capture_output=True, text=True)
            self.assertNotEqual(second.returncode, 0)
            self.assertEqual((output / "report.html").read_bytes(), old)
            source.write_text('{"schemaVersion":1,"schemaVersion":1}', encoding="utf-8")
            invalid = subprocess.run(command[:-1] + [str(Path(tmp) / "bad")], capture_output=True, text=True)
            self.assertNotEqual(invalid.returncode, 0)
            self.assertFalse((Path(tmp) / "bad/report.html").exists())

    def test_cli_argument_errors_do_not_echo_accidentally_supplied_credentials(self):
        credential = "Bearer synthetic-cli-credential-123"
        result = subprocess.run([sys.executable, str(SCRIPT), "--input", "unused.json", "--output-dir", "unused", "--token", credential], capture_output=True, text=True)
        self.assertNotEqual(result.returncode, 0)
        self.assertNotIn(credential, result.stdout + result.stderr)

    def test_unencodable_source_leaves_no_partial_report(self):
        with tempfile.TemporaryDirectory() as tmp:
            source = Path(tmp) / "dataset.json"
            source.write_text(json.dumps(dataset(record(actual="\ud800"))), encoding="utf-8")
            output = Path(tmp) / "report"
            result = subprocess.run([sys.executable, str(SCRIPT), "--input", str(source), "--output-dir", str(output)], capture_output=True, text=True)
            self.assertNotEqual(result.returncode, 0)
            self.assertFalse((output / "report.html").exists())
            self.assertFalse((output / "report-summary.json").exists())


if __name__ == "__main__":
    unittest.main()
