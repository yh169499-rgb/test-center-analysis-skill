# 秒懂测试执行与分析报告整合实现计划

> **面向 AI 代理的工作者：** 必需子技能：使用 superpowers:subagent-driven-development（推荐）或 superpowers:executing-plans 逐任务实现此计划。步骤使用复选框（`- [ ]`）语法来跟踪进度。

**目标：** 让现有 `test-center-analysis` 能调用 `md` 跑完或读取秒懂测试任务，并从 `md test results` JSONL 生成包含业务通过率、空跑/未跑、失败案例和全部去重输入输出的 HTML 报告。

**架构：** `md` 继续独占测试执行、状态、费用和插件确认；新增 Python 适配器把单任务 JSONL 转成已有数据契约；现有渲染器增加空跑披露而保持旧 API 入口兼容。技能说明负责串联完整工作流，HTML Gallery 只在用户明确要求发布并确认受众后使用。

**技术栈：** Python 3 标准库、Node.js `md` CLI、`unittest`、静态单文件 HTML、Git/GitHub CLI。

---

## 文件结构

- 创建 `skills/test-center-analysis/scripts/prepare_md_results.py`：验证 `md test results` 单任务 JSONL，生成私有原文副本和规范化数据集。
- 创建 `tests/test_prepare_md_results.py`：适配器字段映射、完整性、凭据和文件安全测试。
- 创建 `examples/synthetic-md-results.jsonl`：完全合成的正常通过、失败、空跑、未跑和重复输入示例。
- 修改 `skills/test-center-analysis/scripts/build_report.py`：导出凭据检查入口，增加空跑统计和未正常执行明细。
- 修改 `tests/test_build_report.py`：空跑/未跑分母、章节和旧入口回归。
- 修改 `tests/test_end_to_end.py`：增加 `md` JSONL → dataset → HTML 端到端路径。
- 修改 `tests/test_package.py`：纳入新脚本和 fixture 的公开包安全检查。
- 创建 `skills/test-center-analysis/references/miaodong-workflow.md`：`md` 运行、等待、确认码和结果导出的专门流程。
- 修改 `skills/test-center-analysis/SKILL.md`：新增数据来源路由和完整测试→报告入口。
- 修改 `skills/test-center-analysis/references/data-contract.md`：记录 `md` 映射、空跑和未跑语义。
- 修改 `skills/test-center-analysis/references/metrics.md`：区分秒懂原生通过率与业务通过率。
- 修改 `README.md`：安装依赖、两种入口和使用示例。

### 任务 1：锁定 `md` JSONL 适配契约

**文件：**
- 创建：`tests/test_prepare_md_results.py`
- 创建：`examples/synthetic-md-results.jsonl`

- [ ] **步骤 1：添加合成 JSONL fixture**

创建五行完全合成数据：正常通过、正常失败、同输入的另一种输出、空跑和未跑。每行使用以下字段形状，所有 ID 使用 `example-` 前缀：

```json
{"task":"example-task","taskName":"示例任务","name":"查询示例","caseId":"example-case-1","execId":"example-online-1","scenario":"咨询/地址","user":"示例问题","expect":"返回示例地点","passed":true,"verdict":"","reply":"示例地点 A","actions":"发送文本","cost":0.01,"ms":1200,"testExecId":"example-test-exec-1","noop":false,"notRun":false,"online":"历史线上回复"}
```

其余四行分别满足：失败行 `passed=false` 且有 `verdict`；重复输入不同 `reply`；空跑行 `noop=true`、`passed=false`、`testExecId=""`；未跑行 `notRun=true`、`passed=false`、`testExecId=""`。

- [ ] **步骤 2：编写字段映射失败测试**

在 `tests/test_prepare_md_results.py` 中动态加载待创建脚本，并定义：

```python
def test_maps_normal_failed_noop_and_not_run_without_mutation(self):
    rows = [
        row("1", passed=True),
        row("2", passed=False, verdict="期待不一致"),
        row("3", passed=False, noop=True, testExecId=""),
        row("4", passed=False, notRun=True, testExecId=""),
    ]
    original = copy.deepcopy(rows)
    data = self.module.normalize_md_rows(rows)
    self.assertEqual(rows, original)
    self.assertEqual([r["originalPassed"] for r in data["records"]], [True, False, None, None])
    self.assertEqual([r["completed"] for r in data["records"]], [True, True, True, False])
    self.assertEqual([r["status"] for r in data["records"]], ["completed", "completed", "noop", "not_run"])
    self.assertEqual(data["source"]["total"], 4)
```

- [ ] **步骤 3：编写完整性和文件安全失败测试**

覆盖：混合 `task`、重复 `testExecId`、空 `caseId` 且无法生成 fallback、重复 JSON 字段、非法 UTF-8、非布尔 `noop/notRun/passed`、负耗时、明显 Authorization/Cookie/JWT、目标文件已存在和符号链接。CLI 成功时断言两个文件权限为 `0600`，失败时断言不留任一部分文件。

- [ ] **步骤 4：运行测试并确认失败**

运行：

```bash
python3 -m unittest tests.test_prepare_md_results -v
```

预期：FAIL，原因是 `skills/test-center-analysis/scripts/prepare_md_results.py` 尚不存在。

- [ ] **步骤 5：提交测试与 fixture**

```bash
git add tests/test_prepare_md_results.py examples/synthetic-md-results.jsonl
git commit -m "test: define miaodong result adapter contract"
```

### 任务 2：实现 JSONL 适配器

**文件：**
- 创建：`skills/test-center-analysis/scripts/prepare_md_results.py`
- 修改：`skills/test-center-analysis/scripts/build_report.py`
- 测试：`tests/test_prepare_md_results.py`

- [ ] **步骤 1：从渲染器导出统一凭据检查入口**

在 `build_report.py` 中把内部 `_safe_json` 改名为 `validate_no_credentials`，递归调用和 `build_report()` 的调用点同步改名，行为不变。先运行报告测试，确认只是安全重命名：

```bash
python3 -m unittest tests.test_build_report -v
```

预期：全部 PASS。

- [ ] **步骤 2：实现严格 JSONL 读取和映射**

新脚本提供以下公共接口：

```python
class MdPreparationError(ValueError):
    pass


def read_md_jsonl(path):
    """返回 (原始 bytes, rows)，拒绝空文件、重复 JSON 字段和非对象行。"""


def normalize_md_rows(rows):
    """返回 schemaVersion=1 数据集，不修改 rows。"""


def write_outputs(output_dir, raw_bytes, dataset):
    """以 O_EXCL/O_NOFOLLOW 和 0600 写两个文件；任何失败清理本次部分文件。"""
```

`normalize_md_rows()` 对每行执行精确映射：

```python
actual = {"reply": source["reply"], "actions": source["actions"]}
normal = not source["noop"] and not source["notRun"]
record = {
    "id": stable_id(source, ordinal),
    "caseName": source["name"] or None,
    "input": source["user"],
    "context": {},
    "actual": actual,
    "assertions": [{
        "type": "miaodong-test-result",
        "actualValue": actual,
        "expectedDescription": source["expect"],
        "llmReason": source["verdict"],
        "passed": source["passed"] if normal else None,
    }],
    "originalPassed": source["passed"] if normal else None,
    "status": "not_run" if source["notRun"] else "noop" if source["noop"] else "completed",
    "completed": False if source["notRun"] else True,
    "scene": source["scenario"] or None,
    "handoff": None,
    "handoffEvidence": None,
    "durationMs": source["ms"],
    "attributes": {
        "task": source["task"], "caseId": source["caseId"],
        "execId": source["execId"], "testExecId": source["testExecId"],
        "online": source["online"], "cost": source["cost"],
    },
}
```

`source` 设置 `complete=true`、`scope="task"`、`scopeVerified=true`、单一任务总数，并添加“JSONL 不含完整多轮上下文；context 保持空对象”的警告。`validate_no_credentials(dataset)` 在写文件前执行。

- [ ] **步骤 3：实现 CLI**

CLI 固定为：

```text
prepare_md_results.py --input /absolute/results.jsonl --output-dir /absolute/private-dir
```

成功写 `raw-md-results.jsonl` 和 `dataset.json`；参数错误和运行错误只输出不含源值的中文信息，返回非零。

- [ ] **步骤 4：运行适配器测试确认通过**

```bash
python3 -m unittest tests.test_prepare_md_results -v
python3 -m unittest tests.test_build_report -v
```

预期：全部 PASS。

- [ ] **步骤 5：提交适配器**

```bash
git add skills/test-center-analysis/scripts/prepare_md_results.py skills/test-center-analysis/scripts/build_report.py tests/test_prepare_md_results.py
git commit -m "feat: adapt miaodong test result JSONL"
```

### 任务 3：在 HTML 中单列空跑和未跑

**文件：**
- 修改：`skills/test-center-analysis/scripts/build_report.py`
- 修改：`tests/test_build_report.py`

- [ ] **步骤 1：编写失败的统计测试**

增加：

```python
def test_noop_and_not_run_are_disclosed_but_excluded_from_business_rate(self):
    data = dataset(
        record(1, True, status="completed", completed=True),
        record(2, False, status="completed", completed=True),
        record(3, None, status="noop", completed=True),
        record(4, None, status="not_run", completed=False),
    )
    content, summary = self.build(data)
    self.assertEqual((summary["eligible"], summary["passed"], summary["failed"]), (2, 1, 1))
    self.assertEqual((summary["noop"], summary["notRun"]), (1, 1))
    self.assertIn("空跑 1", content)
    self.assertIn("未跑 1", content)
    self.assertIn("未正常执行明细", content)
```

同时断言空跑不出现在 `未通过案例` 计数中，但空跑与未跑均在全部输入输出分组中，`groupCounts` 总和仍等于总记录数。

- [ ] **步骤 2：运行测试确认失败**

```bash
python3 -m unittest tests.test_build_report.BuildReportTests.test_noop_and_not_run_are_disclosed_but_excluded_from_business_rate -v
```

预期：FAIL，`summary` 尚无 `noop/notRun`。

- [ ] **步骤 3：实现统计和章节**

在 `_stats()` 增加：

```python
"noop": sum((r["status"] or "").strip().lower() == "noop" for r in records),
"notRun": sum((r["status"] or "").strip().lower() == "not_run" for r in records),
```

总览新增两个指标。新增 `未正常执行明细` 章节，只列 `noop/not_run`，逐条展示案例名、实际输入、实际输出和断言原因。正常失败筛选仍只使用 `_outcome(record) == "failed"`。

- [ ] **步骤 4：运行报告回归测试**

```bash
python3 -m unittest tests.test_build_report -v
```

预期：全部 PASS，现有 500 次执行、调整、转人工、耗时和注入测试不变。

- [ ] **步骤 5：提交报告改动**

```bash
git add skills/test-center-analysis/scripts/build_report.py tests/test_build_report.py
git commit -m "feat: disclose noop and not-run test results"
```

### 任务 4：串联 `md` 工作流和兼容入口

**文件：**
- 创建：`skills/test-center-analysis/references/miaodong-workflow.md`
- 修改：`skills/test-center-analysis/SKILL.md`
- 修改：`skills/test-center-analysis/references/data-contract.md`
- 修改：`skills/test-center-analysis/references/metrics.md`
- 修改：`README.md`
- 修改：`tests/test_package.py`

- [ ] **步骤 1：先写文档路由测试**

在 `tests/test_package.py` 增加断言：`SKILL.md` 链接到 `references/miaodong-workflow.md`；该参考文件包含 `md test run`、`md test status`、`md test results`、`--deep`、`prepare_md_results.py`、确认码、空跑和受众确认。继续使用已有链接存在性测试验证相对路径。

- [ ] **步骤 2：运行包测试确认失败**

```bash
python3 -m unittest tests.test_package -v
```

预期：FAIL，参考文件和新路由尚不存在。

- [ ] **步骤 3：编写 `miaodong-workflow.md`**

按批准规格写两个入口。必须包含这些停止条件：

- `md` 不存在时停止并给安装链接。
- 退出码 3 走身份流程；不读取身份配置、不让用户粘贴凭证。
- 退出码 5 若为费用/插件确认，单独请求确认；若为跑前检查，不自动加 `--allow-preflight-errors`。
- 创建任务后立即 `status --wait`；超时继续同一任务；状态不确定先查最近任务。
- 只对单任务运行 `results --deep --out ...jsonl`；比较任务仍用 `md` 原生报告，不送进单任务适配器。
- 报告完成不等于授权发布。

- [ ] **步骤 4：更新主 Skill 路由**

在 `SKILL.md` 的模式识别后增加来源选择：

1. 用户给智能体 + 测试集：读取 `miaodong-workflow.md` 并走完整链路。
2. 用户给智能体 + 任务：读取该参考并从状态检查开始。
3. 用户给 URL/Authorization 或完整 API JSON：保持原有 `prepare_results.py` 路径。
4. 用户只给截图：保持局部分析边界。

更新 description，加入“运行或接手秒懂测试任务后出报告”，同时保留“不修改测试用例或后台判定”的边界。

- [ ] **步骤 5：更新契约、指标和 README**

记录字段映射和两种通过率口径；README 增加：

```text
“用 $test-center-analysis 跑智能体 XXX 的测试集 YYY，完成后生成报告。”
“用 $test-center-analysis 根据任务 abc123 生成报告，不重新执行。”
```

明确完整测试入口依赖单独安装 `miaodong-cli`；只分析 API 数据不依赖 `md`；发布才依赖 `html-gallery`。

- [ ] **步骤 6：运行包测试并提交**

```bash
python3 -m unittest tests.test_package -v
git add README.md skills/test-center-analysis/SKILL.md skills/test-center-analysis/references tests/test_package.py
git commit -m "docs: connect miaodong tests to analysis reports"
```

预期：包测试全部 PASS，commit 成功。

### 任务 5：端到端、安全和兼容性验证

**文件：**
- 修改：`tests/test_end_to_end.py`
- 修改：`tests/test_package.py`

- [ ] **步骤 1：增加合成 `md` 端到端测试**

在新的临时目录依次运行：

```python
subprocess.run([
    sys.executable, str(SCRIPTS / "prepare_md_results.py"),
    "--input", str(ROOT / "examples/synthetic-md-results.jsonl"),
    "--output-dir", str(data_dir),
], check=True, capture_output=True, text=True)
subprocess.run([
    sys.executable, str(SCRIPTS / "build_report.py"),
    "--input", str(data_dir / "dataset.json"),
    "--output-dir", str(report_dir),
], check=True, capture_output=True, text=True)
```

断言合成 fixture 的总数、eligible、passed、failed、noop、notRun、groups 和 variants；断言 HTML 含失败、空跑和未跑原文，且不含 `example-test-exec`、`example-case`、`example-online` 等私有标识。

- [ ] **步骤 2：扩展公开包扫描**

让 `test_package.py` 扫描新脚本和 JSONL fixture，拒绝生产域名、真实 UUID、JWT、GitHub token、`<HOME_ABS>/`、Cookie、Authorization 以及图片/HAR。合成字段值必须带 `example-` 或明确的无效域名。

- [ ] **步骤 3：运行全套测试**

```bash
python3 -m unittest discover -s tests -v
```

预期：所有测试 PASS，无 skipped、errors 或 failures。

- [ ] **步骤 4：验证技能和 HTML 脚本**

```bash
python3 <CODEX_HOME_ABS>/skills/.system/skill-creator/scripts/quick_validate.py skills/test-center-analysis
python3 -c 'import runpy; print(runpy.run_path("skills/test-center-analysis/scripts/build_report.py")["SCRIPT"])' | node --check
```

预期：`Skill is valid!`，Node 语法检查退出码 0。

- [ ] **步骤 5：提交验证改动**

```bash
git add tests/test_end_to_end.py tests/test_package.py
git commit -m "test: verify miaodong report workflow end to end"
```

### 任务 6：安装副本与 GitHub 发布

**文件：**
- 更新安装目标：`<CODEX_HOME_ABS>/skills/test-center-analysis`
- 发布仓库：`https://github.com/yh169499-rgb/test-center-analysis-skill`

- [ ] **步骤 1：完成最终本地审计**

```bash
git status --short
git diff --check HEAD~4..HEAD
git ls-files
rg -n 'aiyw-insight|Bearer eyJ|Cookie:|Authorization:|/Users/|codex-clipboard' README.md skills examples tests
```

预期：仅计划中的源码、文档、测试和合成 fixture；敏感扫描无生产命中。

- [ ] **步骤 2：复跑完成证据**

```bash
python3 -m unittest discover -s tests -v
python3 <CODEX_HOME_ABS>/skills/.system/skill-creator/scripts/quick_validate.py skills/test-center-analysis
```

预期：全套测试 PASS，技能验证成功。没有这次新鲜输出不得宣称完成。

- [ ] **步骤 3：更新本机安装副本**

使用官方 skill-installer 从发布分支安装或更新；若目标目录存在，先比较来源和目标，只替换 `test-center-analysis` 这一精确目录。更新后：

```bash
diff -qr --exclude='__pycache__' skills/test-center-analysis <CODEX_HOME_ABS>/skills/test-center-analysis
```

预期：无差异。不要改动 `miaodong` 或 `html-gallery` 的安装目录和凭据。

- [ ] **步骤 4：发布到既有公开仓库**

提交当前分支并推送到 `yh169499-rgb/test-center-analysis-skill`。如果本机 Git HTTPS 通道仍超时，使用已登录 `gh api` 的 Git Data API 创建 blob/tree/commit，并以远端当前默认分支 HEAD 为 parent，避免覆盖远端历史；更新现有默认分支，不创建另一个产品仓库。

- [ ] **步骤 5：远端核验**

验证仓库仍为 PUBLIC、默认分支不变、远端 tree SHA 与本地目标 tree SHA 一致，并读取远端 `SKILL.md`、新适配器和合成 fixture 的元数据确认存在。

- [ ] **步骤 6：交付使用方式**

向用户提供仓库链接、安装链接、全套测试数量和三种示例：完整运行后报告、已有任务报告、仅 API 数据报告。说明：`miaodong-cli` 只需每台机器安装一次并单独授权；`html-gallery` 只在发布时需要；发布前仍询问客户或同事。
