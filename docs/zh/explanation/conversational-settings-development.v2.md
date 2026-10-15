# 对话内按需配置 · 接续与自动应用开发规格 v2

本版增补[开发文档 v1](conversational-settings-development.v1.md)第 9 节，对应[产品设计 v7](conversational-settings.v7.md)。v1 的配置安全边界、outbox、冻结快照、Stop generation 和故障恢复要求继续有效。

## 服务端状态与顺序

连接器提案增加 `sourceRunId?`、`continuationState`、`continuationRunId?`、`autoApplyAt?` 和审计用的决策来源。明确配置指令使用 `user-request:*`，不创建 continuation。Agent 提案指向真实源 run。运行结束前检查关联待确认提案：正常收尾已完成的工具和消息，源 run 进入 `interrupted`/`configuration_wait`，不能展示为任务完成。配置提交和 continuation intent 在同一数据库提交；run 文件的创建使用预分配 ID 与持久 outbox 幂等恢复。

确认或授权倒计时到期的顺序：复核会话、连接器、基线和 Stop generation → 提交配置与 continuation intent → 确认源 run 已安全终止 → 以源 run 的冻结快照为基线加入获准连接器 → 幂等插入后续 run → 按现有队列调度。后续 run 不生成第二条用户原始提问；用可信的内部接续上下文传递原任务、完成事实和剩余工作。不能只重发原 prompt。外部副作用状态未知时转 `needs_user_action`。

## 自动应用调度

独立会话授权默认 false；变更需持久化并向用户明确提示。白名单由服务端维护，每个连接器先审查工具能力和出站数据，不接受模型传入的 `lowRisk=true`。提案创建时若授权及白名单同时满足，记录固定 `autoApplyAt=createdAt+30s`。服务端调度器在到期后重新校验授权及前置条件，再通过与人工确认相同的配置服务提交；重复唤醒只读已有 receipt。拒绝/取消先发生则自动任务失效。进程启动扫描未决请求以恢复计时。不得依赖浏览器计时器提交配置。

## 实施顺序

1. 先实现人工确认后的关联接续和刷新/重启幂等测试。
2. 再实现独立授权、白名单、30 秒倒计时及服务端恢复。
3. 最后覆盖长任务安全边界、Stop 竞态、外部副作用不重放、UI 接续状态和端到端旅程；未通过这些断言前不得宣称完整自动接续。
