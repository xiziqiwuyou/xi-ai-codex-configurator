# Windows 固定入口调用热修

## Goal

修复线上 `setup.ps1` 在 Windows PowerShell 5.1 中把内嵌 Python 源码通过
`python -c` 传递时发生的引号丢失，使固定入口能够完成发布校验并进入安全检测或交互配置流程。

## Background

- `v0.5.3` 已成功上传且所有线上文件的 SHA-256 回读校验通过。
- 真实运行线上 `setup.ps1 --detect-only` 时，Python 收到的正则字符串引号被 PowerShell
  原生参数传递层移除，触发 `SyntaxError`；配置器尚未启动，因此没有读取 Key 或写入 Codex 文件。
- `setup.sh`、发布清单、Bootstrap 和包内容没有出现哈希或下载完整性问题。

## Requirements

1. `setup.ps1` 必须把下载验证器写入本次随机临时目录中的 `.py` 文件，再由已选定的
   Python 3.11+ 解释器执行；不得继续用 `-c` 传递复杂多行源码。
2. 保留现有的固定 HTTPS 源、禁止重定向、大小限制、重试、manifest/Bootstrap/独立
   checksum 三重一致性校验、临时目录清理和参数透传行为。
3. 添加真实子进程回归测试，证明 Windows PowerShell 能运行打包后的固定入口；测试只能使用
   本地模拟发布源或 `--detect-only`，不得读取 Key 或写入真实 Codex 配置。
4. 通过全量测试、Python 编译、PowerShell 解析、POSIX shell 语法和差异检查后，发布新的
   不可变版本 `v0.5.4`，再把固定入口及 `latest.json` 更新到该版本。

## Acceptance Criteria

- [x] `scripts/remote_setup.ps1` 不再以 `python -c <multiline source>` 执行下载验证器。
- [x] 本地 PowerShell 5.1 真实进程回归测试通过，并能把 `--detect-only` 透传到底层配置器。
- [x] 全量单元测试与发布前静态检查通过。
- [x] 公开 HTTPS 的 `latest.json` 指向 `v0.5.4`，固定入口和 checksum 回读匹配。
- [x] 从公开地址下载并校验 `setup.ps1` 后执行 `--detect-only` 返回成功，且不提示输入 Key。

## Out of Scope

- 不改变 Xi-AI API 地址、模型、上下文、备份、对话可见性或 Codex 路径发现逻辑。
- 不修改 `setup.sh` 的运行语义。
- 不覆盖或删除 `v0.5.3` 不可变版本目录。
