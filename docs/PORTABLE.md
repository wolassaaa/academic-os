# Windows 免安装试用版

本包适用于 Windows x64，内置 Python 运行时、依赖和界面资源。无需安装 Python、pip 或 Node；启动和本地事项管理不下载依赖。Gmail、Moodle、Google/飞书同步及远程公网访问仍需联网和相应授权。

## 三步使用

1. 解压**整个文件夹**到可写的本地位置，例如桌面或 `D:/Apps/AcademicOS`。
2. 双击 **AcademicOS.exe**。程序打开小窗口并自动打开浏览器工作台。
3. 首次在浏览器设置用户名和至少 8 位密码。

不要只复制 EXE，旁边的 `_internal` 包含运行依赖，需要一起保留。关闭浏览器后后台仍运行；小窗口中的“停止并退出”会安全关闭后台。“忘记密码 / 重置”可以直接设置新密码，不需要旧密码；重置后本地、远程浏览器需重新登录。

初始为 shadow、模型分析关闭，包内没有真实账号凭据或学业数据。程序首次启动后才在同目录创建 `config.json` 和 `data`。关闭程序后可以备份此目录；Windows Credential Manager 中的凭据不随文件夹复制，需要在新电脑重新授权。已有 Hermes 数据按运行手册导入，不自动覆盖。

## 已有预览与升级

默认管理端口 8765、只读端口 8766；桌面启动若端口被占用会自动选择空闲端口，启动窗口显示实际地址。同一数据目录重复打开会验证已有后台并打开其工作台。不同数据目录仍是不同实例，不能同时开启同一账号的 live 写入。程序不会杀死其他进程。CLI/服务仍使用配置端口，避免改变既有 HTTPS 转发。升级前停止程序并备份 `data` 与配置，把新版解压到新目录后按备份恢复流程迁移；不要用构建脚本覆盖正在使用的目录。

本包不会注册 Windows 服务、修改开机启动或发布 Funnel。保持小窗口运行即可试用；远程看板使用配置中的实际 dashboard_port 独立入口。正式无人值守部署的 SCM、HTTPS 和第二台物理电脑验收仍按 [部署文档](DEPLOYMENT.md) 与 [运维文档](OPERATIONS.md) 完成；便携启动器不是 SCM 服务安装器。

Google 登录组件随包提供，网络默认自动检测；普通用户无需上传 JSON 或填写代理。Google 的应用发布状态与验证状态是云端条件，参见 [长期授权与发布检查](GOOGLE-AUTH-RELEASE.md)。登录成功不代表应用已通过 Google 验证。

## 高级入口

包内仍保留统一 CLI 和 Agent 接口，无需系统 Python：

```powershell
./AcademicOS.exe --cli --config config.json check
./AcademicOS.exe --cli --config config.json backup backups/before-upgrade
./AcademicOS.exe --cli --config config.json reset-password
./AcademicOS.exe --mcp --token-ref local-agent
./AcademicOS.exe --diagnose
```

`--diagnose` 只使用临时数据和本机 HTTP，检查内置 GUI、TLS 证书、Credential Manager 后端、MCP、启动/停止/重启、8 位登录、持久化、只读权限、Markdown、Moodle 独立进程及模型关闭；不读取你的账号凭据，不访问学校服务。

## 开发者重建

```powershell
.venv/Scripts/python.exe -m pip install '.[build]'
./scripts/build-portable.ps1
```

构建使用 PyInstaller 6.22.3 文件夹模式，运行时随包附带。[上游说明](https://pyinstaller.org/en/stable/operating-mode.html)。构建输出限于项目 `dist` 子目录；若目标含用户配置、数据或数据库，脚本拒绝覆盖。依赖许可随 `licenses` 文件夹保留。当前验证为本机 Windows x64 的独立目录与去除 Python PATH 的运行测试，不等同于第二台物理电脑验证。
