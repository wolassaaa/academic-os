# Academic OS 2.0

开发路线与检查记录：[迭代计划](docs/ROADMAP.md) · [导入前复核](docs/PROJECT-REVIEW-20261013.md)。当前 main 保存交接源码基线，首轮改进通过独立 PR 审阅。源码及 CI 结果不表示便携 EXE 已更新。

后台首页为“账号与同步”：真实 Google 浏览器授权、多个账号管理、日历选择、Moodle 学校网页登录与旧授权接入。连接保存后立即生效；作业与截止供维护核对，日常使用 Google / Apple 日历即可。[账号连接步骤](docs/CONNECTIONS.md)。

**Windows 免安装试用：** 使用新的便携 ZIP，完整解压后双击 `AcademicOS.exe`，无需装 Python 或联网下载依赖。首次在浏览器创建账号，启动器提供图形化密码重置与停止按钮。详见 [免安装版说明](docs/PORTABLE.md)。下面的 Python 安装流程面向源码开发/部署。

单机 Windows 学业工作台：SQLite 持久化任务、来源证据、排程草稿、日历同步、通知、审计及备份。一个进程管理本地工作台 `127.0.0.1:8765`、远程只读端 `127.0.0.1:8766` 和唯一 Worker。初始为 **shadow** 模式；模型分析固定禁用。

```powershell
./scripts/setup.ps1
.venv/Scripts/python.exe -m academic_os.cli --config config.json init --username owner
./scripts/run.ps1
```

打开 [本地工作台](http://127.0.0.1:8765) 并登录。初始化密码交互输入，至少 8 个字符。远程端需要 HTTPS 才能使用其安全会话 Cookie。工作台与远程端会话独立；远程端只提供登录、注销及精简看板，无法操作管理 API。

- [部署、凭据及 HTTPS 网关](docs/DEPLOYMENT.md)
- [备份、恢复、迁移与切换运行手册](docs/OPERATIONS.md)
- [Agent HTTP API 与 MCP](docs/MCP.md)
- [禁用模型的预留协议](docs/MODEL-CONTRACT.md)
- [完整规格](docs/Academic-OS-2.0-规格.md)
- [验证记录与局限](docs/IMPLEMENTATION-LEDGER.md)
- [验收覆盖与待部署验证](docs/ACCEPTANCE.md)

```powershell
.venv/Scripts/python.exe -m pytest
.venv/Scripts/python.exe -m academic_os.cli --config config.json check
./scripts/package.ps1
```

源码包不含真实配置、数据库、Credential Manager 凭据或虚拟环境。新电脑需安装 Python 3.11+ 并重新执行 setup。Google/飞书授权、Windows 服务真实安装与重启、第二台电脑及公网 Funnel 验证需在目标环境完成；代码和离线测试不能替代这些验收。

默认 setup 仅安装运行依赖；开发验证使用 `./scripts/setup.ps1 -Development`。安装使用本次验证的 `requirements.lock.txt` 作为版本约束。复用来源与许可见 [THIRD-PARTY-NOTICES.md](THIRD-PARTY-NOTICES.md)。
