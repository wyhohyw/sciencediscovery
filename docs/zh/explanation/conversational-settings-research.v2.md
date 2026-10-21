# 对话内配置的竞品机制与 openJiuwen 复用分析 v2

**调研结论：WorkBuddy 已公开说明“任务中引导连接、授权后继续当前任务”的产品体验；Codex 和 OpenCode 提供了可复用的交互请求机制；openJiuwen core 与 WorkSwarm 已有中断恢复、凭据表单和配置管理组件。ScienceDiscovery 的投入重点应是把这些能力接成受控的配置事务与任务生效链路。**

面向熟悉 ScienceDiscovery 的项目评审人员。调研日期为 2026-10-19，覆盖官方文档与公开源码；下文区分产品文档承诺、源码机制和接入建议。源码存在不代表已验证 ScienceDiscovery 的集成效果，开发分支也不等同于发布版本。

调研结论已落实到[完整设计 v15](https://github.com/wyhohyw/sciencediscovery/blob/f58c728b4395ed32f7fa1161636e87c9d6123f5d/docs/zh/explanation/conversational-settings.v15.md)和[完整开发文档 v5](https://github.com/wyhohyw/sciencediscovery/blob/f58c728b4395ed32f7fa1161636e87c9d6123f5d/docs/zh/explanation/conversational-settings-development.v5.md)：增加复用边界、外部提交和执行器桥接的待实现合同。源码调研基线保持 v1 的固定提交，本版未重新扩大调研或产品验证范围；既有权限、30 秒策略和执行器行为不变。

## 1. 能力对比与主要判断

比较对象是完整链路：发现配置缺口 → 展示具体变更或收集输入 → 用户决定 → 保存并生效 → 原任务继续。普通提问、工具审批和设置页入口分别只能覆盖其中一部分。

| 对象 | 已确认的相关能力 | 与本功能的关系 | 仍需区分的边界 |
|---|---|---|---|
| Codex | 结构化用户输入、MCP elicitation、作用域化审批、配置读写 RPC | 可借鉴请求协议、关联 ID 和处理完成通知 | 不能把配置 RPC 的存在等同于所有设置都有 Agent 发起的确认卡 |
| OpenCode | question 复用通用 Form；权限 allow/ask/deny；配置文件可由 Agent 修改 | 可借鉴通用交互层和工具等待答案后继续的机制 | 普通问答结果会回到模型；不宜直接作为密钥输入通道 |
| WorkBuddy | 任务中显示连接引导，授权后自动继续；Schema 描述凭据表单 | 本次最直接的产品体验参照 | 文档未说明其内部 run 标识、检查点和配置事务实现 |
| openJiuwen core | ConfirmInterruptRail、AskUserRail、InteractiveInput、中断状态与恢复处理 | 已有暂停与恢复基础，不必另造同类 Python 执行机制 | 不负责 ScienceDiscovery 的设置模型、Session 继承与领域变更事务 |
| WorkSwarm | 结构化问题、凭据 Schema、连接流程、配置保存和运行时重载 | 有现成模块可评估接入，部分能力在旧标签中已存在 | 连接续跑与研究 run 恢复是不同链路，需显式桥接 |

判断依据与版本见第 2～7 节。**评审时不应把“支持卡片”作为完成标准；真正要验证的是修改获准、生效范围正确、原任务实际继续。**

## 2. Codex 的交互协议与配置控制面

### 2.1 请求输入和请求授权分开表达

Codex App Server 将交互请求关联到 thread、turn 和 item。`item/tool/requestUserInput` 收集结构化答案；`mcpServer/elicitation/request` 支持 Schema 表单及 URL 引导；命令、文件变更和权限扩展有各自的审批请求。请求处理或清理后发送 `serverRequest/resolved`。这说明 UI 应随请求状态变化，而不应通过解析助手文字来判断是否等待确认。[官方 App Server 文档](https://learn.chatgpt.com/docs/app-server#approvals)

### 2.2 配置写入有独立接口

同一控制面提供 `config/read`、`config/value/write` 和 `config/batchWrite`；文档给出了应用开关和工具审批设置的示例，批量接口可原子应用多项编辑。它们与前述交互请求分别存在，需要产品将用户决定与配置操作关联起来。[配置 RPC 示例](https://learn.chatgpt.com/docs/app-server#config-rpc-examples-for-app-settings)

**对我们的启发：**公共请求层处理输入、决定与完成通知；配置服务负责校验和修改；执行器负责继续任务。配置变更不应依赖普通聊天文字作为批准记录，也不能从 RPC 返回成功推断当前任务已加载新配置。

### 2.3 自动处理的含义

用户输入请求可携带 `autoResolutionMs`，让宿主按超时自动处理；该字段没有定义“任意审批等待 30 秒就批准”的规则。我们的 30 秒机制仍应以独立授权、领域白名单和服务端到期复核为依据。[用户输入请求协议](https://learn.chatgpt.com/docs/app-server#toolrequestuserinput)

这些证据来自公开 App Server 协议，不构成对当前桌面客户端每个设置入口的逐项实测结论。

## 3. OpenCode 的通用 Form 与权限请求

### 3.1 question 是通用交互层的一个使用方

当前 `dev` 源码中，`question` 工具把问题转换为字段，调用 `Form.Service.ask` 等待答案，再把结果作为工具输出交回模型。Form 服务维护请求状态，校验答案，通过 Deferred 唤醒等待方，并处理重复提交和取消。这个分层说明：领域工具可以复用交互层，不需要每个工具自行写一套等待逻辑。[question 工具源码](https://github.com/anomalyco/opencode/blob/ba06ff9e0bf2dcefbbbc3a1d6094f3572d77c84e/packages/core/src/tool/plugin/question.ts)、[Form 服务源码](https://github.com/anomalyco/opencode/blob/ba06ff9e0bf2dcefbbbc3a1d6094f3572d77c84e/packages/core/src/form.ts)

该 Form 实现使用进程内 Cache 和 Deferred；不能据此宣称重启后恢复仍有保证。ScienceDiscovery 的持久化请求与检查点不能被一个等待中的 Promise 替代。

### 3.2 权限审批有自己的策略与作用域

V2 使用 action、resource、effect 规则表达 `allow/ask/deny`。`always` 保存项目范围的持久规则，不能覆盖显式 deny；V1 文档中的 always 则限定当前 OpenCode session，两者不能混写。V2 的字段名也从 `permission/bash/task` 改成 `permissions/shell/subagent`。[V2 权限文档](https://opencode.ai/v2/docs/permissions/)、[V1 权限文档](https://docs.opencode.ai/docs/permissions/)

### 3.3 配置修改不等于领域配置工作流

官方允许用户让 OpenCode 修改配置文件；模型服务接入仍有 `/connect` 和 `/models`，自定义 provider 要声明协议包、地址与模型。由此可见，“Agent 帮忙改配置”可以复用文件编辑能力，但这并不自动提供领域校验、影响预览或同一任务热生效。[配置文档](https://opencode.ai/v2/docs/config/)、[Provider 文档](https://opencode.ai/v2/docs/providers/)

**对我们的启发：**复用通用表单与请求状态，保留领域适配器。普通 question 的答案会进入模型上下文；API Key 应走专用凭据提交接口，只向 Agent 返回引用或脱敏状态。

## 4. WorkBuddy 已公开的按需连接体验

### 4.1 与本功能直接对应的产品行为

WorkBuddy 官方连接器文档明确说明：执行任务时如需尚未连接的连接器，输入框上方显示引导卡片；用户完成授权后自动继续当前任务，无需重新发起对话。同一文档还说明自然语言安装、查看与确认卸载连接器的入口。[连接器使用说明](https://www.workbuddy.cn/docs/workbuddy/From-Beginner-to-Expert-Guide/Function-Description/Connector)

这足以作为“任务内按需配置并接续”的产品参照。它没有公开承诺相同 run ID 或重启恢复语义，因此评审可以借鉴交互结果，工程上仍需独立验证我们要求的恢复粒度。

### 4.2 表单由连接器声明

开发文档中，连接器可通过 `auth_mode: token` 和 `token-schema.json` 声明凭据字段，包括 key、类型、必填、默认值和帮助链接。客户端生成表单，并把用户输入注入 MCP 配置的占位符；敏感字段使用 password 类型。OAuth 则走另一条认证路径。[连接器开发规范](https://open.workbuddy.cn/docs/connector#用户自填-token-模式)

**对我们的启发：**新增受支持设置时声明字段和验证规则，公共 UI 完成渲染。一个 URL 仍需对应协议与适配器，不能自动成为任意 API 的通用接入方案。

### 4.3 不直接照搬其全局权限模式

其完全访问开关作用于当前客户端的所有任务，减少操作确认。它与我们限定低风险 Session 配置的独立授权不是同一个范围。[权限模式说明](https://www.workbuddy.cn/docs/workbuddy/From-Beginner-to-Expert-Guide/Function-Description/Permission-Modes)

## 5. openJiuwen core 已有的中断与恢复基础

### 5.1 可以复用的标准机制

`BaseInterruptRail` 支持批准、拒绝和中断等待；`ConfirmInterruptRail` 处理确认，`AskUserRail` 收集问题答案。宿主用 `InteractiveInput` 将响应绑定到 interrupt ID，再调用 Agent 恢复执行。[中断 API 文档](https://github.com/openJiuwen-ai/agent-core/blob/1fe6a03690766e64a686ac51e873495a617b5ca5/docs/zh/2.%E5%BC%80%E5%8F%91%E6%8C%87%E5%8D%97/API%E6%96%87%E6%A1%A3/openjiuwen.harness/rails/interrupt.md)

`ToolInterruptHandler` 保存工具调用、迭代位置、原始目标及中断映射；恢复时执行待处理的外层工具调用，再继续 ReAct 循环。配置应用工具因此必须自己保证幂等，不能假设恢复时绝不会重新进入工具。[恢复处理器源码](https://github.com/openJiuwen-ai/agent-core/blob/1fe6a03690766e64a686ac51e873495a617b5ca5/openjiuwen/core/single_agent/interrupt/handler.py)

### 5.2 自动确认与持久化能力

ConfirmPayload 已有 `approved`、`auto_confirm`、`persist_allow`；Permission Host 提供确认、权限快照和持久化回调，包括会话内记住决定。这些能力服务于工具执行授权，不自动覆盖所有业务设置或凭据修改。[确认 Rail](https://github.com/openJiuwen-ai/agent-core/blob/1fe6a03690766e64a686ac51e873495a617b5ca5/openjiuwen/harness/rails/interrupt/confirm_rail.py)、[Permission Host](https://github.com/openJiuwen-ai/agent-core/blob/1fe6a03690766e64a686ac51e873495a617b5ca5/openjiuwen/harness/security/permission_engine/host.py)

### 5.3 卡片输出前保存检查点已有上游依据

开发分支的修复记录要求 NativeHarness 在输出 interrupt 卡片前等待 `Session.commit_interrupt()` 成功；保存失败不输出可恢复卡片，并有重建 SQLite checkpointer 的回归用例。这与我们“先持久化，再开放确认”的要求一致。[检查点顺序修复记录](https://github.com/openJiuwen-ai/agent-core/blob/1fe6a03690766e64a686ac51e873495a617b5ca5/openjiuwen/agent_teams/docs/bugfix/B_02_native-harness-interrupt-checkpoint-before-output.md)

**回答是否已经具备：**中断、确认、输入和框架恢复已经具备；ScienceDiscovery 的配置目录、变更预览、Session 继承、凭据引用及设置事务仍属于产品领域。core 的恢复状态也不会自动维护 ScienceDiscovery 的 run 数据模型。

## 6. WorkSwarm 可复用的产品模块

### 6.1 凭据表单和连接状态已经存在

后端 `credential.py` 读取 `token-schema.json`，构造 `credentials_required` 和字段描述；`ConnectTokenModal` 按字段类型展示输入，并调用保存与连接逻辑。这比单纯的 ask_user 更接近我们的凭据向导。[凭据层](https://github.com/openJiuwen-ai/jiuwenswarm/blob/1372eda94970bf91d5dc0cb03d784e971f39b991/jiuwenswarm/server/runtime/mcp/credential.py)、[凭据组件](https://github.com/openJiuwen-ai/jiuwenswarm/blob/1372eda94970bf91d5dc0cb03d784e971f39b991/jiuwenswarm/channels/web/frontend/src/components/ConnectorMarket/ConnectTokenModal.tsx)

`usePendingConnectorFlow` 顺序处理连接、凭据与认证，全部完成后调用 `onAllConnected`，取消则终止。这是可借鉴的连接编排，但其调用点用于插件和专家安装/重连，不能将函数名中的“续跑”直接理解为研究 run 恢复。[连接流程源码](https://github.com/openJiuwen-ai/jiuwenswarm/blob/1372eda94970bf91d5dc0cb03d784e971f39b991/jiuwenswarm/channels/web/frontend/src/components/ConnectorMarket/usePendingConnectorFlow.tsx)

### 6.2 配置控制面和运行时生效有现成实现

`config_set_handlers.py` 处理配置保存，`config_adapter.py` 将保存事件交给 `reload_runtime_after_config_save`，再调用运行时配置重载。这些可以作为领域适配器的后端能力，而不是另外写一套 YAML 修改器。[配置保存](https://github.com/openJiuwen-ai/jiuwenswarm/blob/1372eda94970bf91d5dc0cb03d784e971f39b991/jiuwenswarm/common/config_panel/config_set_handlers.py)、[运行时重载](https://github.com/openJiuwen-ai/jiuwenswarm/blob/1372eda94970bf91d5dc0cb03d784e971f39b991/jiuwenswarm/server/runtime/gateway_adapter/config_adapter.py)

全局运行时重载不等于某个已暂停 run 获得新工具；还需核对缓存 Agent、工具目录和 Session 覆盖的生效时点。Web 与 TUI 同名配置 RPC 也不能假定契约相同。

### 6.3 问题交互和自动审核不能代替配置事务

WorkSwarm 扩展了结构化 ask_user，并有确认卡及 continuation 身份校验。Smart Approval 在现有权限规则内审查工具请求；审核超时转人工，不会因超时直接批准。[结构化提问](https://github.com/openJiuwen-ai/jiuwenswarm/blob/1372eda94970bf91d5dc0cb03d784e971f39b991/jiuwenswarm/agents/harness/common/rails/ask_user_rail.py)、[恢复关联校验](https://github.com/openJiuwen-ai/jiuwenswarm/blob/1372eda94970bf91d5dc0cb03d784e971f39b991/jiuwenswarm/server/runtime/agent_adapter/permission_continuation.py)、[Smart Approval](https://github.com/openJiuwen-ai/jiuwenswarm/blob/1372eda94970bf91d5dc0cb03d784e971f39b991/docs/en/SmartApproval.md)

**回答是否已经具备：**有大量相关产品组件，值得优先复用；本次核对的链路尚不足以证明“任意设置通过统一提案、确认后在同一研究 run 生效”。不应一方面忽略现成模块，另一方面把多个独立模块的存在写成完整集成已经完成。

## 7. ScienceDiscovery 当前接入的实际差距

### 7.1 先按真实依赖版本评估

本地安装脚本默认 `JIUWENSWARM_TAG=workswarm0.2.6`。该标签对应公开提交 `70aa6715…`，其依赖 core `ad0c9dfb…`；本次核对的 WorkSwarm develop 依赖 core `f9f50368…`，而 core develop 本身又是另一提交。因此不能把 core 最新能力直接视为现有部署已具备。[安装脚本](../../../scripts/jiuwenswarm.sh)、[旧标签依赖](https://github.com/openJiuwen-ai/jiuwenswarm/blob/70aa6715992d9cb2c3ec81ee35991d13747e5ce0/pyproject.toml)、[develop 依赖](https://github.com/openJiuwen-ai/jiuwenswarm/blob/1372eda94970bf91d5dc0cb03d784e971f39b991/pyproject.toml)

旧标签已包含凭据 Schema 和 pending connector 流程，说明复用并不都依赖升级；但检查点顺序修复、Smart Approval 和新的 continuation 合同需要逐项核对版本。[旧标签凭据层](https://github.com/openJiuwen-ai/jiuwenswarm/blob/70aa6715992d9cb2c3ec81ee35991d13747e5ce0/jiuwenswarm/server/runtime/mcp/credential.py)、[旧标签连接流程](https://github.com/openJiuwen-ai/jiuwenswarm/blob/70aa6715992d9cb2c3ec81ee35991d13747e5ce0/jiuwenswarm/channels/web/frontend/src/components/ConnectorMarket/usePendingConnectorFlow.tsx)

此处确定的是仓库默认安装契约，不是对当前 WSL 实际安装版本的在线盘点。

### 7.2 现有桥接只暴露部分控制能力

ScienceDiscovery adapter 的 `/agent/jiuwenswarm-config` 将允许的搜索设置转发到 `config.set`，并拒绝未知字段；当前白名单只有免费搜索开关与相关 API Key。该接口既不是通用配置提案服务，也不能直接承担模型、所有连接器或任意全局设置的修改。[adapter 配置桥接](../../../services/adapter/src/sciencediscovery_adapter/agent_runs.py)

现有设计中，原生执行器的 PubMed 启用已有同一 run 恢复；JiuwenSwarm 路径尚不能据上游中断能力直接宣称同等支持。需要把上游 interrupt ID、ScienceDiscovery request ID、Session 和 run 绑定，再协调配置生效与恢复。[当前执行器边界](https://github.com/wyhohyw/sciencediscovery/blob/f58c728b4395ed32f7fa1161636e87c9d6123f5d/docs/zh/explanation/conversational-settings.v15.md)、[运行适配层](../../../services/api/src/agent-run/jiuwenswarm-agent.ts)

## 8. 建议的复用方式与方案调整

### 8.1 保留一套产品语义，对接不同执行器

| 层次 | 建议复用 | ScienceDiscovery 仍负责 |
|---|---|---|
| 交互请求 | Codex/OpenCode 的请求状态分层思路；WorkSwarm 已有问题与连接状态格式 | 配置原因、差异、范围、决定来源与折叠回执 |
| 字段与凭据 | WorkBuddy/WorkSwarm 的 Schema 表单方式；已有连接和凭据服务 | 适配现有存储，不把密钥放进通用问答或模型上下文 |
| 领域修改 | WorkSwarm 已有 model/MCP/config 管理能力，ScienceDiscovery 现有设置服务 | 输入限制、影响预览、版本冲突、幂等应用和继承关系 |
| 执行暂停恢复 | Native 已有检查点；JiuwenSwarm 路径优先接 core interrupt/resume | 外部中断与本地 run 关联、Stop、队列和恢复结果反馈 |
| 自动处理 | 现有权限与授权规则的复核能力 | 独立配置授权、PubMed 白名单、30 秒取消窗口及到期复核 |

上游 Python 组件不能直接当作 TypeScript 模块调用；优先通过现有控制接口及窄适配端口接入。Schema 只定义输入与展示，后端必须再次校验，不能让 LLM 提供任意 HTML 或可执行配置。

### 8.2 维持既有目标接口，不扩大 Agent 权限

开发 v5 延续 v4 已规划的 `inspect_configuration`、`propose_configuration` 和 `ConfigurationAdapter`。调研支持继续这个方向：Agent 获得脱敏状态并提出请求；用户输入走前端专用通道；后端调用领域服务；执行器按生效能力恢复任务。

设置提交与外部激活分别处理。开发 v5 将适配器分为本地同步事务和外部控制面两类；WorkSwarm 的 config 写入采用持久操作/回执核对，reload/connect 放在激活协调中。保存、激活和恢复分别记录结果，失败时只处理未完成或待核对的阶段。

### 8.3 优先做三项接入验证

1. 固定实际依赖和补丁组合，保留已就绪 PubMed 的 Native 回归；提取公共请求、领域适配器和执行器端口，明确能力门槛。
2. 在该边界内接入 JiuwenSwarm interrupt/resume，以同一 PubMed 任务验证决定、配置生效和原 run 继续；不先扩展更多设置。
3. 以一个受支持的凭据连接器验证 Schema → 专用输入 → 权威凭据存储 → 连接 → 工具重建 → 原任务继续，再按领域扩展。具体工作包与退出条件以设计 v15、开发 v5 为准。

## 9. 接入前必须补齐的验证

| 验证问题 | 通过标准 |
|---|---|
| 收到用户回答是否只结束了提问？ | 原研究实际调用新工具并产出结果；不要求再次发送“继续” |
| 设置保存是否已对当前 run 生效？ | 可观测到新工具/配置被当前任务使用，不能只看 settings API 成功 |
| 卡片输出后立即重启呢？ | 从持久化状态恢复原请求与执行位置；保存失败时不展示可确认卡 |
| 恢复时再次进入应用工具呢？ | 同请求不重复修改、不重复连接、不重复执行有副作用的动作 |
| 等待期间另一窗口改设置呢？ | 旧预览失效或重新确认，不覆盖新值 |
| Stop、取消和迟到确认并发呢？ | 不能唤醒已停止任务；配置已提交时保留真实回执 |
| 密钥经过普通 ask_user 呢？ | 必须拒绝该接入方式；模型和普通轨迹中只出现脱敏状态/引用 |
| 30 秒到期但条件变化呢？ | 服务端停止自动应用；工具完全访问不替代配置授权 |

这些是后续接入验收，尚未因本次文档和源码调研而获得新的运行测试通过证据。

## 10. 来源基线与可追溯范围

| 对象 | 本次核对基线 | 来源范围 |
|---|---|---|
| Codex | 2026-10-19 官方 App Server 与 MCP 文档 | 协议与配置接口；未对桌面全部设置做 UI 实测 |
| OpenCode | dev `ba06ff9e0bf2dcefbbbc3a1d6094f3572d77c84e` | V2 question/Form 源码；V1/V2 文档分别标识 |
| WorkBuddy | 2026-10-19 官方用户与开发文档 | 任务中连接行为、凭据 Schema、权限范围；无私有源码结论 |
| openJiuwen core | develop `1fe6a03690766e64a686ac51e873495a617b5ca5` | Rail、恢复处理器、Permission Host 和检查点修复记录 |
| WorkSwarm | develop `1372eda94970bf91d5dc0cb03d784e971f39b991` | 配置/连接/凭据/恢复源码；该基线固定，不随 develop 漂移 |
| WorkSwarm 旧标签 | workswarm0.2.6 `70aa6715992d9cb2c3ec81ee35991d13747e5ce0` | 依赖声明、凭据 Schema 与 pending connector 流程 |
| ScienceDiscovery | 本地隔离分支 `3076506361bd0b10a497b9e66000e94472422623` | 安装脚本、adapter 白名单、现有设计与运行适配层 |

文中建议应以具体依赖组合做后续集成验证；如决定升级 WorkSwarm/core，先固定目标提交并做兼容回归，再更新产品能力状态。
