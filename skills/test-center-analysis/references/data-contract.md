# 本地数据契约

所有示例使用合成数据。生产原始导出、规范化数据、判定调整、补充证据和报告放在任务独立目录，禁止提交到技能仓库。

## 规范化数据（schemaVersion = 1）

```json
{
  "schemaVersion": 1,
  "source": {"complete": true, "total": 1, "scope": "task"},
  "records": [{
    "id": "execution-example",
    "caseName": "示例测试",
    "input": "示例输入",
    "context": {},
    "actual": "示例输出",
    "assertions": [{"type": "send-text-message", "actualValue": "示例输出", "expectedDescription": "返回示例内容", "llmReason": "内容一致", "passed": true}],
    "originalPassed": true,
    "status": "success",
    "completed": true,
    "scene": "示例场景",
    "handoff": null,
    "handoffEvidence": null,
    "durationMs": null,
    "attributes": {}
  }]
}
```

`id` 是稳定的执行记录标识，不是输入文本、案例名或排序序号。`originalPassed`、`completed` 和 `handoff` 都是 true / false / null；null 表示未知，不得当 false。`input`、`actual`、`context` 可为 JSON 值，字符串必须保留原文。多轮上下文、非文本输入、多动作输出均不能只取第一项。`assertions` 保留完整原始断言对象，允许 API 附加字段；报告仅展示已知的实际值、期待、原因、类型和判定，不公开其他原始元数据。原始完整 API 响应另存以便追溯，不渲染整个原始对象到公开报告。

## `md test results` 单任务映射

`scripts/prepare_md_results.py` 只接受一个任务的 `--deep --out ...jsonl` 结果，产出同一个 `schemaVersion = 1` 契约。`source` 明确记录单任务范围和数据局限：

```json
{
  "schemaVersion": 1,
  "source": {
    "complete": true,
    "scope": "task",
    "scopeVerified": true,
    "total": 1,
    "warnings": ["JSONL 不含完整多轮上下文；context 保持空对象。"]
  },
  "records": []
}
```

关键映射：

- `task` + `testExecId` → `id`：非空 `testExecId` 优先；缺失时使用 `task + caseId + 同任务稳定序号` 组成 `md:<task>:<caseId>:<ordinal>` fallback。`caseId` 也为空时拒绝记录；生成后检查全部 `id` 唯一，不允许重复执行标识。
- `name` → `caseName`，空字符串转为 null；`user` → `input`，保留原文。
- `reply` 与 `actions` 一起组成 `actual = {"reply": ..., "actions": ...}`，即使其中一项为空也保留原值。
- `expect` / `verdict` / `reply` / `actions` / `passed` 生成单个 `assertions` 项：`type = "miaodong-test-result"`，`actualValue` 是上述 `actual`，`expectedDescription` 来自 `expect`，`llmReason` 来自 `verdict`，`passed` 在正常执行时来自源 `passed`。
- 源 `passed` → `originalPassed`，仅在正常执行时保留布尔值。`noop = true` 或 `notRun = true` 时，`originalPassed` 和断言 `passed` 都是 null，表示不纳入业务判定，不得当成 false。
- JSONL 不提供完整多轮上下文，因此每条 `context = {}`，并在 `source.warnings` 披露；不把空对象说成“无上下文”。
- 正常执行的 `status = "completed"` 且 `completed = true`；`noop = true` 映射为 `status = "noop"` 且 `completed = true`；`notRun = true` 映射为 `status = "not_run"` 且 `completed = false`。
- `scenario` → `scene`，`ms` → `durationMs`。`task`、`caseId`、`execId`、`testExecId`、`online` 和 `cost` 放入 `attributes`，仅作私有追溯，默认不渲染到公开报告。
- `handoff = null` 且 `handoffEvidence = null`。扁平的回复与动作摘要不足以证明真实转接；只能通过已核验的完整轨迹补充。

`raw-md-results.jsonl` 和规范化后的 `dataset.json` 均是私有追溯文件，不属于可发布内容。

## 判定调整（独立文件）

```json
{"adjustments": [{"recordId": "execution-example", "reportPassed": true, "reason": "经确认预期存在错误", "evidence": "用户确认的业务规则或可核验依据", "confirmedByUser": true}]}
```

仅匹配精确执行 ID；不存在、重复、未确认或缺少依据的调整必须拒绝。调整不能修改原始输入、输出、系统判定或执行完成状态，也不能将未完成记录硬算为通过。

## 可选证据补充（独立文件）

```json
{"annotations": [{"recordId": "execution-example", "handoff": true, "handoffEvidence": "已核对完整执行轨迹，存在成功的转接事件", "attributes": {"channel": "示例渠道"}}]}
```

证据补充由 agent 在核对源字段或执行轨迹后生成；handoff 为 false 也必须有覆盖整个执行轨迹的证据。单凭回复“为您转人工”、预期动作或某个词语不算实际转接。允许补充 `scene`、`durationMs`（非负数）、`attributes`，不允许补充文件悄悄更改原判定、输入、输出或完成状态。

## 报告选项

```json
{
  "title": "测试结果报告",
  "includeHandoff": true,
  "includeDuration": false,
  "groupBy": ["scene", "attributes.channel"],
  "metrics": [{"label": "自定义指标", "numerator": 1, "denominator": 2, "unit": "%", "definition": "明确的分子和分母定义", "evidence": "计算字段、规则与范围"}],
  "sections": [{"title": "业务分析", "text": "有证据支持的说明", "columns": ["分类", "次数"], "rows": [["示例", 1]]}]
}
```

基础通过率、未通过案例、全部去重实际输入输出始终保留。用户额外章节通过 metrics / sections 添加；这不是不受限制的代码执行或 HTML 注入接口，所有内容按文本转义。扩展统计值必须能由本次数据重新计算，并说明证据；renderer 不替代业务核对。不支持的字段或不完整统计说明应报错，不假装已有能力。

## 默认统计口径

- 业务通过率分母：`completed is true`、不是 `noop` / `not_run` / 错误或未知状态，且报告判定为布尔值的正常执行数；通过数除以该分母。总记录、空跑、未跑、状态未知、判定缺失和异常状态另列。零分母显示“不适用”，不是 0%。
- 去重键：精确的 `(input, context)`；其下精确 `actual` 合并，保留通过／未通过／未纳入统计次数。同文异判不强制合成单一结论。
- 转人工率：正常完成且 `handoff` 明确的执行为分母，true 为分子，同时列出正常完成执行中未知数量和识别覆盖率；一个执行多次转接只计一次。该口径不要求业务判定已知，任何转人工结果都不直接改变通过率。
- 耗时只纳入正常完成、单位已确认、非负且有限的 `durationMs`，报告有效样本数及缺失数；该口径不要求业务判定已知，不得猜测 API 单位。
- 分组统计不跨批次自动对齐；版本对比需要明确匹配键、相同统计口径和另一批数据。
