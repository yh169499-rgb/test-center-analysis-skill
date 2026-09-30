#!/usr/bin/env python3
"""Validate and normalize one task exported by ``md test results``.

The adapter is deliberately local and deterministic. It never logs source
values, and it writes the raw and normalized artifacts as private files.
"""

import argparse
import importlib.util
import json
import math
import os
from pathlib import Path
import sys


class MdPreparationError(ValueError):
    """Raised when an md result export cannot be prepared safely."""


_BUILD_REPORT_PATH = Path(__file__).with_name("build_report.py")
_BUILD_REPORT_SPEC = importlib.util.spec_from_file_location(
    "test_center_analysis_build_report", _BUILD_REPORT_PATH
)
if _BUILD_REPORT_SPEC is None or _BUILD_REPORT_SPEC.loader is None:
    raise ImportError("无法加载报告安全校验。")
_BUILD_REPORT = importlib.util.module_from_spec(_BUILD_REPORT_SPEC)
_BUILD_REPORT_SPEC.loader.exec_module(_BUILD_REPORT)
validate_no_credentials = _BUILD_REPORT.validate_no_credentials


_TEXT_FIELDS = (
    "task", "taskName", "name", "caseId", "execId", "scenario", "user",
    "expect", "verdict", "reply", "actions", "testExecId", "online",
)
_BOOL_FIELDS = ("passed", "noop", "notRun")
_REQUIRED_FIELDS = set(_TEXT_FIELDS) | set(_BOOL_FIELDS) | {"cost", "ms"}
_OUTPUT_NAMES = ("raw-md-results.jsonl", "dataset.json")
_OS_OPEN = os.open
_OS_UNLINK = os.unlink


def _fail(message):
    raise MdPreparationError(message)


def _json_object(pairs):
    value = {}
    for key, item in pairs:
        if key in value:
            _fail("JSONL 包含重复字段。")
        value[key] = item
    return value


def _invalid_constant(_value):
    _fail("JSONL 包含非有限数。")


def _decode_rows(raw_bytes):
    try:
        text = raw_bytes.decode("utf-8")
    except UnicodeError:
        _fail("无法读取严格 UTF-8 JSONL 输入文件。")
    if not text:
        _fail("JSONL 输入文件不能为空。")
    lines = text.split("\n")
    if lines[-1] == "":
        lines.pop()
    rows = []
    for line in lines:
        if line.endswith("\r"):
            line = line[:-1]
        if not line.strip():
            _fail("JSONL 不允许空行。")
        try:
            row = json.loads(
                line,
                object_pairs_hook=_json_object,
                parse_constant=_invalid_constant,
            )
        except (json.JSONDecodeError, UnicodeError, RecursionError):
            _fail("无法读取有效的逐行 JSON 对象。")
        if not isinstance(row, dict):
            _fail("JSONL 每行必须是 JSON 对象。")
        rows.append(row)
    if not rows:
        _fail("JSONL 输入文件不能为空。")
    return rows


def read_md_jsonl(path):
    """Return ``(raw_bytes, rows)`` after strict JSONL decoding."""
    try:
        raw_bytes = Path(path).read_bytes()
    except OSError:
        _fail("无法读取 JSONL 输入文件。")
    return raw_bytes, _decode_rows(raw_bytes)


def _validate_text(row, field, *, nonempty=False):
    value = row[field]
    if not isinstance(value, str) or (nonempty and not value.strip()):
        _fail("JSONL 已知字段类型无效。")


def _validate_number(value, *, nullable=False):
    if nullable and value is None:
        return
    if type(value) is int:
        valid = value >= 0
    else:
        valid = type(value) is float and math.isfinite(value) and value >= 0
    if not valid:
        _fail("JSONL 数值字段必须是非负有限数。")


def _stable_id(row, ordinal):
    if row["testExecId"].strip():
        return row["testExecId"]
    if not row["caseId"].strip():
        _fail("记录缺少可用的稳定执行标识。")
    return "md:{task}:{case}:{ordinal}".format(
        task=row["task"], case=row["caseId"], ordinal=ordinal
    )


def _require_safe_output_capabilities():
    flags = ("O_RDONLY", "O_WRONLY", "O_CREAT", "O_EXCL", "O_DIRECTORY", "O_NOFOLLOW")
    supports_dir_fd = getattr(os, "supports_dir_fd", ())
    if (any(type(getattr(os, name, None)) is not int for name in flags)
            or not callable(getattr(os, "fchmod", None))
            or not callable(getattr(os, "fstat", None))
            or _OS_OPEN not in supports_dir_fd
            or _OS_UNLINK not in supports_dir_fd):
        _fail("当前环境不支持安全输出。")


def _directory_flags():
    return os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW


def normalize_md_rows(rows):
    """Return a schemaVersion=1 dataset without mutating ``rows``."""
    if not isinstance(rows, list) or not rows:
        _fail("JSONL 记录必须是非空数组。")

    # Scan the complete parsed input before selecting or mapping any fields so
    # credentials hidden in taskName or future/unknown fields are still caught.
    validate_no_credentials(rows)

    task = None
    records = []
    record_ids = set()
    for ordinal, row in enumerate(rows, 1):
        if not isinstance(row, dict):
            _fail("JSONL 每行必须是 JSON 对象。")
        if _REQUIRED_FIELDS - set(row):
            _fail("JSONL 记录缺少必要字段。")
        for field in _TEXT_FIELDS:
            _validate_text(row, field, nonempty=(field == "task"))
        for field in _BOOL_FIELDS:
            if type(row[field]) is not bool:
                _fail("JSONL 布尔字段类型无效。")
        _validate_number(row["cost"], nullable=True)
        _validate_number(row["ms"])

        if task is None:
            task = row["task"]
        elif row["task"] != task:
            _fail("JSONL 必须只包含一个测试任务。")

        record_id = _stable_id(row, ordinal)
        if record_id in record_ids:
            _fail("JSONL 包含重复的稳定执行标识。")
        record_ids.add(record_id)

        actual = {"reply": row["reply"], "actions": row["actions"]}
        normal = not row["noop"] and not row["notRun"]
        records.append({
            "id": record_id,
            "caseName": row["name"] or None,
            "input": row["user"],
            "context": {},
            "actual": actual,
            "assertions": [{
                "type": "miaodong-test-result",
                "actualValue": actual.copy(),
                "expectedDescription": row["expect"],
                "llmReason": row["verdict"],
                "passed": row["passed"] if normal else None,
            }],
            "originalPassed": row["passed"] if normal else None,
            "status": "not_run" if row["notRun"] else "noop" if row["noop"] else "completed",
            "completed": False if row["notRun"] else True,
            "scene": row["scenario"] or None,
            "handoff": None,
            "handoffEvidence": None,
            "durationMs": row["ms"],
            "attributes": {
                "task": row["task"],
                "caseId": row["caseId"],
                "execId": row["execId"],
                "testExecId": row["testExecId"],
                "online": row["online"],
                "cost": row["cost"],
            },
        })

    dataset = {
        "schemaVersion": 1,
        "source": {
            "complete": True,
            "scope": "task",
            "scopeVerified": True,
            "total": len(records),
            "warnings": ["JSONL 不含完整多轮上下文；context 保持空对象。"],
        },
        "records": records,
    }
    validate_no_credentials(dataset)
    return dataset


def _open_output_directory(output_dir):
    path = Path(output_dir)
    if path.is_symlink():
        _fail("输出目录不能是符号链接。")
    try:
        path.mkdir(mode=0o700, parents=True, exist_ok=True)
        return path, os.open(path, _directory_flags())
    except OSError:
        _fail("无法安全创建输出目录。")


def _write_private_file(dir_fd, name, payload):
    flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW
    descriptor = os.open(name, flags, 0o600, dir_fd=dir_fd)
    error = None
    try:
        os.fchmod(descriptor, 0o600)
        with os.fdopen(descriptor, "wb", closefd=False) as stream:
            stream.write(payload)
            stream.flush()
            os.fsync(stream.fileno())
    except BaseException as caught:
        error = caught
    try:
        os.close(descriptor)
    except BaseException as caught:
        if error is None:
            error = caught
    if error is not None:
        try:
            os.unlink(name, dir_fd=dir_fd)
        except OSError:
            pass
        raise error


def _unlink_outputs(dir_fd, names):
    remaining = []
    for name in names:
        try:
            os.unlink(name, dir_fd=dir_fd)
        except OSError:
            remaining.append(name)
    return remaining


def _recover_cleanup(directory, identity, names):
    if not names:
        return
    recovery_fd = None
    try:
        recovery_fd = os.open(directory, _directory_flags())
        recovered = os.fstat(recovery_fd)
        if (recovered.st_dev, recovered.st_ino) != identity:
            return
        _unlink_outputs(recovery_fd, names)
    except OSError:
        return
    finally:
        if recovery_fd is not None:
            try:
                os.close(recovery_fd)
            except OSError:
                pass


def write_outputs(output_dir, raw_bytes, dataset):
    """Write private raw and normalized artifacts without overwriting files."""
    _require_safe_output_capabilities()
    if not isinstance(raw_bytes, bytes):
        _fail("原始 JSONL 内容必须是字节。")
    validate_no_credentials(dataset)
    try:
        dataset_bytes = (
            json.dumps(dataset, ensure_ascii=False, indent=2, allow_nan=False) + "\n"
        ).encode("utf-8")
    except (TypeError, ValueError, UnicodeError):
        _fail("无法编码规范化数据集。")

    directory, dir_fd = _open_output_directory(output_dir)
    opened = os.fstat(dir_fd)
    identity = (opened.st_dev, opened.st_ino)
    created = []
    pending_cleanup = []
    error = None
    try:
        for name, payload in zip(_OUTPUT_NAMES, (raw_bytes, dataset_bytes)):
            _write_private_file(dir_fd, name, payload)
            created.append(name)
    except BaseException as caught:
        error = caught
        pending_cleanup = _unlink_outputs(dir_fd, created)
    try:
        os.close(dir_fd)
    except BaseException as caught:
        if error is None:
            error = caught
            pending_cleanup = list(created)
    _recover_cleanup(directory, identity, pending_cleanup)
    if error is not None:
        raise error


class _SafeArgumentParser(argparse.ArgumentParser):
    def error(self, _message):
        raise MdPreparationError("命令参数无效。")


def main(argv=None):
    parser = _SafeArgumentParser(description="转换 md test results JSONL。")
    parser.add_argument("--input", required=True)
    parser.add_argument("--output-dir", required=True)
    try:
        args = parser.parse_args(argv)
        input_path = Path(args.input)
        output_dir = Path(args.output_dir)
        if not input_path.is_absolute() or not output_dir.is_absolute():
            _fail("输入和输出目录必须使用绝对路径。")
        _require_safe_output_capabilities()
        raw_bytes, rows = read_md_jsonl(input_path)
        dataset = normalize_md_rows(rows)
        write_outputs(output_dir, raw_bytes, dataset)
        return 0
    except (MdPreparationError, ValueError, OSError, TypeError):
        print("转换失败：请检查 JSONL 格式、字段、凭据和输出目录。", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
