# 对话内配置：连接器接入切片 v5

本切片依据[多设置接入设计 v5](conversational-settings.v5.md)与[开发文档 v1](conversational-settings-development.v1.md)的 `connector.enable`，保留[模型切片 v4](conversational-settings-integration.v4.md)。

主 Agent 在当前运行发现需要可用但未启用的 MCP 连接器时，可以调用 `propose_connector_enable`。工具只登记待确认提案，不修改配置；仅主 Agent 获得此工具。提案持久保存在 `catalog.sqlite`，刷新后通过会话配置请求接口恢复。卡片展示目标连接器、Agent 提议原因、会话范围、当前运行与后续运行的生效差异。

用户确认时，后端检查会话、运行状态、连接器仍可用、MCP 仍启用、有效连接器集合仍与预览时相同；再一次提交会话覆盖值与已应用状态。重复确认返回原结果，拒绝不修改配置。发生并发变化则标记 stale，不覆盖新设置。连接器实际调用仍受既有权限机制约束。

当前切片的任务接续由用户在输入框发起，未实现[开发文档 v1 第 9 节](conversational-settings-development.v1.md)的自动跨运行接续、通用 `yield` 和 outbox。模型凭据修复、新连接与其他全局设置也尚未接入；因此不能把本切片称为完整首版。
