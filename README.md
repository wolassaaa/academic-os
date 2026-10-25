# Academic OS 2.0

## 写在前面 (｡･ω･｡)ﾉ

这是我第一个正儿八经、想认真做下去的项目。初衷很简单：把分散的邮件、作业和截止时间整理好，少一点手忙脚乱。

目前还在边用边改，不成熟的地方欢迎指出，轻喷就好 (つ﹏⊂) 欢迎提 Issue、反馈体验或提交 PR，一起把它慢慢变好！

👉 [项目声明：第一次认真做项目，请多关照](docs/PROJECT-STATEMENT.md)

---


邮件候选分流与逐条核对见 [迭代 2 使用说明](docs/ITERATION-2.md)。未确认候选保留原邮件和新邮件通知，不产生截止提醒；确认后成为正式邮件待办。

课程／作业／小组多标签、含原文摘录的邮件简报及增量消费见 [迭代 3](docs/ITERATION-3.md)；[Hermes 接入](docs/HERMES.md) · [轻量化实测与后续优先级](docs/PERFORMANCE.md)。

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

## 新看板 v0.3

**邮件阅读 v0.4：** 导航“邮件简报”已接服务已收集邮件，支持服务端分页、搜索与收件日期/核对筛选、全文、候选证据及事项双向跳转。当前核对写操作仍在旧工作台完成。详见 [邮件阅读说明](docs/BOARD-MAIL-READ.md)。

**v0.3 个人重点：** 新增持久保存的“设为重点 / 移出重点 / 恢复自动推荐”，与截止、优先级、安排和业务版本独立；逾期提示保留。详见 [个人重点说明](docs/BOARD-FOCUS.md)。
服务启动后访问 `/board/`，或从旧工作台点击“新看板”。正式事项、时区、来源状态及本地事项完成已接服务；远程仍只读。支持完成确认、版本冲突提示、断线同请求重试、原文证据及精确跳转旧工作台。其他写操作继续在原工作台完成。演示与真实服务严格分开，详见 [读取合同](docs/BOARD-READONLY.md) 和 [本轮完成闭环](docs/BOARD-COMPLETION.md)。源码更新不代表现有便携 EXE 已更新。
