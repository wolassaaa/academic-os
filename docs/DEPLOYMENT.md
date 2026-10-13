# Windows 部署

## 本机启动

安装 Python 3.11+，将源码解压到稳定的本地目录。不要把活动 SQLite 放到网络共享或云盘同步目录。执行 `scripts/setup.ps1` 创建本机 `.venv` 并安装 Python 依赖，无需 Node。联网安装依赖后可运行离线本地工作台。

```powershell
./scripts/setup.ps1
.venv/Scripts/python.exe -m academic_os.cli --config config.json init --username owner
./scripts/run.ps1
```

初始化创建 `config.json` 和数据目录，交互设置密码；已有配置或已有 owner 不会被替换。先在 `http://127.0.0.1:8765` 登录，确认健康、来源和草稿；初期保持 `mode: shadow`。配置更改后重启进程，运行中不热更新。

两个端口始终绑定 `127.0.0.1`。禁止使用 `uvicorn --workers`、`--reload`、分别启动 Worker 或把两个应用分布在两台机器；正式运行唯一入口为 CLI `run` 或 Windows 服务。OS 文件锁保护运行与离线维护，Worker 另有数据库租约。服务、CLI 不可同时运行同一数据目录。异常退出后 OS 锁自动释放，未到期 Worker 租约可能让重启暂时失败；等待租约超时，不手工启动第二个写入者。

## 凭据与提供方

凭据存入 Windows Credential Manager，service 名固定 `academic-os-v2`，配置只存 `secret_ref`。所有 secret 值由 `getpass` 隐藏输入：

```powershell
.venv/Scripts/python.exe -m academic_os.cli secret google-personal
.venv/Scripts/python.exe -m academic_os.cli secret feishu
```

Google 值为 JSON 对象 `client_id`、`client_secret`、`refresh_token`；飞书值为 JSON 对象 `app_id`、`app_secret`。不要把真实值写入命令参数、源码或 JSON 配置。Google refresh token 必须由自己的 OAuth 客户端授权取得，具备实际所需 Gmail 读取、日历及邮件发送权限；飞书应用须具备目标日历、消息权限并在租户启用。这些授权需用户完成，程序不代建第三方应用。

Google 账户配置使用 `id`、`secret_ref`、`gmail`、`school_domains`，日历配置按连接器约定设置；飞书配置使用 `secret_ref`、`calendar_id`、`chat_id`。仅 Google 客户端读取 `google_proxy`；飞书直连，避免把本机全局代理作为默认网络路径。没有凭据时保持账户列表和飞书配置为空。

## Windows 原生服务

在持有上述 Credential Manager 凭据的同一 Windows 用户身份完成安装。提升权限终端中显式执行，输入的是 Windows 账户密码，不是 PIN；该账户需要“作为服务登录”权限、对源码/venv 的读取执行权限和数据目录写权限。

```powershell
./scripts/service.ps1 -Action install -Config config.json -Account 'COMPUTER\owner'
./scripts/service.ps1 -Action start
./scripts/service.ps1 -Action status
./scripts/service.ps1 -Action stop
```

安装创建 pywin32 SCM 服务 `AcademicOSV2`，使用本机 venv 的 Python 调用原生 Service Control Dispatcher，延迟自动启动；失败后依次等待 60、120、300 秒重启，24 小时重置计数。安装本身不会启动服务。停止会关闭两个 HTTP 端并等待 Worker 清理租约；失败退出报告非零服务退出码供 SCM 恢复。检查 Windows 事件查看器及 `sc.exe qfailure AcademicOSV2`。不要使用 LocalSystem：其 Credential Manager 与桌面用户独立。复制源码后须在新电脑重新创建 venv、注册服务、录入凭据，不能复制旧 venv 或假设秘密随备份迁移。

本实现未自动安装/启动任何服务；实际 SCM 权限、开机启动、恢复和目标用户 Credential Manager 必须在目标电脑验收。

## Tailscale HTTPS 远程看板

先完成本地 owner 初始化、登录和安全密码配置，再启用网关。默认方案由操作者显式执行 `tailscale funnel --bg http://127.0.0.1:8766`，访问设备只需浏览器，无需安装 Tailscale。若只希望 tailnet 内访问，可改用 `tailscale serve --bg http://127.0.0.1:8766`。Funnel 要求 tailnet HTTPS 证书及 Funnel 权限，首次运行按官方交互完成设置；用 `tailscale funnel status` 检查映射，`tailscale funnel reset` 关闭。命令依据 [官方 Funnel CLI](https://tailscale.com/docs/reference/tailscale-cli/funnel) 和 [官方 Funnel 前提](https://tailscale.com/docs/features/tailscale-funnel)。

**只允许代理 8766，绝不代理 8765。** 保持 `secure_cookies: true`。远程访问 HTTPS 地址并重新登录，确认匿名访问 `/api/v1/dashboard` 返回 401，远程 `/api/v1/items` 与 `/api/v1/setup` 返回 404，HTTP 管理 API 不在代理目标。API Bearer token 不能登录远程只读端。Tailscale 自身的 Windows 无人值守设置另按官方文档配置。

脚本不会执行 Tailscale 发布。公网连通性、证书及手机访问没有实际验证前，应记录为未验收。
