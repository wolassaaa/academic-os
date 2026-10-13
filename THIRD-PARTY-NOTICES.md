# 复用来源与依赖

实现优先使用已有库，不复制整套 Agent 框架。业务状态只保存在本项目 SQLite 中，第三方库负责 HTTP、协议、日期规则和平台接入。

| 组件 | 用途 | 上游与许可证 |
|---|---|---|
| FastAPI / Starlette | HTTP API、认证入口、静态工作台 | [FastAPI](https://github.com/fastapi/fastapi)、[Starlette](https://github.com/encode/starlette)，MIT / BSD-3-Clause |
| Uvicorn | 同一进程内两个 loopback HTTP 入口 | [Uvicorn](https://github.com/encode/uvicorn)，BSD-3-Clause |
| HTTPX | 连接池、超时、Google 独立代理 | [HTTPX](https://github.com/encode/httpx)，BSD-3-Clause |
| MCP Python SDK 1.x | 官方 FastMCP、stdio 与协议校验 | [modelcontextprotocol/python-sdk](https://github.com/modelcontextprotocol/python-sdk)，MIT |
| python-dateutil | RFC 重复规则、例外日期、时区展开 | [python-dateutil](https://github.com/dateutil/dateutil)，Apache-2.0 / BSD-3-Clause |
| keyring | Windows Credential Manager 凭证引用 | [keyring](https://github.com/jaraco/keyring)，MIT |
| pywin32 | Windows 原生服务、启动和恢复 | [pywin32](https://github.com/mhammond/pywin32)，以上游各文件许可为准 |
| Python / SQLite / tzdata | 运行时、事务、在线备份、时区 | 随各发行包保留其许可 |
| pytest / Playwright / Ruff | 自动化测试、浏览器验证、静态检查 | 仅开发依赖，按其发行包许可 |
| PyInstaller | 免安装 Windows 程序打包，内置 Python 运行时 | [PyInstaller](https://pyinstaller.org/en/stable/operating-mode.html)，GPL-2.0-or-later with bootloader exception；仅构建依赖 |

Python 安装包保留各依赖的 `.dist-info/licenses` / LICENSE 等原始许可文件。`requirements.lock.txt` 是本次通过测试的 Windows / Python 3.11 依赖快照，不包含任何凭证。这里没有把第三方代码重新声明为本项目拥有。

## 内部旧系统

从用户提供的 Hermes 交接系统复用经过审计的 Moodle 读取与数据转换片段；来源、修改范围和 SHA-256 见 [docs/REUSE.md](docs/REUSE.md)。这些是用户原系统代码，没有将其宣称为公共开源代码。旧提醒、日历写入和业务编排脚本不会被运行。

## 协议依据

- Google：[增量同步](https://developers.google.com/workspace/calendar/api/guides/sync)、[版本与条件更新](https://developers.google.com/calendar/api/guides/version-resources)。实现分页、410 游标重建、稳定事件 ID 和 If-Match。
- Apple：[通过 Google 账号使用同一份日历](https://support.apple.com/en-ae/guide/iphone/iphc37be2016/27/ios/27)。不创建 iCloud 副本。
- 飞书：[创建日程](https://open.feishu.cn/document/server-docs/calendar-v4/calendar-event/create)、[官方 SDK](https://github.com/larksuite/oapi-sdk-python)。适配器使用创建幂等键；重复日程按过去 7 天至未来 90 天展开为镜像实例，处理改单次和取消。窗口每日推进，不承诺飞书展示无限未来。
- Tailscale：[Funnel HTTPS](https://tailscale.com/docs/features/tailscale-funnel)、[Windows 无人值守](https://tailscale.com/docs/how-to/run-unattended)。仅暴露独立只读入口。

没有复制网上不明许可的完整项目。选择这些基础库是为了减少依赖、保持 Windows 可迁移，并避免维护重复的 HTTP、MCP 和重复规则实现。
