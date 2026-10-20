# 对话内按需配置 实现与实测 v7

- 更新日期：2026-10-20。分支：`codex/conversational-settings-integration`；基线 `1e157446`。用户已确认跳过缺少环境的 Docker/SSH 外部测试；本轮按明确排除这一项的验证范围交付，真实远程部署链路仍未验证。
- 当前规范：[设计 v17](conversational-settings.v17.md)、[开发 v7](conversational-settings-development.v7.md)；复用来源保留[调研 v2](conversational-settings-research.v2.md)。[状态 v6](conversational-settings-status.v6.md)保留历史证据。
- **本轮交付是本地四适配器与 Native 配置协调，不是完整双执行器发布。** JiuwenSwarm 的实际 core interrupt 宿主与外部条件写入后端尚未接通。代码、合同测试、真实服务验证在下文分开记录。

## 1. 本轮实际实现

| 能力 | 实际交付 | 范围 |
|---|---|---|
| 通用能力与请求 | 四能力 registry、字段 normalizer、revision/stateVersion、服务端 previewDigest、独立决定与脱敏快照 | 未注册能力/字段拒绝；不允许任意 Patch、文件写入或脚本 |
| 选择已有模型 | `model.select` 绑定当前会话 | 沿用默认时不打扰；不热切换运行中的模型 |
| 新连接模型 | `model.connect`，专用草稿、批准后验证、正式保存与绑定 | 首个全局默认；后续只绑定当前会话，其他全局字段保留 |
| 指定模型凭据修复 | `model.repair_credential`，隔离验证候选后更新 profile 覆盖 | 共享 provider 及其他模型不变；使用该 profile 的会话受影响 |
| 启用已安装连接器 | `connector.enable`，保留 effective 其他连接器 | Native 研究任务可走同 run；不安装新 MCP |
| 本地可靠提交 | 共用 SQLite 与领域存储，正式配置/密钥、receipt、激活 outbox 同步事务提交 | 失败回滚目录内存与数据库；不宣称跨远端事务原子 |
| 激活与恢复 | 持久 phase/outbox、租约、Stop、真实 MCP 工具描述 witness、实际装配匹配 | 保存、生效、排队、运行、完成分别反馈 |
| 通用交互 | schema 字段、专用凭据、scope/验证计划、预览/确认、折叠结果、主动菜单 | 旧缺模型向导和专用连接器卡继续兼容 |
| Agent | `inspect_configuration`、`propose_configuration` | 仅主 Agent；无批准、读取草稿或写凭据工具 |
| 外部与 JiuwenSwarm | 认证握手、能力门控、外部 worker/journal、精确 interrupt 绑定合同 | 真实宿主未贯通，相关产品承诺继续禁用 |

## 2. 用户现在怎样触发

空会话不自动展开完整配置卡；默认配置可用时直接运行。缺模型的旧向导仍保留草稿保护与首个全局默认规则。主动配置入口和通用卡调用真实服务，不是只展示固定演示状态。

主 Agent 可先调用 `inspect_configuration`，再提出一个具体动作。例如要求启用已经安装的 PubMed 时，可调用 `propose_configuration` 的 `connector.enable`。Native 保存原 run 检查点并进入非终态等待后，才开放操作卡。

选择/连接/修复模型时可在主动入口完成，也可由 Agent 建立提案。用户输入 Key 进入卡片专用字段；上传草稿不会保存正式资源或立即连接。服务端预览展示作用范围与一次短探测计划，用户批准该版本后才验证、保存。

独立“启用 PubMed”预检、旧 `propose_connector_enable` 以及旧模型向导继续存在。不同入口的卡片按请求类型和 ID 去重；不能从仍有兼容组件推断公共服务没有接通，也不能声称全部旧入口已经迁移。

## 3. Native 暂停、保存、生效与继续

1. 请求关联原 run，先持久化完整工具批次结果和 CAS 检查点，再保存 blocked/configurationWait 绑定；等待不输出最终研究答复。
2. 决定绑定 revision、previewDigest、资源版本和 Stop generation。批准后才验证，正式配置与应用回执一起提交。
3. 连接器激活从原 run 冻结集合加入获准连接器，调用实际 MCP 工具描述构建器，保存绑定 request/revision/receipt 的 witness。
4. 恢复仍沿用原 run 与原提问；执行器重新创建真实可执行工具后匹配 witness 指纹，不匹配则阻止继续，不额外加载等待期间出现的其他设置。
5. 激活与恢复的持久意图分别领取、去重和重试。已保存而恢复失败时，保留 applied，只补接续步骤；Stop 抑制未开始阶段。
6. 用户确认或拒绝后，卡片成为默认折叠的一行结果，仍可展开。实际工具执行权限独立，最终研究答复只在原任务结束时生成。

请求过期/Stop 现在会持久化终态并释放活动去重，清理草稿及 companion 倒计时。独立请求可重新创建；研究 run 的请求过期后保留原 blocked 状态和检查点，标记 needs_user_action，不自动写入、继续或结束任务。同一旧检查点的过期提案替换尚未实现，重提明确返回 `CONFIGURATION_REPLAN_UNAVAILABLE`，需先 Stop 后重试。不能把该限制记录成已支持原地重新规划。同 run 仅开放就绪 MCP 工具源，内置 Web 返回不支持，供后续运行的独立配置仍可使用。

这些机制不表示任意外部业务动作都能自动重放。未知上传、付费或远程作业结果仍需要权威查询或人工核对。当前模型适配器只声明首个/后续运行的生效方式，不修改已冻结模型。

## 4. 兼容分支与尚未开放的能力

### 4.1 30 秒自动应用的真实范围

Native 通用 PubMed 请求已接入独立、默认关闭的会话低风险授权和 30 秒缓冲；工具 Always allow 不代表配置授权。旧专用 PubMed 流程兼容保留。服务端定时器发现同 ID 的 v2 请求后，调用同一通用 revision/previewDigest 决定服务，不走旧单字段写入。

人工与自动决定串行；无效载荷不改决定来源。进入决定与正式领域提交前双重复核授权和 Stop，仍校验资源基线、连接器就绪/插件策略。关闭授权撤回自动意图；UI 按真实截止时间展示倒计时和拒绝入口。只限 Native run 绑定且白名单内的 PubMed，不表示“四个通用适配器都会到期批准”。本轮实际 30 秒用例见第 6 节，重启场景仍需独立验收。

### 4.2 JiuwenSwarm 与外部配置

已实现 `/agent/configuration/capabilities` 握手与固定基线信息，缺少能力时明确报告 false/禁用原因。`JiuwenConfigurationRuntimeBridge` 校验本地 run/request、上游 Session、确切 interrupt、检查点、generation 和恢复键；但实际 core 中断注册、持久宿主与恢复端点仍未接通。

固定 core 的提交顺序缺口已有固定 SHA 补丁提案及临时真实 handler 的顺序/失败验证，未改变用户部署。WorkSwarm 已有持久 checkpointer 和结构化 interrupt continuation；仍待我们开发实际 rail 注册、Session/interrupt 持久绑定、精确恢复、原工具刷新与事件桥接。因此当前结论是“基础合同已实现；上游提交顺序补丁待集成；宿主桥接待开发”，不是上游完全没有恢复能力。审计依据见[执行底座核验 v1 第 7～8 节](conversational-settings-runtime-baseline.v1.md)。

`HttpBackendControl` 和外部 worker/journal 已有条件写入、操作 ID、权威 receipt 与超时 query-only 合同。当前安装的 adapter 不提供对应 operations 宿主；普通 config.set 不作替代，未将真实远端资源注册为可覆盖型通用设置。Web/TUI/config/model/MCP/凭据入口的共享条件提交与权威查询是本项目待开发的控制面，不能归因于上游没有基础能力。

外部提交另有握手后派发前复核合同：`submit(operation, { beforeDispatch })` 必须在异步 capability 检查结束后再核对 Stop/版本/租约，拒绝则 suppressed 且零写请求。它修复“握手期间 Stop、随后仍提交”的窗口；实际宿主接入仍未完成。

旧 JiuwenSwarm/历史记录可以保留明确关联的后续任务路径。它们不能标成“同一 run 中间暂停”。新能力不达门槛就不开放对应承诺；契约测试成功也不是双执行器真实发布证据。

### 4.3 其他设置

新 HTTP/SSE MCP、OAuth、代理、超时、Runner、文件路径导入仍未注册。WorkSwarm 所有的凭据资源仍需接其权威草稿/凭据服务，不能复制本地密钥库充当远端正式来源。

## 5. 公共接口与工作包

`S=/api/sessions/:sessionId`，`R=S/configuration-requests/:requestId`。

| 接口 | 实际作用 |
|---|---|
| `GET S/configuration/capabilities` | 有限目录与执行器支持 |
| `POST S/configuration-requests` | 建立具体提案 |
| `GET S/configuration-requests`、`GET R` | 合并快照与独立请求恢复 |
| `POST R/credentials` | 专用加密草稿，响应 no-store |
| `POST R/preview` | 生成新 revision 与服务端摘要 |
| `POST R/decision` | 精确版本的人工批准/拒绝；受控服务端到期也复用同一服务 |
| `POST R/continue` | 只核对或补已保存决定的激活/恢复 |
| `POST R/reversal` | 支持的模型选择/连接器动作生成反向提案 |

开发 v7 第 18 节给出 DEV-CFG-01～10 完整任务表。当前本地公共层、四领域、前端和 Native 进入实际调用路径；DEV08/09 基础合同已实现，core 提交顺序补丁待集成，宿主桥接待开发。不得将这些工作包统一标成全部完成。

## 6. 验证记录与证据边界

### 6.1 已执行的本地验证

以下为本轮 Windows 隔离工作树的真实执行记录，测试使用临时 SQLite 与本地 HTTP stub，不使用用户真实 Key。

| 命令/验证 | 实际结果 | 证明范围 |
|---|---|---|
| `node --import tsx --test services/api/src/configuration/request-service.test.ts services/api/src/configuration/domain-adapters.integration.test.ts`（最后重跑） | 27 项通过，0 跳过 | 14 公共服务 + 13 实际 Store/Framework 领域集成；继承撤销、旧版本/Stop、profile 修复、receipt 故障回滚、到期终态及保持原 checkpoint、隐式过期/撤销草稿重填 |
| `node --import tsx --test services/api/src/configuration/credential-bridge.test.ts services/api/src/model-connectivity.test.ts` | 22 项通过，0 跳过 | 8 草稿生命周期/加密/lease + 14 协议/HTTP 真实 stub；307 无转发，401/429 不重试，UTF-8 64 KiB 超限中止 |
| `node --import tsx --test services/api/src/configuration/domain-adapters.integration.test.ts`（追加回归） | 10 项通过，0 跳过 | 原 8 项领域集成重跑，并新增自动决定 vs 无效人工摘要并发、验证中撤销授权的 2 项；受控验证闸门/提前截止时间，不替代真实 30 秒测试 |
| `node scripts/build-conversational-settings-page.mjs`；reader 测试 | 构建成功、9 项通过 | 全篇连续 HTML、当前章节侧栏、版本/图示及离线正文 |
| schema 与 artifact-manager 构建 | 通过 | 本轮依赖产物可构建 |
| API typecheck；`node services/api/node_modules/typescript/bin/tsc -p tsconfig.tests.json --noEmit --pretty false` | 通过 | API 与测试源码类型检查 |

测试的 tagged 声明包含实际本地主机平台；没有把“文件注册了零条用例”当作通过。临时 Store 在测试结束关闭数据库并删除所创建目录。Native、前端、HTTP 和 adapter/worker 的最终测试结果以根 WSL 汇总为准，不在这里提前计入通过数。

### 6.2 根流程 WSL 验证

本节只计入根流程已报告的本轮结果；尚未完成的完整套件不得当作全绿。

| 检查 | 本轮状态 |
|---|---|
| 隔离 Linux 工作树与依赖组合 | `/root/sciencediscovery-conversational-settings`，Node 22.19.0、Linux 依赖与 Linux SEA；排除 Windows dist，保留运行数据 |
| `pnpm build` | 本轮 WSL 全部构建通过 |
| API 包测试（最终全包回归内执行） | 1181/1181，0 跳过，729.94 秒；包括本轮新增配置测试与旧队列/权限/模型/存储回归 |
| Web 包测试（最终全包回归内执行） | 889/889，0 跳过；其中新增通用卡/API 的 18 项也有 Windows 定向记录 |
| `pnpm architecture:check`、`pnpm typecheck` | 均 exit 0；架构脚本 71/71，283 个 package 源文件边界检查通过；全仓源码与测试类型检查通过 |
| 初次完整构建与全包回归 | 构建与 binary:test 33/33 通过；`pnpm --recursive --no-bail test` 收齐 31 个包：2999 项中 2998 通过、1 失败、0 跳过。唯一失败是 executor 的真实 Docker/SSH 外部用例缺少 Docker（`spawn docker ENOENT`），历史结果保留 |
| 用户确认跳过 Docker 后的定向复核 | 仅修改外部测试入口，生产代码未变。WSL `pnpm --filter @sciencediscovery/executor test` 为 32 通过、0 失败、1 明确跳过，7.88 秒；测试源码类型检查通过。官方 collector 实际收集到 1 个 `status:external` 用例且没有 forbiddenSkip，专项计划仍可执行它。其他包沿用上轮完整回归结果，本次未重复运行整套 `pnpm test` |
| 通用 UI 自动倒计时及当前错误状态回归 | 18 项通过；不等于浏览器/商业模型端到端 |
| Native 真实 `/runs` 配置流程 | 应用、拒绝、Stop、实际 30 秒自动应用 4 项通过；真实 HTTP API/Native/MCP 调用路径，provider 为本地脚本服务、Runner 为 stub、MCP transport 为离线 fixture，不访问商业 LLM 或 PubMed 网络 |
| 新通用卡浏览器/商业模型闭环 | 4420 实际浏览器完成菜单→模型选择→新预览→确认→折叠→展开→刷新恢复；选择已有模型，无外部探测。商业模型与真实 PubMed 的新通用链路仍未重跑 |
| 服务健康、测试项目/会话/临时目录清理 | 新 WSL 服务 4420 的 `/health` status=ok，Linux Runner 就绪；memory-graph sidecar 本次未启用，显示 degraded。浏览器临时项目及会话已按精确 ID 删除，原有项目及共享模型保留 |

四项系统测试源文件为 `services/api/src/configuration/agent-runtime.integration.test.ts`，标记 `model:mock`。它们验证完整 API/队列/检查点/决定/装配/工具调用链；自动用例等待真实服务端约 30 秒，未从浏览器调用决定或继续接口。fixture 关闭自建 API/provider/Runner 并删除其 `.tmp/configuration-agent-*` 数据目录，未使用用户数据或 Key。

本轮新增 adapter 能力握手测试在独立 pytest 环境执行，3 项通过；该检查不启动 JiuwenSwarm，也不把当前 false 的运行保障变为 true。浏览器临时项目 `27c0fa39-d7e7-4b4b-97d1-e374805d641c`、显式创建会话 `6d603761-c930-4632-82ac-402c2b8b19ba` 已随该项目清理，并通过项目列表复核不存在。Windows 的既有 4410 实例未停止或修改；本任务刚启动的 WSL 4410 子进程已移至 4420，避免误测旧实例。

### 6.3 历史真实模型用例

2026-10-16、旧基线 `a88d9283`，曾用真实 DeepSeek-V4.1-Flash 验证主 Agent 的 PubMed 专用提案、持久暂停、人工确认、同一 run、单次 `limit:3` 实际检索、独立工具权限及带来源最终答复，临时项目已清理。

这证明旧纵向切片的限定闭环，不证明本轮四适配器、通用 UI、JiuwenSwarm、worker 或所有重启/Stop/自动应用场景。旧宽题约 250 篇检索也没有形成完整最终报告，不能据此宣称长任务问题全部解决。详细历史记录见状态 v6。

## 7. 评审、发布与下一步

评审可检查本地四领域完整链路和通用接入方式，重点核对用户看到的范围、批准的版本、原任务身份以及保存/生效/恢复的独立证据。本轮构建、类型、架构、Native 受控端到端和模型选择浏览器验证均已完成；用户已确认跳过 Docker/SSH 外部用例，按该范围交付。初次完整回归与修正入口后的定向复核分别记录，不能把跳过写成真实远程部署已通过。商业模型/PubMed 与 JiuwenSwarm 的完整新链路仍需分别验收。

后续先完成当前 Native/本地能力回归，再接通提供所需保证的 WorkSwarm 条件写入与凭据宿主，最后贯通 core interrupt 和同 run 科研输出。旧专用流程迁移、更多低风险自动适配器、新 MCP/OAuth/高影响设置分别列任务，不能用一个通用卡片声明全部已支持。

阅读页 v9 对应设计 v17、开发 v7、状态 v7；调研 v2 与读者规格 v6保留。所有当前正文仍同页连续滚动，侧栏跟随当前章节；旧版本保持可追溯。
