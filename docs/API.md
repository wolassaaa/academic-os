# HTTP 业务接口 v1

本地地址 `http://127.0.0.1:8765`。以登录会话或 Agent Bearer token 鉴权后使用 `/api/v1`；完整机器可读规格由 `/openapi.json` 提供，本地 `/docs` 可交互检查。远程入口不提供 OpenAPI、邮件正文或以下修改资源。

桌面启动端口冲突时自动选择空闲端口并写回配置，启动窗口显示实际地址。MCP 默认读取同目录 config.json 的实际端口，也支持 --config、--base-url 或 ACADEMIC_OS_URL 覆盖。CLI/服务保持固定配置端口。

账号管理仅限本机 owner，会话写请求需要 CSRF。GET /connections/flows 返回当前会话的授权进度（waiting、processing、done、failed、expired）；authorize 返回 flow_id，UI 自动轮询。Google authorize 可传 account_id 重连，force_consent=true 显式修复授权。POST /connections/proxy 接受 mode=auto/manual/direct 与可选 proxy；旧的仅 proxy 请求兼容。远程和 Agent 不开放这些接口。

| 资源 | 操作 | 约束 |
|---|---|---|
| session、login、logout | 登录状态、登录、注销 | Cookie 独立；写入带会话返回的 X-CSRF-Token |
| items、items/{id} | 列表、新增、详情、PATCH | 新增传 Idempotency-Key；PATCH 传 expected_version |
| mail、mail/{key} | 元数据列表、按需读原文 | limit 1–300，offset ≥0，q 支持正文搜索；key 是 account:id |
| plans/generate | 规则排程并生成草稿 | days 1–30；同键重试返回同一结果，不使用模型 |
| drafts、drafts/{id}/confirm | 创建、读取、确认 | 每项带 item_id、expected_version、start、end；确认再检查全部版本、截止和忙闲冲突，原子生效 |
| sync | 排队同步或导出 | kind 为 sources_sync、calendar_sync、export |
| jobs、calendars、calendar-events | 执行、链路和事件快照 | 实际结果、失败、重试、最后成功时间分别保留 |
| notifications、notifications/{id}/{action} | 记录、确认收到、稍后、完成 | action 为 acknowledge、snooze、complete；minutes 默认60，1–10080 |
| conflicts、conflicts/{id}/resolve | 核对与解决冲突 | choice 为 local 或 remote，必须带 expected_version |
| health、dashboard、artifacts、audit、summary | 健康、看板、档案、历史、Markdown | 不把后台心跳等同于每个来源成功 |
| model/spec | 模型协议概要 | enabled 固定 false；完整 JSON Schema 在 schemas 目录 |
| config | 读取当前配置 | 仅 owner；修改配置文件后重启 |

默认 Agent scope 为 read,draft,sync。确认计划、解决冲突、改来源截止、关闭事项、修改已经排程的标题需要 confirm。通知动作需要 notify，完成还需要 confirm。远程端只保留 session/login/logout/dashboard，不接受 Agent token。

日期使用带时区 ISO8601，内部保存 UTC；全天日历事件保存原始 date，结束日期为不包含的边界。未知日期不能填今天、2099 或假定 23:59。原始截止与执行时段分别保存。日期候选和原文证据不会因调整执行安排而消失。

新增、草稿、生成计划、确认与同步请求使用稳定 Idempotency-Key。不同请求内容不得复用同一键，返回 409。PATCH 以版本检查保证重试不会重复执行，旧版本返回 409，读取最新记录核对后再决定。401 为未登录，403 为权限/CSRF 错误，404 为不存在或未开放接口，422 为输入无效。

通知 delivered 表示渠道接收，acknowledged 表示用户确认收到，completed 表示事项关闭。同一截止提醒的多个渠道共享确认/稍后状态；之后新的截止梯度仍可继续提醒，直到事项完成。失败保留队列并退避重试。外部超时之后的重复邮件可能发生，Message-ID 保持稳定，但 Gmail 发送不提供本系统可用的严格去重承诺。

重复事件由 python-dateutil 处理 RRULE/RDATE/EXDATE；单次取消和改期读回。飞书按过去7天至未来90天展开为实例，稳定映射保持单次身份。源日历和执行事项分别保留，不将旧截止标记当成执行安排。历史飞书映射只在验证同一目标日历和来源身份后复用。
