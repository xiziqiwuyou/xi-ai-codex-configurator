# 技术设计：新端点、进度反馈与长上下文配置

## 1. 边界与原则

本次改动保留现有三条安全边界：固定 Xi-AI Responses provider、会话内容始终只在本机、所有目标写入继续由现有备份/事务/回滚路径负责。进度反馈是观察层，不得成为业务状态的第二来源，也不得改变执行顺序。

不引入第三方依赖。Release bootstrap 在下载项目代码之前必须独立运行，因此下载进度实现留在 `scripts/bootstrap.py`；配置程序内部的会话进度使用包内轻量事件与渲染器。两者共享相同的用户体验规则，但不建立运行时导入关系。

## 2. 端点映射

`src/codex_configurator/endpoints.py` 仍是唯一运行时端点来源：

```text
ORIGIN        = https://api.xi-ai.net
API_BASE      = https://api.xi-ai.net/v1
MODELS_URL    = https://api.xi-ai.net/v1/models
RESPONSES_URL = https://api.xi-ai.net/v1/responses
```

`remote_models.py` 继续使用 `MODELS_URL`，`toml_merge.py` 继续使用 `API_BASE`。不新增 CLI 参数或环境变量覆盖域名，避免配置漂移和 `/v1/v1`。

## 3. 下载进度

### 3.1 事件来源

为 bootstrap 的有限读取函数增加可选进度回调，事件至少包含阶段标签、当前字节数、可选总字节数、尝试序号和状态。`_read_limited` 在每个数据块后报告字节数，`_open_bytes` 在重试时重置当前值，`install_release` 在元数据、checksum、bundle、校验、解压和缓存安装边界报告阶段状态。

保留现有大小上限、GitHub URL 白名单、三次重试、SHA-256 校验和安全解压顺序。实现可继续在内存中读取受限数据；进度功能不要求改变下载存储模型。

### 3.2 渲染

- TTY：使用单行 ASCII 进度条、百分比和字节数原位刷新，阶段结束后换行。
- 非 TTY/注入输出：输出阶段开始和完成，并按固定百分比桶或字节阈值节流；不写 ANSI 转义序列和裸 `\r`。
- 未知总量：显示已接收的 KiB/MiB，不伪造百分比。
- 缓存命中：明确显示“已验证缓存，跳过程序包下载”。

README 一行命令为两个初始 `curl` 调用显式加入 `--progress-bar`；Python bootstrap 负责随后 Release 解析和 bundle 的详细反馈。

## 4. 会话合并进度

### 4.1 包内事件合同

新增一个无依赖的包内进度模块，提供不可变事件和可选回调。事件字段限定为：阶段 ID、中文标签、状态（开始/更新/完成）、当前值、总值。业务函数在回调缺失时行为与当前版本一致。

CLI 创建一个渲染器并把回调传给：

1. `collect_rollout_changes`：先收集两个会话根目录下的 JSONL 路径以确定总数，再扫描首行；只报告计数，不报告路径。
2. `apply_setup` / `create_backup`：报告 readiness、文件备份、SQLite 快照、配置/catalog 写入、rollout 改写、SQLite 更新。
3. 失败分支：在自动恢复前后报告回滚状态，异常类型仍由现有 `TransactionError` 归一化。

非 TTY 渲染器对大量文件按百分比桶节流，确保 913 个文件不会产生 913 行输出。SQLite `VACUUM INTO` 无可靠页级百分比，因此显示明确的开始/完成阶段，不改写为另一种备份机制。

### 4.2 不变量

- `N` 分支不创建或调用任何会话进度生产者。
- 扫描仍只解析 rollout 第一条 `session_meta`。
- 备份仍先于业务数据修改；SQLite 仍使用 `VACUUM INTO`。
- readiness gate、两次进程检测、WAL checkpoint/quick-check/write-lock 与失败回滚保持原顺序。
- 进度事件不包含 token、HTTP header、响应正文、会话正文或单文件路径。

## 5. 长上下文配置

### 5.1 数据模型

引入显式的上下文配置操作，而不是用模糊的 `None` 同时表示“保留”和“删除”：

```text
preserve -> 不管理两个键
set      -> 同时写入 window 与 auto-compact limit
clear    -> 同时删除两个键
```

预设值固定为：

| 预设 | `model_context_window` | `model_auto_compact_token_limit` |
| --- | ---: | ---: |
| 500K | 500000 | 450000 |
| 1M | 1000000 | 900000 |

`toml_merge.merge_config` 接收该操作。`preserve` 时两个键不进入 managed root key 集合；`set`/`clear` 时先移除已有顶层赋值，`set` 再写入两个整数，`clear` 不写回。这样既不会默认覆盖用户设置，也能显式撤销先前由工具写入的长上下文。

在本机 Codex CLI `0.144.1` 的 strict-config 解析中，旧的
`preferred_auth_method` 已不再是受支持的顶层字段。合并器继续把它视为
legacy managed key 以清理旧配置，但不再写回；`forced_login_method` 与
provider 的 `experimental_bearer_token` 保持不变。

### 5.2 CLI 交互

模型选择后，仅对精确 slug `gpt-5.6-sol`、`gpt-5.6-terra`、`gpt-5.6-luna` 显示四项菜单：保留（默认）、500K、1M、恢复默认。其他模型直接使用 `preserve`。

计划摘要显示操作和值。500K/1M 菜单附近显示简短成本提示，并注明 272K 以上计费以服务方当前规则为准。配置完成后沿用现有“重启 Codex，并创建新任务”的使用约束；不自动修改 `model_reasoning_effort`。

## 6. 兼容性与回滚

- `merge_config` 保留未知顶层设置、MCP、projects、profiles 和其他 provider table。
- 所有新增回调都提供默认空值，保持直接调用者兼容。
- 下载/会话渲染器可通过注入输出捕获，便于单元测试，且不影响错误返回码。
- 回滚继续使用当前备份 manifest；上下文键位于原始 `config.toml` 备份内，不需要 manifest schema 变更。
- 新域名发布失败时，代码回滚只需恢复端点常量与文档；实机配置失败由现有备份恢复命令处理。

## 7. 已知限制

- 官方 Codex 文档与 Docs MCP 在当前环境返回 403；配置字段的有效性由本机 `codex-cli 0.144.1` schema、strict parser 和模型目录核验。
- 工具不能验证某个 Xi-AI token 对 500K/1M 的真实额度或计费资格；菜单必须把这类信息表述为用户选择和服务方规则，而非自动保证。
- SQLite `VACUUM INTO` 不提供当前实现可用的百分比回调，因此该阶段为不确定进度状态。
