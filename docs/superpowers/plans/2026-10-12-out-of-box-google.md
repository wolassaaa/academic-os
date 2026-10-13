# 开箱即用与 Google 长期授权修复

Goal: Windows 解压即可登录，无需 Python、代理或 OAuth JSON 配置。沿用现有项目，个人授权不随包分发。

1. OAuth 首次授权请求 consent；重连默认不强制 consent。仅在身份和客户端匹配时保留旧刷新令牌；撤销授权可显式修复。
2. Google 网络 auto/manual/direct，auto 检测系统代理和直连并缓存健康线路，连接失败重选。禁止对可能已接受的写入盲目重试。
3. 桌面启动自动选空闲端口，同一数据目录重复打开验证已有后台后打开工作台；CLI/服务保留固定端口行为。
4. 发布包内置应用级桌面 OAuth 配置，隐藏普通用户技术配置；显示中文授权结果并轮询本地管理员授权状态。
5. 准备应用验证材料并核查实际 Cloud 发布状态；实际人工授权、Google 审核和超过七天的持续运行不可用单元测试替代。
6. 新构建包完成隔离环境、无 Python、授权和恢复行为验证，保留用户现有数据。

## 执行记录

- 使用 executing-plans 与 test-driven-development；当前 codex/academic-os-2 分支尚无初始提交，沿用当前完整实现，不移动或覆盖用户文件。
- Cloud 浏览器已到管理员登录页，已请求用户亲自登录。尚未读取项目状态，也未发布或提交验证。
- Ruling: 重连复用 refresh token 前校验授权码返回的访问令牌身份；避免账号选错时将旧账号身份误认为新登录身份。
- Ruling: 自动端口仅用于桌面启动，服务默认固定端口，避免静默改变现有 HTTPS 转发目标。
- 本地完成：OAuth 身份安全复用、自动网络、端口 fallback/已有实例验证、中文回调/owner 轮询、应用客户端随包、MCP 实际端口发现。
- 验证：134 tests passed；Ruff passed；自动选择系统代理连接 Google HTTP 200；冻结版隔离目录/仅 Windows PATH：14 checks、MCP stdio、学校回调均通过。独立复核及 MCP 增量复核无 P1/P2。
- Cloud 核查：项目 authentic-light-326306 / 924402528361 实际为 External Testing；发布按钮禁用，要求完成品牌配置。浏览器交互连续超时，尚未发布，不能声称七天失效或未验证警告已解决。
