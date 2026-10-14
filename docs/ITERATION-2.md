# 迭代 2：邮件候选分流与逐条确认

计划：docs/superpowers/plans/2026-10-13-mail-triage.md。基线：82677009b08c87747fee00ee78b671dfbd84c6c6（PR7 已核验 CI 后合并）。

## 进度与决策

- 合并及分支：完成；iteration/mail-triage，原始交接与现用运行版不修改。
- 数据与核对：完成；16 项初始 RED→GREEN，新增历史提醒操作、手动来源、并发与离线诊断回归。
- 提醒、统计、排程：完成；未核对／忽略候选不进入正式统计和截止提醒，新邮件通知保持原机制。
- 页面、接口、浏览器：完成；3 个真实 Chromium 回归通过，包含手机端候选冲突核对。
- 复核：完成，2 项 Important 均复现 RED→GREEN；本地全量 204 passed。Windows CI、PR 与独立便携构建交付另附验证记录。

Ruling: 复用独立 Source 副本和专用功能分支，不另建工作树；它与 Runtime 数据和不可变交接副本隔离。

Ruling: mail_review 和 mail_keys 采用现有事项 JSON 的附加字段，不增加业务表；旧备份恢复继续兼容。业务版本在旧数据补分类时不改变，逐条人工操作才递增。

Ruling: 仅纯 Gmail 来源且未核对、未关闭、未安排的事项进入候选；混合来源保持正式。忽略不取消已确认待办。

Ruling: 旧邮件关联只按账号＋原邮件 ID 的精确来源前缀回填；匹配不确定则留空，不按标题猜测。


## 整分支复核与修复

Final: fixed 手动事项合并 Gmail 证据后误入候选 — test_manual_item_stays_formal_after_mail_source_merge_and_restart RED→GREEN，保留 manual_origin（新手动事项及旧 item.create 审计回填）。

Final: fixed 待核对候选有来源冲突时页面不能处理 — triage_browser_check.py 在手机视口重现 disabled 按钮 RED→GREEN；仅按冲突 ID 额外读取事项，不混入正式列表。

Final: 204/204 回归通过；Critical 0，Important 2 均已修复；deferred minors 0。

Final: Ruling: 发布构建、Windows CI 由主执行者完成，审阅者未代跑浏览器；交付前分别保存真实输出 — 防止将代码审阅等同于运行验证 — 成本：增加独立构建和验证时间。

## 使用与接口

邮件收件箱 → 全部／待核对／已忽略 → 查看原文与日期；每个候选分别核对标题、截止（未知留空）、预计用时、优先级，再确认或忽略。忽略可恢复；正式邮件待办可以反向打开原文。来源日期变化在运行记录处理冲突，之后返回邮件继续核对。未修改的截止输入保留原始秒数及时区值。

- GET /api/v1/items?view=all|formal|mail_pending|mail_ignored（默认 all 保持兼容）。
- GET /api/v1/mail/search?review=all|pending|ignored（默认 all）；邮件详情包含 linked_items。
- POST /api/v1/items/{id}/mail-review：action=confirm|ignore|restore，expected_version，changes，Idempotency-Key；confirm scope、CSRF 与远程只读边界均有回归。
- metadata 迁移前生成 SQLite 备份；不改变业务版本、历史 ID、截止修订与日历映射。

## 用户追加需求

2026-10-14：下一轮继续课程／作业／小组多标签、含邮件正文的简报、Hermes MCP 集成与有测量依据的轻量化；不把它们悄悄塞进本轮已经复核的实现。
