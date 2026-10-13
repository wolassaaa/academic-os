# 运行与迁移手册

## 忘记密码

密码最低 8 个字符。首次设置与本地重置使用相同规则。忘记密码时，在后台 Windows 主机的项目目录运行（当前预览使用 `config.local.json`，正式部署使用对应的 `config.json`）：

```powershell
.venv/Scripts/python.exe -m academic_os.cli --config config.local.json reset-password
```

命令隐藏输入新密码并要求重复确认，不需要旧密码；只有一个账户时自动选择，多账户时用 `--username 用户名`。重置在数据库事务内完成，可在后台运行时使用，会清除登录限流并使该账户本地和远程浏览器会话失效。不会创建不存在的账户，也不改邮件/日历授权或独立 Agent token；如需撤销 Agent token，另用 `token-revoke`。远程登录页说明本地主机恢复方式，没有无需验证即可重置的公开接口。

## 日常健康

本地登录后检查健康页：`worker_online`、`heartbeat`、`data_stale`、来源最后成功时间，以及失败任务与通知。心跳停止或来源过期时，旧数据仍可读，但不能当成最新。离线 `check` 执行 SQLite 完整性检查，须先停止服务/CLI；运行中的健康使用工作台或 `/api/v1/health`。

后台任务、通知、草稿和操作幂等键保存在数据库。重试 POST 要复用原来的 `Idempotency-Key`，新业务操作才生成新键；版本冲突应刷新并重建草稿，不覆盖来源证据。外部接口超时后可能已接受操作，不能声称第三方发送严格 exactly-once。

## 停机备份与恢复

```powershell
./scripts/service.ps1 -Action stop
.venv/Scripts/python.exe -m academic_os.cli --config config.json backup backups/before-cutover
.venv/Scripts/python.exe -m academic_os.cli --config config.json check
```

备份使用 SQLite 一致性 backup API，保存可迁移的导入快照、配置及文件哈希清单；Credential Manager 密钥不打包。备份含邮件、学业数据及账户标识，按私人资料保存。维护命令和运行使用同一独占 OS 锁；备份时停止运行可同时固定本地导入文件，不接受正在变化的源文件副本。

恢复时先备份当前状态，再停止唯一运行实例：

```powershell
.venv/Scripts/python.exe -m academic_os.cli --config config.json restore backups/before-cutover
.venv/Scripts/python.exe -m academic_os.cli --config config.json check
```

恢复验证文件哈希，回到 shadow，禁用模型，并使旧会话与 Agent token 失效；重新登录并重新签发 token。先校验 owner、条目数、草稿、映射和来源路径，再在 shadow 启动。恢复不是跨节点复制：**旧电脑停止并确认不再写外部系统后，才可启用新电脑。** 运行锁只能保护同机同数据目录，不能阻止两个独立复制品同时向 Google/飞书写入。

## 导入旧系统

```powershell
.venv/Scripts/python.exe -m academic_os.cli --config config.json import-legacy 'C:/Users/owner/.hermes'
```

导入只读旧根目录，生成来源/哈希/数量清单并保存必要本地快照；默认不导入凭据。只有明确需要迁移 Credential Manager 时才加 `--import-credentials`，导入账户与以后服务账户须相同。旧定时任务不会被修改、停止或删除。检查导入报告中的缺失文件、跳过字段和待人工补充日期，不以假日期填充“待公布”事项。

## shadow 到 live 及回退

1. 保持旧系统运行，2.0 保持 shadow，核对任务来源、截止日、空闲时间、重复项、影子日历变更和通知预览。
2. 本机测试完成后，核验提供方权限、账户身份和 Google 代理/飞书直连；实际联网验收需用户自己的凭据。
3. 保存备份与旧 cron 清单，在明确切换窗口中停止旧系统会写日历/发通知的作业，确认没有运行中任务。此仓库不会替用户关闭 Hermes cron。
4. 停止 2.0，执行下面的显式 cutover。它要求 owner、Google 可写日历、通知目标和当前用户 Credential Manager 凭据完整；先创建备份，再保存 live，且不会启动进程。过期 shadow 日历任务被 superseded，只排队当前条目版本；历史未投递外部通知被 superseded，启动后按当前截止日生成相关提醒，不补发全部历史预览。随后以唯一实例启动，检查第一轮日历写入与通知回执；新的排程仍须审阅并确认草稿。
5. 失败时先停止 2.0，执行 `shadow` 或恢复备份，核对第三方实际状态后恢复旧 cron；数据库恢复不能撤销已发消息或已写日历事件。

```powershell
.venv/Scripts/python.exe -m academic_os.cli --config config.json cutover --legacy-stopped --backup-dir backups/cutover
./scripts/run.ps1
# 回退（先停止运行）
.venv/Scripts/python.exe -m academic_os.cli --config config.json shadow
```

`--legacy-stopped` 是操作者对旧外部写入任务已停止的明确声明，程序不自动控制 Hermes cron。禁止直接编辑配置跳过 cutover 的备份及预览清理步骤。

第二台 Windows 的完整恢复、凭据录入、服务重启、HTTPS 手机访问属于部署验收，本机离线测试没有覆盖实际第二台电脑。
