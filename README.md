# 测试结果分析与报告 Skill

用于秒懂 / JZ Insight / Agent Test Lab 已执行测试任务。支持两种方式，也可以组合：

1. **检查未通过是否真的失败**：逐条给出确实未通过、疑似误判或证据不足的结论与依据。
2. **按需求生成测试报告**：通过率、未通过案例、全部去重实际输入输出；按证据增加转人工率、分场景统计、耗时、稳定性及业务章节。

**发布是最后一步，发布前先问给客户还是同事看。** 同事默认公司登录可见，客户免登录可见。仅生成报告不自动上传。

## 安装

仓库：[yh169499-rgb/test-center-analysis-skill](https://github.com/yh169499-rgb/test-center-analysis-skill)。在 Codex 中提供本仓库地址并说：

> 请安装 https://github.com/yh169499-rgb/test-center-analysis-skill 的 `skills/test-center-analysis` 技能，使用 `codex/initial-skill` 分支。

或将 `skills/test-center-analysis` 整个目录放入自己的 `~/.codex/skills/`；Claude Code 使用 `~/.claude/skills/`。Python 3.10+，脚本仅使用标准库。Codex 安装后下一轮即可使用；Claude Code 可新开会话加载。

HTML 发布是可选能力，需另行安装公司的 `html-gallery` 并使用**自己的飞书账号**授权；仓库不携带任何登录凭证，也不会自动运行远程安装器。

## 怎么用

- “用 `$test-center-analysis` 看看这个任务的未通过是不是误判。”
- “生成客户版测试报告，展示通过率、失败案例、全部不重复的输入输出，再加转人工率。”
- “先检查未通过，再根据确认结果出报告，最后发布；发布前问我给谁看。”
- “只生成 HTML，先不要发布。”

可给完整 JSON 导出；使用接口时提供当前任务的 **Request URL** 和 **Authorization**，不要给 Cookie。获取方法见[操作指引](skills/test-center-analysis/references/intake.md)。不要把授权值写到命令参数、仓库、报告或公开聊天中。

## 完整链路

确定模式与指标 → 获取完整数据 → 按需分析失败 → 确认必要的判定调整 → 去重统计 → 生成 HTML → 校验 → 询问受众 → 按需最后发布。

通过率按执行记录计算，不以去重条目为分母。未知判定/未知转人工单列；实际转人工与回复声称转人工分开；业务调整保留原始记录。数据不足的额外指标明确标注无法统计，不编造。

## 本地运行与验证

从仓库根目录运行：

```bash
python3 -m unittest discover -s tests -v
python3 skills/test-center-analysis/scripts/prepare_results.py --input examples/synthetic-results.json --output-dir /tmp/test-center-example-data
python3 skills/test-center-analysis/scripts/build_report.py --input /tmp/test-center-example-data/dataset.json --annotations examples/synthetic-annotations.json --config examples/report-config.json --output-dir /tmp/test-center-example-report
```

输出目录中如已有同名产物，改用新目录，避免覆盖历史。示例数据完全合成，不会访问任何生产接口或自动发布。

协议与扩展：[数据契约](skills/test-center-analysis/references/data-contract.md)、[误判检查](skills/test-center-analysis/references/review.md)、[指标口径](skills/test-center-analysis/references/metrics.md)、[发布规则](skills/test-center-analysis/references/publishing.md)。

## 安全与边界

- 只读取测试结果，不创建用例、重跑测试或回写后台判定。
- 保留全部分页、断言、上下文和实际原文，不用第一条断言代表完整执行。
- 原始数据、报告、用户截图及令牌不属于可分享技能包。
- HTML 输出安全转义；自定义指标仍需来源和可复算依据，不执行用户提供的表达式。
- Python 测试覆盖确定性数据处理；语义正确性与业务预期仍由证据和必要的用户确认决定。
