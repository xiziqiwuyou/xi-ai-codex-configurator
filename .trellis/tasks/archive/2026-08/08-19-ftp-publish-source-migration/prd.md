# 全面迁移 FTP 发布与更新源

## Goal

让没有仓库副本的电脑可以通过一条经过校验的命令安装或更新
Xi-AI Codex Configurator；GitHub 只保留源码与 Actions 执行器，发布文件由
自建 FTPS 上传到 `download.xi-ai.net`，终端用户只通过 HTTPS 下载，不接触 FTP
账号或密码。

## Background and confirmed facts

- 服务器公开下载域名为 `https://download.xi-ai.net`，发布目录为
  `/xi-ai-codex/`。
- FTPS 仅作为 CI/维护端上传通道；客户端更新通道固定为 HTTPS 443。
- 版本目录不可变，`latest.json` 只在该版本目录全部上传并校验后最后替换。
- 当前版本产物由 `scripts/package_release.py` 生成五个固定资产：bundle、bundle
  校验文件、bootstrap、bootstrap 校验文件和 release manifest。
- 客户端配置、Codex 本地对话和项目内容必须继续留在本机；迁移功能只改本地
  provider 可见性元数据，不能上传历史内容。

## Requirements

1. **固定 HTTPS 源**
   - bootstrap 默认从 `https://download.xi-ai.net/xi-ai-codex` 读取发布数据，
     不再要求 `--repo`，也不调用 GitHub Release API。
   - 支持 `latest` 和显式版本标签。`latest` 先读取根目录 `latest.json`，再读取
     对应版本目录的 `xi-ai-codex-release.json`；显式版本只读取该版本 manifest。
   - URL 只允许 HTTPS、精确下载域名、受控版本标签和受控资产文件名；拒绝查询串、
     路径穿越、外部主机和不匹配的 manifest。

2. **完整校验后执行**
   - manifest 必须校验 schema、版本、资产名称、大小和 64 位 SHA-256。
   - 下载 bundle 与其 `.sha256` 后，必须同时通过 manifest 哈希和 checksum 文件校验，
     再安全解压、缓存并运行 setup。
   - bootstrap 本身也由 manifest 和独立 checksum 校验；失败时不得运行任何配置命令。
   - 继续保留大小限制、重试、ZIP 路径/符号链接/重复项防护和进度反馈。

3. **一键使用体验**
   - README 提供 Windows PowerShell 与 POSIX 的可复制单行命令：下载并校验
     `latest` bootstrap 后，以已解析版本调用 `--configure`。
   - 命令不包含 token、FTP 凭据或远程脚本管道执行；客户端只访问 HTTPS。
   - 保留显式版本、`--detect-only` 和 `--refresh` 能力。

4. **发布自动化**
   - GitHub Actions 在测试和打包成功后，通过 FTPS 将五个版本资产上传到
     `/xi-ai-codex/<tag>/`，不得覆盖已存在的版本资产。
   - 资产全部上传并做远端可读性检查后，才将指向该版本的 `latest.json` 以临时文件
     上传并原子改名/替换。
   - FTPS 主机、端口、用户名、密码均来自 GitHub Secrets，不写入日志、代码或产物。
   - Actions 不再创建或更新 GitHub Release；GitHub tag 仍是发布触发器和源码审计依据。

5. **兼容与隐私**
   - 现有 bundle 内容、配置合并、对话可见性迁移、回滚和模型/上下文设置行为不变。
   - 不修改网站根目录已有文件之外的内容；发布只写入 `xi-ai-codex/` 子目录。
   - 错误和进度信息不得打印 token、FTP 密码、Authorization、响应正文或单个会话路径。

## Acceptance Criteria

- [ ] `scripts/bootstrap.py` 在无 `--repo` 时能解析并校验 HTTPS `latest.json`，并能用显式版本安装；源码中不再依赖 GitHub Release API。
- [ ] manifest、版本、路径、大小、bundle checksum、bootstrap checksum 任一不匹配都会在运行 setup 前失败。
- [ ] 缓存命中、重试、已知/未知长度下载和 TTY/非 TTY 进度测试继续通过。
- [ ] PowerShell/POSIX 一行命令只访问 `download.xi-ai.net`，校验成功后才执行本地 bootstrap，并将 `--configure` 传递下去。
- [ ] Actions YAML 使用 FTPS Secrets 上传版本目录，`latest.json` 最后发布，不再调用 `gh release`。
- [ ] 现有配置器测试及新增 FTP/HTTPS 发布源测试全部通过；`compileall`、脚本语法检查和 `git diff --check` 通过。
- [ ] 服务器上至少验证一个版本的五个资产可通过 HTTPS 下载且 SHA-256 与本地构建一致；客户端安装不需要 FTP 凭据。

## Out of scope

- 不把 FTP 暴露为终端用户更新协议，也不在客户端嵌入 FTP 凭据。
- 不迁移或上传历史对话、附件、项目路径、源码或消息内容。
- 不在本任务中改造 Xi-AI API、Codex 配置语义或会话迁移算法。

## Risks and deferred items

- FTPS 服务必须支持 CI 所用客户端的显式 TLS、被动模式和证书校验；若服务端拒绝
  原子改名，发布脚本使用临时文件 + 最终 rename，并把失败留在版本目录而不更新 latest。
- 服务器上的旧账号目录与新账号根目录可能不同，首次发布需重新上传并用 HTTPS 逐项核验。
