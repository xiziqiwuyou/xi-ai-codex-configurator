# 技术设计：固定入口与安全短命令

## 入口资产

`package_release.py` 在原五个版本资产之外生成四个固定入口资产：

- `setup.ps1`
- `setup.ps1.sha256`
- `setup.sh`
- `setup.sh.sha256`

源文件分别为 `scripts/remote_setup.ps1` 和 `scripts/remote_setup.sh`，不与
仓库内用于本地 checkout 的 `scripts/setup.ps1` / `scripts/setup.sh` 混用。

## 入口流程

入口脚本固定信任 `https://download.xi-ai.net/xi-ai-codex`，使用无重定向、
TLS、大小上限和重试下载 `latest.json`、版本 manifest、Bootstrap 及其校验
文件。它先验证版本、资产名、大小、哈希和 checksum 文件，再调用本机 Python
运行 Bootstrap。Bootstrap 会再次执行现有完整版本校验，形成双层校验。

入口参数不解释业务选项，只原样转发给 Bootstrap；入口默认增加
`--configure`，因此用户不再需要输入版本号。

## 发布流程

版本目录继续只包含五个 immutable 资产。发布器在版本目录验证并切换成功后，
把四个入口资产上传为根目录临时文件，分别通过公开 HTTPS 回读校验，再重命名
到固定文件名；最后才上传并重命名 `latest.json`。失败时删除临时文件，旧
`latest.json` 保持不变。

## 兼容与风险

- 旧版本目录和旧 Bootstrap URL 不变。
- 固定入口只减少用户输入，不改变 token、对话合并或备份逻辑。
- 裸 `irm URL | iex` / `curl URL | sh` 不作为文档命令，以免绕过入口校验。
