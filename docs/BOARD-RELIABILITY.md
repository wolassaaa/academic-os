# v0.6 可靠性与轻量化实测

## 本轮变更

- 409 / 403 / 未决回执后，候选重绘不会让取消对话框后的键盘焦点掉到 BODY；回到该候选标题。普通取消仍回到原按钮。失败回焦验收：桌面1440；常规候选操作：桌面1440与手机390。
- 继续保留本地 GSAP 与 reduced-motion，不新增框架/外部字体/数据库依赖。

## 性能方法与实测

临时虚构数据，1000或10000封邮件、1000事项（900正式/100待核对）。直接 Core 调用，不代表网络往返、UI渲染、账号服务或EXE内存；首次调用不等于操作系统冷缓存。5次暖样本，最近秩p95等于最大样本，不作为稳定SLA。每条SQLite连接预算5秒，超限如实标注，未进行性能修复。

平台：Windows-11-10.0.26200-SP0；Python 3.14.3；SQLite 3.50.4。

|邮件数|操作|p50 ms|p95 ms|状态|
|---:|---|---:|---:|---|
|1000|digest_page_20|146.491|155.026|完成|
|1000|digest_search|161.352|168.632|完成|
|1000|digest_pending_filter|—|—|SQL budget exceeded|
|1000|detail|5.272|5.965|完成|
|1000|formal_items_900|37.586|47.818|完成|

1000封数据库：12124160 bytes；基准Python进程工作集（不含浏览器/账号服务）：{'working_set_bytes': 53673984, 'peak_working_set_bytes': 59285504}。
|10000|digest_page_20|140.278|147.944|完成|
|10000|digest_search|288.567|294.58|完成|
|10000|digest_pending_filter|—|—|SQL budget exceeded|
|10000|detail|5.453|6.471|完成|
|10000|formal_items_900|37.084|43.941|完成|

10000封数据库：99999744 bytes；基准Python进程工作集（不含浏览器/账号服务）：{'working_set_bytes': 52682752, 'peak_working_set_bytes': 59469824}。

全部看板 JS/CSS：19文件，原始 289185 bytes，独立 gzip 合计 91814 bytes；GSAP原始 72927 bytes。这是全部资产，不是单页首屏下载量。

### 已发现的瓶颈（尚未优化）

- 核对状态过滤使用 mail × items/json_each 相关关联扫描；大邮箱下出现 SQL 预算超限。不能用数据库更换掩盖查询形状。下轮先做可索引的邮件—事项关联与查询计划对比，保证迁移/多候选/混合来源一致性。
- 摘录与关联候选按页追加查询，正式事项列表仍全量加载；先减少重复关联扫描、按需读取及分页，再讨论新数据库。
- SQLite已有WAL、1MiB连接缓存与文件临时存储；保留当前方案。当前测量不足以声称便携程序内存已优化。
- 原始结果在交付包 Release/performance.json；验收复跑在 performance-verified.json；脚本 Source/scripts/benchmark-board.py。

## 个人完成与学校提交语义审计

虚构学校 not_started 事项：个人点击后 item.status=completed，source_records.submission_status仍not_started；相同来源刷新保留completed，来源标题变化并仍报告not_started时 item.status变pending。事实证据未被伪造成已提交，但共享状态会重新打开个人完成。

本轮只记录，不擅自改变来源权威语义。下一轮建议拆开 personal_completion 与 source_submission，定义重新打开提示/用户决定及迁移策略，再做测试。原始复现见 Release/semantics.json。

## P2/P3 后同类产品交互复核

- [Sunsama Archive](https://help.sunsama.com/docs/usage-guides/archive/)：归档与待办池分开，存在数量提示和回到任务列表的路径。本产品采用“候选忽略可恢复、原邮件保留、待核对/已忽略明确可查”，不自动归档大学DDL任务。
- [Todoist recurring completion](https://www.todoist.com/help/todoist/features/complete-a-task-with-a-recurring-date-dmI6SVqdP)：完成循环事项提供撤销。本产品只借鉴“动作可核查、状态变化可恢复”的原则；忽略/恢复已接入，正式完成撤销仍由现有事项操作处理，本轮没有新增循环任务。
- 对话框默认取消、失败保留草稿、回执丢失沿同键核实、焦点回到所属候选，避免用动效掩盖不确定结果。

## 下一步

P4 只做交付汇总。之后优先优化核对状态查询与分离个人完成/学校提交状态；再单独规划日历读写、子任务和Hermes，保持本轮边界。
