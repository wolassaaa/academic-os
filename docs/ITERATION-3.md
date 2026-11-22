# 迭代 3：标签、含正文邮件简报、Hermes 与轻量化

基线 fb99e6df43729d54db72bb99ce4284e00bd35c88（PR8），分支 iteration/tags-digest-hermes。用户 2026-10-14 授权按需求自主取舍，不为非关键偏好反复询问。

Ruling: 复用隔离 Source 和独立分支，PR 叠加在 PR8 上，不自动合并未批准 PR — 保持两轮改动可分别审阅 — 成本：最终合并需先 PR8 后本轮。

Ruling: 保留 SQLite，不迁移到服务型数据库 — 单机低进程开销及既有事务／备份更符合轻量目标 — 成本：未来多机同时写需要重新设计。

Ruling: 标签按类型＋scope＋名称区分，不猜测课程、学期或小组 — 避免同名误归类 — 成本：用户需要首次标记真实归属。

Ruling: 邮件简报使用明确标注的原文摘录，默认仅本地，不启用模型或自动向外发送正文 — 可追溯、低成本且不改变已确定执行模式 — 成本：不是语义总结。

Ruling: Hermes 通过 MCP 管理同一 API，不嵌入完整 Hermes 依赖到便携包；本机初检没有 hermes 命令或 ~/.hermes 目录；深入检查 AppData/Local/hermes 仅见 uv 工具，不存在 Hermes EXE 或 profile — 避免为可选集成增加常驻内存 — 成本：模型端实际运行需安装并授权 Hermes。

状态：标签、正文摘录、增量 feed、MCP 与轻量化已实现；回归和交付进行中。

- 标签／简报模块缺失 RED→GREEN；作用域、多标签、重命名、归档、同步保留、版本、幂等、备份和权限已验证。
- 真实浏览器 RED→GREEN：标签创建、选择课程归属、同时多选、组合筛选、重命名、归档、重启；手机简报、截断、原文和纯文本。
- 官方 MCP 实际 stdio 子进程 + 业务 HTTP 端到端通过，默认仍不暴露确认工具。
- 最终本地全量 226 passed，四个真实浏览器回归通过。性能与后续自评见 PERFORMANCE.md，接入步骤和可复用提示词见 HERMES.md。

Ruling: 自动内容简报仅写本地工作台；Hermes 安装及模型授权尚未检测到，交付实际路径配置和已验证工具链，不启动新的外部模型或调度 — 保持轻量及现有授权边界 — 成本：Hermes 主动反馈需其运行环境完成接入。

Ruling: 为降低查询时延与缓存，接受合成库增加 1.17 MiB 索引／feed 存储 — 查询中位数从 44.277 降至 4.350 ms，基准 private memory 20.19 降至 18.77 MiB — 成本：磁盘略增，完整 GUI 常驻内存尚非此基准。


## 最终复核

- Final: fixed 标签递增事项版本使已批准日历任务失效 — test_tag_assignment_preserves_already_approved_calendar_work 在 queued/failed/running 三种状态 RED→GREEN；只承接当前版本已批准的待执行日历任务，未排程标签操作不新增外部效果。
- Final: 原评 Minor 的“简报丢失关键词筛选”按用户所见重评为 Important（会显示无关邮件），test_digest_retains_the_active_text_search RED→GREEN，UI/API/MCP 贯通 q。
- Final: 原评 Minor 的“每日简报超出十封没有总数提示”按漏读风险重评为 Important，test_automatic_mail_content_digest_is_local_and_daily_deduplicated RED→GREEN，正文明确最近 10 / 共 N 封。
- Final: Critical 0；3 项重评 Important 均修复；deferred minors 0。
- Final: Ruling: 审阅者未代替实际构建、CI、模型部署、真实提供方与基准运行；主执行者分别保存测试输出，未部署部分明确记录 — 防止跨环境推断成功 — 成本：Hermes 模型端、第二台 Windows 和真实提供方仍需其环境验收。

## 使用

1. 高级核对工具 → 标签管理。类型选课程时，“所属范围”填写学期；作业或小组可从“所属课程”选择已创建课程。名称＋类型＋范围独立，不猜同名归属。
2. 作业与截止 → 标签。多选保存；组合筛选同时包含所选标签。重命名立即生效；归档保留原关联，不能新分配已归档标签。采集更新不覆盖标签。
3. 邮件收件箱 → 生成内容简报。沿用关键词、账号、核对状态与日期筛选；包含正文摘录、截断标记和原文入口，不虚构截止。
4. Hermes 接入见 HERMES.md。已验证工具链，不代表 Hermes 模型已安装在线；feed 阅读不确认消费，成功反馈后才 ACK。
5. 轻量化实测及后续自评见 PERFORMANCE.md；保留 SQLite，无新数据库服务。新扩展迁移前有独立 SQLite 备份。

## 数据与接口

- GET/POST /tags，PATCH /tags/{id}（名称／归档，expected_version）；PUT /items/{id}/tags（tag_ids、expected_version、Idempotency-Key）。read/draft 权限，浏览器 CSRF，远端不开放。
- GET /items?tag=ID&tag=ID 支持同时包含筛选，默认方式兼容。
- GET /mail/digest（分页／正文摘录／关键词与现有筛选）；GET /mail/feed（consumer、after、limit），POST /mail/feed/ack（consumer、cursor、expected_cursor，sync 权限）。
- 全部更新走同一 SQLite 事务／审计。标签变更不确认候选、不改来源截止、不凭空生成安排或对外发送。

## 交付边界

PR8 已通过 Windows Python 3.11 / 3.14 CI；本轮 PR 叠加在 PR8，不自动合并。原始交接和现用运行目录保留不变。真实交接数据库的独立升级副本已验证：720 封邮件、52 事项、272 提醒、55 审计与来源记录计数及业务版本／证据保持，52 个未核对邮件事项正确进入候选，原数据库 SHA256 不变。

Final: fixed 新扩展升级前备份 — test_schema_upgrade_takes_a_pre_extension_database_backup RED→GREEN；完整回归 226/226 通过。便携构建、CI 与源代码回滚证据随交付 artifacts/iteration-3 保存。
