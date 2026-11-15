# 浏览器端到端验收

```bash
npm ci
PLAYWRIGHT_BROWSERS_PATH="$PWD/.playwright" npx playwright install chromium
npm run test:browser
```

默认自动启动独立的 `127.0.0.1:8876` 演示服务，不复用占用该端口的其他服务。
测试数据保存在 `data/browser-test-db/`；已有测试数据不会清除，每轮会新建一个
带唯一标识的目标和一轮探索。服务配置强制为 `demo` 数据源和规则引擎，关闭自动调度。
测试先验证 `mode=demo`、`llm_mode=demo` 与 CoDiscover Video 页面标题，避免误操作
真实服务；不会读取真实 B 站内容或调用付费模型。

如需连接自行启动的测试服务，可显式设置 `CDV_BROWSER_BASE_URL`；相同的模式与
品牌断言仍会执行。请只指向专用演示实例，不要使用真实账号工作台。

覆盖真实点击、表单输入、API 返回、离线标记、探索完成、推荐证据、聊天、评分反馈、
知识库检索/导入、记忆导出，以及 390px 下三个页面的横向布局。检查所有页面 JS 异常、
控制台 error 和请求失败。运行前需要完成 Python `uv sync --extra dev`。

截图：`data/ui-smoke.png`、`data/ui-smoke-mobile.png`。
失败保留 trace、截图和 JSON 报告：`data/browser-test-results/`。

在当前 macOS 环境中，Chromium headless-shell 会暂停 requestAnimationFrame，
因此配置使用完整 Chrome for Testing (`channel: chromium`) 并禁用帧率限制；
测试保持真实浏览器点击与渲染，没有用 DOM 事件模拟替代交互。
