# Google 长期授权与应用验证

## 已确定的程序行为

- 新账号使用离线授权与 PKCE；正常同步通过 refresh token 自动更新 access token，不弹浏览器。
- 普通重连不强制 consent。Google 不返回新 refresh token 时，只为客户端匹配、且授权码访问令牌身份与原账号一致的重连保留旧 refresh token。
- “修复授权”显式请求 consent，适用于撤销授权、权限不足或无有效离线令牌。网络失败不等于授权失效，不删除凭证。
- Windows 凭据管理器保存个人令牌。发布包只包含应用桌面客户端标识与 Google 所要求的桌面客户端参数，不包含用户 refresh/access token。
- 应用客户端由构建脚本从明确的 `google-oauth-client` 条目读取。构建前缺少客户端则失败，不能把首次配置转交普通用户。

## Google Cloud 发布检查

现有客户端对应项目编号：924402528361，项目 ID `authentic-light-326306`，显示名 My First Project。2026-10-12 已在 Cloud 控制台实际核查 Audience：External / Testing，2 个测试用户；“发布应用”按钮禁用，明确要求先完成 Branding 配置。尚未修改或完成发布，尚未核查验证状态。

进一步实际核查：Branding 当前应用名 Hermes，已有徽标、支持邮箱和开发者联系邮箱，首页/隐私政策/条款网址为空；Verification Center 显示测试状态无需验证，未进入审核。已准备 APPLICATION-HOMEPAGE-DRAFT.md 和 PRIVACY-POLICY-DRAFT.md，需确认真实公开托管地址后补齐 Cloud 资料，不填无关或虚构网址。

1. 在项目选择器中按项目编号确认项目，在 Clients 核对桌面客户端，确认 Gmail API、Calendar API 可用。
2. Audience 若为 Testing，改为 In production。该操作是 OAuth 应用发布，不是部署一个公开邮件服务器。涉及 Google 确认条款时由账号所有者完成。
3. 完成发布后重新授权。旧的测试期 token 不能承诺自动延长。
4. 如需去掉未经验证警告，按 Verification Center 要求完成品牌与数据权限审核。个人自用例外不等于已验证，也不自动隐藏警告。
5. 发布与验证成功均记录控制台证据和时间；没有实际证据不能标记通过。

官方规则：Testing 的非基础身份权限授权通常七天到期；生产状态仍可能显示未验证警告。撤销授权、密码变更等因素仍可能使授权失效，不能承诺永久有效。

- https://developers.google.com/identity/protocols/oauth2
- https://support.google.com/cloud/answer/15549945?hl=en
- https://developers.google.com/identity/protocols/oauth2/native-app

## 验证提交材料草案（发布方维护，用户无需配置）

应用用途：个人学业管理。读取本人学校邮件以展示作业、考试、截止和邮件依据；读取本人选择的日历以避免冲突；用户确认执行计划后写入系统管理的日程；仅向用户配置的收件人发送提醒。模型接口保留，当前版本不启用模型分析。

| 权限 | 当前用途 | 审核演示 |
|---|---|---|
| gmail.readonly | 读取邮件及原文依据 | 登录、同步邮件、展示邮件、断开连接 |
| gmail.send | 配置好的邮件通知渠道 | 显式配置收件人、预览提醒、投递记录 |
| calendar.calendarlist.readonly | 列出账号日历供本人选择 | 日历列表与范围选择 |
| calendar.events | 读取占用、确认后写入及同步修改 | 计划预览、确认、Google 日历结果、删除意图 |

当前保留这些已实现功能的权限，不声称删掉权限即可解决测试模式七天过期。若未来取消邮件通知功能，再删除 gmail.send 并兼容现有授权。

所需发布资料：应用名称与支持邮箱、开发者联系邮箱、可验证的应用网站/隐私政策（若控制台要求）、数据使用和删除说明、实际功能演示视频。不得凭空填写域名、审核视频或宣称已有认证。个人桌面应用是否需要安全评估以 Google 对实际数据流的审核要求为准。

数据说明：个人令牌位于本机 Windows 凭据管理器；邮件、作业和日历映射位于本机 SQLite。默认后台只监听 loopback；远程看板独立登录且只读。没有模型数据发送。用户可断开连接停止后续同步；断开连接不自动删除历史事项、不等同于 Google 撤销授权。清理历史数据与 Google 账号第三方应用撤销入口须明确提供操作说明。

## 验收记录

- 本地自动化测试覆盖：重连不强制 consent、令牌保留与身份不匹配拒绝、网络恢复、端口冲突、密码重置、只读权限。
- Cloud 发布状态：已确认 Testing；发布按钮因品牌配置不完整被禁用，待补齐并实际发布。
- 应用验证：待 Google 审核，不能声称未验证警告已消失。
- 持续超过七天：须在生产状态下重新授权后实际运行并记录，不以模拟时钟代替。
