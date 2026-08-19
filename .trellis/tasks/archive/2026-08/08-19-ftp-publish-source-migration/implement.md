# 实施计划

## 1. Bootstrap 协议迁移

- [ ] 替换 GitHub URL、请求头、Release API 和资产解析为固定 HTTPS 源。
- [ ] 实现 `latest.json` pointer、版本 manifest 解析和严格字段/路径/大小/哈希校验。
- [ ] 保留缓存、重试、下载限制、ZIP 安全校验、进度和 setup 参数转发。
- [ ] 更新错误文本，确保不再泄露 GitHub/FTP 凭据或响应正文。

## 2. 测试与打包

- [ ] 重写 bootstrap 单元测试的 fake source payload，覆盖 latest、显式版本、路径拒绝、manifest mismatch、checksum mismatch、缓存和默认 detect-only。
- [ ] 扩充 package manifest 测试并保持五个资产不变。
- [ ] 保持全量配置器测试、compileall、PowerShell/POSIX 语法检查通过。

## 3. 发布工作流

- [ ] 修改 `.github/workflows/release.yml`：安装/配置 FTPS 客户端，使用 Secrets 上传版本目录。
- [ ] 实现版本不可变检查、临时文件上传、HTTPS 可读性验证和最后更新 latest。
- [ ] 移除 GitHub Release 创建权限和命令，保留 tag 触发与测试。

## 4. 文档与实机发布

- [ ] 将 README 的一键命令、手动版本命令、发布说明和安全说明迁移到 HTTPS 源。
- [ ] 本地构建目标版本，使用维护端 FTPS 上传到 `download.xi-ai.net/xi-ai-codex/`。
- [ ] 通过 HTTPS 下载五个资产、校验 SHA-256、运行 `--detect-only`，再进行真实配置测试。

## 验证命令

```powershell
$env:PYTHONPATH = "src"
python -m unittest discover -s tests -v
python -m compileall src scripts tests
sh -n scripts/setup.sh
git diff --check
curl.exe --fail --silent --show-error --head https://download.xi-ai.net/xi-ai-codex/latest.json
```

Windows PowerShell 脚本使用 `Parser::ParseFile` 做语法检查；网络验证只读取公开
HTTPS 资产，不在终端打印 FTP 密码。

## 风险点与回滚点

- bootstrap 协议改动与测试必须同时提交；若失败，旧版本缓存仍可直接运行。
- Actions 修改先以语法检查验证，确认远端五个资产均可读后才写 latest。
- 不删除旧版本目录或网站既有文件；发布失败时只留下未指向 latest 的新目录，可人工清理。
