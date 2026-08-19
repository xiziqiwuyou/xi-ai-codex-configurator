# 固定下载入口与一行配置命令

## 目标

为没有仓库副本的电脑提供稳定的下载入口，让用户只需要复制一条短命令
即可启动当前最新版 Codex 配置流程。

## 需求

1. 提供固定 HTTPS 入口：
   - `https://download.xi-ai.net/xi-ai-codex/setup.ps1`
   - `https://download.xi-ai.net/xi-ai-codex/setup.sh`
2. 入口脚本解析 `latest.json`，下载并校验对应版本的 Bootstrap，再以
   `--configure` 启动本地配置；不得执行未经校验的远程代码。
3. 入口脚本支持把额外参数转发给配置器，例如 `--backup-root` 和
   `--detect-only`。
4. 发布器把固定入口及其独立 SHA-256 文件同步到 FTP 根目录，并在公开
   HTTPS 地址回读校验后才更新 `latest.json`。
5. 保持现有五个版本资产、版本目录不可变、FTPS 被动 TLS 和凭据不进仓库。
6. README 提供最短的 Windows/macOS/Linux 命令，并说明不能安全地把裸 URL
   直接当作命令执行。

## 验收标准

- 固定入口在 `latest` 更新后仍无需修改用户命令。
- 入口下载失败、重定向、版本清单错误或 SHA-256 不匹配时，在配置器启动前
  退出且不写入 Codex 文件。
- `setup.ps1 --detect-only` 和 `setup.sh --detect-only` 能透传到配置器。
- 发布测试验证固定入口临时上传、公开回读、原子替换顺序和 latest 最后更新。
- 全量测试通过，README 不出现 `| iex` 或 `curl | sh`。
