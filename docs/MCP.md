# Agent API 与 MCP

Agent 仅使用 `http://127.0.0.1:8765/api/v1` 的同一业务 API，不接触 SQLite、Moodle 或第三方凭据。需要 Bearer token；默认 token scopes 是 `read,draft,sync`，确认和通知权限须单独显式授予。维护命令在运行时被独占锁拒绝，先停机签发后重启。

```powershell
.venv/Scripts/python.exe -m academic_os.cli --config config.json token --name local-agent --store-ref academic-os-agent
.venv/Scripts/python.exe -m academic_os.mcp --token-ref academic-os-agent
```

MCP 使用官方 Python SDK `FastMCP` 和 stdio transport，标准输出只写 JSON-RPC，日志走 stderr。MCP 是 HTTP 代理，不会启动另一个 Worker。客户端配置示例（绝对路径换成自己的）：

```json
{
  "mcpServers": {
    "academic-os": {
      "command": "C:/AcademicOS/.venv/Scripts/python.exe",
      "args": ["-m", "academic_os.mcp", "--token-ref", "academic-os-agent"],
      "env": {"ACADEMIC_OS_URL": "http://127.0.0.1:8765"}
    }
  }
}
```

也可在进程环境设置 `ACADEMIC_OS_TOKEN`；不要把 token 明文写入共享客户端配置。`ACADEMIC_OS_TOKEN_REF` 指向当前 Windows 用户的 Credential Manager。URL 只接受 `http://127.0.0.1:PORT`，拒绝公网目标、重定向、URL 用户凭据及路径前缀；HTTP 客户端不读取系统代理环境变量。

| 工具 | 业务 API | scope |
| --- | --- | --- |
| health | GET /health | read |
| list_items / list_mail / list_calendars | GET /items、/mail、/calendars | read |
| get_mail | GET /mail/{account-qualified key} | read |
| get_item / list_artifacts / list_notifications | GET /items/{id}、/artifacts、/notifications | read |
| calendar_events / model_spec | GET /calendar-events、/model/spec | read |
| list_jobs / list_drafts / list_conflicts / summary | GET 对应资源 | read |
| create_item | POST /items | draft |
| create_draft | POST /drafts | draft |
| generate_plan | POST /plans/generate | draft |
| sync | POST /sync | sync |
| confirm_draft（默认不存在） | POST /drafts/{id}/confirm | confirm |

要暴露确认工具，必须同时在 token 中显式授予 `confirm`，并以 `--allow-confirm` 启动 MCP。授予 scope 本身不会打开工具，打开工具本身也不会绕过 API 权限。每次确认仍须对应 owner 明确批准的草稿。

停机后使用 `token-revoke --name local-agent` 撤销该名称下所有 token，再用 `token --name local-agent --store-ref academic-os-agent` 重新签发；存储在 Credential Manager 的旧值不再被 API 接受。

排程草稿的每项包含 `item_id`、`expected_version`、带时区 ISO8601 `start` 和 `end`。HTTP 写操作传 `Idempotency-Key`；MCP 写工具要求 `idempotency_key`。同一键绑定相同业务内容，重放返回原结果；不同内容同键返回冲突。401 表示缺失/失效 token，403 表示 scope 不足，409 表示版本/时间冲突，422 表示输入不合法。

HTTP 也提供 `/artifacts`、`/calendar-events`、`/notifications`、`/audit`、`/model/spec` 等只读资源；owner 配置接口不允许 Agent。模型协议仅保留，模型调用不会发生。测试包含官方 MCP 客户端与真实子进程 stdio 握手及工具调用。

邮件列表默认仅取最近 100 封的元数据和候选日期，原文按稳定 `key` 单独读取；`limit`（1–300）、`offset` 和 `q` 支持分页、正文搜索。MCP 对应 `list_mail(limit,offset,query)` 与 `get_mail(mail_key)`。不会每 30 秒重传全部邮件正文。

HTTP 的事项 PATCH 使用 `expected_version` 防止覆盖并发修改；修改来源截止、关闭事项或改已排程标题需要 `confirm` 权限。默认 Agent 不能通过更改标题间接写日历，未解决冲突也不能用修改 `review_state` 绕过核对。
