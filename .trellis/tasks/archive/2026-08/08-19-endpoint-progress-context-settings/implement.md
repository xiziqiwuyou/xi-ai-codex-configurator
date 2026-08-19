# 实施计划：新端点、进度反馈与长上下文配置

## 1. 锁定回归测试

- [x] 在 endpoint、remote model、TOML 和 README 断言中加入 `.net` 精确 URL，并确保 provider base 只包含一个 `/v1`。
- [x] 为 TOML 合并增加 preserve、500K、1M、clear、非目标模型保留和无关设置保留测试。
- [x] 为 CLI 增加 Sol/Terra/Luna 上下文菜单、默认回车、无效输入重试、摘要与成本提示测试；验证其他模型不新增 prompt。
- [x] 为 bootstrap 增加已知/未知 `Content-Length`、单调进度、100% 完成、重试重置、缓存命中和非 TTY 节流测试。
- [x] 为会话扫描与事务增加阶段顺序、计数单调、批量节流、`N` 分支零事件、失败回滚事件及无敏感内容测试。

## 2. 更新固定端点

- [x] 修改 `src/codex_configurator/endpoints.py` 的 Origin。
- [x] 同步生成 TOML、远程模型、endpoint 测试和 README 端点表。
- [x] 使用无令牌请求验证 `.net` 的 `/v1/models` 与 POST `/v1/responses` 到达鉴权层。

## 3. 实现下载进度

- [x] 在 `scripts/bootstrap.py` 增加轻量进度事件/渲染，不增加依赖。
- [x] 将可选回调贯穿 `_read_limited`、`_open_bytes`、`_download`、`resolve_release` 和 `install_release`。
- [x] 为元数据、checksum、bundle、SHA-256、解压、缓存命中/安装增加阶段消息。
- [x] TTY 使用原位进度条；非 TTY 使用节流普通文本；未知总量显示字节数。
- [x] README 一行命令的 `curl` 显式使用 `--progress-bar`。

## 4. 实现会话合并进度

- [x] 新增包内进度事件与控制台渲染器，定义 TTY 与非 TTY 行为。
- [x] `collect_rollout_changes` 在不暴露路径的前提下报告扫描总数与当前数。
- [x] `create_backup` / `apply_setup` 报告 readiness、文件备份、SQLite 快照、配置/catalog、rollout、SQLite 和完成阶段。
- [x] 自动回滚时报告开始/完成，同时保持原异常链和事务顺序。
- [x] CLI 仅在 `Y` 分支传递会话进度回调；`N` 分支继续不触碰会话存储。

## 5. 实现上下文预设

- [x] 定义 preserve/set/clear 操作以及 500K/450K、1M/900K 预设。
- [x] 扩展 `merge_config`，仅在 set/clear 时管理两个顶层上下文键。
- [x] 清理 Codex `0.144.1` strict-config 已废弃的 `preferred_auth_method`，不再写回旧字段。
- [x] 在选择 Sol/Terra/Luna 后显示四项菜单，回车默认 preserve；其他模型跳过。
- [x] 在计划摘要中显示上下文操作，并展示带“以服务方规则为准”的 272K 成本提示。
- [x] 保持 `model_reasoning_effort` 和 catalog 模型元数据不变。

## 6. 文档与规范

- [x] 更新 README 的交互步骤、端点、进度表现、上下文示例、重启/新任务说明和计费提示边界。
- [x] 在项目 Codex configurator 规范中记录新端点、进度事件不变量和上下文合并合同。
- [x] 不修改已归档任务的历史记录。

## 7. 验证与审查

- [x] `python -m unittest discover -s tests -v`
- [x] `python -m compileall src scripts tests`
- [x] PowerShell parser 校验 `scripts/setup.ps1` 和 README Windows 一行命令。
- [x] `sh -n scripts/setup.sh`，并对 README POSIX 一行命令做 shell 语法校验。
- [x] `rg` 检查现行源码、测试、README、规范无 `api.xi-ai.cn` 残留（archive 除外）。
- [x] 使用临时 `CODEX_HOME` 做 dry-run/配置/validate/restore 冒烟测试，不使用真实 Key 或真实会话目录。
- [x] 运行 Trellis check，复核无 token/会话正文/路径泄漏、无事务顺序回归。

## 8. 回滚点

- 端点、progress、context 三组提交内容保持可独立审查；若某组测试失败，回退该组而不改动用户真实 `CODEX_HOME`。
- 实机配置由现有 `<CODEX_HOME>/backup-xi-ai/<timestamp>/` 和 `restore` 命令恢复。
- 本任务不创建或替换 GitHub Release；发布动作另行授权。
