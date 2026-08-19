# 更新 Xi-AI 域名、进度反馈与长上下文配置

## Goal

让用户在新电脑上运行一行安装命令时，可以明确看到下载和本地会话合并的执行进度，并可在选用 GPT-5.6 Sol、Terra 或 Luna 时显式启用 500K 或 1M 上下文，同时保持现有配置、会话内容和失败回滚能力。

## Background

- Xi-AI 固定服务域名由 `api.xi-ai.cn` 迁移到 `api.xi-ai.net`，Codex 供应商仍通过 `/v1` 下的 Responses API 工作。
- 当前启动器会下载 GitHub Release 元数据、校验文件和程序包，但下载及解压阶段缺少持续反馈。
- 当前会话可见性合并包含扫描、SQLite 检查、备份、rollout 改写和 SQLite 更新；大量会话时，用户只能等待最终结果。
- Codex CLI `0.144.1` 的本机 schema 将顶层 `model_context_window` 和 `model_auto_compact_token_limit` 定义为可空 `int64`。本机内置模型目录包含 `gpt-5.6-sol`、`gpt-5.6-terra`、`gpt-5.6-luna`，但长上下文不是默认配置。
- 用户提供的 272K 以上计费说明属于使用提示；当前官方 Codex 文档在本环境返回 403，不能把该说明表述为已由 OpenAI 官方文档核验的事实。

## Requirements

### R1. 固定新端点

- 固定 Origin 为 `https://api.xi-ai.net`。
- 模型列表必须请求 `https://api.xi-ai.net/v1/models`。
- 生成的 Codex provider `base_url` 必须为 `https://api.xi-ai.net/v1`，并继续使用 `wire_api = "responses"`，从而命中 `https://api.xi-ai.net/v1/responses`。
- 活跃代码、测试、README 和项目规范中的现行端点必须同步；已归档任务保留历史记录，不做机械改写。

### R2. 下载进度反馈

- README 的 Windows 与 macOS/Linux 一行命令必须让 `curl` 显示明确的下载进度。
- Python bootstrap 必须对 Release 元数据解析、校验文件下载、程序包下载、SHA-256 校验、解压与缓存安装给出阶段反馈。
- 响应包含有效 `Content-Length` 时，程序包下载显示字节数与百分比并最终到达 100%；缺少或无法解析总长度时，显示已下载字节数和完成状态。
- 交互式终端可原位刷新；非 TTY/测试输出必须降级为节流后的普通文本，不输出 ANSI 控制噪声或每个数据块一行。
- 重试必须清楚显示当前尝试，且不得把前一次失败的字节数计入新尝试。

### R3. 会话合并进度反馈

- 仅在用户选择 `Y` 时，为会话扫描、配置/目录/rollout 备份、SQLite 快照、rollout 改写、SQLite 元数据更新、完成或失败回滚提供阶段反馈。
- 可计数的阶段显示当前数量、总数和百分比；SQLite 快照等无法可靠获知内部比例的阶段至少显示“开始/完成”状态，避免无输出等待。
- 进度输出只能包含阶段名、计数、百分比和通用状态，不输出 API Key、会话正文或单个会话文件路径。
- `N` 分支继续保持严格边界：不扫描 rollout，不打开会话 SQLite，不生成会话合并进度，也不改写任何会话数据。
- 进度机制不得改变现有原子写入、SQLite readiness gate、完整备份和自动回滚顺序。

### R4. 长上下文选择

- 仅当所选默认模型为 `gpt-5.6-sol`、`gpt-5.6-terra` 或 `gpt-5.6-luna` 时显示上下文菜单。
- 菜单提供四种行为：
  - 保留现有设置（默认，直接回车）：不新增、删除或覆盖两个上下文键；没有现有值时继续使用 Codex/模型默认值。
  - 500K：写入 `model_context_window = 500000` 和 `model_auto_compact_token_limit = 450000`。
  - 1M：写入 `model_context_window = 1000000` 和 `model_auto_compact_token_limit = 900000`。
  - 恢复 Codex 默认：删除这两个顶层键。
- 对其他模型不显示长上下文菜单，并原样保留用户已有的两个顶层键。
- 选择摘要必须显示最终操作；500K/1M 选项需提示上下文越大可能增加额度/费用，并将“输入超过 272K 后可能触发更高计费”标为以当前服务方规则为准。
- 不自动修改 `model_reasoning_effort`；用户提到的 Luna `xhigh` 仅作为后续手动选择建议，不与上下文预设绑定。

### R5. 兼容性与安全

- 保留现有 Codex bundled catalog 与 Xi-AI 远程模型合并行为。
- 保留现有未知 TOML 顶层键、MCP、profiles、projects 和非 Xi-AI provider 配置。
- 不增加第三方 Python 依赖，继续支持 Python 3.11+、PowerShell 与 POSIX shell。
- 所有错误和进度信息继续使用简体中文，并且不得泄露令牌、Authorization header 或远端响应正文。

## Acceptance Criteria

- [ ] `ORIGIN`、模型 URL、Responses URL、生成的 provider base 和 README 全部使用 `api.xi-ai.net`；现行源码/测试/README 不再引用 `.cn`。
- [ ] 无令牌连通性检查能够到达新域名的 `/v1/models` 和 POST `/v1/responses`，并收到鉴权拒绝而不是 DNS/TLS/路由错误。
- [ ] 已知下载总量时可观察到单调递增并以 100% 完成的程序包进度；未知总量时可观察到递增字节数及完成状态。
- [ ] TTY 输出可原位刷新，非 TTY 输出无 ANSI/回车噪声且有阶段开始、节流进度和完成记录。
- [ ] 选择 `Y` 后，会话扫描、备份、SQLite 快照、rollout 改写和 SQLite 更新均有可观察反馈；913 个文件一类的大批量场景不会逐文件刷屏。
- [ ] 选择 `N` 后，rollout 与 SQLite 保持逐字节不变，且没有会话扫描/迁移进度事件。
- [ ] Sol/Terra/Luna 的 500K 和 1M 选项分别生成 `500000/450000` 与 `1000000/900000` 两个顶层整数键。
- [ ] “保留现有设置”不会改变已有上下文键；“恢复 Codex 默认”会同时删除两个键；其他模型不会改变这些键。
- [ ] 生成的 TOML 可被 `tomllib` 解析，并能被本机 Codex strict config 路径接受；令牌不出现在任何进度或错误输出中。
- [ ] 既有事务失败注入测试继续证明配置、catalog、rollout 和 SQLite 任一阶段失败都会自动回滚。
- [ ] 单元测试、Python compileall、PowerShell 解析和 POSIX `sh -n` 验证全部通过。

## Out of Scope

- 上传、同步或重放历史提示词、回复、附件、项目路径或源文件。
- 为任意自定义数值提供自由输入，或自动探测账号实际可用的最大上下文。
- 自动设置 Luna `xhigh` 或改变任何模型的 reasoning effort。
- 修改已归档 Trellis 任务中的历史域名记录。
- 创建 GitHub tag/Release 或部署到生产环境；发布需由后续明确请求触发。
