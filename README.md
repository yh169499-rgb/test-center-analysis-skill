# 测试结果分析与报告 Skill

用于秒懂 / JZ Insight / Agent Test Lab 测试任务。既可以从“智能体 + 测试集”开始跑测试并出报告，也可以接手已有任务，或继续使用 API / 本地 JSON 做分析。报告支持业务通过率、未通过案例、空跑/未跑披露、全部去重实际输入输出，以及有证据支持的转人工率、场景和耗时统计。

**生成报告不代表授权发布。** 未要求发布时只交付本地 HTML。

## 依赖矩阵

| 能力 | 必需 | 说明 |
| --- | --- | --- |
| 从测试集运行完整链路，或读取已有任务 | [`miaodong-cli`](https://github.com/Spider615/miaodong-cli) | 只有这两个入口需要 `md`；它负责跑前检查、费用/插件确认、状态和 JSONL 导出 |
| 分析 API JSON、本地 JSON 或生成本地 HTML | Python 3.10+ | 不依赖 `md`；脚本只使用标准库 |
| 公网发布或更新报告 | 公司 `html-gallery` 技能 | 仅在用户明确要求发布时需要，且使用用户自己的飞书账号授权 |

## 安装

技能仓库：[yh169499-rgb/test-center-analysis-skill](https://github.com/yh169499-rgb/test-center-analysis-skill)。在 Codex 中可提供仓库地址并说：

> 请安装 https://github.com/yh169499-rgb/test-center-analysis-skill 的 `skills/test-center-analysis` 技能，使用 `codex/initial-skill` 分支。

也可将 `skills/test-center-analysis` 整个目录放入自己的 Codex 或 Claude Code 技能目录。需要“跑测试→等任务→出报告”的完整链路时，再按 [`miaodong-cli`](https://github.com/Spider615/miaodong-cli) 项目说明单独安装并授权；一台机器通常只需安装一次。只分析已有 API / JSON 时无需安装 `md`。

`html-gallery` 是发布时的可选依赖，不会因为安装或使用本技能而自动上传报告。

## 用户需提供什么

- **从测试集开始**：智能体和测试集。版本、轮数、并发、任务名只在有明确要求时使用，不自行扩大。
- **从已有任务开始**：智能体，以及任务名、完整 ID 或可唯一解析的 ID 前缀。
- **从旧入口开始**：完整本地 API JSON 导出，或当前任务的完整 Request URL + Authorization。如果只需检查截图里的少数案例，也可先做明确标注范围的局部分析。
- **报告需求**：是否需要误判复核，以及除基础通过率、失败案例、全部去重输入输出以外的指标或章节。

## 使用例子

- “用 `$test-center-analysis` 跑智能体 XXX 的测试集 YYY，完成后生成报告。”
- “用 `$test-center-analysis` 根据任务 abc123 生成报告，不重新执行。智能体是 XXX。”
- “用 `$test-center-analysis` 分析这份完整 JSON 导出，检查未通过是否误判，再生成本地 HTML。”
- “生成报告，展示业务通过率、失败案例、全部不重复的实际输入输出，再加转人工率。只生成 HTML，先不要发布。”

完整运行链路由 `md test run` 做跑前检查和费用预估，用同一任务的 `md test status --wait` 跟进，再导出单任务 `md test results --deep --out ...jsonl`，转换后生成 HTML。具体停止条件见[秒懂测试任务流程](skills/test-center-analysis/references/miaodong-workflow.md)。多任务对比继续使用 `md` 原生对比，不送入单任务报告适配器。

## Authorization 安全

使用 API 时，Authorization 只通过脚本的 `--authorization-stdin`、`TEST_CENTER_AUTHORIZATION` 或终端隐藏输入提供。不把凭据放进命令参数，以免进入命令历史；不写入文件、报告、仓库、Git 历史或公开聊天。

`md` 退出码 3 要求身份时，使用 `md auth snippet` / `md auth import` 流程；用户只需在自己的浏览器或终端操作，不要把 `md-auth:` 内容或任何 token 粘贴到对话。技能不读本机身份配置，不从其他文件寻找凭据。

## 本地运行与验证

从仓库根目录运行：

```bash
python3 -m unittest discover -s tests -v

REPO_ROOT_ABS="$(pwd -P)"
EXAMPLE_ROOT="$(mktemp -d)"
API_DATA_DIR="$EXAMPLE_ROOT/api-data"
API_REPORT_DIR="$EXAMPLE_ROOT/api-report"
MD_DATA_DIR="$EXAMPLE_ROOT/md-data"
MD_REPORT_DIR="$EXAMPLE_ROOT/md-report"

python3 "$REPO_ROOT_ABS/skills/test-center-analysis/scripts/prepare_results.py" --input "$REPO_ROOT_ABS/examples/synthetic-results.json" --output-dir "$API_DATA_DIR"
python3 "$REPO_ROOT_ABS/skills/test-center-analysis/scripts/build_report.py" --input "$API_DATA_DIR/dataset.json" --output-dir "$API_REPORT_DIR"

python3 "$REPO_ROOT_ABS/skills/test-center-analysis/scripts/prepare_md_results.py" --input "$REPO_ROOT_ABS/examples/synthetic-md-results.jsonl" --output-dir "$MD_DATA_DIR"
python3 "$REPO_ROOT_ABS/skills/test-center-analysis/scripts/build_report.py" --input "$MD_DATA_DIR/dataset.json" --output-dir "$MD_REPORT_DIR"
```

上面是一个自包含代码块，要从仓库根目录整块执行。`REPO_ROOT_ABS` 使 API JSON 和 `md` JSONL 的示例输入、以及脚本位置都使用绝对路径；如果只复制其中一条命令，先把所有目录变量替换成字面绝对路径。API 示例报告在 `$API_REPORT_DIR`，`md` JSONL 示例报告在 `$MD_REPORT_DIR`；两条准备链路不共用模糊的数据目录。每次通过 `mktemp -d` 使用新目录，避免覆盖历史。仓库示例数据完全合成，不会访问生产接口或自动发布。

协议与扩展：[数据契约](skills/test-center-analysis/references/data-contract.md)、[误判检查](skills/test-center-analysis/references/review.md)、[指标口径](skills/test-center-analysis/references/metrics.md)、[发布规则](skills/test-center-analysis/references/publishing.md)。

## 发布的受众确认

发布是最后一步。在内容调整、数据统计、去重和本地校验全部完成后，上传前必须明确询问：

> 当前报告是给客户还是给同事看？给同事看默认公司同事登录后可见；给客户看则免登录可见。

给同事用 `login`，给客户/对外用 `open`，明确要求密码保护时用 `public`。未回复不等于同意上传。更新已有报告时保留原 slug，但仍说明原可见性并按本次确认设置权限。发现客户数据、内部标识或其他敏感信息时，先取得针对性确认或脱敏；密钥、令牌、Authorization、Cookie、原始 JSONL 和规范化数据永不上传。

## 安全与边界

- 只在用户指定范围内运行或读取测试任务；不创建/编辑用例，不回写后台判定。
- `md` 的跑前检查、费用和插件确认码不得绕过；超时继续同一任务，不重复创建。
- 保留全部分页、断言、上下文和实际原文；`md` JSONL 不含完整多轮上下文时显式披露。
- HTML 输出安全转义；自定义指标仍需来源和可复算依据，不执行用户提供的表达式。
- Python 测试覆盖确定性数据处理；语义正确性与业务预期仍由证据和必要的用户确认决定。
