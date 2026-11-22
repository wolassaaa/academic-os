# 交接手册：把 Academic OS 迁到 Hermes 主机（给 B 机上的 Codex）

读者：在 **B 机**（Hermes 常驻的那台 Windows，原生运行，不是 WSL）上执行任务的 Codex。
目标：Academic OS 在 B 机上 24 小时运行，作为唯一的事实来源；Hermes 在同一台机器上通过 `http://127.0.0.1:8767/mcp` 读取；用户在 A 机用浏览器通过 Tailscale 访问完整页面。

## 必须遵守

1. **同一时刻只能有一份 Academic OS 在运行。** 用户确认 A 机上的旧版已经退出、并且关掉了自启之后，B 机才能启动。两份同时跑会重复推送通知、重复写日历。
2. **不要在聊天或日志里输出任何令牌、密码或 OAuth 内容。** 令牌直接写进文件，不要回显。
3. 以下几步**必须停下来，交给用户亲自操作**：Google 授权（修复授权）、学校 SSO 登录、设置本地密码、Google Cloud 控制台里的任何操作、暂停或删除 Hermes 原有的定时任务。
4. 不删除 Hermes 原有的任何配置或数据。修改前先备份，只改需要改的条目。
5. 每一步做完都要拿实际命令输出来验收；没验证过的步骤，不能报告为“已完成”。

## 输入（由用户提供）

- 便携包 `AcademicOS-20261025-RemoteFull-Portable.zip`（以 SHA256 为准，见交付记录）。
- A 机生成的备份目录，例如 `to-B-20261025`。用户用 U 盘或 Taildrop 拷过来，不经过网盘。
- 用户的 Tailscale 登录名（邮箱形式），用于远程访问的白名单。

## 步骤

### 1. 检查环境
```powershell
whoami
tailscale status
Get-Process | Where-Object { $_.ProcessName -match 'AcademicOS' }
```
- 记下 Hermes 是以哪个 Windows 用户运行的。Academic OS 必须用**同一个用户**运行，因为授权令牌存在这个用户的凭据管理器里。
- 确认 B 机上没有正在运行的 AcademicOS。

### 2. 解压并核验
解压到 `C:\AcademicOS`（本地磁盘，不要放在网盘同步目录）。计算 zip 的 SHA256，和交付记录比对。然后运行自检：
```powershell
& 'C:\AcademicOS\AcademicOS.exe' --diagnose
```
期望最后一行是 `{"ok": true, ...}`，检查项里包含 `agent_gateway_bearer` 和 `remote_full_tailnet_only`。

### 3. 恢复数据（用户确认 A 机已经停机之后）
```powershell
& 'C:\AcademicOS\AcademicOS.exe' --cli --config 'C:\AcademicOS\config.json' restore '<备份目录>'
& 'C:\AcademicOS\AcademicOS.exe' --cli --config 'C:\AcademicOS\config.json' check
```
恢复后会自动回到 shadow 模式，旧的 Agent 令牌全部作废，这是设计如此。

### 4. 写配置
在 `C:\AcademicOS\config.json` 里合并下面这些键，其他内容保留：
```json
"agent_gateway_port": 8767,
"sync_interval_seconds": 120,
"remote_access": "full",
"remote_allowed_logins": ["<用户的 Tailscale 登录名>"],
"google_oauth_publishing": "production"
```
- 如果用户说 Google 应用还没发布，`google_oauth_publishing` 写 `"testing"`。
- `secure_cookies` 保持 `true`（远程访问走 HTTPS）。

### 5. 签发 Hermes 令牌（Academic OS 处于停止状态，不回显）
```powershell
$token = & 'C:\AcademicOS\AcademicOS.exe' --cli --config 'C:\AcademicOS\config.json' token --name hermes-local --scopes read draft sync | Select-Object -Last 1
$hermes = Join-Path $env:USERPROFILE '.hermes'   # 以 Hermes 实际使用的 profile 目录为准
Add-Content -Path (Join-Path $hermes '.env') -Value "`nACADEMIC_OS_TOKEN=$token"   # 若已有同名行，先手动删除旧行
@{ url = 'http://127.0.0.1:8767/mcp'; token = $token } | ConvertTo-Json | Set-Content -Path (Join-Path $hermes 'academic-os.json') -Encoding utf8
Remove-Variable token
```

### 6. 开机自启（任务计划程序）
新建一个任务：
- 触发器：“用户登录时”，用户选第 1 步记下的那个 Hermes 用户；
- 操作：`C:\AcademicOS\AcademicOS.exe`，参数 `--cli --config C:\AcademicOS\config.json run`；
- 设置：失败时每 1 分钟重启，最多 3 次；不要勾选“如果任务运行时间超过…则停止”。

然后手动运行一次这个任务。

### 7. 交给用户：授权与登录
请用户在 B 机（本地或远程桌面）打开 `http://127.0.0.1:8765`：
- 用原来的密码登录；
- 设置 → 账号连接：Google 账号点“修复授权”，学校点“网页登录”。

然后验收 health：
```powershell
# 用户登录后，在浏览器打开 http://127.0.0.1:8765/api/v1/health，确认：
# worker_online: true；gmail 和 moodle 来源的 last_success 是新的；authorizations[*].state == "ok"
```

### 8. 接入 Hermes（同机，不需要 Tailscale）
1. 备份 Hermes 的 `config.yaml`，然后在 `mcp_servers` 里合并：
   ```yaml
   academic-os:
     url: "http://127.0.0.1:8767/mcp"
     headers:
       Authorization: "Bearer ${ACADEMIC_OS_TOKEN}"
     timeout: 30
     connect_timeout: 15
     tools:
       include: [health, summary, list_items, get_item, list_tags, set_item_tags, list_mail, get_mail, mail_digest, mail_feed, acknowledge_mail_feed, list_conflicts, list_drafts, generate_plan, create_draft, sync]
   ```
   如果 `/reload-mcp` 之后 `health` 返回 401，说明 headers 里不支持 `${VAR}`，就把令牌原文写进去，并收紧这个文件的权限。
2. 把 `scripts/hermes/academic_os_wake.py` 复制到 Hermes 的 `scripts` 目录，手动运行一次，最后一行应该是 `{"wakeAgent": ...}`。
3. 按 [HERMES-REMOTE.md](HERMES-REMOTE.md) 第 5 步创建两个 cron 任务：每 2 分钟的邮件推送，和每天 8:30 的早报。投递渠道问用户。
4. 运行 `hermes cron list`，把 Hermes **原有的**作业状态、Gmail、Moodle 相关任务列给用户看，**由用户决定**是否暂停。只有暂停了这些任务，“交了还说没交”的问题才能根治。

### 9. 开放给 A 机浏览器（只在 tailnet 内）
```powershell
tailscale serve --bg --https=443 http://127.0.0.1:8766
tailscale serve status
```
- `status` 里这个端口**不能**带 Funnel 标记。只用 serve，不要用 funnel。
- 让用户在 A 机打开 `https://<B机名>.<tailnet>.ts.net/board/` 并登录。页面状态栏应显示“远程 · tailnet”，能看到全部事项和邮件，能标记完成。
- 如果登录提示 “Cross-origin login denied”：记下请求里的 Host 和 X-Forwarded-Host，报告给用户，**不要关闭这项检查**。

### 10. 最终验收（用实际结果报告）
- [ ] `--diagnose` 输出 ok
- [ ] health：`worker_online` 为 true；来源的成功时间是最近的；授权状态为 ok
- [ ] Hermes 调 `health` 成功；预检脚本输出合法 JSON；两个 cron 任务已创建
- [ ] 在 Moodle 交一份作业（或用户指定一条已提交的作业），2 分钟内 A 机页面和 Hermes 都显示“已提交”
- [ ] A 机浏览器：完整模式能看能操作；从公网（Funnel）访问不到完整模式
- [ ] A 机旧版已退出，并且没有自启

## 回退
停止 B 机上的任务计划和 Academic OS，执行 `tailscale serve --https=443 off`，然后在 A 机重新运行旧目录。B 机的数据目录保留不删。
