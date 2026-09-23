#!/usr/bin/env python3
"""Fetch a complete test task or normalize a complete private JSON export.

This script preserves evidence; it does not reassess verdicts or infer handoffs.
Use --help for CLI usage. Credentials are accepted through stdin, an environment
variable, or a hidden terminal prompt, never through a command-line argument.
"""

import argparse
import copy
import getpass
import json
import math
import os
from pathlib import Path
import sys
from urllib.error import HTTPError
from urllib.parse import parse_qs, urlencode, urlsplit, urlunsplit
from urllib.request import HTTPRedirectHandler, Request, build_opener


API_PATH = "/api/test-center/test-task-item/list"
PAGE_SIZE = 200
TIMEOUT_SECONDS = 30
SCOPE_KEYS = ("orgId", "testTaskId")
COMPLETE_STATUSES = {"success", "completed", "finished", "done", "error", "failed", "timeout"}
INCOMPLETE_STATUSES = {"pending", "running", "queued"}
CONTEXT_KEYS = ("sessionMemoryCustomData", "history", "conversationHistory", "messages",
                "context", "businessContext", "testContext", "workflowContext")


class PreparationError(ValueError):
    """A safe error whose message contains no source values or credentials."""


def validate_source_url(url):
    """Validate a copied unfiltered list URL and return a canonical target."""
    if not isinstance(url, str) or not url or any(ord(char) <= 32 for char in url):
        raise PreparationError("URL 无效：不得包含空白或控制字符。")
    try:
        parsed = urlsplit(url)
        port = parsed.port
    except ValueError:
        raise PreparationError("URL 格式无效。") from None
    if (parsed.scheme != "https" or not parsed.hostname or parsed.username is not None
            or parsed.password is not None or parsed.fragment or parsed.path != API_PATH
            or (port is not None and not 1 <= port <= 65535)):
        raise PreparationError("URL 必须是 HTTPS 测试结果列表接口，且不得带账号信息或片段。")
    query = parse_qs(parsed.query, keep_blank_values=True)
    allowed = {*SCOPE_KEYS, "current", "pageSize"}
    if any(key not in allowed and any(values) for key, values in query.items()):
        raise PreparationError("链接包含筛选条件；请清除所有筛选后重新复制完整任务链接。")
    if any(len(values) != 1 for key, values in query.items() if key in allowed):
        raise PreparationError("URL 参数重复；请重新复制完整任务链接。")
    scope = {}
    for key in SCOPE_KEYS:
        values = query.get(key, [])
        if len(values) != 1 or not values[0].strip():
            raise PreparationError("URL 必须各含一个非空 orgId 和 testTaskId。")
        scope[key] = values[0]
    scope["url"] = urlunsplit(("https", parsed.netloc, API_PATH, urlencode(scope), ""))
    return scope


def _page_url(target, current):
    parsed = urlsplit(target["url"])
    query = {key: target[key] for key in SCOPE_KEYS}
    query.update(current=current, pageSize=PAGE_SIZE)
    return urlunsplit((parsed.scheme, parsed.netloc, parsed.path, urlencode(query), ""))


class _NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


def _default_request_json(request):
    # urllib must not forward Authorization even to a same-origin redirect.
    with build_opener(_NoRedirect()).open(request, timeout=TIMEOUT_SECONDS) as response:
        return json.load(response)


def _validate_authorization(value):
    if not isinstance(value, str) or not value.strip() or any(ord(char) < 32 or ord(char) == 127 for char in value):
        raise PreparationError("Authorization 为空或包含非法控制字符。")
    return value


def _page_data(payload, current=None):
    if not isinstance(payload, dict) or type(payload.get("code")) is not int or payload["code"] != 0:
        raise PreparationError("响应结构无效或接口未返回成功状态。")
    data, page = payload.get("data"), payload.get("page")
    if not isinstance(data, list) or not all(isinstance(record, dict) for record in data):
        raise PreparationError("响应 data 必须是对象数组。")
    total = page.get("total") if isinstance(page, dict) else None
    if type(total) is not int or total < 0:
        raise PreparationError("page.total 必须是非负整数。")
    if current is not None and "current" not in page:
        raise PreparationError("接口未返回 page.current，无法验证分页完整性。")
    if "current" in page:
        if type(page["current"]) is not int or page["current"] < 1 or (current is not None and page["current"] != current):
            raise PreparationError("返回页码与请求页码不一致或格式无效。")
    return data, total


def _check_ids(records, seen=None):
    seen = set() if seen is None else seen
    for record in records:
        identifier = record.get("testTaskItemId")
        if not isinstance(identifier, str) or not identifier.strip():
            raise PreparationError("执行记录缺少非空字符串 testTaskItemId。")
        if identifier in seen:
            raise PreparationError("发现重复执行 ID，无法确认数据完整性。")
        seen.add(identifier)


def _check_scope(records, envelopes, target=None):
    """Reject every explicit conflict; missing fields remain unverified."""
    verified = bool(target and records)
    for key in SCOPE_KEYS:
        values = set()
        for obj in [*envelopes, *records]:
            value = obj.get(key)
            if value is None or value == "":
                continue
            if not isinstance(value, str) or not value.strip():
                raise PreparationError("返回范围字段类型无效。")
            values.add(value)
        if len(values) > 1:
            raise PreparationError("返回数据混合了不同组织或测试任务。")
        if target and values and values != {target[key]}:
            raise PreparationError("返回的组织或测试任务与请求范围不一致。")
        if not target or any(record.get(key) != target[key] for record in records):
            verified = False
    return verified


def fetch_all(url, authorization, request_json=None):
    """Fetch all pages without redirects; retain original page envelopes."""
    target = validate_source_url(url)
    authorization = _validate_authorization(authorization)
    requester = request_json or _default_request_json
    combined, pages, seen_ids, signatures = [], [], set(), set()
    expected_total, current = None, 1
    while expected_total is None or len(combined) < expected_total:
        request = Request(_page_url(target, current), headers={"Authorization": authorization, "Accept": "application/json"})
        try:
            response = requester(request)
        except HTTPError as exc:
            if 300 <= exc.code < 400:
                raise PreparationError("接口返回重定向，已拒绝跟随；请核对原始接口地址。") from None
            if exc.code in (401, 403):
                raise PreparationError("授权过期或权限不足，请重取当前任务授权。") from None
            raise PreparationError("接口请求失败；请核对权限与网络后重试。") from None
        except Exception:
            # Exception strings may contain credentials, URLs, or response bodies.
            raise PreparationError("接口请求或 JSON 解析失败；未保存不完整结果。") from None
        data, total = _page_data(response, current)
        if expected_total is None:
            expected_total = total
        elif total != expected_total:
            raise PreparationError("分页过程中 total 发生变化，请重新获取完整任务。")
        if not data and len(combined) < expected_total:
            raise PreparationError("分页提前返回空页，结果不完整。")
        signature = json.dumps(data, ensure_ascii=False, sort_keys=True)
        if data and signature in signatures:
            raise PreparationError("接口重复返回同一页，结果不完整。")
        signatures.add(signature)
        _check_ids(data, seen_ids)
        _check_scope([*combined, *data], [*pages, response], target)
        combined.extend(data)
        pages.append(response)
        if len(combined) > expected_total:
            raise PreparationError("实际记录数超过 total，无法确认完整性。")
        current += 1
    return {"code": 0, "data": combined, "page": {"current": 1, "pageSize": PAGE_SIZE, "total": expected_total}, "pages": pages}


def _text_or_object(content):
    if isinstance(content, dict) and isinstance(content.get("text"), str):
        if set(content) <= {"text", "type"} and content.get("type", "text") == "text":
            return content["text"]
    return copy.deepcopy(content)


def _input(record):
    values = {}
    trigger = record.get("triggerContent")
    if trigger is not None:
        content = trigger.get("content") if isinstance(trigger, dict) and "content" in trigger else trigger
        values["triggerContent"] = _text_or_object(content)
    if record.get("triggerInputs") is not None:
        values["triggerInputs"] = _text_or_object(record["triggerInputs"])
    if not values:
        return None
    if len(values) == 1:
        return next(iter(values.values()))
    return values


def _context(record):
    context = {key: copy.deepcopy(record[key]) for key in CONTEXT_KEYS if key in record}
    trigger = record.get("triggerContent")
    if isinstance(trigger, dict):
        nested = {key: copy.deepcopy(trigger[key]) for key in CONTEXT_KEYS if key in trigger}
        if nested:
            context["triggerContent"] = nested
    return context


def _normalize_record(record, warnings):
    assertions = record.get("canvasActionOutputAssertionResult")
    assertions = [] if assertions is None else assertions
    if not isinstance(assertions, list) or not all(isinstance(assertion, dict) for assertion in assertions):
        raise PreparationError("断言结果必须是对象数组；无法无损规范化该记录。")
    actual = None
    if len(assertions) == 1:
        actual = assertions[0].get("actualValue")
    elif assertions:
        actual = [{"type": assertion.get("type"), "actualValue": assertion.get("actualValue")} for assertion in assertions]
    elif "text" in record:
        actual = record["text"]
    passed = record.get("passed") if type(record.get("passed")) is bool else None
    raw_status = record.get("status")
    status = raw_status if isinstance(raw_status, str) else None
    canonical_status = status.strip().lower() if status else None
    if canonical_status in COMPLETE_STATUSES:
        completed = True
    elif canonical_status in INCOMPLETE_STATUSES:
        completed = False
    elif raw_status is None or raw_status == "":
        completed = True if passed is not None else None
    else:
        completed = None
        warnings.append("存在无法识别的执行状态，完成状态保留为未知。")
    scene = None
    for key in ("scene", "scenarioPath", "sceneName", "scenarioName", "testScenarioName"):
        if isinstance(record.get(key), str) and record[key]:
            scene = record[key]
            break
    if scene is None:
        for key in ("scenario", "testScenario"):
            value = record.get(key)
            if isinstance(value, dict) and isinstance(value.get("name"), str):
                scene = value["name"]
                break
    duration = record.get("durationMs")
    if type(duration) not in (int, float) or not math.isfinite(duration) or duration < 0:
        duration = None
    return {"id": record["testTaskItemId"], "caseName": record.get("testCaseName") if isinstance(record.get("testCaseName"), str) else None,
            "input": _input(record), "context": _context(record), "actual": copy.deepcopy(actual),
            "assertions": copy.deepcopy(assertions), "originalPassed": passed, "status": status, "completed": completed,
            "scene": scene, "handoff": None, "handoffEvidence": None, "durationMs": duration,
            "attributes": copy.deepcopy(record["attributes"]) if isinstance(record.get("attributes"), dict) else {}}


def normalize_payload(payload, source_url=None):
    """Validate a complete export and produce version 1 evidence records."""
    target = validate_source_url(source_url) if source_url is not None else None
    data, total = _page_data(payload)
    if len(data) != total:
        raise PreparationError("本地导出不完整：data 长度必须等于 page.total。")
    _check_ids(data)
    envelopes = [payload]
    if "pages" in payload:
        if not isinstance(payload["pages"], list) or not all(isinstance(page, dict) for page in payload["pages"]):
            raise PreparationError("原始分页响应格式无效。")
        envelopes.extend(payload["pages"])
    verified = _check_scope(data, envelopes, target)
    warnings = []
    if not verified:
        warnings.append("范围未完全验证：缺少来源链接、返回范围字段或执行记录。")
    records = [_normalize_record(record, warnings) for record in data]
    source = {"complete": True, "total": total, "scope": "task", "scopeVerified": verified,
              "warnings": list(dict.fromkeys(warnings))}
    if source_url is not None:
        source["sourceUrl"] = source_url
    return {"schemaVersion": 1, "source": source, "records": records}


def _authorization(args):
    if args.authorization_stdin:
        value = sys.stdin.read().rstrip("\r\n")
    elif os.environ.get("TEST_CENTER_AUTHORIZATION"):
        value = os.environ["TEST_CENTER_AUTHORIZATION"]
    elif sys.stdin.isatty():
        value = getpass.getpass("Authorization（不会显示）: ")
    else:
        raise PreparationError("请通过 --authorization-stdin 或 TEST_CENTER_AUTHORIZATION 提供凭证。")
    return _validate_authorization(value)


def _write_outputs(output_dir, raw, dataset):
    directory = Path(output_dir)
    outputs = [(directory / "raw-results.json", raw), (directory / "dataset.json", dataset)]
    if any(os.path.lexists(path) for path, _ in outputs):
        raise PreparationError("输出文件已存在，拒绝覆盖；请选择新的任务目录。")
    encoded = [(path, (json.dumps(value, ensure_ascii=False, indent=2, allow_nan=False) + "\n").encode("utf-8")) for path, value in outputs]
    directory.mkdir(mode=0o700, parents=True, exist_ok=True)
    created = []
    try:
        for path, content in encoded:
            descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_NOFOLLOW", 0), 0o600)
            created.append(path)
            with os.fdopen(descriptor, "wb") as stream:
                stream.write(content)
    except Exception:
        # Remove only the precise new output files created by this invocation.
        for path in created:
            path.unlink(missing_ok=True)
        raise PreparationError("无法安全写入两个私有输出文件；未保留部分结果。") from None


class _ArgumentParser(argparse.ArgumentParser):
    def error(self, message):
        # argparse's normal error can echo a supplied URL or accidental secret.
        raise PreparationError("命令行参数无效；请使用 --help 查看用法。")


def main(argv=None):
    parser = _ArgumentParser(description=__doc__)
    sources = parser.add_mutually_exclusive_group(required=True)
    sources.add_argument("--url", help="完整任务的 HTTPS 列表接口 URL；请清除所有筛选")
    sources.add_argument("--input", help="完整的本地 {code,data,page} JSON 导出")
    parser.add_argument("--source-url", help="为本地输入提供原始 URL，用于组织及任务范围核对")
    parser.add_argument("--authorization-stdin", action="store_true", help="从标准输入读取 Authorization；未指定时使用环境变量或隐藏输入")
    parser.add_argument("--output-dir", required=True, help="私有任务目录；拒绝覆盖已有 raw-results.json 或 dataset.json")
    try:
        args = parser.parse_args(argv)
        if (args.url and args.source_url) or (args.input and args.authorization_stdin):
            raise PreparationError("--source-url 仅用于本地输入；凭证输入仅用于 --url。")
        if args.url:
            validate_source_url(args.url)
            raw = fetch_all(args.url, _authorization(args))
            dataset = normalize_payload(raw, source_url=args.url)
        else:
            with open(args.input, encoding="utf-8") as stream:
                raw = json.load(stream)
            dataset = normalize_payload(raw, source_url=args.source_url)
        _write_outputs(args.output_dir, raw, dataset)
    except PreparationError as exc:
        print("未完成：" + str(exc), file=sys.stderr)
        return 1
    except Exception:
        print("未完成：读取、解析或写入失败；未输出凭证、来源地址或响应内容。", file=sys.stderr)
        return 1
    print("已验证完整数据，并写入 raw-results.json 与 dataset.json（仅当前用户可读写）。")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
