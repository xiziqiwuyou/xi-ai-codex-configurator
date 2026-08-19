# 技术设计：FTPS 发布、HTTPS 消费

## 边界

```text
Git tag -> GitHub Actions -> package_release.py -> FTPS /xi-ai-codex/<tag>/
                                              -> FTPS /xi-ai-codex/latest.json

客户端 -> HTTPS latest.json -> HTTPS <tag>/xi-ai-codex-release.json
        -> HTTPS bootstrap/checksum -> 本地 bootstrap
        -> HTTPS bundle/checksum -> 本地缓存 -> setup
```

GitHub 仓库和 tag 仍用于源码审计、测试触发和回滚；GitHub Release API 不再是
客户端或发布产物的来源。

## 发布文件协议

每个不可变版本目录包含：

```text
/xi-ai-codex/<version>/
  xi-ai-codex-bundle.zip
  xi-ai-codex-bundle.zip.sha256
  xi-ai-codex-bootstrap.py
  xi-ai-codex-bootstrap.py.sha256
  xi-ai-codex-release.json
```

`xi-ai-codex-release.json` 沿用现有 schema，并额外由 bootstrap 严格检查资产
名称、大小、哈希和 `version`。根目录 `latest.json` 使用：

```json
{
  "schema_version": 1,
  "version": "v0.5.0"
}
```

bootstrap 只接受上述精确字段的有效值，并自行拼接受控 URL；不信任 manifest 中
提供的任意下载 URL。

## Bootstrap 流程

1. 验证 Python 版本和版本标签。
2. `latest` 下载并解析 `latest.json`，显式版本跳过 pointer。
3. 下载版本 manifest，校验 schema、版本和两个资产描述。
4. 下载 bundle checksum 与 bundle，验证 Content-Length、manifest size、两个
   SHA-256；验证通过后安全解压到版本缓存。
5. 缓存 marker 绑定 manifest 中 bundle SHA-256；缓存命中时仍先验证 manifest，
   不重复下载 bundle。
6. 启动本地 setup；缺少 `--configure` 时自动加 `--detect-only`。

URL 构造集中在受控路径函数，只允许 `https://download.xi-ai.net/xi-ai-codex`
及其 `/latest.json`、`/<tag>/<asset>` 路径。网络错误可重试，HTTP、协议和
校验错误立即失败。进度事件沿用现有通用下载事件。

## Actions 上传流程

Actions 使用 FTPS CLI（`lftp`）并启用证书校验、被动模式和显式 TLS：

1. 测试、compileall、打包到临时目录。
2. 检查远端版本目录不存在；创建目录并逐个上传五个文件到临时文件名。
3. 远端用 HTTPS HEAD/GET 检查五个文件可读且大小正确。
4. 上传 `latest.json.tmp`，检查内容，再 rename 为 `latest.json`。
5. 任一步失败都不更新 latest；Secrets 通过环境变量传给 lftp，日志关闭命令回显。

由于版本目录不可变，重复 tag 默认失败，避免客户端看到部分覆盖的版本。

## 兼容与回滚

- `package_release.py` 保持五个资产名字和 manifest schema，减少客户端迁移风险。
- bootstrap 的 `--repo` 被移除；旧 GitHub bootstrap 仍可在本地缓存中运行，但新发布
  的安装入口和 README 全部指向 HTTPS 源。
- 回滚发布只需把 `latest.json` 指向已验证旧版本；不删除旧版本目录。
- 若 FTP 服务暂时关闭，客户端只会 HTTPS 失败并保留现有本地缓存，不会接触 FTP。

## 安全约束

- FTP 密码只存在 GitHub Secrets/维护者本地安全输入；绝不进入 README、Actions 输出、
  manifest、bootstrap 或客户端环境。
- 客户端固定 HTTPS 主机，防止 manifest 将安装流量导向任意主机。
- 版本和文件名采用白名单字符；所有文件下载有大小上限和完整哈希校验。
