# 对话内按需配置 · 多设置接入 v5

本版接续[交互设计 v4](conversational-settings.v4.md)的模型流程，并沿用[交互设计 v1](conversational-settings.v1.md)第 6 节与[开发文档 v1](conversational-settings-development.v1.md)第 12 节的能力范围。用户决定继续接入其他设置；实施方式是共用确认与应用机制，按设置类型逐项验证，而不是把任意设置字段一次性暴露给 Agent。

## 接入顺序

1. `connector.enable`：仅限已安装、配置完整且可用的 MCP 连接器。Agent 只能提议，用户在当前会话卡片中确认后，后端在同一提交中保存配置与决策；当前运行沿用旧快照，后续运行使用新配置。
2. `model.repair_credential`：专用凭据输入、验证与独立连接，避免修改共享凭据影响其他会话。
3. 新增 HTTP/SSE MCP 连接、OAuth，以及代理、超时等全局设置：各自增加输入校验、目标预览和生效范围说明。
4. 任意 REST API、STDIO 启动、存储根目录与部署端口仍需单独设计，不将路径或 URL 直接解释为可执行配置。

每项以实际后端配置生效和真实任务可验证为准。首个连接器切片允许用户确认后在输入框继续任务；自动接续与跨运行幂等仍按[开发文档 v1 第 9 节](conversational-settings-development.v1.md)实现，未完成前界面必须如实说明，不得宣称已自动接续。Agent 工具不得携带密钥或自行调用确认端点。
