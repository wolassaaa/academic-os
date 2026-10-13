# 验收记录与上线边界

日期：2026-10-12。当前交付为可本机运行的 shadow 版本，模型分析固定关闭。自动化使用隔离数据库与提供方替身；浏览器检查使用真实 Chrome 和临时 HTTP 服务。

| 场景 | 已验证的结果 |
|---|---|
| 跨来源去重、多日期邮件 | 来源证据保留；同课程/标题关联；不同日期节点分别保留；冲突进入待核对；未给出明确时间不捏造截止时刻 |
| 延期、取消、已交作业 | 截止版本变化使旧待发提醒失效；明确 submitted/finished 才视为已交；草稿及 abandoned 不误判完成 |
| 计划与并发修改 | 原始截止和执行时段独立；已占用时段、过期版本、过截止、过期日历快照阻止确认；无法安排的事项显示原因 |
| Agent 重复调用 | 草稿、确认及触发任务复用幂等键；不同权限分别控制准备和执行；MCP 只调用统一 HTTP 接口 |
| Google/Apple 改期回传 | Google 事件替身的改期、取消和删除意图读回；并发修改保留冲突；Apple 通过同一 Google 账号使用 |
| 飞书镜像 | 增改取消、重复实例、移动例外及旧映射接管测试通过；不读取飞书编辑作为来源事实 |
| 时区与重复事件 | 全天日期、命名时区、夏令时长度及窗口回看、RRULE、单次取消和移入窗口的例外均有回归测试 |
| 投递失败与恢复 | 渠道回执前不记成功；持久化重试；租约恢复；确认/稍后提醒；断网后已过期任务仍可补提醒 |
| 单后台与健康 | 同目录 OS 锁及 Worker 租约；部分失败不报全绿；独立提醒线程不等待慢采集；来源过期明确显示 |
| 登录与只读权限 | 密码/会话、CSRF、限流、Agent scopes、远程端拒绝管理 API；邮件原文按纯文本呈现 |
| 迁移 | 备份校验、路径穿越拒绝、凭据排除、配置/路径重建、恢复后 shadow 与 token 失效；本机读取旧 16 份快照、生成 35 个基线事项 |

运行验证：

```powershell
.venv/Scripts/python.exe -m pytest -q
.venv/Scripts/python.exe -m ruff check src tests
.venv/Scripts/python.exe tests/browser_check.py
.venv/Scripts/python.exe -m pip wheel --no-deps --no-build-isolation --wheel-dir dist .
```

本次最终自动化结果：106 项通过。依赖 Starlette 的测试客户端会输出一条 AnyIO 弃用提示，不影响结果。真实 Chrome 检查包含桌面和手机布局、首次初始化、邮件详情、手动事项、草稿确认、提醒操作、远程只读与异常状态。打包后另在独立虚拟环境安装 wheel，检查包内静态资源、模型契约及 CLI。

以下仍需目标环境实际验收，不能由上述测试代替：

- 双 Gmail、HKBU Moodle、Google/飞书应用权限与真实联网结果；Google/Apple 实际设备编辑、飞书更新及消息/邮件回执。
- Windows SCM 安装、重启开机自启、异常恢复和服务账户访问 Credential Manager。
- Tailscale Funnel 的 HTTPS、公网手机访问及远程登录。只代理 8766。
- 第二台物理 Windows 的完整接管；旧节点确认停止写入后，新节点才进入 live。

本次未调用模型、未切换 live、未更改 Hermes cron、未注册服务或发布 Funnel。操作顺序见 [运行与迁移手册](OPERATIONS.md)。

免安装版补充验证：PyInstaller Windows x64 包在独立中文目录、移除系统 Python PATH/PYTHONHOME/venv 后完成 11 项内置运行诊断；冻结 EXE 的 MCP stdio 握手通过。启动器打开、图形化密码重置、安全停止及构建时拒绝覆盖用户数据有回归测试。实际第二台物理 Windows 验证仍待完成。
