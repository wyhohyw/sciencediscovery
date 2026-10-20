# 对话配置：执行底座核验与接入边界 v1

- 日期：2026-10-19。
- 依据：设计 v16；开发 v6 的 2.4、5.4、9.4～9.5、14、17 节；CFG-T24～CFG-T28。
- 核验方式：只读已安装源码、Python 分发元数据和补丁清单，未升级依赖，未启动或修改主运行时。

## 1. 已安装的实际组合

| 对象 | 实际证据 | 说明 |
|---|---|---|
| WorkSwarm 源码 | `70aa6715992d9cb2c3ec81ee35991d13747e5ce0` | 与安装脚本的 `workswarm0.2.6` 标签基线一致 |
| core 源码 | `ad0c9dfbbd577423cc60f73edffa0a4699cc53f9` | 由已安装 `openjiuwen` 的 `direct_url.json` 核对 |
| 分发元数据 | `workswarm 0.2.5b1`、`openjiuwen 0.1.17` | 包版本不代替 Git 提交；标签与分发版本不同不可据此推断能力 |
| 兼容补丁 | `scripts/swarm-patches.py verify` 对固定标签校验成功 | 当前安装涉及 7 个源码文件；没有新增上游补丁 |
| 隔离试用服务 | Native 执行器 | 隔离目录未安装独立的 JiuwenSwarm 源码实例 |

核对到 core 中存在 `openjiuwen.harness` 和 `openjiuwen.core`。本次门控的原因是 ScienceDiscovery adapter 尚未实现完整宿主合同，不是认为 core 不具备中断机制。

## 2. 当前可以复用的链路

Native 仍然使用已有 `suspendAfterTurn`、`VersionStore`、`configurationWait`、原 run 重新入队及工具目录重建。新增 `persistNativeConfigurationWait` 包装持久顺序：先保存检查点，再提交 run 等待绑定，两侧复核 Stop generation。失败时不返回可展示的等待回执。

该包装已经接入 `runs/index.ts`。不另建研究 run，不把配置暂停转换为最终答复，不把模型历史伪装成 JiuwenSwarm 的 core 检查点。

Native 连接器激活新增真实工具装配证据：`native-activation` 使用与 `createMcpWorkspaceTools` 执行工厂共用的 descriptor 装配，只合入原 run 冻结快照和获准增量，并保存完整工具 Schema、说明与路由的指纹。原 run 恢复构造 executable 工具后，在模型运行前再次核对工具指纹、连接器集合、配置依赖版本和 Stop generation；不匹配即拒绝继续。

配置已保存、生效证据与研究恢复分别记录。激活完成与 resume outbox 在同一事务保存；恢复失败只重试原 run 的恢复阶段。各阶段按请求串行并领取持久 outbox 租约；发生入队后确认前崩溃，优先核对原 run 已有入队状态，不再执行配置写入。

## 3. 新增的端口与实现

| 模块 | 本次实现 | 当前边界 |
|---|---|---|
| `backend-capabilities` | 协议 v1 校验、无副作用探测、设置/范围/生效模式求交及门控 | 版本字符串、RPC 同名或 reload 成功不能开启 same_run |
| adapter `/agent/configuration/capabilities` | 鉴权、实际部署源码/补丁证据、明确不支持项 | 不调用 gateway，不保存配置；部署源码未知时返回 null |
| `backend-control` | inspect / submit / queryOperation 的窄合同与严格操作回执校验 | 不回退至普通 config.set；当前 adapter 尚无 submit/query 权威服务 |
| `operation-worker` | SQLite 操作日志、不可变操作、租约、提交前持久 sending、超时/重启查询核对 | 只有宿主提供条件写入、去重和查询后才可以启用外部配置写入 |
| `activation-coordinator`、`native-activation` | 保存与激活/恢复分离；批准 revision/digest、目标版本、真实工具装配与恢复前核对 | Native 已接入上述证据路径；Jiuwen 宿主仍需提供独立真实生效证据；激活失败不重复 apply |
| `runtime-bridge` | Native 等待顺序；Jiuwen Session/run/request/interrupt/checkpoint/generation 精确绑定与单中断恢复合同 | Jiuwen 端口合同已实现和测试，实际 core 宿主尚未接入，功能保持关闭 |

外部调用一旦发出，Stop 不能撤回其远端副作用。worker 会在需要时继续查询并记录已经保存的事实，后续激活和恢复由独立授权检查抑制。

## 4. 为什么 JiuwenSwarm 当前不能声明 same_run

现有 adapter 的 `AgentRunRequest` 没有配置 interrupt、检查点和恢复身份字段；`/agent/runs` 也没有持久配置等待/恢复协议。现有工具权限的提问与回答链路不能自动承担配置提案的业务授权与持久恢复。

因此能力握手明确返回以下保证为 false：条件写入、操作幂等、权威操作查询、持久配置 interrupt、精确恢复、目标工具刷新核对以及持久等待绑定。它仍可运行原有研究任务；配置功能不能借此承诺尚未实现的同 run 恢复。当前受支持的本地独立配置可按明确的 next_run 语义执行。

## 5. 仍需补齐的宿主工作

1. 在确切的主 Agent 配置工具边界调用真实 core interrupt，并证明确切检查点先于卡片持久化。
2. 持久保存 ScienceDiscovery 与上游 Session/interrupt/checkpoint 的关联，支持服务重启后的查询和恢复。
3. 用 InteractiveInput 只回答绑定的配置 interrupt，保留同批未获准的工具权限与其他问题；桥接工具重入只读已有决定和回执。
4. 在原 run 范围验证工具目录已经加载获准增量；不能通过全局 reload 成功推断。
5. 外部配置由资源所属宿主实现条件版本、operationId 去重及权威操作查询，并让设置页等所有写入口服从相同边界。
6. 完成上述宿主合同及真实双执行器故障注入验收后，再在握手中开放相应能力；不能仅修改布尔值开放。

## 6. 验证口径

`services/api/src/configuration/runtime-contract.test.ts` 对应 CFG-T24～CFG-T28，覆盖真实 SQLite 操作恢复、未知外部结果不重发、Stop 竞态、错误 Session/interrupt、等待持久顺序及激活/恢复分离。测试中的 Jiuwen 宿主是合同替身，不作为真实 core 集成成功证据。

`services/adapter/tests/test_configuration_capabilities.py` 核对握手鉴权、无 gateway 副作用、未知部署证据及补丁文件漂移。完整构建、包测试和产品端到端结果应以本轮统一交付记录为准。

`services/api/src/configuration/native-activation.test.ts` 额外覆盖实际 workspace 工具工厂、Schema/未批准连接器漂移、冻结插件策略、并发决定、active 后恢复失败重试、outbox 租约崩溃恢复和 Stop 后保持已保存事实。

`services/api/src/configuration/agent-runtime.integration.test.ts` 已经通过真实 Native `/runs` 路径：本地 HTTP 模型依次调用 inspect/propose，任务持久 blocked 后通过 V2 决定恢复同一 run，并实际经过 MCP broker 调用离线 PubMed 工具。批准、拒绝、Stop 三项 WSL 用例通过；独立低风险授权的第四项使用真实服务端约 30 秒倒计时、没有发送配置确认请求，同 run 自动继续且只产生一次工具副作用和最终答复。该文件限定 Linux，因为当前 Windows CAS fsync 不支持这条运行链路。

## 7. DEV-CFG-09：固定 core 的真实缺口和宿主自研责任

再次直接核对已安装固定源码后，需要把两类事情分开说明。

### 7.1 固定 core 存在 emit 早于 durable commit 的窗口

以下均来自 `ad0c9dfbbd577423cc60f73edffa0a4699cc53f9` 的实际安装源码：

- `core/single_agent/interrupt/handler.py:279` 的 `commit_interrupt` 先调用 `context_engine.save_contexts`、再 `self.save(state, session)`，随后返回 interrupt。`self.save` 只更新会话内存状态，没有调用持久 checkpointer。
- `core/single_agent/agents/react_agent.py:2983` 的 streaming 分支先 `_write_invoke_result_to_stream`；`session.commit()` 在之后的 finally 分支约 3013 行执行。
- `core/runner/runner.py:470` 也在 streaming 的 yield 完成后才调用 `post_run`。
- `core/session/agent.py:242` 的 Agent 会话包装器 `Session.commit` 才真正调用 `_inner.checkpointer().post_agent_execute`。

因此不能用“已经收到 interrupt 事件”证明检查点已提交。固定 core 最小修复提案位于 `jiuwen_swarm/core-patches/ad0c9dfbbd577423cc60f73edffa0a4699cc53f9/0001-commit-agent-interrupt-before-return.patch`：仅对 Agent 会话包装器，在保存中断状态后调用现有 `session.commit()`，成功后才返回可输出的结果；Workflow Session 保持原生命周期。检查点写入失败必须没有可见的可确认结果。

补丁是待纳入安装清单并完成真实运行验收的提案，没有应用到用户已安装依赖。`scripts/check-configuration-core-interrupt.py` 只在临时副本执行补丁检查和真实 core handler 方法的顺序/故障合同；它不能替代跨进程恢复、真实工具刷新或完整 core 集成验收。

### 7.2 不能将我们的宿主工作误称为依赖不支持

固定 WorkSwarm 的 `server/runtime/agent_adapter/interface_deep.py:1312` 已提供 `ensure_persistent_checkpointer`，通过 `PersistenceCheckpointerProvider` 和 SQLite 初始化默认 checkpointer；约 10645 行也已有结构化 interrupt continuation 入口。持久化和 InteractiveInput 的基础模块可以复用。

下列仍是本项目必须新增、尚未完成的宿主工作，而非等待外部依赖凭空提供的能力：

1. 在真实主 Agent 配置工具调用前注册专用 core rail；以可信工具参数建立本项目提案并绑定工具调用和 core 中断，避免从 MCP 文本结果伪造无法恢复的中断。
2. 在 adapter request/response 增加持久等待与恢复合同，保存原上游 Session、tool call、interrupt、检查点和 Stop generation；配置等待返回不能被映射为 `done` 或最终答复。
3. 同一工具重入时只读取已有决定/receipt，不再次创建提案或提交配置；仅通过 InteractiveInput 回答匹配的配置 interrupt，不放行同批其他权限。
4. 服务重启后重新绑定原 MCP toolset、模型路由和桥接身份，并在恢复前验证上游持久状态；不能依赖当前内存中的活动 WebSocket/approval map。
5. 针对原上游 Session 重建获准工具增量并取得真实目录证据；再将后续事件接回原产品 run。

只有固定 core 修复、上述宿主桥接及真实跨重启闭环都完成后，才能把 Jiuwen same_run 握手改为 true。当前这些工作明确为未完成，不能把合同替身测试计为 DEV-CFG-09 完工。

## 8. DEV-CFG-08：需要统一的真实上游写入口

固定 WorkSwarm 的控制面并不是一个纯 `config.set` 转发点。已有 Web/TUI、模型、凭据和 MCP 状态会分别持久化；新增权威条件写服务必须在这些领域入口统一版本与操作日志：

| 固定源码入口 | 当前行为及必须新增的约束 |
|---|---|
| `server/runtime/gateway_adapter/config_adapter.py:86` 的 `ConfigAdapter.methods/handle` | 分流 CONFIG_SET、CONFIG_SAVE_ALL、MODELS_REPLACE_ALL 等；新增窄的受控操作协议，保留通道各自 schema |
| `gateway/channel_manager/web/app_web_handlers.py:3647`、3953、3990 | Web `_config_set`、`_models_replace_all`、`_config_save_all`；所有入口共享版本递增、写入串行边界和操作去重，不能只包住新 Agent 入口 |
| `gateway/channel_manager/tui/tui_connect.py:1034` | TUI `_config_set` 会写环境、`.env`、`config.yaml` 并安排后台 reload；需要纳入相同写入版本和可核对阶段 |
| `server/runtime/mcp/credential.py:357/394` | CredentialStore `_save/save_token` 仍是正式凭据唯一所有者；增加受控提交关联与凭据 generation，不能把明文复制进本项目 |
| `server/runtime/mcp/state_store.py:103/272/336/391` | 状态文件 `_save`、upsert、状态转换和 enable；纳入资源版本与操作核对，同时保留连接/工具注册的独立激活阶段 |

配置文件、环境文件、凭据和 MCP 状态分属不同持久化机制，不能把这些调用顺序包装成一个本地 SQLite 原子事务。需要由宿主实现确定性的操作计划、条件版本、不可变 operationId、可查询的权威回执，以及跨文件提交中断的核对/恢复。普通设置页等既有入口也必须遵循共享写入边界，否则本项目侧的锁无法保证 CAS。

当前 backend-control/operation-worker 提供了受控客户端、真实本地持久日志和故障恢复机制；上述宿主写入改造尚未实现。因此 DEV-CFG-08 不能按“已经提供接口类型”宣布完成，也不能开放覆盖型外部配置写入。
