# 修复残留 SQLite WAL/SHM 误判

## Goal

让选择 `Y` 的会话可见性迁移在 Codex 已完全退出但 `state_5.sqlite-wal` / `state_5.sqlite-shm` 合法残留时继续安全执行，并发布 `v0.3.1` 供 Windows 实机复测。

## Background

- `v0.3.0` 的 `ensure_sqlite_ready()` 只要发现 WAL 或 SHM 文件存在就报告“数据库正在使用中”。
- 用户在关闭 Codex 后仍稳定遇到该错误；输出显示迁移计划已生成，但事务在任何目标写入前停止。
- 隔离复现证明：无进程占用、保留有效 WAL/SHM 的数据库会被当前检查拒绝，但同一数据库可以通过 `VACUUM INTO` 完成一致备份，备份 `integrity_check=ok` 且包含 WAL 中已提交的数据。
- SQLite 官方文档明确：进程未干净关闭或使用持久 WAL 时，WAL 可以在所有连接关闭后继续存在；WAL 属于数据库持久状态，不能与主数据库分离或直接删除。
- SQLite 官方文档也明确：WAL 模式下 `BEGIN EXCLUSIVE` 与 `BEGIN IMMEDIATE` 等价，不能单独证明没有其他读连接。

## Requirements

1. 不得再把 WAL/SHM 文件存在本身当作 `setup + Y` 的占用证据。
2. 不得手工删除、截断、重命名或绕过 WAL/SHM；恢复和检查必须交给 SQLite。
3. 只有正常 `setup`、用户选择 `Y`，并且最新进程探测确认无 Codex 桌面后端时，事务才能启用残留 WAL 恢复。
4. 选择 `Y` 后，无论初始预检是否发现桌面进程，都必须在读取数据库和会话文件前重新探测；探测失败或发现后端时停止。
5. 在正式调用事务前再次探测，防止用户在扫描大量 rollout 文件期间重新启动 Codex。
6. 允许恢复时，SQLite 必须执行有界等待的 `wal_checkpoint(RESTART)`、完整性检查和写事务锁检查；busy、损坏或锁失败均在目标配置/rollout 写入前停止。
7. 数据库备份继续使用 `VACUUM INTO`，必须包含残留 WAL 中已提交的数据并在任何业务数据修改前完成。
8. `restore` 和未携带最新进程验证证据的底层事务调用继续保留原有保守 sidecar 拒绝策略。
9. `N`、`--detect-only`、`--dry-run` 不恢复 WAL、不关闭进程、不修改会话或数据库。
10. 错误路径不得删除 sidecar，不得留下部分配置、rollout 或 SQLite 迁移。
11. 版本升级为 `0.3.1`，更新 README 与后端契约，并发布带 SHA-256 校验的 GitHub Release。

## Acceptance Criteria

- [x] 有效残留 WAL/SHM、无占用进程时，`setup + Y` 成功备份并迁移，WAL 中已提交的数据没有丢失。
- [x] 活跃 WAL 读/写事务、SQLite busy、完整性失败或 Codex 后端重新出现时，在任何目标业务写入前报错。
- [x] 初始预检未发现桌面进程的 `Y` 分支仍会执行两次新鲜进程探测。
- [x] `restore` 和默认底层事务调用仍拒绝未经进程验证的 sidecar 状态。
- [x] 失败路径保持配置、模型目录、rollout、SQLite 和 sidecar 的逻辑内容不变；不会直接删除 WAL/SHM。
- [x] `N`、`--detect-only` 和 `--dry-run` 的无副作用行为保持不变。
- [x] 全量单元测试、Python 编译、PowerShell 解析、POSIX `sh -n`、README 单行命令解析和 release 打包校验通过。
- [x] `v0.3.1` GitHub Actions 成功，5 个 Release 资产哈希匹配，远端 bootstrap `--detect-only` 成功。

## Out of Scope

- 手工清理用户的 WAL/SHM 文件。
- 允许 Codex 桌面后端保持运行时迁移会话。
- 上传、复制或重放历史消息、附件、项目或源码。
- 改变 SQLite schema 或 rollout JSONL 格式。
- 在当前 Codex 任务内执行会终止本任务的真实 `Y` 关闭测试。
