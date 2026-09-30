# 秒懂测试任务到报告

当用户要“跑完测试后出报告”或“用已有任务出报告”时使用本流程。`md` 负责跑前检查、费用与插件确认、任务状态和结果导出；本技能只把单任务 JSONL 转成报告数据，不绕过 `md` 直调秒懂接口。

## 准备与停止规则

1. 本会话首次使用时，先在一个自包含命令中解析并验证 `md`：

   ```bash
   MD_BIN="$(command -v md 2>/dev/null || true)"
   case "$MD_BIN" in
     /*) [ -x "$MD_BIN" ] || MD_BIN="" ;;
     *) MD_BIN="" ;;
   esac
   if [ -z "$MD_BIN" ]; then
     if [ -x "$HOME/.local/bin/md" ]; then
       MD_BIN="$HOME/.local/bin/md"
     else
       printf '%s\n' 'md 未安装' >&2
       exit 127
     fi
   fi

   if MD_VERSION="$("$MD_BIN" --version 2>/dev/null)"; then
     MD_VERSION_STATUS=0
   else
     MD_VERSION_STATUS=$?
   fi
   case "$MD_VERSION" in
     "md "*) MD_VERSION_PREFIX_OK=1 ;;
     *) MD_VERSION_PREFIX_OK=0 ;;
   esac
   if { [ "$MD_VERSION_STATUS" -ne 0 ] || [ -z "$MD_VERSION" ] || [ "$MD_VERSION_PREFIX_OK" -ne 1 ]; } \
     && [ "$MD_BIN" != "$HOME/.local/bin/md" ] && [ -x "$HOME/.local/bin/md" ]; then
     MD_BIN="$HOME/.local/bin/md"
     if MD_VERSION="$("$MD_BIN" --version 2>/dev/null)"; then
       MD_VERSION_STATUS=0
     else
       MD_VERSION_STATUS=$?
     fi
     case "$MD_VERSION" in
       "md "*) MD_VERSION_PREFIX_OK=1 ;;
       *) MD_VERSION_PREFIX_OK=0 ;;
     esac
   fi
   [ "$MD_VERSION_STATUS" -eq 0 ] && [ -n "$MD_VERSION" ] && [ "$MD_VERSION_PREFIX_OK" -eq 1 ] || {
     printf '%s\n' 'md 版本检查失败' >&2
     exit 2
   }
   printf '%s\n' "$MD_BIN"
   ```

   先用 `command -v md`；找不到可执行文件或命中 shell 别名时，再检查可执行的 `"$HOME/.local/bin/md"`。两者都不可用才停止，请用户按 [miaodong-cli](https://github.com/Spider615/miaodong-cli) 安装；不从未知来源自动安装。版本验证只要求上面命令块内的版本检查子命令退出码为 0、输出非空且以 `md ` 开头；不绑定 ASCII 或全角括号等构建信息格式。

   把该命令最后输出的已解析的字面绝对路径记为 `<MD_BIN_ABS>`。`<MD_BIN_ABS>` 是文档占位符，不是要原样传给 shell 的文本；agent 要在每一次后续独立命令调用前，将它替换为刚才返回的绝对路径字符串。在 agent 内部记住这个字符串，不暴露或依赖跨命令的 shell 变量状态。
2. 退出码 3 表示需要身份：询问用户的秒懂控制台域名，运行 `"<MD_BIN_ABS>" auth snippet <域名>` 并原样转交输出；用户在自己的浏览器完成后只需回复“好了”，然后运行 `"<MD_BIN_ABS>" auth import`。非 macOS 无 `pbpaste` 时，请用户在自己的终端运行 `"<MD_BIN_ABS>" auth import --stdin`。不读身份配置文件，不让用户把 `md-auth:` 内容、token、Cookie 或 Authorization 粘贴到对话。
3. 退出码 5 必须先区分原因：
   - 费用或插件确认：把智能体、测试集、版本、轮数、预估费用及插件影响写清楚，使用确认码单独询问这一项；用户明确同意后，才用原命令加 `--confirm <码>` 重试。费用确认和插件确认若为两个独立闸门，逐项单独确认，不与发布或其他授权捆绑。
   - 跑前检查失败：列出哪些用例可能空跑或为什么不能跑，然后停止。不自动加 `--allow-preflight-errors`；只有用户在知道影响后明确要求继续，才使用该参数。
4. 建任务的输出或连接状态不确定时，先使用只读查询 `"<MD_BIN_ABS>" test status --bot <智能体>` 检查该智能体最近任务；若当前版本参数有变化，再查 `"<MD_BIN_ABS>" test --help`。不重复执行 `"<MD_BIN_ABS>" test run`。

## 路径 A：智能体 + 测试集

1. 用户必须给出智能体和测试集。只在用户指定时传版本、轮数、并发或任务名，不自行扩大数量。先运行跑前检查和费用预估：

   ```bash
   "<MD_BIN_ABS>" test run <测试集> --bot <智能体> [--version <版本>] [--rounds <轮数>]
   ```

   命令要求确认时，按上面的退出码 5 规则处理；不用其他命令绕过跑前检查。

2. 拿到新任务后立即监看同一任务：

   ```bash
   "<MD_BIN_ABS>" test status <任务> --bot <智能体> --wait
   ```

   单次等待不超过 540 秒。若超时但任务仍在运行，继续对同一任务执行这条状态命令，绝不重复创建新任务。

3. 完成后按“单任务深度结果”导出到任务独立的私有目录。先将该目录解析成字面绝对路径，并在本流程中记为 `<RESULTS_DIR_ABS>`：

   ```bash
   "<MD_BIN_ABS>" test results <任务> --bot <智能体> --deep --out "<RESULTS_DIR_ABS>/results.jsonl"
   ```

   每次只允许一个任务，必须同时使用 `--deep` 和 `--out ...jsonl`。局部深取失败、暂停、失败、未跑或空跑都要保留并在报告中披露，不宣称任务已完整跑完。

4. 转换并生成本地报告。脚本必须从已安装的 skill 目录解析绝对路径，不依赖当前工作目录。在 Codex 默认安装中，把当前用户主目录下的 `.codex/skills/test-center-analysis` 展开为绝对路径；非默认安装时，由调用方提供技能根目录的绝对路径。将该字面字符串记为 `<SKILL_DIR_ABS>`，将本次任务的私有绝对目录记为 `<RESULTS_DIR_ABS>`。

   下面的占位符同样必须在每一次独立命令调用前替换成对应的字面绝对路径；不原样执行，不依赖跨命令的 shell 变量。先确认两个脚本都存在，再分别调用：

   ```bash
   test -f "<SKILL_DIR_ABS>/scripts/prepare_md_results.py" || { printf '%s\n' '找不到 prepare_md_results.py' >&2; exit 2; }
   test -f "<SKILL_DIR_ABS>/scripts/build_report.py" || { printf '%s\n' '找不到 build_report.py' >&2; exit 2; }

   python3 "<SKILL_DIR_ABS>/scripts/prepare_md_results.py" \
     --input "<RESULTS_DIR_ABS>/results.jsonl" \
     --output-dir "<RESULTS_DIR_ABS>/prepared"
   python3 "<SKILL_DIR_ABS>/scripts/build_report.py" \
     --input "<RESULTS_DIR_ABS>/prepared/dataset.json" \
     --output-dir "<RESULTS_DIR_ABS>/report-v1"
   ```

   这些路径由调用方解析或传入，不得写死某个用户的本机目录。即使两个 Python 命令分属不同的 exec/shell，替换后的每条命令也应能独立运行。保留 `raw-md-results.jsonl` 和 `dataset.json` 作为私有追溯证据，不放入仓库或待发布目录。

## 路径 B：智能体 + 已有任务

1. 用户给出智能体，以及任务名、完整 ID 或可唯一解析的 ID 前缀。从下面的状态检查开始，不重新执行测试：

   ```bash
   "<MD_BIN_ABS>" test status <任务> --bot <智能体>
   ```

2. 仍在运行时，按用户意图使用同一任务的 `"<MD_BIN_ABS>" test status <任务> --bot <智能体> --wait`。暂停、失败或存在未跑记录时，可导出现有结果但必须在报告中说明覆盖范围；不能写成“全部执行完成”。
3. 状态允许导出后，从路径 A 的单任务 `"<MD_BIN_ABS>" test results ... --deep --out ...jsonl` 开始，然后执行 `prepare_md_results.py` 和 `build_report.py`。

## 任务对比不进入报告合并

两个或多个任务的任务对比继续使用 `"<MD_BIN_ABS>" test results <任务1> <任务2> ... --bot <智能体> --out <对比文件.xlsx>` 的原生对比。不把多任务输出送入 `prepare_md_results.py`，不把对比伪装成单任务，也不在 HTML 报告中自动合并。

## 报告与发布是两个授权

报告生成不等于授权发布。未明确要求发布时，只交付本地 HTML。发布是整个流程的最后一步，且上传前必须询问：

> 当前报告是给客户还是给同事看？给同事看默认公司同事登录后可见；给客户看则免登录可见。

- 同事：`login`。
- 客户或对外：`open`。
- 明确要求密码保护：`public`，再按发布技能设置密码。

没有明确答复就不上传。更新时保留原 slug，说明旧可见性并按本次确认设置权限，不静默沿用旧受众。上传前只选最终 HTML 与必要资源；遇到客户输入输出、内部标识或其他疑似敏感信息时，先说明范围并取得针对性确认或脱敏。密钥、令牌、Authorization、Cookie、原始 JSONL 和 `dataset.json` 永不上传。
