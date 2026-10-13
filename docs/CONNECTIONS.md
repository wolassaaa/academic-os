# 账号与同步后台

本地入口默认打开“账号与同步”。这里只配置和维护：Google 登录、Moodle 登录、日历范围、网络与运行记录。日常查看安排可以继续使用 Google / Apple 日历。模型分析仍关闭。

## Google

1. 点击“登录 Google 账号”，在系统浏览器完成登录与授权，工作台自动更新连接状态。应用级桌面客户端随发布包提供，网络默认自动检测系统代理和直连，无需上传 JSON 或填写代理。
2. 点击账号的“检查连接 / 选择日历”。后台实际读取 Gmail profile 和日历列表，选择要读取的日历和唯一一个可写日历。需要切换账号时先取消另一个账号的写入选择。
3. 正常同步自动刷新访问令牌。普通重连不强制确认权限；撤销授权、缺少权限或无有效离线令牌时点击“修复授权”。网络失败不删除账号或令牌。

发布方负责应用 API、桌面客户端、发布状态与验证材料，参见 [长期授权与发布检查](GOOGLE-AUTH-RELEASE.md)。测试模式七天失效与未经验证警告必须分别处理，不能靠安装包规避。高级维护入口保留网络 auto/manual/direct 与更换客户端功能。

支持多个账号、重新登录、检查连接与断开。离线 refresh token 和客户端密钥存入 Windows 凭据库，页面/API/配置/导出不返回密钥。断开停止后续同步但保留历史记录，不等于撤销 Google 服务器授权；彻底撤销请到 Google 账号的第三方应用权限页面。

授权使用系统浏览器、loopback 回调和 PKCE。授权流程有效期 10 分钟，绑定发起授权的本地后台会话，单次使用；后台重启、退出登录、重置密码后需重新发起。Google 测试模式的离线授权可能按 Google 政策过期，需要重新登录。

## HKBU Moodle

学校网址为 `https://buelearning.hkbu.edu.hk`。点击“学校网页登录”，在系统浏览器完成学校 SSO/MFA，允许浏览器打开 Academic OS。此操作为当前 Windows 用户注册 `academicos://` 回调，不需要管理员权限。

使用 Moodle 官方移动应用登录协议，回调校验一次性 passport，然后实际请求 `core_webservice_get_site_info` 与课程列表；验证成功才启用。学校若禁用移动服务、强制自己的客户端 scheme 或限制登录服务，网页登录不能绕过这些限制。可使用“检查并接入本机已有授权”，或展开高级入口验证并保存学校支持的移动服务 token。学校密码不会保存。

Moodle token 保存在 Windows 凭据库，采集通过独立只读进程读取课程、作业、截止和提交状态。移动网页登录的临时回调包含 token，由 Windows URI 协议交给启动器后直接发送到本地后台，程序不记录该 URI。不要复制或分享回调链接。

## 本机已有授权与运行方式

“检查并接入本机已有授权”只检查旧安装固定目录 `D:\AgentNode\Core\Hermes\google_token*.json` 和旧 Moodle 凭据库；实际联网验证成功才接入，不修改旧系统。每台新电脑可直接重新登录，无需迁移这些旧文件。凭据库不会打包进便携 ZIP。

保存连接或日历范围后自动排队采集和同步，无需重启。进行中的只读请求可能先完成，断开后不再发起该账号的新一轮同步。后台默认仍为影子模式：真实读取，外部日历写入只预览。转入 live 仍按现有切换流程停用旧写入任务并备份，避免两套程序同时写入。此页面不自动停用旧任务或开启通知。

远程端维持只读，账号管理只在主机本地后台提供；Agent 凭证没有账号管理权限。

参考：[Google 桌面 OAuth](https://developers.google.com/identity/protocols/oauth2/native-app)、[Google 日历列表](https://developers.google.com/workspace/calendar/api/v3/reference/calendarList/list)、[Moodle 官方移动登录实现](https://github.com/moodle/moodle/blob/MOODLE_405_STABLE/admin/tool/mobile/launch.php)。协议独立实现，没有复制 Moodle GPL 源代码。
# 学校登录网络修复（2026-10-12）

HKBU Moodle 登录由独立 Edge/Chrome 窗口直连完成，不继承系统浏览器代理。Google 登录仍使用原有浏览器和自动网络策略。学校 SSO、密码与多因素验证由本人完成；窗口不会跳过证书验证或浏览器安全检查。

独立学校浏览器配置保存在 `%LOCALAPPDATA%/AcademicOS/SchoolBrowser`，可能保留学校登录 Cookie；不复制或导入原有浏览器账号，不随发布包分发，也不随应用数据目录备份迁移。新电脑需重新登录。断开应用内学校连接不会同时清除该浏览器配置的 Cookie。

