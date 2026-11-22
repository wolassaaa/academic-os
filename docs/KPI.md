# KPI 周报

v1.0 圣诞版的 KPI 定义和每周读数在 GitHub Issue #11。这份报告从本机数据里把能算的 KPI 算出来，**只读，不修改任何数据**。

## 怎么读

服务在运行时（B 机常驻），用 API 或 MCP：

- HTTP：`GET /api/v1/kpi?days=7`，需要 `read` 权限的令牌。
- MCP：`kpi_report(days=7)`。Hermes 可以每周日晚上调一次，把结果发给你。

服务停止时，也可以用命令行（命令行会占用实例锁，所以运行中不能用）：

```powershell
AcademicOS.exe --cli --config config.json kpi --markdown
AcademicOS.exe --cli --config config.json kpi --save   # 另存 data/exports/kpi-YYYY-Www.md
```

`--days` 可选 1–31，默认 7。

## 现在能算的

| KPI | 来源 |
|---|---|
| R3 推送延迟 p95 | `health.latency`：采集 → Hermes 确认。要得到“邮件到达 → 推送”，再加上 `collection_p95_seconds`。 |
| R4 邮件候选精确率 | 窗口内的 确认 ÷（确认 + 忽略）。 |
| R5 排程覆盖率 | 未来 7 天截止的未完成正式事项中，已排时段的比例；没排上的，用排程器空跑一次给出原因（`missing_estimate`、`insufficient_time`、`needs_review`、`fits_but_not_scheduled`）。 |
| H1 需重新授权 | 窗口内授权失效告警次数（每次故障算一次，不按通知渠道重复计）。 |
| H4 Hermes 心跳 | 窗口内 stale / stuck 告警次数和当前状态。 |
| H5 重复日历事件 | 同一事项在同一日历上有多个事件的数量。 |
| H6 过期来源 | 当前超过新鲜度阈值没有成功同步的来源。 |

## 暂时算不了的

报告里标为 `not_measured` 并写明原因，**不会当成 0 或达标**：

- R1 漏截止、R2 Moodle 覆盖率：等第 1 周的 Moodle 对账报告。
- R6 每日使用：还没有按天记录看板访问和早报阅读。
- R7 学习块完成率：等第 2 周排程 v2 单独记录学习块。
- H2 可用率、H3 同步成功率：心跳和来源状态只保存最新一次，没有历史。
