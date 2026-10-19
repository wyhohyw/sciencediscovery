# 对话内按需配置 · 完整开发文档 v4

- 日期：2026-10-19；产品依据：[完整设计 v11](conversational-settings.v11.md)，CFG-01～CFG-06、CFG-T01～CFG-T22。
- 本文是完整工程规格，整合基础架构、现有实现、接口、数据、恢复、扩展和验收；不要求先阅读开发 v1～v3。
- 核对范围：隔离分支 `codex/conversational-settings-integration`，运行实现基线 `a88d9283`。真实模型记录见[实现与实测 v2](conversational-settings-status.v2.md)。
- 标记约定：“现有”是已核对代码；“目标/待实现”是完整能力所需的工程要求。接口、表和类型不可因为出现在本文就当作已经存在。

## 1. 交付范围与技术决策

当前纵向切片包括：对话模型选择/首次连接、已安装连接器启用、主 Agent 提案、独立启用指令、连接器决定与回执、独立授权的低风险倒计时，以及原生执行器的同 run 持久暂停恢复。

完整通用框架后续覆盖 `model.select`、`model.connect`、`model.repair_credential`、`connector.enable`，再按领域接入新 MCP/OAuth、代理、超时和 Runner。任意 REST API、STDIO、宿主部署设置不自动获得支持。

关键决策：

1. 正式设置和凭据复用现有领域存储，提案不是第二套配置数据库。
2. 配置机制放在 API 层；Runtime Core 只表达通用执行边界，不依赖 provider、配置 DTO 或 HTTP。
3. Agent 有检查/提案能力，用户决定端点保持独立；不得向模型提供 bearer、凭据 handle 或批准工具。
4. 当前原生连接器走同一 run 恢复；关联新 run 只用于旧记录与尚未接入暂停钩子的 JiuwenSwarm。
5. 设置扩展使用有限能力目录和领域适配器，不提供任意 JSON Patch、写文件或执行命令接口。
6. 单独记录配置、激活和恢复结果；`applied`、`queued`、`running`、`completed` 含义不同。

## 2. 模块边界与代码入口

### 2.1 已有实现

| 位置 | 当前职责 |
|---|---|
| `packages/schema/src/configuration.ts` | 连接器请求与独立指令预检 DTO |
| `packages/schema/src/session.ts` | `configurationCheckpoint`、`configurationWait`、`configurationResume` 和旧关联字段 |
| `services/api/src/configuration/explicit-connector.ts` | 独立启用指令的确定性目标解析 |
| `services/api/src/store.ts` | 请求持久化、基线校验、配置决定、会话授权与恢复标记 |
| `services/api/src/http/index.ts` | 预检、请求列表、决定接口与调度衔接 |
| `services/api/src/runs/index.ts` | 主 Agent 工具、运行装配、检查点、暂停、决定注入、原 run 重新调度 |
| `services/api/src/runs/model-context.ts` | 形成闭合的模型/工具历史上下文 |
| `apps/web/src/configuration/ConversationalModelCard.tsx` | 已有模型选择与复用模型连接向导 |
| `apps/web/src/configuration/ConversationalConnectorCard.tsx` | 待确认连接器卡片 |
| `apps/web/src/configuration/ConversationalConnectorReceipt.tsx` | 基于 `ProcessRecord` 的只读可展开回执 |
| `apps/web/src/App.tsx` 与会话运行/轨迹模块 | 入口、事件、快照、按 run 归位和上下文隔离 |

### 2.2 待提取的公共机制

```text
主 Agent / 确定性诊断 / 用户主动入口
                ↓
能力目录 → 配置服务：检查、提案、预览、验证、应用
                ↓                         ↓
领域适配器 → 现有设置/凭据存储        检查点与激活协调器
                ↓                         ↓
       请求/操作/应用回执/outbox       现有 run 队列与事件
                ↓                         ↓
           schema 表单、卡片、可展开结果与设置页刷新
```

建议在 `services/api/src/configuration/` 内提取 registry、diagnostics、service、verification、adapters 与恢复协调端口；用户契约留在 schema 包，前端保留领域专用凭据组件。此目录规划不表示这些通用模块均已落地。

提取时先让现有模型/连接器切片调用公共服务，再接新适配器；不得长期保留两份配置值和两套不同的授权规则。

## 3. 通用能力目录和适配器协议

以下是目标内部端口，辅助类型须在实现时定义并校验：

```ts
interface ConfigurationAdapter<Input, Plan> {
  readonly id: string;
  readonly version: number;
  inspect(context: ReadonlyConfigurationContext): Promise<SafeCapabilityState>;
  normalize(input: unknown): Input;
  preview(input: Input, snapshot: ConfigurationSnapshot): Plan;
  verify(plan: Plan, context: ApprovedVerificationContext): Promise<VerificationResult>;
  apply(plan: Plan, transaction: ConfigurationTransaction): ApplyReceipt;
  previewReversal(receipt: ApplyReceipt, snapshot: ConfigurationSnapshot): Plan;
}
```

Registry 必须显式注册 schema、允许范围、敏感字段、验证预算、自动应用资格、生效方式与撤销支持。新增条目不默认加入自动应用白名单。

- `inspect` 脱敏、只读，不主动联网或遍历密钥库。
- `preview` 确定性、无网络/激活；列出目标主机、请求类型、次数预算、可能费用和作用范围。
- `verify` 只可访问已批准计划对应的凭据，不自行发现其他目标。
- `apply` 为事务内同步领域变化，不联网、不安装、不重启、不自行提交事务。
- `previewReversal` 根据 receipt 和最新版本构造反向提案；不直接覆盖。

生效方式至少区分：尚未启动任务、可重建工具后同 run 恢复、仅后续运行生效、需重启。只有实现并验证过的适配器可以声明对应能力；不能从 `applied` 推断热生效。

`connector.enable` 使用最新 effective 集合增加一项并保留其他项；不能把仅含新增 ID 的数组整表替换进去。模型选择与连接优先会话绑定，零模型首个默认按第 7 节例外处理。

## 4. 数据契约与状态

### 4.1 当前连接器请求

现有 `ConnectorConfigurationRequest` 包含：

```ts
interface ConnectorConfigurationRequest {
  id: string;
  sessionId: string;
  projectId: string;
  runId: string; // 独立配置指令使用 user-request:*，不是实际任务
  connectorId: string;
  connectorName: string;
  reason: string;
  baselineConnectorIds: string[];
  state: "awaiting_confirmation" | "applied" | "dismissed" | "stale";
  createdAt: string;
  updatedAt: string;
  continuationRunId?: string; // 原生恢复时可等于原 run ID
  autoApplyAt?: string;
  decisionSource?: "user" | "auto";
  stopGeneration?: number;
}
```

该 DTO 不提供通用 revision、字段 schema 或草稿密钥通道。不得把目标框架的丰富状态直接假设为当前 wire contract。

### 4.2 当前 run 扩展

```ts
configurationCheckpoint?: {
  requestId: string;
  history?: Array<Record<string, unknown>>; // 旧内联格式，只读兼容
  historyRef?: {
    pool: "agent-state";
    digest: `sha256:${string}`;
    size: number;
    mediaType: string;
  };
  stopGeneration: number;
  committedAt: string;
};
configurationWait?: boolean;
configurationResume?: {
  requestId: string;
  decision: "apply" | "dismiss";
  connectorName: string;
};
```

`SessionRun.status` 实际为 `blocked`，以 `configurationWait` 标明配置等待。`configuration_wait` 是等待语义，不是另一个 status 枚举值。旧 `configurationContinuation`、通知去重字段和旧内联检查点继续兼容读取。

### 4.3 目标通用请求

目标 DTO 增加 `capabilityId`、schema/适配器版本、`revision`、`stateVersion`、输入描述、脱敏差异、范围/影响、验证计划、`previewDigest`、凭据可用状态、应用结果、激活/恢复状态、错误与允许动作。

内部另存规范化计划、基线 overrides 的字段缺失信息、资源版本向量、凭据草稿引用、原任务与附件/产物引用、确认记录、验证 attempt、receipt 与恢复幂等键。

目标公共视图示意如下；它与第 4.1 节现有 DTO 是不同版本的合同，不可直接强制类型转换：

```ts
type ConfigurationCapabilityId =
  | "model.select" | "model.connect"
  | "model.repair_credential" | "connector.enable";
type ConfigurationScope =
  | { kind: "session" | "project"; id: string }
  | { kind: "global" };
type ConfigurationRequestState =
  | "collecting_input" | "awaiting_confirmation" | "validating"
  | "applying" | "applied" | "needs_fix" | "stale" | "failed"
  | "dismissed" | "cancelled" | "expired";
type ConfigurationFieldView = {
  id: string; label: string; required: boolean;
} & (
  | { kind: "credential"; status: "missing" | "provided" | "expired" }
  | { kind: "text" | "url" | "number" | "boolean" | "enum";
      value?: string | number | boolean;
      choices?: Array<{ id: string; label: string }> }
);
interface ConfigurationRequestView {
  id: string; sessionId: string; projectId: string; runId?: string;
  revision: number; stateVersion: number;
  capabilityId: ConfigurationCapabilityId;
  state: ConfigurationRequestState;
  reason: { code: string; message: string };
  scope: ConfigurationScope;
  fields: ConfigurationFieldView[];
  changes: Array<{ field: string; before?: string; after?: string }>;
  impact: { resourceLocation: string; effectiveScope: string; message: string };
  verificationPlan: {
    targets: string[]; actions: string[]; maxRequests: number; mayCharge: boolean;
  };
  previewDigest?: string;
  activation: {
    mode: "before_first_run" | "same_run" | "next_run" | "requires_restart";
    state: "not_requested" | "pending" | "queued" | "started"
      | "suppressed" | "needs_user_action";
    runId?: string;
  };
  error?: { code: string; message: string; field?: string; retryable: boolean };
  allowedActions: string[];
  createdAt: string; updatedAt: string; expiresAt?: string;
}
```

实现时将 capability、状态、字段类型和动作约束为共用 schema，按能力区分参数，拒绝未知字段；`changes` 只能是脱敏展示，不能作为可执行补丁。凭据字段没有明文 value 属性，浏览器的临时输入缓冲不属于卡片快照。

输入、范围、目标、验证动作或基线变化提升 revision 并废止旧确认；阶段进度只提升 stateVersion。DTO 必须白名单投影，不包含 secretRef、ciphertext、内部计划、原始 provider 响应或 credential handle。

通用状态目标为：

```text
collecting_input → awaiting_confirmation → validating → applying → applied
                         ↓                   ↓             ↓
                    dismissed             needs_fix      failed
另有 stale / cancelled / expired；激活和恢复状态独立保存。
```

已应用后恢复失败不改成“配置未保存”；撤销是新提案，保留原审计。未知提交结果先查 receipt，不直接改 failed 后重试。

## 5. 存储、版本和事务

### 5.1 现有边界

连接器请求存于现有 `catalog.sqlite` 的 `connector_configuration_requests`，配置沿用目录/会话领域存储。run 列表仍有文件写入边界；新检查点通过 `VersionStore.putRecord("ConfigurationCheckpoint", ...)` 保存不可变记录并在 run 中存引用。

当前已对连接器基线、请求状态、Stop generation 和重复决定做检查/幂等。配置变化、恢复入队和通知分别持久化，尚未形成通用跨存储事务；不能声称仅因使用 SQLite 和 CAS 就获得所有步骤的 exactly-once 保障。

### 5.2 目标持久记录

可在同一 SQLite 连接上增加：请求和不可变 revision、验证/应用操作、加密凭据草稿、资源版本、应用 receipt、事件/恢复 outbox。请求与操作分别有唯一幂等键；活动需求按会话/能力/目标 fingerprint 去重。

原会话停止 generation 应与既有通知/停止语义协调，避免另建互不一致的计数器。草稿凭据建议空闲 30 分钟、提案最长 24 小时；这是待实现默认值，不是当前连接器 DTO 已提供的有效期。

所有设置入口，包括设置页和 provider/model 删除，都需要更新相关资源版本。版本向量覆盖目标 overrides、父级继承、provider/model、凭据 generation、连接器安装/配置与策略指纹。不得存明文凭据 hash 充当版本。

### 5.3 目标条件提交

```text
事务外完成获准验证并记录 attempt
→ 运行安全边界和写入串行边界就绪
→ 根据最新目录准备候选变化
→ 同步事务内复核 revision/digest/资源版本/Stop/授权/有效期
→ 写配置、正式凭据、版本、applied receipt 与恢复 outbox
→ COMMIT 后切换内存目录、通知并处理恢复
```

事务内不 `await`，不调用会独立保存的公共 mutator。必须提取无独立提交的内部领域操作；顺序调用 createProvider、登记模型和写绑定不能自动视为一个原子事务。写失败要恢复内存目录并保证队列可再次工作。

单进程写入是当前基线；未经验证不宣称多 API 进程可以共同写同一目录。原生恢复 outbox 应引用既有 run ID 和检查点，不通过新建后续 run 规避同一 run 语义。

## 6. HTTP 与 Agent 工具

### 6.1 当前已存在的接口

前缀 `S = /api/sessions/:sessionId`，`R = S/configuration-requests/:requestId`。

| 方法与路径 | 当前请求/结果 |
|---|---|
| `POST S/configuration/preflight` | `{content}`；返回 `not_applicable`、`proposal`（含 request）、`already_enabled` 或 `unavailable` |
| `GET S/configuration-requests` | 返回 `{items}`；只在原 run 已具备检查点等待或兼容旧记录时暴露研究任务待确认卡 |
| `POST R/decision` | `{decision: "apply" | "dismiss"}`；返回请求结果并按需要恢复调度 |
| `POST S/runs`、`GET S/runs/:runId` 及事件接口 | 正常提交、状态和事件；配置恢复沿用原 ID |

当前决定路由是单数 `/decision`；开发 v1 拟定的 `/decisions` 不是现有实现。模型卡复用已有设置客户端和模型连接向导，没有把当前连接器 DTO 伪装成完整通用模型提案。

主 Agent 当前工具为 `propose_connector_enable({connectorId})`，只能从已安装、就绪、插件允许且当前尚未启用的目录中提案。工具登记请求后返回，不等待用户 Promise，不提供决定能力。

### 6.2 目标通用扩展

通用服务需要独立卡片快照、凭据草稿、无副作用 preview、带版本的决定、单独激活/恢复重试与反向提案能力。建议新增 `GET R`、`POST R/credentials`、`POST R/preview`、`POST R/continue` 和 `POST R/reversal`，并对现有单数决定接口按版本扩展；这些尚非已可调用契约。

目标 preview 输入包括已注册 capability、用户字段、范围、expectedRevision 和浏览器内存中的 credential handle；决定输入包括 revision、服务端 previewDigest、批准/拒绝和精确操作。凭据不能放进模型工具参数。

写接口支持持久 `Idempotency-Key`：同键同规范化载荷返回同结果，同键不同载荷返回冲突；不同键也不能重复应用同一 revision。不要把此目标断言冒充当前所有专用路由都已支持该请求头。

目标 Agent 工具 `inspect_configuration` 和 `propose_configuration` 只返回脱敏状态/请求 ID；拒绝任意 HTML、密钥、headers/env、URL userinfo、通用文件写路径和自定义适配器代码。

所有用户端点校验已有鉴权、session/project 归属与可写状态。审计没有独立用户身份时记录已认证客户端，不伪造多租户隔离。目标错误至少区分非法输入、旧预览、权限不足、资源变化、停止、凭据/提案过期和幂等载荷冲突。

| 目标 HTTP / code | 前端处理 |
|---|---|
| `400 INVALID_CONFIGURATION_INPUT` | 字段级错误，保留其余输入 |
| `401/403 UNAUTHENTICATED / CONFIGURATION_FORBIDDEN` | 应用访问权限处理，不误报成模型 API Key 失败 |
| `404 CONFIGURATION_REQUEST_NOT_FOUND` | 移除无效操作，不切换到其他会话 |
| `409 STALE_CONFIGURATION_PREVIEW / RESOURCE_VERSION_CONFLICT` | 获取新差异、重新预览和确认 |
| `409 IDEMPOTENCY_MISMATCH` | 保留原操作，拒绝同键换载荷 |
| `409 SESSION_NOT_WRITABLE / CONTINUATION_STOPPED` | 显示停止/归档原因，禁止旧提案自动恢复 |
| `410 CONFIGURATION_EXPIRED / CREDENTIAL_DRAFT_EXPIRED` | 重建提案或重新输入凭据 |
| `429 CONFIGURATION_RATE_LIMITED` | 显示重试时间，不密集自动验证 |

验证失败是操作结果而非统一 HTTP 500：卡片进入 needs_fix，使用认证、模型不存在、网络不可达、额度/限流、协议不支持或能力未验证等领域代码。当前专用接口的旧错误响应应在兼容迁移中适配，不能提前假定都已返回本表 code。

## 7. 模型引导、验证和凭据

### 7.1 当前引导规则

前端缺模型检查不依赖 LLM。空会话只保留轻量入口；提交非空草稿时打开卡并阻止 run 创建，确认后经正常路径只提交一次。取消保留草稿；无任务时不提交空 run。

`ConversationalModelCard` 复用 `ModelConnectWizard`。零模型直接进入连接向导，首次连接前固定并展示全局默认范围；写默认时读取并保留其他全局 overrides。已有模型时选择或新连接绑定当前会话。当前会话已通过继承取得首个模型时避免额外覆盖；并发变化应使旧绑定确认失效。

### 7.2 通用验证要求

连接验证按正式运行的 `apiProtocol/apiVariant` 生成请求，支持 provider 明确允许的 tokenOptional；不能根据 hostname 猜协议或构造伪 token。

目标验证计划包括固定、无用户数据的输入、主机与协议、有限次数、费用提示、超时与响应预算。建议连接单次默认 10 秒、响应 64 KiB，模型发现另设列表上限；这些作为统一验证器的待实现合同，不能替代对当前向导行为的单独核对。

默认不把凭据随重定向传到其他主机。模型 API 沿用模型层的外连/代理策略，不把 Runner 沙箱网络策略误当成全部 API 策略。错误分类区分认证、模型不存在、网络、额度/限流、协议和能力未知，回传有限脱敏摘要。

未知模型允许先授权有限发现，用户再选择实际结果并批准绑定；不支持发现时手填。发现授权不升级成保存授权，连接成功不证明视觉能力。外部验证已发出但结果未知时不自动重复可能计费调用。

### 7.3 凭据通道

```text
专用表单 → 后端加密草稿 → 获准验证器 → 正式凭据存储
Agent / 普通快照 / SSE / 日志：仅见 missing、provided、expired 等状态
```

通用草稿以请求、字段、目标、版本绑定，只在浏览器内存保留短期 handle；不得进入聊天状态、URL、持久浏览器存储或模型上下文。已有 provider 凭据只用于获准端点。正式提交后转存或清理，取消/过期清理未引用草稿，执行中的有界验证租约不能被清理器误删。

保持现有 CORS/鉴权边界，关闭敏感请求体日志，限制字段和大小。当前向导已复用后端密钥存储，但通用草稿接口、TTL、修复共享凭据和原子多资源提交尚未全部接入。

用户曾在普通消息中粘贴的凭据不因新增专用通道自动从历史消失；不在新提案或工具结果中复制明文，既存消息清理需要单独的历史管理流程。

## 8. 触发、去重与 Specialist 边界

独立指令预检只解析已安装连接器的 ID/显示名与完整肯定启用命令；名称不唯一、不认识、否定或复合任务返回不适用。已启用返回当前状态，未就绪返回不可用；其他普通任务继续走正常 Agent 流程。

独立指令请求用 `user-request:*` 区分，不对应研究 run，不生成 continuation。已有 applied 记录不能作为“仍启用”的判断依据，必须读取当前 effective 设置。待确认请求按会话和连接器复用。

主 Agent 的研究任务通过 `propose_connector_enable` 提案；不要把该模型工具路径描述为所有复合自然语言需求的确定性识别。专用工具仅提供给主 Agent，参数与目录由服务端校验。

Specialist 的现有预设连接器仍可与会话设置组合；本功能不改变该授权/装配规则。当前子 Agent 没有通用提案工具。未来如增加，所有提案仍需服务端归属、去重、决定与恢复协调，不能并行卡片各自覆盖设置。测试主 Agent 路径应禁用委派，另测 Specialist 既有路径。

## 9. 原生执行器持久暂停与同 run 恢复

### 9.1 暂停顺序

1. 创建持久连接器请求，提案工具返回 pending 结果。
2. 运行时的 `suspendAfterTurn` 在完整工具批次结束后交回控制，不依赖模型口头承诺停止。现有生命周期记录和工具结果应先提交。
3. 关闭模型历史中的 assistant/tool-call 配对，通过 CAS 写不可变 `ConfigurationCheckpoint`，含 requestId、history、Stop generation 和 committedAt。
4. run 写入匹配引用与 `configurationWait=true`，由调度器映射为非终态 blocked。
5. 列表接口确认 run/检查点就绪后才暴露待确认卡。此分支不写最终助手答复、不发 `run.completed`、不填完成时间。

等待不持有模型请求或长期 Promise，不依赖 SSE 连接。普通工具权限 blocked 必须与 `configurationWait` 区分。工具批次未完成时不能提前把配置当作已经生效。

### 9.2 恢复顺序

1. 人工或获准自动决定经同一服务复核请求、会话、基线、连接器与 generation。
2. 记录决定；确认时更新会话配置，并只把获批准连接器合入原 run 的冻结快照。拒绝不写连接器。
3. 原 run 持久标记 `configurationResume` 并重新入队，保留原 ID、原始 userMessageId、事件流与检查点。
4. 调度器在领取前复核决定状态、检查点关联和 generation；重建工具注册表，读取检查点历史并校验其 requestId/generation。
5. 通过可信内部上下文明确告知批准/拒绝结果和原任务继续语义。旧工具结果中的“待确认”是历史，不能让模型重新等待同一卡。
6. 从未完成位置继续；不重复创建用户原始提问，不重放已提交工具。最终答复只在原任务真正结束时生成。

原 run 恢复重新入队是执行状态变化，不是新的研究任务。恢复后的 `run.started` 事件也不能按“新用户问题”另插一组重复轨迹。

### 9.3 尚未完成的通用保障

现有 checkpoint 以模型历史为核心，并不等于已实现所有外部动作/产物的提交协议。通用协调器还应明确记录已完成动作、产物和附件引用、资源依赖版本、恢复位置与不确定外部提交。

未知副作用先查权威状态，否则需要用户处理；不能猜测成功、重新上传、重新付费或重复提交远程作业。冻结模型 ID 也不自动冻结共享 provider 地址与密钥依赖，跨类型恢复需增加依赖版本检查。

## 10. 决定、并发、队列与兼容

当前连接器应用检查会话可写、请求 pending、基线连接器集合匹配、连接器就绪与插件允许、Stop generation 有效。并发配置变化不能用旧完整数组覆盖；请求变 stale 时不写配置。重复决定返回既有结果，不重复恢复。

人工/到期决定、Stop 与配置写入须在明确写屏障里排序。后续用户消息可入队，不能使原配置请求的 generation 意外失效，也不能越过前面的配置等待 run。无关 queued run 的取消不能等价为整个会话 Stop。

Stop 先发生则禁止应用/恢复；提交先发生则保留 applied 事实，Stop 抑制恢复。会话归档/删除同样阻断旧请求。任务恢复领取前再检查，不仅在点击决定时检查。

JiuwenSwarm 与旧 `configurationContinuation` 继续使用有关联的后续 run；该分支沿用历史去重和来源校验，不迁移或篡改成同一 run。原生新请求不通过创建关联任务来伪装暂停。已失败模型 run 保持失败，未来修复不能追溯改其历史状态。

目标通用服务用持久应用回执与恢复 outbox 统一跨存储重试：原生 outbox 指向原 run，旧兼容路径需要新 run 时预分配候选 ID、在同一 run 文件写屏障幂等查找/创建。服务内部旧函数名包含 continuation 不意味着原生路径一定新建 run，应以持久 ID 核对。

## 11. 低风险自动应用与服务恢复

当前会话字段 `autoApplyLowRiskConfiguration` 独立于 `approvalMode`，默认 false。`isAutoApplicableConnector` 当前仅允许 PubMed，服务端控制资格，模型不能新增白名单。

提案时授权和白名单满足则记录 `autoApplyAt=createdAt+30s`。前端倒计时只展示；到期由服务端检查授权、基线、连接器/插件、会话、Stop generation 和任务状态，经同一决定服务应用并恢复。浏览器关闭不能取消服务端既有授权，进程重启应恢复未决计时。

关闭授权应清除/抑制到期操作，使请求回到人工确认。到期与人工确认只有一个结果，重复扫描读取已有状态。拒绝、取消、Stop 或前提失效均阻止自动应用。启用后的工具仍按自身权限策略执行。

| 中断窗口 | 恢复要求 |
|---|---|
| 工具批次未结束、检查点未提交 | 不开放确认；核查实际执行状态，不重放未知动作 |
| 检查点与 blocked 已提交、尚未决定 | 恢复同一 run 与待确认卡 |
| 已应用、恢复尚未入队 | 依据持久决定和匹配检查点幂等补齐恢复；通用 outbox 仍待加强 |
| 原 run 已 queued/running/terminal | 关联原 ID，不另建第二个任务或重复执行 |
| 外部验证发出而结果未知 | 不自动重发，标明未知并等待明确处理 |
| Stop 发生、旧到期操作再次被领取 | generation 校验失败，不唤醒 |

普通崩溃中的 running/工具权限等待保留各自恢复策略，不能把所有 blocked 都当成配置暂停。重启支持与自动到期都需独立故障测试，不能只根据人工确认真实用例推定通过。

## 12. 前端、事件与设置同步

卡片展示按 session/run/request 绑定，决定前恢复实体快照，运行过程继续使用既有事件流。当前实现不能假设已经有全部通用 `configuration.required/updated/resolved` 事件；这些可作为后续带 revision/stateVersion 的 DTO 事件设计。

| 状态 | 展示与动作 |
|---|---|
| 无模型、未打开卡 | 轻量入口；可输入草稿 |
| 待确认连接器 | 完整卡，范围、任务等待、确认/拒绝与可选倒计时 |
| validating/applying（通用目标） | 明确阶段，禁止重复提交；保留 Stop |
| applied/dismissed/stale | `ProcessRecord` 默认折叠的一行只读结果，可展开详情 |
| 配置已保存、恢复未开始 | 如实显示排队或待处理，不能显示任务完成 |
| 工具权限 blocked | 独立权限卡，不重复完整配置卡 |
| 最终任务结束 | 单次最终助手答复，轨迹可回看 |

决定后隐藏可操作卡并归位到原 run，避免同时在历史和底部显示重复回执。展开状态 key 使用 request 稳定 ID。回执不能替代权限，也不能把已应用错误标为任务已完成。

异步请求捕获 sessionId/requestId，SSE 核对 session/run，再更新当前视图。会话切换不得混入旧卡、旧工具输出或文件。设置提交后通过既有加载入口刷新模型列表、会话设置和来源；避免设置页与对话显示两份状态。

目标事件与普通 DTO 一样脱敏，用 ID/version 去重；刷新以快照补齐，SSE 重放不能重新调用决定接口。表单 label、错误提示、状态播报、键盘展开、窄屏与 i18n 纳入验收。

## 13. 撤销、迁移、上线与回退

通用撤销创建反向提案，复核资源版本并恢复原字段缺失/继承；默认只撤销绑定，保留新建共享资源。已被引用资源不因一次撤销删除。外部验证、已发送请求和业务动作不能撤销。当前没有完整通用撤销服务。

迁移采用新增表/可选字段与独立 schema 版本；不占用未经核对的全库版本号，不整体重写旧 run/事件。旧检查点和关联后续记录继续可读，历史语义不被篡改。

完整框架可另设发布开关，但该开关及默认值必须真实实现后才写入部署合同。关闭新增功能时应仍能读历史、取消等待和清理草稿，不能隐式回滚已保存配置。

删除会话清理其请求、未引用凭据草稿、通知和相关恢复记录，不顺带删除全局 provider。物理清理 CAS 遵循既有引用/保留策略。普通测试只按明确 project/session ID 清理。

降级前先抑制/排空待恢复操作、清理草稿并备份实际数据目录；旧二进制忽略新字段不等于调度安全。前后端同步发布，验证 schema、决定、快照和历史显示兼容。

日志只记录 request/run ID、版本、能力、范围、字段名、耗时与结果，不记录密钥、handle、认证 URL 或原始 provider 响应。正常配置等待不计作模型故障，验证失败、应用失败、恢复失败与用户拒绝分别统计。

## 14. 开发拆分与完成条件

| 工作包 | 交付与状态 | 完成条件 |
|---|---|---|
| DEV-CFG-01 通用契约 | 现有专用 DTO 已有；通用 schema/revision 待提取 | 领域与 DTO 投影分离，兼容现有 singular decision |
| DEV-CFG-02 配置服务 | 现有连接器链路可复用；Registry/适配器与统一版本待完善 | 设置页与对话共用条件写入，保留其他字段 |
| DEV-CFG-03 验证/凭据 | 已有模型向导；通用草稿、认证修复、计划授权待补齐 | 协议/无密钥、脱敏、最小探测、原子资源绑定通过 |
| DEV-CFG-04 运行协调 | 原生连接器同 run 已实现；通用 outbox/不确定提交待加强 | 无提前最终答复，故障/Stop/重复决定不重复执行 |
| DEV-CFG-05 前端 | 模型/连接器卡、折叠回执已实现；schema 表单待抽取 | 快照/事件一致，范围/状态准确，键盘/i18n 可用 |
| DEV-CFG-06 Agent 接入 | 主 Agent 连接器工具和独立指令已实现 | 通用 inspect/propose 有限可见，Specialist 既有规则保留 |
| DEV-CFG-07 验收发布 | 有限定真实模型闭环；完整门槛未全部完成 | 全部承诺能力逐项验收，完整 WSL 检查与清理记录齐全 |

实施顺序：提取现有切片公共层 → 补齐基线/回执/outbox → 模型修复与诊断 → 新设置适配器 → 完整发布验收。每包可独立评审；不能把已有单个连接器成功当成全部四类能力或通用框架完成。

## 15. 测试矩阵与执行方式

### 15.1 产品与工程映射

| 设计验收 | 测试层 | 关键断言 |
|---|---|---|
| CFG-T01～05f | settings/store、模型卡、连接 API、旅程 | 默认继承；不初始弹卡；双入口；零模型全局默认；原草稿只提交一次 |
| CFG-T06～07 | DTO/API/日志/前端 | 凭据不泄漏、Agent 无决定能力、保存/使用范围真实 |
| CFG-T08～10 | 决定服务、并发与恢复 | 拒绝、去重、重放、旧基线、重启与重复决定 |
| CFG-T11～13 | native/runtime、调度、故障注入 | 同 ID/原用户消息；区分提交/恢复失败；Stop/归档/切会话 |
| CFG-T14～15 | 适配器、反向提交、前端 | 继承撤销、不删共享资源、窄屏/键盘/中英文 |
| CFG-T16～19 | 预检、Agent、真实工具与轨迹 | 独立指令/复合任务分流；检查点先于卡；一次最终答复；回执可展开 |
| CFG-T20～22 | 服务端时钟/重启、权限、Specialist | 独立授权、白名单、30 秒竞态、保留子 Agent 既有设置 |

额外故障断言包括：同幂等键不同载荷、配置后恢复前退出、CAS 写后 run 引用前退出、queued 后 Stop 再重启、并行工具未结束、外部提交未知、父级/凭据/安装版本变化，以及一次写失败后队列仍可用。

当前相关测试入口包括 `services/api/src/configuration/explicit-connector.test.ts`、`runs/index.test.ts`、`runs/model-context.test.ts`、`store.test.ts`、`server.test.ts`、native-agent 测试和 `apps/web/tests/` 中的配置相关组件测试。新增文件必须进入包实际测试发现规则，不仅写一个无人执行的文件。

### 15.2 环境与命令

运行时验证在隔离 WSL Linux 树中执行，不能把 Windows 依赖目录复制过去替代 Linux 依赖。同步排除实际数据目录、node_modules、.git、.tmp，禁止未经确认 `rsync --delete`。本任务使用独立分支/目录和端口，不操作用户主服务数据。

适用命令如下；这是开发验证要求，本次完整版文档整合不宣称重新跑过产品全量测试：

```bash
pnpm build
pnpm --filter @sciencediscovery/api test
pnpm --filter @sciencediscovery/web test
pnpm architecture:check
pnpm typecheck
pnpm test
```

`pnpm test` 本身包含 build/binary/workspace 测试，记录一次完整结果即可。使用当前 tagged E2E 清单与 `pnpm ci:e2e` 配置隔离环境；服务地址和 token 由环境提供，不写入文档或测试代码。普通 CI 使用有限假 provider，不隐性依赖用户真实凭据；真实模型试验独立标记。

### 15.3 真实闭环

准备真实模型、仅启用 Web 的会话和已安装就绪的 PubMed；要求主 Agent 不委派，限定一次检索三篇。检查提案、blocked/检查点先后、同一 run 的决定/恢复、独立工具权限、真实 PubMed 结果与最终回复。随后分别测试拒绝、Stop、重启、重复决定和自动应用，不能用一次人工确认覆盖全部场景。

测试后只清理记录下来的明确临时 ID，保留用户会话与共享模型，记录服务健康、清理和未跑检查。大型研究题/过大工具输出的模型行为独立验证，不归为简单配置闭环已解决。

## 16. 实施状态、交付记录与版本

2026-10-16 的真实模型用例完成了主 Agent 提案、持久暂停、人工确认、同 run 恢复、独立检索授权、单次 `limit: 3` 检索和带 PubMed 来源的最终答复。测试项目清理完成；未使用子 Agent 或 shell。该结果仅证明限定用例。

完整交付需逐项提供：实现分支/commit、适配器支持表、schema/迁移和回退影响、CFG 验收结果、故障与并发证据、WSL 命令和结果、真实/模拟边界、健康检查与临时数据清理。未执行、失败或依赖环境阻塞的项目分别列明。

本版将开发 v1 的领域契约、安全/版本/凭据/测试要求，v2 的独立授权与服务端倒计时，v3 的同 run 检查点恢复统一重写，并校正当前单数决定接口、DTO、Specialist 规则与实现缺口。本文为新的完整开发入口，旧文档只作历史追溯；本次整合不改变运行时代码或扩大现有权限。
