# DeepSeek Harness 集成

这是原生 DSH 插件，向 `ctx.tools` 注册 8 个有界工具，将 DSH 对话接到
CoDiscover Video 本地 API。搜索/字幕/故事板来自固定版本的 dsh-bili-scroll
只读代码。宿主兼容加载器改编自同一上游，保留 `LICENSE.upstream`。

## 安装和使用

先在项目根目录启动 CoDiscover Video：

```bash
uv sync --frozen --extra bilibili --extra dev
uv run codiscover-video serve
```

另开终端，在项目根目录安装固定版本 DSH（Node >=24.9.0）：

```bash
(cd integrations/dsh/runtime && npm ci --ignore-scripts)
export DSH_HOME="$PWD/data/dsh"
./integrations/dsh/runtime/node_modules/.bin/dsh plugin --profile web add "link:$PWD/integrations/dsh"
./integrations/dsh/runtime/node_modules/.bin/dsh web
```

初次初始化 DSH profile 可能下载其依赖。DSH 默认 Web UI 是
`http://127.0.0.1:3080`；CoDiscover Video 工作台是 `http://127.0.0.1:8765`。
在 DSH 的模型设置中配置你自己的模型；安装插件后重启 DSH。
不要把项目的 API 调用指南或密钥粘贴进 DSH 对话。

对话示例：

> 使用 cdv_create_goal 建立「下班后想找安静陪伴的视频」目标，再调用
> cdv_explore 启动探索；用 cdv_jobs 查看结果，最后给我有证据的推荐。

可用工具：`cdv_status`、`cdv_goals`、`cdv_create_goal`、`cdv_explore`、
`cdv_jobs`、`cdv_recommend`、`cdv_chat`、`cdv_feedback`。
`cdv_feedback` 只用于记录用户明确给出的评分。登录在 CoDiscover Video 工作台完成。
本插件没有平台评论、私信、上传或其他平台写操作，也没有命令执行工具。
默认 API 为 localhost:8765；可在插件 patch 中修改为其他本机端口。

## 固定依赖与更新

runtime 固定为 `@deepseek-ai/dsh` **0.2.2-rc.3**，并提交 npm lockfile。
为处理本次依赖审计发现的问题，`package.json` 对以下来源版本设置精确覆盖：

| 依赖来源版本 | 安装版本 |
| --- | --- |
| `undici@8.11.2` | `8.12.2` |
| `@modelcontextprotocol/client@2.2.0` | `2.3.0` |
| `fflate@0.8.2` | `0.8.3` |

2026-11-15 的 `npm audit` 对该锁定依赖树报告 0 项已知漏洞。
兼容性检查覆盖本项目 8 个原生工具的注册与 schema、本地 HTTP 桥接和
`dsh --version` 启动，不覆盖 DSH 的所有插件、外部 MCP 服务或付费模型对话。
依赖审计结果会随公告更新而变化；升级 DSH 时应重新检查这些覆盖是否仍有必要，
并运行下列测试，不要直接将本次结果理解为完整安全审计。

## 独立 MCP 边界

若已有 DSH MCP client，也可把以下命令作为 stdio MCP server：

```bash
uv run --extra bilibili python -m codiscover_video.providers.mcp_server
```

此服务器仅暴露 `video_search`、`video_inspect`、`source_health`，主要用于
只读采集或其他 MCP 宿主。它不替代上面的持久化目标/记忆/反馈 API 插件。

## 本机验证

```bash
node --test integrations/dsh/tests/*.test.mjs
uv run pytest tests/test_providers.py -q
```

已使用真实 `@deepseek-ai/dsh` 0.2.2-rc.3 内的 Cordis、SystemPrompt 与
ToolsService 激活插件，校验 8 个工具的注册与 schema；HTTP 桥接另有实际
localhost 测试。`runtime.test.mjs` 在没有安装 runtime 时明确显示 skip。
这不等价于已经登录 DSH 模型账号或在其 Web UI 内完成付费模型对话。

上游一手资料：
- https://github.com/c1overzz/dsh-bili-scroll
- https://github.com/deepseek-ai/deepseek-harness
