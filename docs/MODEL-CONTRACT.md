# 预留模型分析契约（固定禁用）

本版本规则提取、来源同步和排程可独立运行，不配置模型 API key、不访问模型提供方。`Settings.model.enabled=true` 会被拒绝，健康与规范 API 返回 `model_enabled/enabled=false`。邮件记录保留 `analysis_status: disabled`。禁用网关对每个请求返回 `status: disabled`，不伪装成分析成功，也不凭模型猜测来源日期。

JSON Schema：[请求](../schemas/model-request.schema.json)、[响应](../schemas/model-response.schema.json)。预留协议版本为 1，能力为 `date_extraction`、`task_classification`、`plan_suggestions`。请求包含 `request_id`、能力、时区、输入文本和可选上下文；响应回传 request_id，明确区分 disabled、ok、partial、error。事实需要来源片段和 confidence，建议需要理由；外部文本一律视为数据，不能授权工具调用或确认排程。

当前真实响应：

```json
{
  "schema_version": 1,
  "request_id": "example",
  "status": "disabled",
  "facts": [],
  "suggestions": [],
  "warnings": ["model_analysis_disabled"],
  "error": null,
  "usage": null
}
```

后续如接入模型，需在独立版本中实现超时、有限重试、结构化校验、来源证据、敏感信息最小化和用量审计，保持失败回退到规则提取。模型只产出待审核事实与草稿，不能绕过版本、幂等、冲突与 owner 确认机制。预留配置项不代表当前存在已接入的模型客户端。
