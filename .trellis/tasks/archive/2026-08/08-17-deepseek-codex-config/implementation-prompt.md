# Codex Implementation Prompt

请在当前仓库实现一个跨平台的 Xi-AI Codex 一键配置工具。实现前必须阅读
当前 Trellis 任务的 `prd.md`、`design.md`、`implement.md` 和 `research/`。

## 目标

用户运行 Windows PowerShell 或 macOS/Linux shell 一键脚本后，可以完成
Codex 路径探测、隐藏输入令牌、获取远端模型、选择默认模型、合并 Codex
内置模型目录、写入第三方 Provider 配置，并可选择是否迁移旧对话的本地
可见性。不要把真实令牌写入仓库或输出到终端。

## 固定接口

- 用户认知的站点根地址固定为 `https://api.xi-ai.cn`。
- Codex Provider id 固定为 `xi_ai`。
- Provider `base_url` 固定写为 `https://api.xi-ai.cn/v1`。
- `wire_api` 固定为 `responses`，最终请求路径必须是
  `https://api.xi-ai.cn/v1/responses`。
- 模型列表固定从 `https://api.xi-ai.cn/v1/models` 获取。
- 不提供自定义地址参数，不允许产生 `/v1/v1`。

## 交互流程

1. 先检测 Codex 可执行文件、版本、`CODEX_HOME`、`config.toml` 和会话状态
   文件位置，并在输入令牌前显示检测结果。
2. 显示“按 Enter 继续输入令牌”，用户回车后调用隐藏输入，整个 `setup`
   流程只询问一次令牌。
3. 不得从环境变量或 `--api-key` 读取令牌。空令牌立即退出且不写文件。
4. 使用令牌请求 `/v1/models`，解析 OpenAI 兼容的 `data[].id`，去重后显示
   编号菜单并要求用户选择默认模型。401、网络错误、JSON 错误均不得写配置。
5. 运行 `codex debug models --bundled` 获取 Codex 内置目录；不可用时使用
   仓库中的版本化回退快照。保留全部内置模型，并添加全部远端模型。
6. 远端 id 与内置 slug 相同时保留内置元数据；未知模型使用保守的纯文本
   Responses 模板生成 Codex 目录项。生成后的 JSON 必须校验成功。
7. 询问 `是否合并现有项目和对话? [y/N]`。`N` 不修改任何会话文件。
8. `Y` 表示本地可见性迁移，必须先完整备份，再将
   rollout `session_meta.payload.model_provider` 和
   `state_5.sqlite.threads.model_provider` 更新为 `xi_ai`，修复当前架构需要的
   可见性字段并保留 id、标题、cwd、项目路径、消息内容、附件和时间顺序。
   不得把历史对话或项目内容上传到 Xi-AI。
9. 最后显示脱敏变更摘要，校验 TOML/JSON 后原子写入；任一阶段失败必须
   恢复所有已修改文件与 SQLite 备份。

## Codex 配置

只管理必要根键和 `[model_providers.xi_ai]`，保留 MCP、hooks、profiles、
trust、sandbox、其他 Provider 和未知 TOML 内容。写入结果包含：

```toml
model = "<用户选择的远端模型>"
model_provider = "xi_ai"
preferred_auth_method = "apikey"
forced_login_method = "api"
model_catalog_json = "~/.codex/xi-ai-model-catalog.json"

[model_providers.xi_ai]
name = "Xi-AI"
base_url = "https://api.xi-ai.cn/v1"
wire_api = "responses"
experimental_bearer_token = "<本机令牌>"
```

## 安全与恢复

- 使用 Python 3.11+ 标准库，不增加运行时依赖。
- 令牌不得出现在参数、日志、异常、manifest、测试、快照或 Git 中。
- 修改前创建带 SHA-256 manifest 的时间戳备份。
- SQLite 使用一致性备份；数据库被 Codex 占用时要求用户关闭 Codex，不得
  强行删除 WAL/SHM。
- 临时文件必须与目标同目录，并通过原子替换提交。
- 提供 `setup`、`status`、`validate`、`restore` 命令。
- 所有测试只能使用临时 `CODEX_HOME`。

## 验证

```text
python -m unittest discover -s tests -v
python -m compileall src tests
PYTHONPATH=src python -m codex_configurator --help
git diff --check
```

在用户明确批准 Trellis 的最终规划前，不要执行 `task.py start`，也不要
写产品代码。
