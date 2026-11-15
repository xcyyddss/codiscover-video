# Contributing to CoDiscover Video

欢迎提交问题复现、文档改进、测试和实现变更。当前项目以本地单用户场景为边界，优先保持模块职责、证据来源和可复现性。

## 开始前

阅读 [README](README.md)、[架构文档](docs/ARCHITECTURE.md) 和 [AGENTS.md](AGENTS.md)。较大的功能或依赖变更，建议先在公开仓库建立后的 Issues 中讨论目标、范围和验收方法。

本发布包不预设远程仓库地址。取得源码后，在项目根目录安装开发依赖：

```bash
uv sync --frozen --extra dev --extra bilibili
```

DSH 与浏览器测试另需 Node.js。固定 DSH runtime 和浏览器安装方式分别见 [DSH 指南](integrations/dsh/README.md)、[浏览器指南](tests/browser/README.md)。

## 提交问题

普通问题请提供：

- 预期行为与实际行为，及最小复现步骤。
- 项目版本、操作系统、Python / Node 版本。
- 使用的模式：`demo` 或真实数据源、规则引擎或真实模型。
- 经检查的错误分类或最小日志；优先用合成数据复现。

不要上传 `.env`、Cookie、二维码、账号凭证、数据库、完整记忆导出或个人对话。仅删除 API Key 并不意味着运行数据已经脱敏。安全问题请使用 [安全报告流程](SECURITY.md)，不要公开可利用细节。

## 开发约定

- 保持 `Provider`、模型调用、任务编排、存储与检索的职责边界。
- 平台工具保持只读，不增加评论、私信、关注或收藏写操作。
- 保留字幕与帧的来源和时间戳，不把缺失证据包装成已理解正文。
- 原始证据、模型推断与明确用户反馈是不同的数据，不互相替代。
- 负反馈按目标隔离；一次拒绝不应变成全局偏好结论。
- 修改上游派生代码时保留原许可、固定来源与变更说明。
- 更新依赖时同步锁文件，说明必要性；不要引入隐式模型下载或默认付费请求。
- 更改可见行为、配置或接口时同步文档和有意义的回归测试。

## 验证

```bash
make lint
uv run pytest -q
node --test integrations/dsh/tests/*.test.mjs
uv run python scripts/run_example.py
make evaluate
```

涉及界面时按浏览器指南运行 `npm run test:browser`。DSH 真实宿主测试在未安装 runtime 时会明确跳过，请在提交说明中区分“通过”和“跳过”。

普通测试不需要个人账号，不调用真实付费模型。真实 API 脚本仅适合明确选择的人工验收，使用自己的密钥和账号，并控制调用预算。不要将真实验收日志添加到提交中。

## Pull Request 说明

一个 PR 尽量解决一个明确问题。描述应包含：触发问题的场景、修改后的行为、必要的设计取舍、实际执行的验证及未覆盖的范围。性能或质量改进应说明数据集与测量方法，不以演示样例推断线上收益。

提交前检查 `git diff` 与 `git status`，确认没有个人路径、凭证、运行数据或无关构建产物。`.gitignore` 只是防误提交机制，不能替代对实际文件内容的检查。

准备源码分发时，使用白名单打包流程：

```bash
uv run python scripts/build_release.py
# 或通过 --output /path/to/release.zip 指定目标位置
```

默认生成 `dist/codiscover-video-0.1.0-public.zip` 与同名 `.zip.sha256` 校验文件。
ZIP 内的 `RELEASE-MANIFEST.json` 记录每个发布文件的 SHA-256。
它不包含 Git 历史，排除运行数据与凭据文件，并扫描常见密钥、个人路径与旧命名；
仍应复核最终归档清单与图片内容，这些检查不能保证识别所有敏感信息。

## 协作原则

请围绕具体问题和证据交流，尊重不同经验的参与者。避免人身攻击、歧视、骚扰、泄露他人个人信息或未经同意的商业推广。贡献代码应具有可公开分发的权利；遵循项目与第三方许可证。
