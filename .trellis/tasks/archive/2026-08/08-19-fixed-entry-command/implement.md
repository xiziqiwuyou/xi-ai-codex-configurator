# 实施计划

1. 编写 PowerShell/POSIX 固定入口及参数转发、Python 版本检查和下载校验。
2. 扩展发布打包器生成入口文件及 checksum。
3. 扩展 FTPS 发布器上传、回读、临时文件清理和 latest 顺序。
4. 更新发布/Bootstrap/README/后端规范测试。
5. 运行全量单元测试、compileall、PowerShell 解析、`sh -n`（可用时）和
   `git diff --check`。
6. 以新不可变版本发布到 FTP，验证固定入口和 latest 公网地址。
