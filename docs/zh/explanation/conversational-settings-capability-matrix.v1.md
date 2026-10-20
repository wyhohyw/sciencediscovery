# 对话式配置：设置支持矩阵 v1

版本日期：2026-10-20。对应设计 v18 §15、开发 v8 §19 的 DEV-CFG-11/16，验收 CFG-T29/33/34/35。本文盘点当前正式设置入口，并区分已经接线的领域操作和后续候选。新增源码的 Windows 专项结果只用于开发反馈；WSL、浏览器与真实/脚本模型证据由集成状态记录，不能用本表替代。

“可识别”指 Agent 能从有限目录获知字段、适用场景和不支持原因，不保证任意自然语言均被正确理解。“可提案/保存”指已注册且复用正式存储；“本任务”指已冻结的活动 run，“后续任务”指保存后新排队的 run，不包括保存前已排队的任务。P1 目前仅接入 Native，JiuwenSwarm 的共享配置宿主与 scoped 激活未完成。

## 1. 已接入的有限能力

| 设置 / capability | 可识别 | 可提案 / 保存 | 本任务生效 | 后续任务生效 | 真正保存范围 / 继承 | 临时使用 / 凭据 | 风险、验证与反向操作 |
|---|---|---|---|---|---|---|---|
| 模型选择 `model.select` | 是，当前模型、来源和注册候选 | 已有正式模型注册表 | 不热切换 | 新排队 run | Session 覆盖 Global/Project；不创建模型资源 | 无 overlay；不读取密钥 | 无外部探测；receipt 反向提案恢复父级或旧覆盖 |
| 模型连接 `model.connect` | 是，有限协议与字段 | 已有 provider/model/加密凭据存储 | 不热切换 | 新排队 run；首模型可用于首次运行 | 首模型设置 Global 默认；后续资源仍是共享注册项，但绑定当前 Session | 卡片专用加密草稿；无临时凭据 | 用户批准后有界模型验证，可能计费；失败不污染正式资源；不提供通用删除回滚 |
| 模型凭据修复 `model.repair_credential` | 是，精确模型；网络失败不是凭据失效证据 | 已有 model profile 独立凭据覆盖 | 不热切换 | 后续使用该 profile 的 run | Global profile 资源；使用它的其他 Session 也受影响 | 专用凭据通道；无 run-only | 候选验证成功再替换，保留同 provider 其他模型；无通用旧密钥恢复 |
| 已就绪连接器 `connector.enable` | 是，实际安装目录、有效启用集合 | 正式 Session runtime settings | Native 已就绪 MCP 可同 run 激活并核对 factory witness；built-in Web 不据此承诺同 run | Session 保存后新排队 run | Session；保留其他继承工具 | 新 v3 的 Native ready MCP 可有真正 run overlay；不复制已有凭据 | 独立工具权限仍有效；Session 保存可 receipt 反向；run-only 不写 Session 后回滚 |
| Skill / Library `skill.select` | 是，已安装 Skill ID、库 ID/head、当前值与来源 | 正式 runtime settings；库来自 SkillLibraryCatalog | 不改变活动装配 | 新排队 run | Project 或 Session；Global 不提供 Skill 选择；Project 不覆盖已有 Session 覆盖 | 无 overlay、无安装、无文件导入 | 只允许有限 ID 多选；新增库固定获准 head；继承的已固定版本/priority/limit 保留；0 外部请求；后续显式提案或设置页恢复 |
| 思考配置 `thinking.configure` | 是，实际 API variant 与模型合法取值 | 正式 Session runtime settings | 不改变活动模型或已冻结参数 | 新排队 run | Session，保留未修改字段的继承；不改共享模型 profile | 无 overlay、无凭据 | 协议须有真实思考字段，模型特定非法取值直接拒绝，不静默 clamp；0 探测；思考可能增加延迟/费用；后续提案或设置页恢复 |
| 会话审阅 `review.configure` | 是，审阅模型/语义审阅/会话自动审阅/level | 正式 runtime settings + SessionReviewerSpecialistSettings | 不改活动任务 | 新排队 run 的设置；自动审阅仍受全局 Specialist 开关约束 | Session；全局 Reviewer Specialist 开关保持不变 | 无 overlay，模型凭据继续归已有模型存储 | 审阅模型必须已注册；quick/deep 与布尔字段复用正式校验；0 探测；deep 可能增加模型费用；全局禁用时预览明确“偏好已保存但仍未启用” |

所有新 P1 能力默认人工确认。`Always allow` 不产生配置授权；既有独立 PubMed 30 秒策略不能授权新 v3 方案、Scope 或 P1 设置。

Skill 的 `all` 表示所有已安装 Skill 已经可用，不应为单个已可用 Skill 再弹启用卡。`selected` 才使用白名单。不得把这个语义写成“所有新项目默认 all”：正式项目创建可设置 `selected`，目录读取当前值而非猜测默认。

P1 首轮不提供“移除覆盖并恢复继承”的专门动作；未出现在提交中的字段继续继承，提交的字段成为明确覆盖。已有模型/连接器 receipt 的反向能力不据此扩大到所有设置。

## 2. 已盘点但未开放 Agent 修改的设置

以下各行可从有限目录识别为尚未集成；“本任务 / 后续任务 / 临时”均不作新支持承诺，禁止生成成功卡或以直接编辑配置文件绕过。

| 设置入口 / 正式字段 | 真实范围与凭据归属 | 已有服务可复用 | 仍需开发与审核的边界 |
|---|---|---|---|
| Web：`fetchCacheTtlSeconds`、`fetchProvider`、`freeSearchEngines`、`paidSearchProviders`、`proxyPolicy`、`searchCacheTtlSeconds`、`providerApiKeys` | System；Key 在既有 `web_provider_secrets` | WebSettingsEditor、搜索/抓取领域 API 与 provider 状态 | P2：Native/Jiuwen 引擎字段差异；专用 Key 草稿；有界验证；付费供应商明确选择与费用 |
| MCP 资源：`name`、`description`、`transport`、`enabled`、`url`、`headers`、STDIO `command`/`args`/`env`/`cwd`、`timeoutSeconds`、`authMode`、`oauth` | 共享 server 资源；既有 MCP 凭据/授权服务 | McpServerSettings、McpAuthorization、CustomMcpServerInput、连接与安装服务 | P2 修复与 P4 新增分离；条件写入、连接验证和共享影响；安装命令仍受执行授权，不作为普通字符串执行；OAuth 回调绑定 |
| Proxy：`defaultPolicy`、server name/kind/url、各模块/服务器 proxy policy | System/共享 proxy；URL 可含敏感凭据，正式库加密 | ProxySettingsEditor、proxy registry、各 transport resolver | P3：部署全局影响、代理 URL 安全通道、实际 transport 探测和目标限制；不能伪装 Session/run-only |
| Timeout：`gatewayIdleTimeoutMs`、`gatewayTurnTimeoutMs`、`kernelIdleTimeoutMs`、`permissionWaitTimeoutMs`、`runnerExecTimeoutMs` | System；无凭据 | SystemTimeoutSettings 和已有 getter/setter | P3：数值边界、生效节点与所有任务影响；0 可关闭限制，不能因一次超时自动放宽 |
| Quota：`maxConcurrentSubagents`、`runnerMaxOutputBytes`、`runnerMaxWorkspaceBytes`、`uploadMaxFileBytes`、`uploadMaxRequestBytes` | System；无凭据 | SystemQuotaSettings 与 Runner/上传消费点 | P3：资源预算、并发 1～10 与 byte quota 的 0 语义；需要明确批准，不从工具权限继承 |
| Memory graph：`enabled`、`backend`、`neo4jHttp`、`neo4jUser`、`neo4jPassword` | System；Neo4j 密码由既有 write-only store 管理 | MemoryGraphSettingsEditor、sidecar 状态与密码服务 | P3：local/neo4j 准备状态、外部连接和后端激活；保存密码不等于启用 |
| Idea Tree：`templateId`、`explorationIntensity`、round/candidate/token/depth/node/search limits、`scoreDirection`、design/aggregator/propagate prompts、三个 assessor 配置 | System；不是 Session 设置；无凭据字段 | IdeaTreeSettingsEditor、正式权重/参数校验与后续 run 注入 | P3：结构化配置、权重和 token/search 预算；不能塞进任意 JSON；活动 run 不受保存影响 |
| 环境来源 / 分配：`pipSource`、`condaSource`、`runnerIds`、`remoteRunnerHostIds` | 包源为 System；Runner 分配属于 Project/Session | EnvironmentSourceSettings、RunnerEnvironmentSettings 与正式会话/项目 API | P3：逐资源 scope、连接验证与实际生效，不把环境 revision 伪装成一个自由填写的字段 |
| 环境创建 / 包操作：`baseEnvironmentId`、`language`、`name`、`channels`、`indexUrl`、`manager`、`packages` | Runner resource；无新凭据库 | CreateEnvironmentRequest、InstallEnvironmentRequest、UninstallEnvironmentRequest 和环境 revision 服务 | P3：安装副作用、包来源和预算；revision 是正式操作生成的审计对象，不是任意 JSON 配置 |
| Runner 注册 / SSH：`id`、`runnerName`、`description`、`alias`、`connectionKind`、`endpoint`、`port`、`runnerCommand`、`token`、`username`、`password`、`passphrase`、`privateKeyPath`、`trustHostKey` | Runner 共享资源；凭据留在既有加密 store | RegisterRemoteHostRequest、Runner/SSH host 服务 | P3/P4：连接验证、host key 信任、密钥路径与凭据专用通道；命令仍需执行授权 |
| Sandbox network：`mode`、`allowedDomains`、`allowPrivateNetwork`、`egressProxyPolicy` | Admin-owned System；冻结在 Permission Epoch | SandboxNetworkSettings、egress gateway、权限服务 | 不属于本轮普通配置适配器；不会因为配置选择绕过网络/安全批准 |
| 权限/自动策略：`approvalMode`、`autoApplyLowRiskConfiguration` | Session 的独立用户授权 | 既有工具权限与独立配置授权 UI | 不向 Agent 提供修改授权的工具；新增配置不继承 `Always allow` 或旧授权 |
| Plugins / Specialist：namespaced `plugins`、`specialistId`、全局 Reviewer Specialist `enabled`、legacy `feedbackPolicy` | plugin scope/资源不同；全局 Reviewer 与会话审阅不同 | PluginControl、scoped editor、Specialist API | plugin config 可能包含敏感信息，不能整体暴露给 inspect；全局开关不是 P1 会话审阅；legacy feedbackPolicy 不恢复为可配置项 |
| 导入 / 新服务 / OAuth | 来源、路径和资源范围由正式入口决定 | 既有受控文件选择、安装和授权服务 | P4：安全文件选择、认证回调绑定、来源信任、凭据/执行权限及中断恢复；不接受任意 config path |

对应的机器可读有限目录在 `services/api/src/configuration/settings-catalog.ts`，只包含字段名、归属、风险和未支持原因，不包含真实配置值或凭据。`inspect_configuration` 当前值采用显式白名单投影，不返回完整 plugins config、endpoint URL、headers、密码或凭据 handle。

## 3. 目录、参数和诊断如何复用

每项能力在注册时附上受信任的 label、适用任务、有限字段和参数规则、诊断分类、生效方式。通用 Agent 工具的 schema 根据**实际已注册能力**生成；通用工具中没有四/七种能力的参数并集维护表。P1 领域 normalizer 直接复用相同字段描述，并增加已安装资源、模型合法取值和作用范围检查。

诊断只接收结构化分类：缺模型、精确模型凭据缺失/认证拒绝、连接器未启用、Skill 被白名单排除、显式思考/审阅需求。未知故障、网络错误、超时、限流、无结果均不映射为修改 Key、代理或配额。目录提示是识别依据，不是用户授权，也不承诺已有全部工具错误都自动转换成诊断；新的工具领域仍需把可核实分类接到相应边界。

主 Agent 的工具仍只有 inspect 与 propose；没有批准、密钥读取、配置文件写入、安装命令或授权策略修改。P1 保存结果明确 next_run，不借用连接器的同 run 恢复承诺。JiuwenSwarm 未通过真实宿主验证的 scoped 操作，在目录中明确不可用，并在提案、预览和写入边界拒绝。

会话审阅的 `automaticReviewEnabled` / `level` 原来由执行器和后台审阅 scheduler 读取实时 Session 偏好，单改配置表无法兑现“旧排队任务不变”。本轮因此在正式入队时写入 `settingsSnapshot.sessionReviewer`，运行器使用该冻结值；自动审阅通过 Artifact version 的 `turnId` 及 Subagent 的 `parentTurnId` 精确绑定原 root Run，`ReviewerAuditTask.sourceRunId` 保留该归属，合批按 source Run/level 分隔，后台执行继续读取原 Run 冻结值。Deep 审阅复用原 Run 的模型/工具运行快照；全局 Reviewer Specialist 开关继续作为独立实时总闸。没有来源绑定的历史产物、已存在但缺少新增快照的旧 Run，以及手动发起审阅保留原有当前偏好语义，不按时间猜测所属 Run。已经明确绑定 `sourceRunId` 后若来源 Run 丢失，审阅失效收束，不能借用当前 Session 偏好/模型继续。忙碌检查、检查点和 claim 的异步持久化完成后，在实际调用审阅执行器前再次核实来源与实时全局开关；手动审阅同样不能在全局关闭后新启动。

仅本次连接器 overlay 生效后，inspect 分别返回活动任务的实际获准工具集和 Session 已保存工具集。活动集来自同 run、已应用且已激活、匹配批准 revision 的持久 witness；不以当前安装目录代替授权。这样“本次使用了 PubMed、Session 没有长期启用”不会在恢复后的第二次 inspect 被误判为仍需启用。

## 4. 本轮专项证据与待验证项

新增 `p1-adapters.integration.test.ts` 覆盖：注册参数生成/跨领域拒绝、受限多选、Project/Session 继承、安装目录校验、库版本 CAS 与继承 pin 保留、协议/模型思考合法值、冻结任务不变、会话/全局审阅边界、正式页面编辑造成旧批准失效、脱敏 inspect、all 模式不重复提案、v3 选项和未验证后端门控。新增真实 ReviewerAuditCoordinator 用例证明：旧已排队 Run 冻结 `true/quick`，保存新偏好 `false/deep` 后，新 Run 得到 `false/deep`；旧 Run 的产物仍被实际执行 Quick 自动审阅，新 Run 的产物不触发自动审阅；旧 Run 的 settingsSnapshot 不变。

以上是测试覆盖说明，最终通过数量、WSL/浏览器端到端、脚本模型与真实 LLM、真实或离线工具、Runner 与 Jiuwen 后端的证据由集成记录逐项回填；未执行部分保持待验证。
