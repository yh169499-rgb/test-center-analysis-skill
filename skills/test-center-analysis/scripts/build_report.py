#!/usr/bin/env python3
"""Render a complete normalized test batch using only the Python standard library.

No network, expression evaluation, identifier inference, or changes to source data.
Public outputs deliberately omit record identifiers and source metadata.
"""
import argparse
import copy
import html
import json
import math
import os
from pathlib import Path
import re
import sys


RECORD_KEYS = {
    "id", "caseName", "input", "context", "actual", "assertions", "originalPassed",
    "status", "completed", "scene", "handoff", "handoffEvidence", "durationMs", "attributes",
}
ERROR_STATUSES = {"error", "errored", "failed", "failure", "exception", "timeout", "timed_out", "cancelled", "canceled", "aborted"}
SECRET_KEYS = {"password", "passwd", "secret", "clientsecret", "apikey", "accesstoken", "refreshtoken", "authorization", "proxyauthorization", "cookie", "setcookie", "token"}
SECRET_PATTERNS = (
    re.compile(r"\bBearer\s+[A-Za-z0-9._~+/=-]{8,}", re.I),
    re.compile(r"\beyJ[A-Za-z0-9_-]{5,}\.[A-Za-z0-9_-]{8,}\.[A-Za-z0-9_-]{8,}\b"),
    re.compile(r"\b(?:password|passwd|secret|client[_-]?secret|api[_-]?key|access[_-]?token|refresh[_-]?token)\b[\"']?\s*[:=]\s*[\"']?[^\s\"',;}]{4,}", re.I),
)
# Match the value on this line only: a blank header must not consume the next
# header. Quoted JSON/log fields need their quotes parsed before testing empty.
HTTP_CREDENTIAL_HEADER = re.compile(
    r'''(?<![\w-])(?:authorization|proxy-authorization|cookie|set-cookie)["']?[ \t]*:[ \t]*'''
    r'''(?:"(?P<double>[^"\r\n]*)"|'(?P<single>[^'\r\n]*)'|(?P<plain>[^\r\n,}]*))''',
    re.I,
)


def _fail(message):
    raise ValueError(message)


def _object(value, allowed=None, required=(), label="对象"):
    if not isinstance(value, dict):
        _fail(label + "必须是 JSON 对象。")
    if allowed is not None and set(value) - set(allowed):
        _fail(label + "包含不支持的字段。")
    if set(required) - set(value):
        _fail(label + "缺少必要字段。")


def _text(value, label, nonempty=False):
    if not isinstance(value, str) or (nonempty and not value.strip()):
        _fail(label + "必须是" + ("非空" if nonempty else "") + "文本。")


def _nullable_bool(value, label):
    if value is not None and type(value) is not bool:
        _fail(label + "必须是 true、false 或 null。")


def _number(value, label):
    if type(value) not in (int, float) or not math.isfinite(value) or value < 0:
        _fail(label + "必须是非负有限数。")


def _has_secret_value(value):
    if value is None:
        return False
    if isinstance(value, str):
        return bool(value.strip())
    if isinstance(value, dict):
        return any(_has_secret_value(item) for item in value.values())
    if isinstance(value, list):
        return any(_has_secret_value(item) for item in value)
    return True


def _has_http_credentials(value):
    for match in HTTP_CREDENTIAL_HEADER.finditer(value):
        for name in ("double", "single"):
            if match.group(name) is not None and match.group(name).strip():
                return True
        plain = match.group("plain")
        # Unquoted JSON null is an absent value; the quoted string "null" is not.
        if plain is not None and plain.strip().lower() not in ("", "null"):
            return True
    return False


def _safe_json(value):
    """Fail closed on obvious credentials without echoing their contents."""
    if isinstance(value, dict):
        for key, item in value.items():
            if not isinstance(key, str):
                _fail("JSON 对象的键必须是文本。")
            normalized = re.sub(r"[^a-z0-9]", "", key.lower())
            if normalized in SECRET_KEYS and _has_secret_value(item):
                _fail("检测到疑似凭据，已停止生成报告；请在受控源数据中核实后重试。")
            _safe_json(key)
            _safe_json(item)
    elif isinstance(value, list):
        for item in value:
            _safe_json(item)
    elif isinstance(value, str):
        try:
            value.encode("utf-8")
        except UnicodeError:
            _fail("JSON 文本包含无法编码为 UTF-8 的字符，未生成报告。")
        if _has_http_credentials(value) or any(pattern.search(value) for pattern in SECRET_PATTERNS):
            _fail("检测到疑似凭据，已停止生成报告；请在受控源数据中核实后重试。")
    elif type(value) is float:
        if not math.isfinite(value):
            _fail("JSON 数据包含非有限数。")
    elif value is not None and type(value) not in (int, bool):
        _fail("包含不支持的 JSON 值。")


def _evidence_fields(record):
    if record.get("scene") is not None:
        _text(record["scene"], "场景")
    if "attributes" in record:
        _object(record["attributes"], label="attributes")
    if "durationMs" in record and record["durationMs"] is not None:
        _number(record["durationMs"], "durationMs")
    if "handoff" in record:
        _nullable_bool(record["handoff"], "handoff")
    if "handoffEvidence" in record and record["handoffEvidence"] is not None:
        _text(record["handoffEvidence"], "转人工证据", nonempty=True)


def _validate_dataset(data):
    _object(data, {"schemaVersion", "source", "records"}, {"schemaVersion", "source", "records"}, "数据集")
    if type(data["schemaVersion"]) is not int or data["schemaVersion"] != 1:
        _fail("仅支持 schemaVersion = 1。")
    source = data["source"]
    _object(source, required={"complete", "total"}, label="source")
    if source["complete"] is not True:
        _fail("来源数据尚未确认完整，拒绝生成总体报告。")
    if type(source["total"]) is not int or source["total"] < 0:
        _fail("source.total 必须是非负整数。")
    if not isinstance(data["records"], list) or source["total"] != len(data["records"]):
        _fail("source.total 与执行记录数量不一致。")
    if "scopeVerified" in source and type(source["scopeVerified"]) is not bool:
        _fail("source.scopeVerified 必须是布尔值。")
    ids = set()
    required = {"id", "input", "context", "actual", "originalPassed", "completed", "assertions", "status"}
    for record in data["records"]:
        _object(record, RECORD_KEYS, required, "执行记录")
        _text(record["id"], "执行 ID", nonempty=True)
        if record["id"] in ids:
            _fail("执行 ID 重复，拒绝重复计数。")
        ids.add(record["id"])
        _nullable_bool(record["originalPassed"], "originalPassed")
        _nullable_bool(record["completed"], "completed")
        if record["status"] is not None:
            _text(record["status"], "status")
        if record.get("caseName") is not None:
            _text(record["caseName"], "caseName")
        _evidence_fields(record)
        if not isinstance(record["assertions"], list):
            _fail("assertions 必须是数组。")
        for assertion in record["assertions"]:
            # Raw assertion objects may contain API-specific metadata. Validate
            # known fields and publish only known evidence fields; all original
            # fields were still included in the credential scan above.
            _object(assertion, label="断言")
            for field in ("type", "expectedDescription", "llmReason"):
                if field in assertion and assertion[field] is not None:
                    _text(assertion[field], "断言文本")
            if "passed" in assertion:
                _nullable_bool(assertion["passed"], "断言判定")


def _apply_extensions(records, adjustments, annotations):
    by_id = {record["id"]: record for record in records}
    for record in records:
        record["reportPassed"] = record["originalPassed"]
    for value, name in ((adjustments, "adjustments"), (annotations, "annotations")):
        if value is None:
            continue
        _object(value, {name}, {name}, name)
        if not isinstance(value[name], list):
            _fail(name + "必须是数组。")
        seen = set()
        for entry in value[name]:
            allowed = ({"recordId", "reportPassed", "reason", "evidence", "confirmedByUser"}
                       if name == "adjustments" else {"recordId", "handoff", "handoffEvidence", "scene", "durationMs", "attributes"})
            required = allowed if name == "adjustments" else {"recordId"}
            _object(entry, allowed, required, name + "条目")
            _text(entry["recordId"], "recordId", nonempty=True)
            rid = entry["recordId"]
            if rid not in by_id:
                _fail(name + "包含不存在的执行 ID。")
            if rid in seen:
                _fail(name + "包含重复的执行 ID。")
            seen.add(rid)
            target = by_id[rid]
            if name == "adjustments":
                if entry["confirmedByUser"] is not True:
                    _fail("判定调整必须经用户明确确认。")
                if type(entry["reportPassed"]) is not bool:
                    _fail("调整后的报告判定必须是布尔值。")
                if target["completed"] is not True:
                    _fail("不能调整未完成或完成状态未知的执行。")
                _text(entry["reason"], "调整原因", nonempty=True)
                _text(entry["evidence"], "调整依据", nonempty=True)
                target["reportPassed"] = entry["reportPassed"]
                target["adjustment"] = {key: entry[key] for key in ("reason", "evidence")}
            else:
                _evidence_fields(entry)
                if type(entry.get("handoff")) is bool:
                    _text(entry.get("handoffEvidence"), "转人工补充证据", nonempty=True)
                for key, item in entry.items():
                    if key == "attributes":
                        target[key] = {**target.get(key, {}), **copy.deepcopy(item)}
                    elif key != "recordId":
                        target[key] = copy.deepcopy(item)


def _validate_config(config):
    allowed = {"title", "includeHandoff", "includeDuration", "groupBy", "metrics", "sections"}
    _object(config, allowed, label="报告配置")
    if "title" in config:
        _text(config["title"], "报告标题", nonempty=True)
    for key in ("includeHandoff", "includeDuration"):
        if key in config and type(config[key]) is not bool:
            _fail(key + "必须是布尔值。")
    group_by = config.get("groupBy", [])
    if not isinstance(group_by, list):
        _fail("groupBy 必须是数组。")
    seen = set()
    for key in group_by:
        if not isinstance(key, str) or not (key == "scene" or re.fullmatch(r"attributes\.[A-Za-z_][A-Za-z0-9_-]*", key)):
            _fail("groupBy 仅支持 scene 或 attributes.KEY；KEY 使用字母、数字、下划线和连字符，以字母或下划线开头。")
        if key in seen:
            _fail("groupBy 包含重复字段。")
        seen.add(key)
    metrics = config.get("metrics", [])
    if not isinstance(metrics, list):
        _fail("metrics 必须是数组。")
    required = {"label", "numerator", "denominator", "unit", "definition", "evidence"}
    for metric in metrics:
        _object(metric, required, required, "扩展指标")
        for key in ("label", "unit", "definition", "evidence"):
            _text(metric[key], "扩展指标说明", nonempty=True)
        for key in ("numerator", "denominator"):
            _number(metric[key], "扩展统计值")
    sections = config.get("sections", [])
    if not isinstance(sections, list):
        _fail("sections 必须是数组。")
    for section in sections:
        _object(section, {"title", "text", "columns", "rows"}, {"title", "text"}, "扩展章节")
        _text(section["title"], "章节标题", nonempty=True)
        _text(section["text"], "章节说明", nonempty=True)
        if ("columns" in section) != ("rows" in section):
            _fail("章节表格必须同时提供 columns 和 rows。")
        if "columns" in section:
            if not isinstance(section["columns"], list) or not section["columns"]:
                _fail("章节表头必须是非空数组。")
            for column in section["columns"]:
                _text(column, "表头", nonempty=True)
            if not isinstance(section["rows"], list):
                _fail("章节 rows 必须是数组。")
            if any(not isinstance(row, list) or len(row) != len(section["columns"]) for row in section["rows"]):
                _fail("章节表格每行必须与表头列数一致。")


def _ratio(numerator, denominator):
    return numerator / denominator if denominator else None


def _stats(records, judgment="reportPassed"):
    eligible = [r for r in records if r["completed"] is True and type(r[judgment]) is bool]
    passed = sum(r[judgment] is True for r in eligible)
    return {
        "total": len(records), "completed": sum(r["completed"] is True for r in records),
        "eligible": len(eligible), "passed": passed, "failed": len(eligible) - passed,
        "rate": _ratio(passed, len(eligible)), "excluded": len(records) - len(eligible),
        "incomplete": sum(r["completed"] is False for r in records),
        "completionUnknown": sum(r["completed"] is None for r in records),
        "judgmentUnknown": sum(r[judgment] is None for r in records),
        "completedJudgmentUnknown": sum(r["completed"] is True and r[judgment] is None for r in records),
        "errorStatus": sum((r["status"] or "").strip().lower() in ERROR_STATUSES for r in records),
    }


def _exact(value):
    # Type-tagged tuples avoid collisions between false/0, strings/numbers, or
    # missing/null. Object member order is not part of JSON value identity.
    if isinstance(value, dict):
        return ("object", tuple(sorted((key, _exact(item)) for key, item in value.items())))
    if isinstance(value, list):
        return ("array", tuple(_exact(item) for item in value))
    return (type(value).__name__, value)


def _outcome(record):
    if record["completed"] is not True or type(record["reportPassed"]) is not bool:
        return "excluded"
    return "passed" if record["reportPassed"] else "failed"


def _count(records):
    value = {"executions": len(records), "passed": 0, "failed": 0, "excluded": 0}
    for record in records:
        value[_outcome(record)] += 1
    return value


def _group(records):
    groups = {}
    for record in records:
        key = (_exact(record["input"]), _exact(record["context"]))
        group = groups.setdefault(key, {"input": record["input"], "context": record["context"], "records": [], "variants": {}})
        group["records"].append(record)
        variant = group["variants"].setdefault(_exact(record["actual"]), {"actual": record["actual"], "records": []})
        variant["records"].append(record)
    return list(groups.values())


def _breakdown(records, key):
    values = {}
    for record in records:
        container, field = (record, "scene") if key == "scene" else (record.get("attributes", {}), key.split(".", 1)[1])
        missing = field not in container
        value = None if missing else container[field]
        bucket = values.setdefault((missing, _exact(value)), {"value": value, "missing": missing, "records": []})
        bucket["records"].append(record)
    return {"field": key, "values": [{"value": b["value"], "missing": b["missing"], **_stats(b["records"])} for b in values.values()]}


def _display(value):
    return value if isinstance(value, str) else json.dumps(value, ensure_ascii=False, indent=2, allow_nan=False)


def _escape(value):
    return html.escape(str(value), quote=True)


def _pre(label, value):
    # A span prevents HTML's special removal of the first newline after <pre>.
    return '<div class="field"><h4>' + _escape(label) + '</h4><pre><span>' + _escape(_display(value)) + '</span></pre></div>'


def _percent(value):
    return "不适用" if value is None else f"{value * 100:.2f}%"


def _count_text(counts):
    return f'执行 {counts["executions"]} · 通过 {counts["passed"]} · 未通过 {counts["failed"]} · 未纳入统计 {counts["excluded"]}'


def _table(columns, rows):
    head = "".join("<th scope=\"col\">" + _escape(col) + "</th>" for col in columns)
    body = "".join("<tr>" + "".join("<td>" + _escape(_display(cell)) + "</td>" for cell in row) + "</tr>" for row in rows)
    return '<div class="table-wrap"><table><thead><tr>' + head + '</tr></thead><tbody>' + body + '</tbody></table></div>'


def _pager():
    return '<div class="pager controls"><button type="button" data-prev>上一页</button><span data-page role="status"></span><button type="button" data-next>下一页</button></div>'


STYLE = """
:root{font-family:system-ui,-apple-system,'PingFang SC','Microsoft YaHei',sans-serif;color:#152b3b;background:#f5f7f9;line-height:1.65}
*{box-sizing:border-box}body{margin:0}main{max-width:1180px;margin:auto;padding:36px 24px 64px}h1{font-size:30px;line-height:1.3;margin:0 0 12px}h2{font-size:22px;margin:0 0 18px}h3{font-size:18px;margin:0 0 12px}h4{font-size:14px;color:#496170;margin:8px 0}p{margin:8px 0 14px}.note{color:#496170}.warning{background:#fff1d6;border-left:4px solid #aa6300;padding:14px}
section{margin:30px 0;padding:26px;background:white;border:1px solid #dce4e9;border-radius:12px}.metrics{display:grid;grid-template-columns:repeat(auto-fit,minmax(150px,1fr));gap:12px}.metric{padding:16px;background:#f2f6f8;border-radius:8px}.metric strong{display:block;font-size:26px;color:#133e53}.metric span{font-size:14px}.card{border-top:1px solid #dce4e9;padding:24px 0}.card:first-of-type{border-top:0}.variant{border-left:3px solid #cbd9e2;margin:16px 0;padding:2px 0 2px 18px}.bad{color:#a82f2a}.mixed{font-weight:600;color:#865316}
pre{font:14px/1.6 ui-monospace,SFMono-Regular,Consolas,monospace;white-space:pre-wrap;overflow-wrap:anywhere;word-break:break-word;background:#f5f7f9;border:1px solid #e4eaef;padding:14px;border-radius:6px;margin:0 0 12px}button,input,select{font:inherit}button{padding:6px 14px;background:#fff;border:1px solid #9cabb8;border-radius:6px;cursor:pointer}button:disabled{opacity:.4;cursor:default}input[type=search]{width:min(540px,100%);padding:9px 12px;border:1px solid #9cabb8;border-radius:6px}.toolbar{display:flex;gap:14px;flex-wrap:wrap;align-items:center;position:sticky;top:0;background:#f5f7f9ef;padding:12px 0;z-index:2}.pager{display:flex;justify-content:center;gap:18px;align-items:center;margin:14px 0}.table-wrap{overflow-x:auto}table{border-collapse:collapse;width:100%;font-size:14px}th,td{border-bottom:1px solid #dce4e9;padding:10px;text-align:left;white-space:pre-wrap;overflow-wrap:anywhere}th{background:#f3f6f8}.empty{color:#496170}footer{font-size:13px;color:#496170}[hidden]{display:none!important}
@media(max-width:600px){main{padding:20px 12px}section{padding:18px 14px}h1{font-size:25px}}
@media print{body{background:white;color:black}main{max-width:none;padding:0}.controls{display:none!important}section{padding:12px;border:0;box-shadow:none}pre{font-size:10px}.card[hidden],.variant[hidden]{display:block!important}h2,h3,h4{break-after:avoid}pre,tr{break-inside:avoid}.metrics{grid-template-columns:repeat(4,1fr)}.metric strong{font-size:20px}}
"""

SCRIPT = """
(()=>{'use strict';
 const search=document.getElementById('search');
 const failedOnly=document.getElementById('failed-only');
 const size=document.getElementById('page-size');
 const lists=Array.from(document.querySelectorAll('[data-list]')).map(root=>({root,page:0,cards:Array.from(root.querySelectorAll(':scope > .card'))}));
 function render(state,reset){
  if(reset)state.page=0;
  const query=search.value.toLocaleLowerCase();
  const matches=state.cards.filter(card=>(!failedOnly.checked||Number(card.dataset.failed)>0)&&card.textContent.toLocaleLowerCase().includes(query));
  const count=Number(size.value);const pages=Math.max(1,Math.ceil(matches.length/count));
  state.page=Math.max(0,Math.min(state.page,pages-1));
  state.cards.forEach(card=>{card.hidden=true;card.querySelectorAll('.variant').forEach(v=>{v.hidden=failedOnly.checked&&Number(v.dataset.failed)===0;});});
  matches.slice(state.page*count,(state.page+1)*count).forEach(card=>{card.hidden=false;});
  state.root.querySelector('[data-page]').textContent='第 '+(state.page+1)+' / '+pages+' 页 · 匹配 '+matches.length+' 项';
  state.root.querySelector('[data-prev]').disabled=state.page===0;
  state.root.querySelector('[data-next]').disabled=state.page===pages-1;
 }
 lists.forEach(state=>{state.root.querySelector('[data-prev]').addEventListener('click',()=>{state.page--;render(state,false);});state.root.querySelector('[data-next]').addEventListener('click',()=>{state.page++;render(state,false);});render(state,true);});
 [search,failedOnly,size].forEach(control=>control.addEventListener('input',()=>lists.forEach(state=>render(state,true))));
 document.getElementById('print').addEventListener('click',()=>window.print());
})();
"""


def _render(records, groups, summary, config, source):
    title = config.get("title", "测试结果报告")
    parts = ['<!doctype html><html lang="zh-CN"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1">',
             '<meta http-equiv="Content-Security-Policy" content="default-src \'none\'; style-src \'unsafe-inline\'; script-src \'unsafe-inline\'; base-uri \'none\'; form-action \'none\'">',
             '<title>' + _escape(title) + '</title><style>' + STYLE + '</style></head><body><main><header><h1>' + _escape(title) + '</h1>',
             '<p class="note">本报告以执行记录为计数单位。输入、上下文、实际输出和对应期待均保留完整内容；文本按原文展示。</p></header>']
    if source.get("scopeVerified") is False:
        parts.append('<p class="warning">来源组织 / 任务范围未核实：数量完整性检查通过，不代表组织或任务范围已验证。</p>')
    parts.append('<div class="toolbar controls"><label for="search">搜索原文</label><input id="search" type="search" placeholder="输入、上下文、输出、期待或原因"><label><input id="failed-only" type="checkbox">只看未通过</label><label>每页 <select id="page-size"><option>10</option><option>25</option><option>50</option></select> 项</label><button id="print" type="button">打印 / 保存 PDF</button></div>')
    parts.append('<section id="overview"><h2>结果总览</h2><div class="metrics">')
    for label, value in (("总执行数", summary["total"]), ("可判定数（通过率分母）", summary["eligible"]), ("通过", summary["passed"]),
                         ("未通过", summary["failed"]), ("通过率", _percent(summary["rate"])), ("未完成", summary["incomplete"]),
                         ("完成状态未知", summary["completionUnknown"]), ("判定缺失（全部执行）", summary["judgmentUnknown"]), ("异常状态", summary["errorStatus"])):
        parts.append('<div class="metric"><span>' + _escape(label) + '</span><strong>' + _escape(value) + '</strong></div>')
    parts.append('</div><p class="note">通过率 = 通过数 ÷ 可判定数。仅 completed = true 且报告判定为 true / false 的执行纳入分母。未知判定、未完成与完成状态未知均不计为失败；分母为零时显示“不适用”。</p>')
    parts.append('<p class="note">已完成 ' + str(summary["completed"]) + ' 次，其中已完成但判定缺失 ' + str(summary["completedJudgmentUnknown"]) + ' 次；未纳入通过率统计共 ' + str(summary["excluded"]) + ' 次。判定缺失与完成状态指标可能重叠。</p>')
    parts.append('<p class="note">异常状态单独计数：error、errored、failed、failure、exception、timeout、timed_out、cancelled、canceled、aborted（忽略大小写及首尾空格）。异常标记本身不改变分母规则。</p>')
    if summary["adjustmentCount"]:
        original = summary["original"]
        parts.append('<p>本报告使用 ' + str(summary["adjustmentCount"]) + ' 条经用户确认的独立判定调整。系统原判定：通过 ' + str(original["passed"]) + ' / 可判定 ' + str(original["eligible"]) + '，通过率 ' + _percent(original["rate"]) + '。原始输入、输出和系统判定未被改写。</p>')
    parts.append('</section>')
    if "handoff" in summary:
        handoff = summary["handoff"]
        parts.append('<section id="handoff"><h2>转人工情况</h2>' + _table(["已完成", "已知转接结果（分母）", "已转人工", "未转人工", "未知", "转人工率", "识别覆盖率"],
                     [[handoff["completed"], handoff["known"], handoff["transferred"], handoff["notTransferred"], handoff["unknown"], _percent(handoff["rate"]), _percent(handoff["coverage"])]]) +
                     '<p class="note">转人工率 = 已完成且明确已转人工的执行数 ÷ 已完成且转接结果已知的执行数；识别覆盖率 = 已知数 ÷ 已完成数。一个执行多次转接只计一次。仅依据结构化证据或完整轨迹补充，回复中出现“为您转人工”不视为发生转接。转人工不改变通过率。</p></section>')
    if "duration" in summary:
        duration = summary["duration"]
        parts.append('<section id="duration"><h2>执行耗时</h2>' + _table(["有效样本", "缺失", "平均（毫秒）", "最短（毫秒）", "最长（毫秒）"],
                     [[duration["samples"], duration["missing"], *["不适用" if duration[k] is None else duration[k] for k in ("meanMs", "minMs", "maxMs")]]]) +
                     '<p class="note">仅纳入已完成执行中单位已确认为毫秒、非负且有限的 durationMs。缺失数以已完成执行为范围；不推断其他单位。</p></section>')
    for breakdown in summary.get("groupBreakdowns", []):
        rows = [["（字段缺失）" if v["missing"] else _display(v["value"]), v["total"], v["eligible"], v["passed"], v["failed"], _percent(v["rate"])] for v in breakdown["values"]]
        parts.append('<section><h2>分组统计：' + _escape(breakdown["field"]) + '</h2>' + _table(["分组值", "执行数", "可判定数", "通过", "未通过", "通过率"], rows) + '<p class="note">按字段的完整 JSON 值精确分组，缺失字段单列；不进行跨批次匹配。</p></section>')
    if config.get("metrics"):
        rows = []
        for metric in config["metrics"]:
            ratio = _ratio(metric["numerator"], metric["denominator"])
            rendered = _percent(ratio) if metric["unit"] == "%" else ("不适用" if ratio is None else str(ratio) + " " + metric["unit"])
            rows.append([metric["label"], metric["numerator"], metric["denominator"], rendered, metric["definition"], metric["evidence"]])
        parts.append('<section><h2>扩展指标</h2><p class="note">扩展统计由提供者依据本批数据计算；渲染器校验格式与比例，不代替业务核对。口径和证据须支持从本批数据重新计算；不执行表达式。</p>' + _table(["指标", "分子", "分母", "结果", "统计口径", "依据"], rows) + '</section>')
    for section in config.get("sections", []):
        parts.append('<section><h2>' + _escape(section["title"]) + '</h2>' + _pre("分析说明", section["text"]))
        if "columns" in section:
            parts.append(_table(section["columns"], section["rows"]))
        parts.append('</section>')
    failures = [r for r in records if _outcome(r) == "failed"]
    parts.append('<section id="failures" data-list><h2>未通过案例 · ' + str(len(failures)) + ' 次执行</h2><p class="note">以下逐次列出报告判定未通过的完整案例；每条期待、断言实际值及判定原因保留对应关系。</p>')
    for index, record in enumerate(failures, 1):
        parts.append('<article class="card" data-failed="1"><h3 class="bad">案例 ' + str(index) + ' · ' + _escape(record.get("caseName") or "未命名案例") + '</h3>')
        for label, key in (("实际输入", "input"), ("执行上下文", "context"), ("实际输出", "actual")):
            parts.append(_pre(label, record[key]))
        if record["assertions"]:
            for n, assertion in enumerate(record["assertions"], 1):
                parts.append('<div class="variant"><h4>断言 ' + str(n) + ' · ' + _escape(assertion.get("type") or "未提供类型") + '</h4>')
                for label, key in (("断言实际值", "actualValue"), ("对应期待", "expectedDescription"), ("对应判定原因", "llmReason"), ("系统断言判定", "passed")):
                    parts.append(_pre(label, assertion.get(key)))
                parts.append('</div>')
        else:
            parts.append('<p class="note">未提供对应期待或判定原因，无法从本报告补充推断。</p>')
        if "adjustment" in record:
            parts.append(_pre("用户确认的调整原因", record["adjustment"]["reason"]) + _pre("调整依据", record["adjustment"]["evidence"]))
        parts.append('</article>')
    if not failures:
        parts.append('<p class="empty">本批次没有纳入通过率统计的未通过案例。</p>')
    parts.append(_pager() + '</section>')
    parts.append('<section id="all-groups" data-list><h2>全部实际输入输出 · ' + str(summary["groups"]) + ' 组 / ' + str(summary["variants"]) + ' 种输出</h2><p class="note">以精确的（input, context）分组，同组内仅合并完全相同的实际输出。保留大小写、空白、数组顺序与 JSON 类型；对象键顺序不影响等价。相同输入输出的不同判定均单独计数。所有记录均已嵌入本文件，搜索与分页仅影响屏幕显示；打印包含全部内容。</p>')
    for index, group in enumerate(groups, 1):
        counts = _count(group["records"])
        parts.append('<article class="card" data-failed="' + str(counts["failed"]) + '"><h3>输入组 ' + str(index) + '</h3><p>' + _count_text(counts) + '</p>' + _pre("实际输入", group["input"]) + _pre("执行上下文", group["context"]))
        for number, variant in enumerate(group["variants"].values(), 1):
            counts = _count(variant["records"])
            parts.append('<div class="variant" data-failed="' + str(counts["failed"]) + '"><h4>实际输出 ' + str(number) + '</h4><p>' + _count_text(counts) + '</p>')
            if counts["passed"] and counts["failed"]:
                parts.append('<p class="mixed">混合判定：相同文本存在通过和未通过记录。</p>')
            parts.append(_pre("完整实际输出", variant["actual"]))
            for record in variant["records"]:
                if "adjustment" in record:
                    parts.append(_pre("经用户确认的独立判定调整", {"systemPassed": record["originalPassed"], "reportPassed": record["reportPassed"], **record["adjustment"]}))
            parts.append('</div>')
        parts.append('</article>')
    if not groups:
        parts.append('<p class="empty">本批次没有执行记录。</p>')
    parts.append(_pager() + '</section><footer>本报告不嵌入执行 ID、来源 URL、组织标识或原始 API 元数据。实际业务文本以规范化数据为准。</footer>')
    summary_json = json.dumps(summary, ensure_ascii=False, allow_nan=False).replace("&", "\\u0026").replace("<", "\\u003c").replace(">", "\\u003e").replace("\u2028", "\\u2028").replace("\u2029", "\\u2029")
    parts.append('<script type="application/json" id="report-summary">' + summary_json + '</script><script>' + SCRIPT + '</script></main></body></html>')
    return "".join(parts)


def build_report(dataset, adjustments=None, annotations=None, config=None):
    """Return (HTML, sanitized summary) without mutating any caller-owned value."""
    config = {} if config is None else config
    for value in (dataset, adjustments, annotations, config):
        _safe_json(value)
    _validate_dataset(dataset)
    _validate_config(config)
    records = copy.deepcopy(dataset["records"])
    _apply_extensions(records, adjustments, annotations)
    groups = _group(records)
    summary = {"schemaVersion": 1, "title": config.get("title", "测试结果报告"), **_stats(records),
               "original": _stats(records, "originalPassed"), "adjustmentCount": sum("adjustment" in r for r in records),
               "groups": len(groups), "variants": sum(len(g["variants"]) for g in groups),
               "groupCounts": [{**_count(g["records"]), "variantCounts": [_count(v["records"]) for v in g["variants"].values()]} for g in groups]}
    completed = [r for r in records if r["completed"] is True]
    if config.get("includeHandoff"):
        known = [r for r in completed if type(r.get("handoff")) is bool]
        transferred = sum(r["handoff"] is True for r in known)
        summary["handoff"] = {"completed": len(completed), "known": len(known), "transferred": transferred,
                              "notTransferred": len(known) - transferred, "unknown": len(completed) - len(known),
                              "rate": _ratio(transferred, len(known)), "coverage": _ratio(len(known), len(completed))}
    if config.get("includeDuration"):
        durations = [r["durationMs"] for r in completed if r.get("durationMs") is not None]
        summary["duration"] = {"completed": len(completed), "samples": len(durations), "missing": len(completed) - len(durations),
                               "meanMs": sum(durations) / len(durations) if durations else None,
                               "minMs": min(durations) if durations else None, "maxMs": max(durations) if durations else None}
    if config.get("groupBy"):
        summary["groupBreakdowns"] = [_breakdown(records, key) for key in config["groupBy"]]
    return _render(records, groups, summary, config, dataset["source"]), summary


def _json_pairs(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            _fail("JSON 包含重复字段。")
        result[key] = value
    return result


def _read_json(path):
    def invalid_constant(_value):
        _fail("JSON 包含非有限数。")
    try:
        with open(path, encoding="utf-8-sig") as handle:
            return json.load(handle, object_pairs_hook=_json_pairs, parse_constant=invalid_constant)
    except (OSError, UnicodeError, json.JSONDecodeError):
        _fail("无法读取有效的 UTF-8 JSON 输入文件。")


def _write_outputs(directory, report, summary):
    directory.mkdir(mode=0o700, parents=True, exist_ok=True)
    outputs = [(directory / "report.html", report),
               (directory / "report-summary.json", json.dumps(summary, ensure_ascii=False, indent=2, allow_nan=False) + "\n")]
    if any(path.exists() or path.is_symlink() for path, _ in outputs):
        _fail("输出已存在，拒绝覆盖；请使用新的输出目录。")
    created = []
    try:
        for path, content in outputs:
            fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
            created.append(path)
            with os.fdopen(fd, "w", encoding="utf-8", newline="\n") as handle:
                os.fchmod(handle.fileno(), 0o600)
                handle.write(content)
    except (OSError, UnicodeError):
        for path in created:
            path.unlink(missing_ok=True)
        _fail("无法安全写入报告；未覆盖已有文件。")


class _ArgumentParser(argparse.ArgumentParser):
    def error(self, message):
        _fail("命令行参数无效；请使用 --help 查看用法。")


def main(argv=None):
    parser = _ArgumentParser(description="由完整规范化数据生成本地中文报告；不会联网或覆盖已有文件。")
    parser.add_argument("--input", required=True, help="schemaVersion=1 的完整规范化 JSON")
    parser.add_argument("--output-dir", required=True, help="生成 report.html 与 report-summary.json 的目录")
    parser.add_argument("--adjustments", help="经用户确认的独立判定调整 JSON")
    parser.add_argument("--annotations", help="独立证据补充 JSON")
    parser.add_argument("--config", help="可选报告配置 JSON")
    try:
        args = parser.parse_args(argv)
        report, summary = build_report(_read_json(args.input),
                                       adjustments=_read_json(args.adjustments) if args.adjustments else None,
                                       annotations=_read_json(args.annotations) if args.annotations else None,
                                       config=_read_json(args.config) if args.config else None)
        _write_outputs(Path(args.output_dir), report, summary)
    except (ValueError, OSError):
        error = sys.exc_info()[1]
        print("报告生成失败：" + (str(error) if isinstance(error, ValueError) else "无法安全访问输入或输出文件。"), file=sys.stderr)
        return 2
    print("已生成 report.html 和 report-summary.json（文件权限 0600）。")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
