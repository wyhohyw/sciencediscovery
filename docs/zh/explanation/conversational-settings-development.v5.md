# 对话内按需配置 · 完整开发文档 v5

- 日期：2026-10-19；产品依据：[完整设计 v15](conversational-settings.v15.md)，CFG-01～CFG-06、CFG-T01～CFG-T28；复用依据：[竞品与底座调研 v2](conversational-settings-research.v2.md)。
- 本文是完整工程规格，保留 v4 的安全、数据、接口和恢复合同，增加已有能力复用清单、依赖能力门槛、外部控制面提交与双执行器桥接。保留[开发 v4](conversational-settings-development.v4.md)供追溯。
- 核对范围：隔离分支 `codex/conversational-settings-integration`；运行实测仍以 `a88d9283` 及[实现与实测 v5](conversational-settings-status.v5.md)为准。文档新增目标端口与验收不表示已实现。
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
7. 先复用现有模型/凭据/配置领域服务；JiuwenSwarm 执行路径优先桥接 core interrupt/resume，不另造 Python 中断引擎。Native 继续使用本项目现有检查点实现。
8. 区分本地事务写入和外部控制面写入；远端 config.set、连接和重载不能包进本地同步事务，也不能宣称跨存储原子提交。
9. 能力按固定依赖组合与执行器探测结果开放；仅发现同名 API 或上游新版本存在某模块，不构成支持证据。

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

### 2.3 复用清单与新增模块责任

| 现有能力及来源 | 接入方式 | 新增责任与限制 |
|---|---|---|
| ModelConnectWizard、模型协议/验证层、正式凭据存储 | 直接调用本项目已有模块 | 统一请求/预览与模型绑定，不另写 provider SDK 或复制密钥库 |
| connector 请求、effective 设置、ProcessRecord、Native 检查点/调度 | 先提取再复用 | 保留当前行为，通用化 revision、回执、激活与恢复端口 |
| WorkSwarm token-schema / credential / connect | 后端控制接口加字段映射；凭据直达资源所属存储 | 新增 schema-normalizer 与 credential-bridge；不得把上游 UI store/账户上下文移植进本项目 |
| WorkSwarm config/model/MCP 管理与 reload | 经窄的 backend-control 适配端口调用 | 新增范围白名单、条件提交/幂等封装、权威结果查询和生效核对 |
| core ConfirmInterruptRail / AskUserRail / InteractiveInput / checkpointer | JiuwenSwarm 执行路径注册与调用 | 新增 runtime-bridge，持久映射 request/run/interrupt/Session，接本地 Stop 和队列 |
| WorkSwarm 结构化交互与 continuation 合同 | 适配上游响应/恢复格式，不通过解析聊天文字对接 | 校验确切中断身份，分别处理提问、工具权限与配置提案 |
| Codex/OpenCode 的请求状态与 WorkBuddy 的按需连接体验 | 设计借鉴，不新增运行时依赖 | 沿用本项目请求、卡片和事件，实现解析、决定与完成的公共合同 |

目标新增模块集中在现有 `configuration/` 内：registry/diagnostics、schema-normalizer、request-service、credential-bridge、backend-control、operation-worker、activation-coordinator 和 runtime-bridge。模块名是规划，尚非可导入路径。

其中 request-service 只负责计划/授权/版本/回执，领域服务负责实际领域规则，operation-worker 处理外部提交，activation-coordinator 处理连接/重载/工具目录生效，runtime-bridge 处理执行恢复；不能用一个工具函数同时收密钥、改远端配置并新建后续 run。

### 2.4 固定依赖与后端能力门槛

| 基线 | 固定版本 | 本次可以据此决定什么 |
|---|---|---|
| 仓库默认 WorkSwarm 安装标签 | workswarm0.2.6：`70aa6715992d9cb2c3ec81ee35991d13747e5ce0`；其 core 依赖为 `ad0c9dfbbd577423cc60f73edffa0a4699cc53f9` | 已核对凭据 Schema 和连接流程存在；当前部署仍需读取实际版本及已应用兼容补丁 |
| 调研 WorkSwarm develop | `1372eda94970bf91d5dc0cb03d784e971f39b991`；其 core 依赖为 `f9f50368d526dde64cd3ad10473956b660a50694` | 可作为新桥接候选，不自动升级安装脚本 |
| 调研 core develop | `1fe6a03690766e64a686ac51e873495a617b5ca5` | 中断持久化顺序与恢复合同的来源；不等于任一 WorkSwarm 组合已通过验证 |

固定源码与来源见调研第 5～7、10 节。本次文档更新不改变依赖。实施前记录实际组合、补丁集和协议版本；通过无副作用的能力握手或服务端固定能力清单取得：可写字段/范围、schema 版本、credential 提交通道、条件写入、操作回执/状态查询、工具目录刷新、持久 interrupt 与精确 resume 支持。

能力按设置类型与执行器求交集。关键能力不满足时返回明确不可用原因，或仅开放已经声明的 next_run/requires_restart 模式；不能仅凭版本字符串、global reload 成功或 API 名称宣称 same_run。握手格式与 bridge API 本身均待实现。

已有 `/agent/jiuwenswarm-config` 只允许搜索设置，不扩成原样透传 config.set 的万能端点。Web/TUI 同名 RPC 按各自 schema 映射；每个新增字段都登记资源归属、作用范围与版本检查方式。

## 3. 通用能力目录和适配器协议

以下是目标内部端口，辅助类型须在实现时定义并校验：

```ts
interface ConfigurationAdapterBase<Input, Plan> {
  readonly id: string;
  readonly version: number;
  inspect(context: ReadonlyConfigurationContext): Promise<SafeCapabilityState>;
  normalize(input: unknown): Input;
  preview(input: Input, snapshot: ConfigurationSnapshot): Plan;
  verify(plan: Plan, context: ApprovedVerificationContext): Promise<VerificationResult>;
  previewReversal(receipt: ApplyReceipt, snapshot: ConfigurationSnapshot): Plan;
}
interface LocalConfigurationAdapter<Input, Plan>
  extends ConfigurationAdapterBase<Input, Plan> {
  readonly persistence: "local_transaction";
  apply(plan: Plan, transaction: ConfigurationTransaction): ApplyReceipt;
}
interface ExternalConfigurationAdapter<Input, Plan>
  extends ConfigurationAdapterBase<Input, Plan> {
  readonly persistence: "external_control";
  prepareExternalWrite(plan: Plan, transaction: ConfigurationTransaction): ExternalOperationRef;
}
type ConfigurationAdapter<Input, Plan> =
  | LocalConfigurationAdapter<Input, Plan>
  | ExternalConfigurationAdapter<Input, Plan>;
```

Registry 必须显式注册 schema、允许范围、敏感字段、验证预算、自动应用资格、生效方式与撤销支持。新增条目不默认加入自动应用白名单。

- `inspect` 脱敏、只读，不探测用户填写的外部目标或遍历密钥库；外部适配器可读取已登记控制面的受控状态，实际连接验证仍需按获准计划执行。
- `preview` 确定性、无网络/激活；列出目标主机、请求类型、次数预算、可能费用和作用范围。
- `verify` 只可访问已批准计划对应的凭据，不自行发现其他目标。
- 本地 `apply` 为事务内同步领域变化，不联网、不安装、不重启、不自行提交事务。
- 外部 `prepareExternalWrite` 只在本地同步事务中保存获准操作计划与 outbox 引用，不调用远端；后续 worker 调用受控的外部接口并核对结果。外部配置的 applied 必须等远端权威回执，不能由本地 outbox 写入代替。
- `previewReversal` 根据 receipt 和最新版本构造反向提案；不直接覆盖。

生效方式至少区分：尚未启动任务、可重建工具后同 run 恢复、仅后续运行生效、需重启。只有实现并验证过的适配器可以声明对应能力；不能从 `applied` 推断热生效。

`connector.enable` 使用最新 effective 集合增加一项并保留其他项；不能把仅含新增 ID 的数组整表替换进去。模型选择与连接优先会话绑定，零模型首个默认按第 7 节例外处理。

### 3.1 声明式字段的复用与归一化

WorkSwarm/连接器包的 token-schema 可作为输入源，转换到本项目 `ConfigurationFieldView`。保留稳定 key、label、必填、类型、非敏感默认值和获准帮助链接；上游 password 映射为 credential 状态，不复制明文/default token。当前已核对的 text/password 之外类型，不按名称猜测支持，必须由领域注册显式映射。

Schema 来源限定为受支持的适配器或连接器包，不接受 LLM 自定义 schema、HTML、脚本或提交地址。未知字段、类型、重复 key 与不匹配的凭据占位符返回接入错误；链接协议和目标由服务端策略限制。前后端分别验证字段、大小与枚举值，字段归一化不能降低领域验证。

### 3.2 每个资源只保留一个正式来源

本项目模型使用本项目模型/凭据存储；WorkSwarm 所有的远端连接资源使用其领域控制面/凭据存储。本地只保留映射、非敏感版本和结果摘要，不把同一正式密钥或有效配置镜像成第二份权威值。Session 绑定与继承继续由本项目控制。

适配器须声明持久化归属、可写范围、验证预算、是否能查询实际生效版本、是否支持条件写入与幂等。目标激活合同至少返回 `effectiveRevision` 或工具目录指纹及目标执行器关联；如果后端只能声明“重载已安排”，状态保留 pending，不能标 active 或恢复任务。

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

复用上游时再保存内部 `backendKind/backendRevision`、外部 operation ID 与状态查询信息、配置 receipt、激活 receipt，以及 executor/外部 Session/interrupt/checkpoint 的绑定。外部 opaque ID 只在服务端使用，不能成为 Agent 的批准凭证；不把上游问题原始 payload 直接放入通用 DTO。

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
  description?: string; placeholder?: string;
  help?: { label: string; url: string }; // 只允许已校验的静态帮助链接
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
    state: "not_requested" | "pending" | "active" | "failed"
      | "suppressed" | "needs_user_action";
    effectiveRevision?: string;
  };
  resumption: {
    state: "not_requested" | "pending" | "queued" | "started"
      | "completed" | "failed" | "suppressed" | "needs_user_action";
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

上表将 activation 和 resumption 分开：active 表示已核对目标配置生效；queued/started/completed 表示研究 run 的执行进度。外部写入超时进入内部 `external_unknown`，公共请求保持 applying 并提供可理解错误；允许动作仅含查询、停止接续或人工核对，不提供盲目重新提交。

## 5. 存储、版本和事务

### 5.1 现有边界

连接器请求存于现有 `catalog.sqlite` 的 `connector_configuration_requests`，配置沿用目录/会话领域存储。run 列表仍有文件写入边界；新检查点通过 `VersionStore.putRecord("ConfigurationCheckpoint", ...)` 保存不可变记录并在 run 中存引用。

当前已对连接器基线、请求状态、Stop generation 和重复决定做检查/幂等。配置变化、恢复入队和通知分别持久化，尚未形成通用跨存储事务；不能声称仅因使用 SQLite 和 CAS 就获得所有步骤的 exactly-once 保障。

### 5.2 目标持久记录

可在同一 SQLite 连接上增加：请求和不可变 revision、验证/应用操作、加密凭据草稿、资源版本、应用 receipt、事件/恢复 outbox。请求与操作分别有唯一幂等键；活动需求按会话/能力/目标 fingerprint 去重。

原会话停止 generation 应与既有通知/停止语义协调，避免另建互不一致的计数器。草稿凭据建议空闲 30 分钟、提案最长 24 小时；这是待实现默认值，不是当前连接器 DTO 已提供的有效期。

所有设置入口，包括设置页和 provider/model 删除，都需要更新相关资源版本。版本向量覆盖目标 overrides、父级继承、provider/model、凭据 generation、连接器安装/配置与策略指纹。不得存明文凭据 hash 充当版本。

### 5.3 本地领域存储的条件提交

```text
事务外完成获准验证并记录 attempt
→ 运行安全边界和写入串行边界就绪
→ 根据最新目录准备候选变化
→ 同步事务内复核 revision/digest/资源版本/Stop/授权/有效期
→ 写配置、正式凭据、版本、applied receipt 与激活 outbox
→ COMMIT 后切换内存目录、通知，取得生效证据后安排原 run 恢复
```

事务内不 `await`，不调用会独立保存的公共 mutator。必须提取无独立提交的内部领域操作；顺序调用 createProvider、登记模型和写绑定不能自动视为一个原子事务。写失败要恢复内存目录并保证队列可再次工作。

单进程写入是当前基线；未经验证不宣称多 API 进程可以共同写同一目录。原生恢复 outbox 应引用既有 run ID 和检查点，不通过新建后续 run 规避同一 run 语义。

### 5.4 外部控制面提交与激活

WorkSwarm 的 config.set、model/MCP 保存自行持久化，不能加入本地同步 SQLite 事务。外部适配路径使用以下待实现协议：

```text
获准验证 + 暂停/版本/Stop 检查
→ 本地事务保存不可变决定、外部 operationId 和提交 outbox（尚非 applied）
→ worker 领取租约并复核范围、版本、Stop 与远端条件写入能力
→ 提交受控远端修改（operationId、expectedBackendRevision、获准计划）
→ 查询/核对权威配置 receipt，本地记 applied 并生成激活 outbox
→ 连接/重载/重建工具，核对目标生效 receipt
→ 持久安排原 run 恢复，再标记实际恢复结果
```

受控 backend-control 需提供逻辑上的 inspect、submit、queryOperation 和 activate 端口；这不是宣称 WorkSwarm 已有同名 HTTP API。现有 RPC 没有条件写入或幂等键时，需由宿主扩展统一提供版本、去重和回执，并让设置页等所有写入口遵循同一约束。仅在本地加锁再远端 get/set 无法防止远端其他入口竞争；缺少这些保证的覆盖型操作不得开放。

外部执行至少按 `(backend, operationId)` 去重，且 revision/计划摘要绑定。收到超时先查权威状态；只有权威服务保证该操作未执行且可安全重新提交时，才以原 operationId 重试。没有可靠查询或幂等支持时保持未知，交人工核对，不能把 readback 值相同当作所有多步副作用均已完成。

正式配置仍归远端所有。本地 applied receipt 记录已观察的远端提交事实；读取后还需复核目标版本，激活前变化应重新规划或停止接续。正式配置、凭据和 Session 绑定跨不同存储时分别记阶段，不承诺整体原子，也不擅自回滚已被其他会话引用的资源。

连接探测、MCP 启动、reload 和工具注册均在事务外执行，按批准的目标/预算和作用范围处理。全局 reload 若影响其他任务，必须在 preview 明示且采用获准安全边界；后端无法隔离时禁止承诺仅本会话热生效。

Stop 与已领取外部操作的检查使用调度租约/取消边界排序。Stop 后禁止新领取和新外部调用；已经发出的调用可能仍完成，应核对并记录已保存事实，抑制后续激活/恢复，不能宣称能撤回已发出的网络请求。

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

### 7.4 复用 WorkSwarm 凭据服务的边界

目标 credential-bridge 复用 `credentials_required` 与字段描述，将表单提交路由到该资源的权威凭据服务。已有正式凭据通过服务端引用使用，不返回明文给浏览器或模型。新输入通过本项目鉴权后的专用端点传输，禁记请求体；普通 ask_user/InteractiveInput 只传配置决定或字段可用状态。

若现有 WorkSwarm 接口会“保存凭据并立即连接”，不能把它当作无副作用的草稿上传：只有当前预览已覆盖该存储范围、目标、连接行为和预算，且用户已批准时才能调用。未获批准的新凭据留在所属后端的隔离草稿；后端没有草稿能力时补受控接口，或在确认前只保留浏览器临时输入，不提前永久保存。取消/刷新后的行为应明示。

该类保存并连接接口属于外部应用操作，不能塞进 verify 阶段以规避提交回执。修复现有共享凭据时须先验证隔离候选再受控绑定；后端无法保护原有效配置时，不开放该修复能力。

对同一资源不同时写两份正式凭据。外部服务无法提供所需草稿/引用/字段隔离时，该凭据类型不开放通用接入。配置文件和 OAuth URL 不通过文本提问交给 LLM 解析；OAuth 引导可复用已有认证服务，但回调必须绑定用户、请求、目标与有效期，不能把收到一个回调当成其他请求的授权。

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

### 9.4 两种执行器的目标恢复端口

下列为待实现的内部端口，位于 API/adapter 边界；opaque 引用不进 Agent 工具或普通公共 DTO：

```ts
interface ConfigurationRuntimeBridge {
  describe(context: RunContext): Promise<RuntimeConfigurationCapabilities>;
  persistWait(input: ConfigurationWaitInput): Promise<DurableWaitReceipt>;
  activate(input: ApprovedActivationInput): Promise<ActivationReceipt>;
  resume(input: BoundResumeInput): Promise<ResumeReceipt>;
  cancel(input: BoundCancelInput): Promise<void>;
  reconcile(input: BoundRecoveryInput): Promise<RecoveryReceipt>;
}
```

- describe 按固定依赖与实际后端能力返回 same_run 等支持范围，不能触发设置修改。
- persistWait 只有执行器检查点和本地 request/run 等待状态均已持久化后才返回可展示回执；部分成功由 reconcile 继续核对，保存失败不开放确认。
- activate 只使用批准的增量与有效版本，重建/刷新目标工具目录并返回证据；不能顺便加载等待期间出现的其他未经批准设置。
- resume 校验原 run、checkpoint、Stop generation、配置 revision、激活 receipt 和确切外部中断，使用唯一恢复键；返回 queued 不代表实际 started。
- cancel 使本地恢复票据失效，并尽力取消上游等待；reconcile 核对已提交事实后只补未完成阶段，不重写已应用配置或再生成研究 run。

Native 端口包装第 9.1～9.2 节现有行为。JiuwenSwarm 端口通过现有 adapter 与上游 Session/checkpointer 对接，不把 Python 对象序列化到 TypeScript run 历史。

### 9.5 JiuwenSwarm 基于 core interrupt 的接续顺序

1. 配置提案桥接在确定的主 Agent 工具调用处注册中断，绑定本地 project/session/run/request/revision、上游 Session、工具调用 ID、配置相关 interrupt ID、检查点引用与 Stop generation。不要在工具已经返回后伪造一个无法恢复的 interrupt。
2. core 返回 interrupt 时，宿主确认已提交持久化检查点；本地提交匹配的 blocked/configurationWait 后才显示卡。底层一次 invoke 返回 interrupt 不代表研究 run 完成，不生成最终助手答复。
3. 用户决定走本项目配置服务。确认时按本地/外部适配器提交，取得生效证据后再恢复；拒绝时不写配置，准备有限拒绝结果。
4. runtime-bridge 读取绑定的原上游 Session 和待处理中断，使用 InteractiveInput 提交到确切的配置 interrupt。若同批还存在其他提问或工具权限中断，只处理获准集合，不能批量放行剩余项。
5. core 可能重进被中断的外层工具。该桥接工具读取已有决定/receipt 返回批准或拒绝结果，不再执行配置写入、建连接或另创建提案；业务写入始终由配置服务负责。
6. 后续输出接入原研究 run 的事件流，原 userMessageId 与 run ID 不变，只有研究真正结束才转终态。底层多次 invoke 用独立 attempt 记录，不另造产品 run。

恢复必须匹配持久化的身份与有效 generation。上游中断不存在、工具目录刷新不可确认或检查点已失效时返回独立恢复错误并保留配置事实；不退回发送一句“继续”的普通聊天。新自由文本是否丢弃上游等待须经本项目队列策略协调，不能让上游默认行为越过正在等待配置的原任务。

升级候选需验证 core 的“先 commit_interrupt 再输出卡”保证及宿主 checkpointer 跨重启恢复；仅注册 ConfirmInterruptRail 或在内存保存 interruption state 不足以开放同 run 模式。旧标签与新开发分支分别执行合同测试。

## 10. 决定、并发、队列与兼容

当前连接器应用检查会话可写、请求 pending、基线连接器集合匹配、连接器就绪与插件允许、Stop generation 有效。并发配置变化不能用旧完整数组覆盖；请求变 stale 时不写配置。重复决定返回既有结果，不重复恢复。

人工/到期决定、Stop 与配置写入须在明确写屏障里排序。后续用户消息可入队，不能使原配置请求的 generation 意外失效，也不能越过前面的配置等待 run。无关 queued run 的取消不能等价为整个会话 Stop。

Stop 先发生则禁止应用/恢复；提交先发生则保留 applied 事实，Stop 抑制恢复。会话归档/删除同样阻断旧请求。任务恢复领取前再检查，不仅在点击决定时检查。

JiuwenSwarm 与旧 `configurationContinuation` 继续使用有关联的后续 run；该分支沿用历史去重和来源校验，不迁移或篡改成同一 run。原生新请求不通过创建关联任务来伪装暂停。已失败模型 run 保持失败，未来修复不能追溯改其历史状态。

这是当前兼容行为。第 9.5 节新桥接通过能力门槛后，新请求改走明确标识的同 run 模式；不回写旧记录。新桥接不可用时保留当前兼容路径的真实标识，或禁止该能力，不自动降级后仍显示同 run。针对外部控制面的操作回执与配置检查点分别版本化。

目标通用服务用持久应用回执与恢复 outbox 统一跨存储重试：原生 outbox 指向原 run，旧兼容路径需要新 run 时预分配候选 ID、在同一 run 文件写屏障幂等查找/创建。服务内部旧函数名包含 continuation 不意味着原生路径一定新建 run，应以持久 ID 核对。

## 11. 低风险自动应用与服务恢复

当前会话字段 `autoApplyLowRiskConfiguration` 独立于 `approvalMode`，默认 false。`isAutoApplicableConnector` 当前仅允许 PubMed，服务端控制资格，模型不能新增白名单。

提案时授权和白名单满足则记录 `autoApplyAt=createdAt+30s`。前端倒计时只展示；到期由服务端检查授权、基线、连接器/插件、会话、Stop generation 和任务状态，经同一决定服务应用并恢复。浏览器关闭不能取消服务端既有授权，进程重启应恢复未决计时。

关闭授权应清除/抑制到期操作，使请求回到人工确认。到期与人工确认只有一个结果，重复扫描读取已有状态。拒绝、取消、Stop 或前提失效均阻止自动应用。启用后的工具仍按自身权限策略执行。

上游 `auto_confirm`、工具 Always allow、Smart Approval 与本项目配置授权保持独立。不得复制这些标记作为本地配置批准。Smart Approval 的审核超时与本项目预授权后的 30 秒缓冲含义不同；现有配置定时器继续由本地服务端持有，外部 worker 到实际提交时再复核授权和 Stop。

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

### 12.1 复用组件与协议映射

保留 ModelConnectWizard、ConversationalConnectorCard 与 ProcessRecord 的体验，逐步用归一化字段 renderer 替换重复表单。WorkSwarm ConnectTokenModal 的字段/状态处理作为参考，调用方使用本项目设置客户端与 credential-bridge，不引入其全局 store、路由、桌面壳或账户状态。

普通选项可以复用统一交互请求模型；凭据字段单独渲染和提交，不放入通用问答 answers。外部 credentials_required/auth_required/connected 映射为本项目请求/操作状态，connected 仅说明连接就绪，不能直接发送配置 applied 或 run.completed。多个连接依赖的 onAllConnected 只触发后续激活核对，不直接声明原任务恢复。

事件带本地 session/run/request 与 revision/stateVersion；来自上游的状态先经绑定和脱敏投影，再进入既有 SSE。决定处理完成后发统一 resolved 语义，使卡片收起并可回看；丢失事件由快照恢复，不以清掉 UI 代替服务端请求完成。

## 13. 撤销、迁移、上线与回退

通用撤销创建反向提案，复核资源版本并恢复原字段缺失/继承；默认只撤销绑定，保留新建共享资源。已被引用资源不因一次撤销删除。外部验证、已发送请求和业务动作不能撤销。当前没有完整通用撤销服务。

迁移采用新增表/可选字段与独立 schema 版本；不占用未经核对的全库版本号，不整体重写旧 run/事件。旧检查点和关联后续记录继续可读，历史语义不被篡改。

完整框架可另设发布开关，但该开关及默认值必须真实实现后才写入部署合同。关闭新增功能时应仍能读历史、取消等待和清理草稿，不能隐式回滚已保存配置。

删除会话清理其请求、未引用凭据草稿、通知和相关恢复记录，不顺带删除全局 provider。物理清理 CAS 遵循既有引用/保留策略。普通测试只按明确 project/session ID 清理。

降级前先抑制/排空待恢复操作、清理草稿并备份实际数据目录；旧二进制忽略新字段不等于调度安全。前后端同步发布，验证 schema、决定、快照和历史显示兼容。

日志只记录 request/run ID、版本、能力、范围、字段名、耗时与结果，不记录密钥、handle、认证 URL 或原始 provider 响应。正常配置等待不计作模型故障，验证失败、应用失败、恢复失败与用户拒绝分别统计。

## 14. 开发拆分与完成条件

| 工作包 | 复用基础 | 本项目新增交付 | 完成条件 |
|---|---|---|---|
| DEV-CFG-01 通用契约 | 现有专用 DTO、上游字段描述 | Schema 归一化、revision/状态投影、能力登记 | 未知字段拒绝，凭据不出 DTO，兼容现有 singular decision |
| DEV-CFG-02 配置服务 | 现有连接器/模型领域存储与服务 | 本地条件事务、统一预览/版本/receipt/outbox | 设置页与对话共用版本约束，其他字段保留 |
| DEV-CFG-03 验证/凭据 | ModelConnectWizard、现有密钥库、WorkSwarm 凭据服务 | 草稿/引用桥接、获准验证、认证修复适配器 | 同一资源单一正式来源，取消不提前保存/连接，输入脱敏 |
| DEV-CFG-04 运行协调 | Native 检查点与调度 | runtime-bridge、激活/恢复回执、故障核对 | 同 run、无提前最终答复，保存/生效/继续分别验证 |
| DEV-CFG-05 前端 | 现有卡片/ProcessRecord、声明式表单模式 | 归一化 renderer、状态映射和范围预览 | 不引入上游全局 store，快照/事件/设置页一致 |
| DEV-CFG-06 Agent 接入 | propose_connector_enable、独立指令预检 | inspect/propose 通用工具与受控诊断 | 不获得批准/写配置/密钥工具，Specialist 既有规则保留 |
| DEV-CFG-07 验收发布 | 既有测试与限定真实模型闭环 | 两执行器/两基线合同、故障与兼容回归 | 承诺能力逐项验收，完整 WSL 检查与清理记录齐全 |
| DEV-CFG-08 外部控制面 | WorkSwarm config/model/MCP 管理与连接/重载 | 条件写入/幂等宿主接口、操作 worker、query/reconcile | 超时未知不盲重试，远端结果与激活独立核对 |
| DEV-CFG-09 JiuwenSwarm 接续 | core interrupt/resume/checkpointer、上游交互合同 | interrupt/run 绑定、状态顺序、工具刷新和 Stop 桥接 | 原 run 实际调用新工具；重启、错配、重复/迟到决定通过 |
| DEV-CFG-10 依赖能力 | 安装脚本、现有 adapter | 固定依赖/补丁清单、能力握手和设置支持矩阵 | 不升级即假定能力，不开放无条件写入或不可恢复的模式 |

实施顺序：DEV-CFG-10 核对基线并保留现有回归 → DEV-CFG-01/02/04/05 提取公共端口 → DEV-CFG-08/09 按依赖接入并以 PubMed 验证 → DEV-CFG-03 验证一个受支持的凭据连接器 → DEV-CFG-06 扩展工具与领域能力 → DEV-CFG-07 完整发布验收。可按已验证的执行器能力分期发布；本表各新增工作包目前均待实现。

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
| CFG-T23 | schema-normalizer、凭据 API、renderer | 上游字段/占位符一致；未知 schema 失败；模型/事件/日志无密钥 |
| CFG-T24 | 外部 worker、宿主控制面、故障注入 | 同操作幂等；超时前后退出可查询；不确定结果不重发，不将远端写入伪装成本地原子事务 |
| CFG-T25 | JiuwenSwarm adapter、core 合同、跨会话测试 | 本地 run/上游 Session/interrupt/generation 精确匹配；不放行无关工具权限 |
| CFG-T26 | 固定依赖、能力握手、预检/前端 | 缺少条件写入/同 run 能力时不暴露错误承诺；next_run/restart 如实展示 |
| CFG-T27 | 两执行器 checkpointer 与宿主恢复、故障注入 | 检查点及等待绑定先于可确认卡；重启恢复同请求，失败不输出可操作卡 |
| CFG-T28 | 配置/激活/恢复协调器、凭据 owner | 三阶段回执分离；激活失败不重复保存，原 run 不加载未批准增量或复制凭据 |

额外故障断言包括：同幂等键不同载荷、配置后恢复前退出、CAS 写后 run 引用前退出、queued 后 Stop 再重启、并行工具未结束、外部提交未知、父级/凭据/安装版本变化，以及一次写失败后队列仍可用。

上游测试用于筛选候选组合，不能代替本项目合同测试。外部控制面模拟器必须覆盖“不支持幂等/条件写入”“写入已成功但响应丢失”“查询未知”“配置后被另一入口修改”；这些情况必须验证禁止入口或进入明确待核对状态。真实桥接使用第 2.4 节固定版本及补丁组合，并记录实际 backend capabilities。

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

### 15.4 复用接入的最小交付证明

| 用例 | 必须取得的证据 |
|---|---|
| Native 现有 PubMed | 当前能力无回退；原 run、原提问和已提交工具结果保留 |
| JiuwenSwarm PubMed | 固定依赖清单、两端检查点/绑定、人工决定、获准工具目录、同一研究 run 的真实检索与最终答复 |
| 一个受支持的凭据连接器 | 声明字段到专用输入、唯一正式存储、获准连接、生效 receipt、原任务继续；取消/错误凭据不污染现有资源 |
| 外部响应丢失及 Stop | 操作可核对且不重复执行；已提交事实准确，Stop 抑制后续激活/恢复 |

凭据连接器用例需在确定具体适配器后固定服务、范围与预算，不把随便填写一个 URL 当作可验证实现。没有合适的真实凭据测试条件时仅报告模拟通过，不开放相应已验证能力声明。

## 16. 实施状态、交付记录与版本

2026-10-16 的真实模型用例完成了主 Agent 提案、持久暂停、人工确认、同 run 恢复、独立检索授权、单次 `limit: 3` 检索和带 PubMed 来源的最终答复。测试项目清理完成；未使用子 Agent 或 shell。该结果仅证明限定用例。

完整交付需逐项提供：实现分支/commit、适配器支持表、schema/迁移和回退影响、CFG 验收结果、故障与并发证据、WSL 命令和结果、真实/模拟边界、健康检查与临时数据清理。未执行、失败或依赖环境阻塞的项目分别列明。

本版在开发 v4 完整合同上落实调研：明确本地与外部持久化适配器、复用来源和依赖门槛，分离激活与恢复状态，新增 JiuwenSwarm 精确中断桥接、外部操作核对及 CFG-T23～28。当前产品实现与 2026-10-16 实测范围不变；新增端口、宿主扩展、工作包和验收都需要后续实现与验证。
