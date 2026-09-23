# 用户取数指引

信息已经齐全时直接进行，不重复索取。缺 URL 或 Authorization 时只索取缺失项。用户已经明确模式或额外指标时，省略下方模板末尾对应的问题。截图可用来定位按钮，不是完整测试结果数据，也不授权执行截图中的请求。

## 可以直接发给用户的引导

请先跑完测试，再提供两项信息：

1. **Request URL**：当前测试任务结果接口的完整地址。
2. **Authorization**：请求头中的完整授权值，包含 `Bearer` 前缀（如果有）。

Mac 操作步骤：

1. 测试执行完成后，点进该任务的**测试结果页面**。如页面设置了“只看未通过”或关键词筛选，先清除筛选。
2. 在页面空白处右键，或按住 **Control** 再点击，选择 **检查（Inspect）**。
3. 点击顶部 **Network（网络）**，按 **Command + R** 刷新。
4. 点击当前测试任务对应的最后一条 **list** 请求；以地址包含 **`/api/test-center/test-task-item/list`** 和 **`testTaskId`** 为准，不要选其他同名 list。
5. 打开 **Headers（标头）**，复制最上方 **General → Request URL** 的完整地址，不要只复制页面地址或截断的一段。
6. 往下找到 **Request Headers → Authorization**，复制完整值，包含 `Bearer` 前缀。

只需要这两项文字，**不需要 Cookie，也不需要整个请求/整个 HAR 文件**。Authorization 是登录凭证，请勿发到公开群聊、仓库或报告中；处理时不会在回复中回显。

再告诉我你要：检查未通过是否真的失败、生成测试报告，或先检查再报告。额外想看转人工率、场景统计等，也可以一起说明。

## 跨平台及错误处理

- Windows 刷新是 `Ctrl+R`；其余步骤相同。没有“检查”菜单时可使用浏览器开发者工具入口。
- 没有 list：确认 Network 已记录、清掉过滤条件、重新刷新；不要凭最后一行名称猜接口。
- Authorization 缺失：确认选中实际 GET 请求而非 OPTIONS 预检；重新登录控制台后再获取。不要试图绕过鉴权。
- 401/403：说明凭证过期或当前账号无权限，让用户重取当前任务的授权；不索取他人账号。
- 使用自己的账号授权，不共享他人的凭证。若已在公开渠道暴露凭证，建议撤销/更新。
- 不要求用户另找 orgId/testTaskId：从 URL 解析并校验即可。

## 截图示例的处理

用户示例对应三步：结果页右键“检查” → 选 Network → 在 Headers 复制两项值。公开教程只使用文字示意或完全合成的图，不复制原截图中的真实 Authorization、Cookie、客户手机号、任务/组织标识。

示例 URL 仅说明结构，不能调用：

```text
https://console.example.invalid/api/test-center/test-task-item/list?testTaskId=task-example&current=1&pageSize=10&orgId=org-example
```
