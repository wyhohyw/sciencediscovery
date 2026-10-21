# 对话内按需配置 · 完整开发文档 v12

- 日期：2026-10-21；产品依据：[完整设计 v22](conversational-settings.v22.md)，CFG-01～CFG-06、CFG-T01～CFG-T36；复用依据：[竞品与底座调研 v2](conversational-settings-research.v2.md)。
- 本文保留[开发 v8](https://github.com/wyhohyw/sciencediscovery/blob/f58c728b4395ed32f7fa1161636e87c9d6123f5d/docs/zh/explanation/conversational-settings-development.v8.md)及[历史开发 v7](https://github.com/wyhohyw/sciencediscovery/blob/f58c728b4395ed32f7fa1161636e87c9d6123f5d/docs/zh/explanation/conversational-settings-development.v7.md)，延续完整章节和验收编号，按 [#278 维护者决策](https://github.com/openJiuwen-ai/sciencediscovery/issues/278#issuecomment-6238467927)更新 DEV-CFG-11～17 实施状态。第 19 节记录本轮 P0/P1 接线与未完成边界，第 20 节内嵌完整设置矩阵；最终结果见状态 v10，不由文档更新推定端到端全部通过。
- 核对范围：隔离分支 `codex/conversational-settings-review-fixes` 本轮实现。最终提交标识与 WSL 结果见[状态 v10](conversational-settings-status.v10.md)；旧 `a88d9283` 只对应 2026-10-16 的历史真实模型用例。
- 标记约定：“已接入”表示实际产品调用路径；“合同验证”表示接口/模拟器/故障测试已有，真实后端未贯通；“待开发/待集成”表示仍需本项目宿主工作或固定补丁集成，尚不能开放产品承诺。文档中的未来宿主接口不能当作已安装能力。

## 1. 交付范围与技术决策

当前通用切片包含七个已注册适配器：`model.select`、`model.connect`、`model.repair_credential`、`connector.enable`、`skill.select`、`thinking.configure`、`review.configure`。公共服务处理版本化预览、用户批准、批准后验证、正式提交、receipt 与持久阶段；Native 连接器已接工具装配 witness 和同 run 调度。对话内无模型引导也使用统一持久请求；连接器预检/专用工具和旧 PubMed 计时器保留兼容。设置页继续使用原有模型管理接口。

本轮已经接入注册驱动的 Agent 发现/参数、v3 可信选项、Native 已就绪 MCP 运行级覆盖、结构化替代/拒绝结果及 P1 三项领域。最终测试结果见状态 v10；P2～P4 和实际 JiuwenSwarm 宿主仍按后续计划逐项开发。任意 REST API、STDIO、宿主部署设置不自动获得支持。

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

### 2.1 本轮实际模块

| 位置 | 实际职责 |
|---|---|
| `packages/schema/src/configuration.ts` | legacy/v2 兼容，v3 协议版本、可信 options、selectedOptionId、choiceReceipt、受限 ID 多选共用类型 |
| `configuration/schema-normalizer.ts`、`registry.ts` | 有限能力登记；上游 text/password 字段归一化、静态帮助链接与字段白名单 |
| `configuration/request-service.ts`、`repository.ts` | 提案、preview、revision/digest、用户决定、反向提案、持久幂等、receipt/outbox 与未知验证恢复 |
| `configuration/credential-bridge.ts` | 本地加密草稿、精确绑定、30 分钟有效期、20 秒验证租约、撤销与清理 |
| `configuration/domain-adapters.ts`、`p1-adapters.ts` | 原四能力及三个 P1 适配器，复用正式模型/provider/凭据、Project/Session runtime 与会话审阅服务 |
| `configuration/agent-metadata.ts`、`settings-catalog.ts`、`diagnostics.ts` | 有限注册描述与 Agent 参数生成；设置盘点；结构化诊断 code 映射，不是任意真实故障自动诊断 |
| `configuration/choices.ts`、`native-choices.ts` | v3 选项校验、选择预览和 receipt；Native 任务上下文、独立 overlay、已有 Web 替代与拒绝 needKey |
| `reviewer-specialist/audit-coordinator.ts`、`http/index.ts` | 原 Run 审阅快照与来源绑定、按来源合批、最终 admission 全局 gate；绑定来源丢失时不回退其他任务 |
| `configuration/framework.ts`、`http.ts`、`agent-tools.ts` | 领域装配、用户端点与只读/提案工具；Agent 不获得批准或凭据能力 |
| `store.ts` 的配置事务 hooks | 共用目录 SQLite、设置写屏障、内存回滚、同步领域操作；不另建正式配置库 |
| `configuration/native-activation.ts`、`activation-coordinator.ts` | 从冻结 run 加获准增量准备真实工具描述，保存 witness，并在实际工具重建后匹配 |
| `configuration/runtime-bridge.ts` | Native 持久等待顺序；JiuwenSwarm 精确中断身份/恢复合同，后者尚无实际宿主接入 |
| `configuration/backend-capabilities.ts`、`backend-control.ts`、`operation-worker.ts` | 握手、能力门控、受控外部接口、SQLite journal 和超时查询恢复合同 |
| `services/adapter/src/sciencediscovery_adapter/configuration_capabilities.py` | 认证后的实际 adapter 握手；未实现的条件写入与精确恢复报告为不支持 |
| `runs/index.ts` | 主 Agent 工具、原生检查点、等待、同 run 恢复、真实工具 witness 核对 |
| `apps/web/src/configuration/ConfigurationRequestCard.tsx`、`QuickConfigurationMenu.tsx` | 统一字段 renderer、专用凭据输入、预览/确认、折叠结果与主动入口 |
| 旧模型组件、连接器卡及 `explicit-connector.ts` | 对话不再挂载旧模型直写卡；旧连接器指令及历史记录继续兼容，设置页模型管理保持独立 |

### 2.2 当前调用链

`Agent / 主动入口 → registry → request-service → 用户字段/preview → 独立决定 → 获准验证 → 同步领域事务 → receipt + outbox → 激活核对 → 原 run 调度`。

Agent 只看到脱敏状态与请求 ID。HTTP 列表按同一 ID 合并 v2/v3 与旧 companion；研究请求只有检查点和 blocked 等待就绪后才可展示并确认。普通设置仍以领域存储为正式来源。

### 2.3 复用与尚未贯通的边界

| 复用来源 | 本轮接入 | 仍需完成 |
|---|---|---|
| 现有模型协议、加密密钥库与设置继承 | 原模型/连接器领域同步 mutator、协议一致的短连接验证，P1 正式 runtime/审阅事务 hooks | 新 MCP/OAuth/文件导入及其专用适配器 |
| Native 检查点、队列和 MCP 工具装配 | 等待顺序、阶段 outbox、工具 witness、Stop/CAS | 按新适配器逐项扩展生效方式，不能默认热切换模型 |
| WorkSwarm token-schema/credential/connect | text/password 归一化规则与本地草稿通道 | WorkSwarm 资源所属草稿/凭据接口的真实接入；不复制其正式密钥库 |
| WorkSwarm config/model/MCP 控制面 | 窄 backend-control 与 worker/journal 合同及模拟验证 | 实际条件写入、操作去重/权威查询宿主接口及领域注册 |
| core interrupt/resume/checkpointer | 精确身份绑定、能力门控与恢复合同 | 实际主 Agent interrupt 注册、宿主持久检查点和同 run 输出贯通 |

### 2.4 固定依赖与后端能力门槛

| 基线 | 固定版本 | 本次可以据此决定什么 |
|---|---|---|
| 仓库默认 WorkSwarm 安装标签 | workswarm0.2.6：`70aa6715992d9cb2c3ec81ee35991d13747e5ce0`；其 core 依赖为 `ad0c9dfbbd577423cc60f73edffa0a4699cc53f9` | 已核对凭据 Schema 和连接流程存在；当前部署仍需读取实际版本及已应用兼容补丁 |
| 调研 WorkSwarm develop | `1372eda94970bf91d5dc0cb03d784e971f39b991`；其 core 依赖为 `f9f50368d526dde64cd3ad10473956b660a50694` | 可作为新桥接候选，不自动升级安装脚本 |
| 调研 core develop | `1fe6a03690766e64a686ac51e873495a617b5ca5` | 中断持久化顺序与恢复合同的来源；不等于任一 WorkSwarm 组合已通过验证 |

固定源码与来源见调研第 5～7、10 节。本轮不升级依赖。已安装源码/分发版本与兼容补丁核验见[执行底座核验 v1](https://github.com/wyhohyw/sciencediscovery/blob/f58c728b4395ed32f7fa1161636e87c9d6123f5d/docs/zh/explanation/conversational-settings-runtime-baseline.v1.md)。接入时继续记录实际组合、补丁集和协议版本；通过无副作用的能力握手或服务端固定能力清单取得：可写字段/范围、schema 版本、credential 提交通道、条件写入、操作回执/状态查询、工具目录刷新、持久 interrupt 与精确 resume 支持。

能力按设置类型与执行器求交集。关键能力不满足时返回明确不可用原因，或仅开放已经声明的 next_run/requires_restart 模式；不能仅凭版本字符串、global reload 成功或 API 名称宣称 same_run。握手与 bridge 合同现已实现；安装的 Python adapter 如实报告缺失能力。实际 interrupt/control 宿主仍未接通，不因合同存在就开放 same_run 或远端覆盖型修改。

固定 core `ad0c9df…` 的 Agent interrupt 存在先输出、后 durable commit 的窗口。已新增固定 SHA 补丁提案，并以 `scripts/check-configuration-core-interrupt.py` 在临时副本执行真实 handler，验证提交先于结果暴露、提交失败不返回可确认结果；补丁未应用到用户部署，仍待安装清单集成与真实恢复验收。WorkSwarm 已有 `ensure_persistent_checkpointer`、SQLite checkpointer 与结构化 interrupt continuation，不能将它们误写为缺失。我们仍需开发真实 rail/Session/interrupt 绑定、精确恢复端点、原工具集合重建和事件接续。DEV08 同样需统一现有 Web、TUI、模型、MCP 与凭据写入口的条件版本、operationId 与权威回执；跨文件写入不能由本地 SQLite 事务代替。详见[执行底座核验 v1 第 7～8 节](https://github.com/wyhohyw/sciencediscovery/blob/f58c728b4395ed32f7fa1161636e87c9d6123f5d/docs/zh/explanation/conversational-settings-runtime-baseline.v1.md#7-dev-cfg-09固定-core-的真实缺口和宿主自研责任)。

已有 `/agent/jiuwenswarm-config` 只允许搜索设置，不扩成原样透传 config.set 的万能端点。Web/TUI 同名 RPC 按各自 schema 映射；每个新增字段都登记资源归属、作用范围与版本检查方式。

## 3. 通用能力目录和适配器协议

当前真实接口定义在 `configuration/types.ts`，由 `ConfigurationRegistry` 显式注册。七项有限能力之外返回不可用，不接受 LLM 提供 schema、代码、任意 Patch 或文件路径。

`ConfigurationAdapter` 声明 id、adapter/schema 版本、scope、local_transaction/external_control、字段策略，并提供以下有限端口：

| 端口 | 本轮合同 |
|---|---|
| `normalize(input)`、`target(input)` | 拒绝未知字段，产生有限标量/注册 ID 集合输入和稳定目标；密钥不在 values |
| `preview(input,snapshot,scope)` | 同步无网络，返回字段、脱敏 changes、范围、验证预算、生效方式、ready 和服务端 data |
| `verify(context)` | 用户批准后才调用；只使用批准目标与专用 credential handle，返回有限分类结果 |
| `apply(context)` | local_transaction 同步领域闭包，不联网、不独立提交；正式设置/密钥与审计共享事务 |
| `previewReversal(receipt,snapshot)` | 可选；使用旧 receipt 与最新版本建立新提案，不直接回写旧值 |

`ConfigurationHost` 注入 `runInTransaction(sessionId,action)`、同步 `snapshot(...)`，以及可选 `onPreviewRevision`。最后一个 hook 在同一 SQLite 事务内重绑定凭据草稿与新 revision，避免先保存预览、后改 handle 的崩溃窗口。

本轮七项适配器的持久保存均为本地领域提交；运行级 overlay 是独立的已批准任务资源，不调用正式 Session setter。external_control 的公共服务只登记 applying 与 external_submit outbox；实际 worker 还需要宿主控制面和 journal 调度接线，不把写出 outbox 当作远端 applied。

### 3.1 声明式字段的复用与归一化

`normalizeTokenSchema` 支持已核对的 text/password 输入，password 映射为 credential 可用状态，不复制默认 Key 或 placeholder。`normalizeConfigurationFields` 支持已注册 text/url/number/boolean/enum/multiselect/credential 视图；`normalizeConfigurationValues` 约束标量和受限 ID 集合，拒绝重复/未知 ID、逗号字符串和任意 JSON。

未知字段、类型、重复 key、凭据占位符不一致、非法枚举与未批准帮助链接均拒绝。Schema 只来自受支持适配器/连接器包。静态帮助地址按服务端 host 白名单且不允许认证信息、query 或 fragment；当前模型适配器不依赖外部帮助脚本。

当前 registry.register 同时保存受信任元数据，目录和 Agent 参数按实际已注册能力生成；通用 `agent-tools.ts` 不再手工枚举四/七类参数并集。P1 normalizer 使用同一字段描述，再执行已安装资源、模型取值和 scope 校验。旧四适配器保留领域内部的反向/凭据校验，不把它们变成 Agent 可写字段。

### 3.2 正式来源与生效方式

本项目模型继续使用原 provider/model 与加密密钥存储；草稿不是正式资源。远端所有的资源继续归远端控制面，本地只保存非敏感映射、版本与回执。

连接器启用使用最新 effective 集合增加一项，并保留其他项；模型选择只写会话绑定。零模型首次连接明确写全局默认并保留其他全局 overrides。模型修复写目标 profile 的独立凭据，不修改共享 provider。

生效方式区分 before_first_run、same_run、next_run、requires_restart。当前只有 Native 已就绪 MCP 连接器承诺任务中 same_run；模型和 P1 变更不热切换活动 run 或改写旧排队快照。会话审阅 automatic/level 在入队时额外冻结，后台审阅按产物来源读取；全局 Reviewer enabled 仍是实时总闸。`enqueueArtifactVersion` 不因全局关闭而提前返回：来源会话快照允许自动审阅时仍持久任务，后台以 failed checkpoint 明示 Reviewer 已关闭，且不调用审阅执行器；来源会话快照明确 opt-out 才不入队。保留后台 admission 和所有异步等待之后、执行器调用之前的全局检查；若关闭发生在后一道检查，则按现有收束分支更新任务及回执，不启动执行。仅后续任务生效的配置显示对应限制，不用 applied 代替 active。

## 4. 数据契约与状态

### 4.1 兼容连接器请求

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

### 4.3 已接入的 v2 基线与兼容视图

v2 DTO 已增加 `capabilityId`、schema/适配器版本、`revision`、`stateVersion`、输入描述、脱敏差异、范围/影响、验证计划、`previewDigest`、凭据可用状态、应用结果、激活/恢复状态、错误与允许动作。

内部另存规范化计划、基线 overrides 的字段缺失信息、资源版本向量、凭据草稿引用、原任务与附件/产物引用、确认记录、验证 attempt、receipt 与恢复幂等键。

复用上游时再保存内部 `backendKind/backendRevision`、外部 operation ID 与状态查询信息、配置 receipt、激活 receipt，以及 executor/外部 Session/interrupt/checkpoint 的绑定。外部 opaque ID 只在服务端使用，不能成为 Agent 的批准凭证；不把上游问题原始 payload 直接放入通用 DTO。

历史 v2 的 `scope` 只有 session/project/global，用户决定仅有 apply/dismiss。当前 v3 已增加 options、selectedOptionId、choiceReceipt 和 needKey；run-only 是被批准的任务 overlay，不把 run 塞入正式持久设置 scope。旧四能力保留 v2 语义；P1 仅接受显式 protocolVersion=3，新选择不能经旧端点批准。实际 v3 wire 字段见第 4.4、19.3 节。

兼容公共视图摘录如下；为便于查看已经列入七项能力与 multiselect，但省略的 v3 选项字段见第 4.4 节，完整可导入类型以 schema 包为准。它与第 4.1 节兼容 DTO 不同，前端以 capabilityId 和 protocolVersion 判别：

```ts
type ConfigurationCapabilityId =
  | "model.select" | "model.connect"
  | "model.repair_credential" | "connector.enable"
  | "skill.select" | "thinking.configure" | "review.configure";
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
  | { kind: "multiselect"; value?: string[]; choices: Array<{ id: string; label: string }> }
  | { kind: "text" | "url" | "number" | "boolean" | "enum";
      value?: string | number | boolean;
      choices?: Array<{ id: string; label: string }> }
);
interface ConfigurationRequestView {
  protocolVersion?: 3; // 缺省为历史 v2；v3 的 options/selectedOptionId/choiceReceipt 见 4.4
  id: string; sessionId: string; projectId: string; runId?: string;
  revision: number; stateVersion: number;
  capabilityId: ConfigurationCapabilityId;
  adapterVersion: number; schemaVersion: number;
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
  autoApplyAt?: string;
  decisionSource?: "user" | "auto";
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

capability、状态、字段类型和动作已进入共用类型及服务端验证，按能力区分参数，拒绝未知字段；`changes` 只能是脱敏展示，不能作为可执行补丁。凭据字段没有明文 value 属性，浏览器的临时输入缓冲不属于卡片快照。

输入、范围、目标、验证动作或基线变化提升 revision 并废止旧确认；阶段进度只提升 stateVersion。DTO 必须白名单投影，不包含 secretRef、ciphertext、内部计划、原始 provider 响应或 credential handle。

本轮公共状态为：

```text
collecting_input → awaiting_confirmation → validating → applying → applied
                         ↓                   ↓             ↓
                    dismissed             needs_fix      failed
另有 stale / cancelled / expired；激活和恢复状态独立保存。
```

已应用后恢复失败不改成“配置未保存”；撤销是新提案，保留原审计。未知提交结果先查 receipt，不直接改 failed 后重试。

上表将 activation 和 resumption 分开：active 表示已核对目标配置生效；queued/started/completed 表示研究 run 的执行进度。外部写入超时进入内部 `external_unknown`，公共请求保持 applying 并提供可理解错误；允许动作仅含查询、停止接续或人工核对，不提供盲目重新提交。

### 4.4 当前 v3 协议与真实作用范围

新提案显式传 protocolVersion=3；Agent 工具由服务端固定发送，Agent 不获得切换协议绕过校验的参数。原四能力的历史 v2 仍可读/按原约定处理，三个 P1 未提供 v3 时返回 CONFIGURATION_PROTOCOL_REQUIRED。

| 字段/接口 | 实际合同 |
|---|---|
| options[] | 服务端有限目录，kind 只允许 persisted_apply/run_overlay/alternative/dismiss；不是 Agent 提交的脚本或 schema |
| preview.selectedOptionId | 用户选择方案，服务器重新校验可用性、字段、scope 与预算，提升 revision 并生成绑定该方案的 digest |
| decision.selectedOptionId | 与 request/revision/previewDigest/Idempotency-Key 一同校验；旧 payload 或篡改方案不能批准 |
| choiceReceipt | 只投影 optionId/kind/label/configurationWritten/scope/runId/alternativeId；正式保存、overlay 和不写配置可区分 |
| needKey | 服务端按原任务、能力目标和配置基线计算；已选择拒绝/替代的相同 need 不反复提案 |
| run overlay | 独立配置记录，绑定原 run/检查点/revision/digest/Stop generation，仅参与获准 Native MCP 增量装配 |

默认方案为持久保存和暂不配置；只有原 Native 工厂确有当前可用 Web，且设置版本复核通过时才提供 Web 替代；只有 ready MCP 及持久任务绑定可用时才提供 run-only。替代不是批准其他工具，关闭也不是 Stop。

## 5. 存储、版本和事务

### 5.1 共用正式领域存储

本轮通用请求与正式目录共用 `catalog.sqlite`，兼容请求仍在 `connector_configuration_requests`。Native 检查点继续通过 CAS/VersionStore 保存，run 文件与事件流沿用已有机制；这些跨存储步骤由持久阶段协调，不宣称整个系统 exactly-once。

### 5.2 已实现的持久记录

| 表 | 作用 |
|---|---|
| `configuration_requests_v2`、`configuration_revisions_v2` | 当前视图及不可变输入/计划版本；revision 与 stateVersion 分开 |
| `configuration_idempotency_v2` | 同键同规范化操作回放，同键换载荷冲突 |
| `configuration_receipts_v2`、`configuration_outbox_v2` | 唯一应用事实、激活/恢复/外部提交意图 |
| `configuration_verification_attempts_v2` | 验证开始/完成/未知；旧进程未决验证不自动重发 |
| `configuration_credential_drafts`、`configuration_credential_uploads` | 加密草稿与加密上传幂等载荷；不充当正式密钥库 |
| `configuration_native_activation_v2` | 获准增量的真实工具描述 witness，以及恢复时实际装配核对时间 |
| `configuration_external_operations_v2` | 外部 worker 的不可变操作、租约及查询恢复 journal；实际后端尚待接线 |
| `configuration_native_runs_v3`、`configuration_native_overlays_v3` | 真实 Native factory 可用性/检查点证据与独立任务覆盖；不充当第二份正式 Session settings |

资源版本来自当前有效目录：继承层设置、provider/model、加密凭据 generation、已登记连接器与策略，以及 Skill 安装目录/Library head/作用层、会话审阅与全局 Reviewer gate。事务内重新读取并严格比较，设置页经正式领域服务的变化会使旧预览失效。新连接器若含额外安装/config 资源，需要补相应版本后再开放，不能用 ID 列表替代全部依赖版本。

### 5.3 本地条件提交与失败恢复

1. 同一设置写屏障内创建不可变预览，绑定版本、Stop generation 和有效期。
2. 用户批准精确 revision/digest；记验证 attempt，并在发出短验证前再次检查 Stop/版本。
3. 在事务外进行批准目标的有限验证，不持有长期 SQLite 事务。
4. 同步事务内复核所有前提；调用领域 mutator，写正式配置/凭据、receipt 与激活 outbox。
5. COMMIT 后核对生效证据并持久安排恢复；激活/恢复失败不重写已保存配置。

事务不 await、不调用会独立保存的公共 mutator。`SessionStore.runConfigurationTransaction` 同时保护目录内存快照与 SQLite；失败回滚两者，后续写队列仍可用。应用阶段失败进入脱敏 failed，需新预览再决定。

验证中退出后，启动恢复将旧 owner 未决 attempt 标为未知并进入 needs_fix，要求用户新预览/确认；不自动重复可能计费的探测。单进程写入是当前支持基线，不宣称多个 API 实例共用目录安全。

### 5.4 外部控制面提交与激活

WorkSwarm 的 config.set、model/MCP 保存自行持久化，不能加入本地同步 SQLite 事务。backend-control、operation-worker 与 SQLite journal 已实现以下合同，并提供模拟器/故障测试；实际宿主控制接口及设置适配器尚未贯通：

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

具体合同为 `submit(operation, { beforeDispatch })`：HTTP 后端完成异步 capability 握手后，必须在真正发出写请求前调用授权回调，再核对 Stop、批准版本和 worker 租约。握手前的一次检查不能覆盖握手期间发生的 Stop；回调拒绝时标记 suppressed，不能发送写入，也不作为已发出但结果未知的操作自动重试。该合同已有受控测试，真实 WorkSwarm operations 宿主仍待开发。

## 6. HTTP 与 Agent 工具

### 6.1 已接入的用户端点

前缀 `S = /api/sessions/:sessionId`，`R = S/configuration-requests/:requestId`；由现有鉴权/CORS 后调用 `configuration/http.ts`。

| 方法/路径 | 当前合同 |
|---|---|
| `GET S/configuration/capabilities` | 有限目录、执行器与 same_run 能力/禁用原因 |
| `GET S/configuration-requests` | 同 ID 合并 v2 与 legacy，请求 checkpoint 未就绪时不开放操作卡 |
| `POST S/configuration-requests` | capabilityId、values、可选 scope/reason/protocolVersion；P1 要求 v3，不接受 run/interrupt/内部恢复字段 |
| `GET R` | 独立脱敏快照与 revision/stateVersion |
| `POST R/endpoint-consent` | 用户专属精确目的地同意；绑定 revision/endpointTarget，只记同意，不上传密钥或探测；Agent 无此接口能力 |
| `POST R/credentials` | expectedRevision、fieldId、secret；专用 no-store 响应返回短期 handle |
| `POST R/preview` | expectedRevision、values、可选 scope/credentialHandle；v3 另有 selectedOptionId，产生新 revision 与方案绑定 digest |
| `POST R/decision` | revision、previewDigest、apply/dismiss；v3 必须有匹配 selectedOptionId 和幂等键；单数 decision 路径兼容保留 |
| `POST R/continue` | 只重试/核对已保存请求的激活或恢复，不重新写正式设置 |
| `POST R/reversal` | 基于 receipt、最新版本和原字段缺失状态创建反向提案 |

旧 `POST S/configuration/preflight`、旧决定载荷和模型向导 API 仍保留。只有同会话中确实存在的 legacy 请求才接受旧决定载荷；v2/v3 请求不能借该分支绕过 revision/digest 或所选方案。

### 6.2 Agent、版本与错误边界

`inspect_configuration` 返回有限目录、脱敏当前值/来源/资源、实际 thinking 取值与后端不可用原因；`propose_configuration` 的七项有限参数由登记元数据生成，不获得批准工具、bearer、handle、headers/env、任意文件或脚本接口。运行中 connector v3 提案绑定原 run；模型与 P1 明确用于后续新排队 run。诊断只提供有限 code 映射；没有串联任意真实工具失败的自动定位和修复，不能将网络失败当作换 Key/代理的证据。Specialist 既有配置与工具组合规则保持原样。

持久 Idempotency-Key 用于提案、预览、决定和凭据上传；同键不同载荷拒绝，不同键也不能重复应用同一 revision。激活/恢复由独立持久 phase/outbox 与租约去重；continue 不被描述为重新应用配置。恢复请求本身不能制造新原始用户提问。

端点复核会话归属、可写状态、检查点、资源版本、Stop、提案/草稿有效期。错误区分非法字段、旧预览、资源冲突、停止、过期、认证与幂等冲突；原始 provider 错误及凭据不直接写回对话。验证失败进入 needs_fix；配置已保存而激活/恢复失败保留 applied 事实。

## 7. 模型引导、验证和凭据

### 7.1 当前引导规则

前端缺模型检查不依赖 LLM。空会话只保留轻量入口；提交非空草稿时打开卡并阻止 run 创建，确认后经正常路径只提交一次。取消保留草稿；无任务时不提交空 run。

零模型通过确定性前端入口创建 `model.connect` 持久请求，在统一卡片收集连接字段；已有模型走 `model.select` 或新连接请求。它们使用同一幂等、预览、用户决定与持久回执，不再直接从对话调用 `updateSession`。首次连接前固定并展示全局默认范围；写默认时保留其他全局 overrides。已有模型的选择或新连接只绑定当前会话。并发变化使旧确认失效；原研究草稿只续交一次。`ModelConnectWizard` 保留用于普通设置页。

### 7.2 已接入的协议验证

连接验证按正式运行的 `apiProtocol/apiVariant` 生成请求，支持 provider 明确允许的 tokenOptional；不能根据 hostname 猜协议或构造伪 token。

通用模型验证计划展示主机、固定无研究内容输入、单次请求预算与可能计费。验证器复用模型客户端 endpointRoot 规则，按显式 apiProtocol/apiVariant 生成 Anthropic、Chat 或 Responses 短请求；只有旧无协议配置沿用原推断。最多 10 秒、64 KiB UTF-8 原始字节；不跟随重定向、不透明重试，超限立即中止响应。tokenOptional 不生成伪 Key。模型发现仍属于旧向导的独立能力，不将发现授权当成保存授权。

默认不把凭据随重定向传到其他主机。模型 API 沿用模型层的外连/代理策略，不把 Runner 沙箱网络策略误当成全部 API 策略。错误分类区分认证、模型不存在、网络、额度/限流、协议和能力未知，回传有限脱敏摘要。

未知模型允许先授权有限发现，用户再选择实际结果并批准绑定；不支持发现时手填。发现授权不升级成保存授权，连接成功不证明视觉能力。外部验证已发出但结果未知时不自动重复可能计费调用。

### 7.3 凭据通道

```text
专用表单 → 后端加密草稿 → 获准验证器 → 正式凭据存储
Agent / 普通快照 / SSE / 日志：仅见 missing、provided、expired 等状态
```

通用草稿已按会话、请求、字段、目标和版本精确绑定，只在浏览器组件内存保留短期 handle；不得进入聊天状态、URL、持久浏览器存储或模型上下文。已有 provider 凭据只用于获准端点。正式提交后转存或清理，取消/过期清理未引用草稿，执行中的有界验证租约不能被清理器误删。

重新预览时，未显式提供 handle 而旧草稿已经过期或被撤销，应移除旧绑定，产生 credential=missing 的新 revision，允许“预览字段 → 专用上传 → 带新 handle 预览”的重填流程；不改正式凭据、不发验证请求。显式提交失效 handle 仍返回 `CREDENTIAL_DRAFT_EXPIRED`（410），不能悄悄接受或递增 revision。草稿重绑与预览 revision 在同一事务完成。

保持现有 CORS/鉴权边界，专用输入不写聊天/普通快照。提案最长有效期 24 小时；草稿有效期 30 分钟、验证租约 20 秒；clear 立即撤销新访问，活动租约结束后清理密文。重叠验证以 CAS 拒绝；旧 callback 不会释放新租约。共享 provider 修复采用指定 profile 覆盖；正式资源、密钥及 receipt 在同一同步事务中提交，错误候选不替换旧配置。

用户曾在普通消息中粘贴的凭据不因新增专用通道自动从历史消失；不在新提案或工具结果中复制明文，既存消息清理需要单独的历史管理流程。

### 7.4 后续复用 WorkSwarm 凭据服务的边界

外部凭据接入的目标合同复用 `credentials_required` 与字段描述，将表单提交路由到该资源的权威凭据服务。已有正式凭据通过服务端引用使用，不返回明文给浏览器或模型。新输入通过本项目鉴权后的专用端点传输，禁记请求体；普通 ask_user/InteractiveInput 只传配置决定或字段可用状态。

若现有 WorkSwarm 接口会“保存凭据并立即连接”，不能把它当作无副作用的草稿上传：只有当前预览已覆盖该存储范围、目标、连接行为和预算，且用户已批准时才能调用。未获批准的新凭据留在所属后端的隔离草稿；后端没有草稿能力时补受控接口，或在确认前只保留浏览器临时输入，不提前永久保存。取消/刷新后的行为应明示。

该类保存并连接接口属于外部应用操作，不能塞进 verify 阶段以规避提交回执。修复现有共享凭据时须先验证隔离候选再受控绑定；后端无法保护原有效配置时，不开放该修复能力。

对同一资源不同时写两份正式凭据。外部服务无法提供所需草稿/引用/字段隔离时，该凭据类型不开放通用接入。配置文件和 OAuth URL 不通过文本提问交给 LLM 解析；OAuth 引导可复用已有认证服务，但回调必须绑定用户、请求、目标与有效期，不能把收到一个回调当成其他请求的授权。

## 8. 触发、去重与 Specialist 边界

独立指令预检只解析已安装连接器的 ID/显示名与完整肯定启用命令；名称不唯一、不认识、否定或复合任务返回不适用。已启用返回当前状态，未就绪返回不可用；其他普通任务继续走正常 Agent 流程。

独立指令请求用 `user-request:*` 区分，不对应研究 run，不生成 continuation。已有 applied 记录不能作为“仍启用”的判断依据，必须读取当前 effective 设置。待确认请求按会话和连接器复用。

主 Agent 的研究任务可通过通用 `propose_configuration` 请求 `connector.enable`，也保留 `propose_connector_enable` 兼容提案；不要把该模型工具路径描述为所有复合自然语言需求的确定性识别。专用工具仅提供给主 Agent，参数与目录由服务端校验。

Specialist 的现有预设连接器仍可与会话设置组合；本功能不改变该授权/装配规则。当前通用配置工具仅给主 Agent，子 Agent 没有该提案工具。未来如增加，所有提案仍需服务端归属、去重、决定与恢复协调，不能并行卡片各自覆盖设置。测试主 Agent 路径应禁用委派，另测 Specialist 既有路径。

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
2. 记录所选方案与 receipt；持久保存更新 Session，run-only 写独立 overlay，替代/拒绝不写连接器。原历史 settingsSnapshot 保持不变，实际装配从冻结基础加批准增量读取。
3. 原 run 持久标记 `configurationResume` 并重新入队，保留原 ID、原始 userMessageId、事件流与检查点。
4. 调度器在领取前复核决定状态、检查点关联和 generation；重建工具注册表，读取检查点历史并校验其 requestId/generation。
5. 通过可信内部上下文明确告知保存/本次使用/可用替代/拒绝、needKey 与原目标；同 need 的已保存决定不能循环弹卡。旧工具结果中的“待确认”是历史，不能让模型重新等待同一卡。
6. 从未完成位置继续；不重复创建用户原始提问，不重放已提交工具。最终答复只在原任务真正结束时生成。

原 run 恢复重新入队是执行状态变化，不是新的研究任务。恢复后的 `run.started` 事件也不能按“新用户问题”另插一组重复轨迹。

### 9.3 持久阶段与仍未保证的外部副作用

现有 checkpoint 以模型历史为核心，并不等于已实现所有外部动作/产物的提交协议。本轮协调器已明确绑定 request/revision、应用 receipt、冻结 run、激活/恢复意图及资源版本；但这不是所有业务工具、产物和附件的跨系统提交协议。

正式写入的 CAS 与恢复依赖证明分别维护。`receipt.executionDependencies` 绑定原任务实际挂载的源与获准增量，并对这些源的安装、传输/认证/OAuth、代理和相关权限做代际核对；prepare、激活协调器和最终工具装配使用同一证明。无关模型默认、其他 MCP 或其他设置变化不应阻止恢复。旧记录没有执行证明时保留原有保守检查，不凭空补造历史批准依据。

未知副作用先查权威状态，否则需要用户处理；不能猜测成功、重新上传、重新付费或重复提交远程作业。冻结模型 ID 也不自动冻结共享 provider 地址与密钥依赖，跨类型恢复需增加依赖版本检查。

### 9.4 两种执行器的目标恢复端口

下列是保留的完整目标端口示意；实际代码拆为 Native 等待函数、激活协调器、phase/outbox 与 JiuwenConfigurationRuntimeBridge。后者只有合同，尚无实际 interrupt 宿主。opaque 引用不进 Agent 或公共 DTO：

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

Native 已接持久等待顺序：先 CAS 检查点、后 blocked 绑定。配置提交后按原 run 冻结工具集合加获准连接器，调用实际 MCP descriptor builder，存配置/receipt 绑定的工具 witness；在执行器重新创建可执行工具后再次比较真实指纹，才允许模型继续。新增的其他设置不会顺便加入冻结 run。

此同 run 路径仅接受已就绪的 MCP 工具源；内置 Web 在提案入口返回 `CONFIGURATION_ACTIVATION_UNAVAILABLE`，可改走独立配置供后续运行使用。不能因为 Web 也属于 connector 目录，就跳过真实工具装配 witness 的边界。

JiuwenSwarm 精确绑定/恢复合同与能力门控已有，但实际 adapter 主 Agent interrupt 注册、上游 Session/checkpointer 和精确 resume 未接通。不能调用普通 chat(继续)冒充中断恢复。

### 9.5 JiuwenSwarm 实际宿主仍需完成的接续顺序

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

`reconcileLifecycle` 在提案去重、预览、凭据提交、决定和服务端扫描时持久化 editable/validating 请求的过期或 Stop：置 expired/cancelled、抑制未开始 outbox、结束未决幂等响应并释放活动去重；清隔离草稿和 companion 倒计时。若过期请求关联原 run，resumption=needs_user_action，保留原 blocked/configurationWait、检查点和未完成状态；不写配置、不调度、不结束 run。独立请求可以新建；同一过期检查点重提暂返回 `CONFIGURATION_REPLAN_UNAVAILABLE`，需先 Stop 后重试，原地换提案仍是待开发项。验证返回后的最后写屏障也再次落实终态，迟到验证结果不能写配置。

Stop 先发生则禁止应用/恢复；提交先发生则保留 applied 事实，Stop 抑制恢复。会话归档/删除同样阻断旧请求。任务恢复领取前再检查，不仅在点击决定时检查。

JiuwenSwarm 与旧 `configurationContinuation` 继续使用有关联的后续 run；该分支沿用历史去重和来源校验，不迁移或篡改成同一 run。原生新请求不通过创建关联任务来伪装暂停。已失败模型 run 保持失败，未来修复不能追溯改其历史状态。

这是当前兼容行为。第 9.5 节新桥接真实宿主接通并通过能力门槛后，新请求改走明确标识的同 run 模式；不回写旧记录。新桥接不可用时保留当前兼容路径的真实标识，或禁止该能力，不自动降级后仍显示同 run。针对外部控制面的操作回执与配置检查点分别版本化。

通用服务已使用持久应用回执与恢复 outbox 分阶段处理重试：原生 outbox 指向原 run，旧兼容路径需要新 run 时预分配候选 ID、在同一 run 文件写屏障幂等查找/创建。服务内部旧函数名包含 continuation 不意味着原生路径一定新建 run，应以持久 ID 核对。

## 11. 低风险自动应用与服务恢复

兼容会话字段 `autoApplyLowRiskConfiguration` 独立于 `approvalMode`，默认 false。`isAutoApplicableConnector` 当前仅允许 PubMed，服务端控制资格，模型不能新增白名单。

仅历史 v2 Native PubMed 提案复用原 companion 的独立授权与 `autoApplyAt=createdAt+30s`；旧专用流程继续兼容。新 Agent/主动入口生成 v3 请求，撤回 companion 倒计时，framework 的自动授权检查明确拒绝 protocolVersion=3，不选第一个方案作为隐式同意。前端倒计时只展示；到期由服务端检查授权、基线、连接器/插件、会话、Stop generation 和任务状态，经同一决定服务应用并恢复。浏览器关闭不能取消服务端既有授权，进程重启应恢复未决计时。

关闭授权应清除/抑制到期操作，使请求回到人工确认。到期与人工确认只有一个结果，重复扫描读取已有状态。拒绝、取消、Stop 或前提失效均阻止自动应用。启用后的工具仍按自身权限策略执行。

API 定时器识别同 ID 的 v2 请求时，必须调用 `framework.decide(..., revision, previewDigest, ..., "auto")`，不能调用 legacy 写入。`decisionQueues` 将载荷校验、决定来源、公共决定和阶段补齐一并串行；无效 revision/digest 不写 decisionSource。进入决定及同步领域 apply 前双重检查独立授权、白名单、到期时间和 Stop generation；公共服务继续检查资源版本与批准摘要。撤销授权删除/抑制倒计时元数据，不改原预览摘要。

`autoApplyAt` 和 `decisionSource` 是可选阶段元数据，不是可由 Agent 修改的配置字段。旧倒计时只为符合历史 v2/legacy 语义、Native run 绑定且已安装就绪的 PubMed 开放；新 v3 及全部 P1 人工决定，模型/凭据和其他连接器同样不自动批准。UI 展示真实服务端截止时间并提供拒绝；旧专用 wizard/preflight 保留兼容，未整体迁移。

上游 `auto_confirm`、工具 Always allow、Smart Approval 与本项目配置授权保持独立。不得复制这些标记作为本地配置批准。Smart Approval 的审核超时与本项目预授权后的 30 秒缓冲含义不同；现有配置定时器继续由本地服务端持有，外部 worker 到实际提交时再复核授权和 Stop。

| 中断窗口 | 恢复要求 |
|---|---|
| 工具批次未结束、检查点未提交 | 不开放确认；核查实际执行状态，不重放未知动作 |
| 检查点与 blocked 已提交、尚未决定 | 恢复同一 run 与待确认卡 |
| 已应用、恢复尚未入队 | 依据持久决定和匹配检查点幂等补齐恢复；本轮通用 phase/outbox 幂等补齐；兼容记录维持旧恢复路径 |
| 原 run 已 queued/running/terminal | 关联原 ID，不另建第二个任务或重复执行 |
| 外部验证发出而结果未知 | 不自动重发，标明未知并等待明确处理 |
| Stop 发生、旧到期操作再次被领取 | generation 校验失败，不唤醒 |

普通崩溃中的 running/工具权限等待保留各自恢复策略，不能把所有 blocked 都当成配置暂停。重启支持与自动到期都需独立故障测试，不能只根据人工确认真实用例推定通过。

## 12. 前端、事件与设置同步

卡片展示按 session/run/request 绑定，决定前恢复实体快照，运行过程继续使用既有事件流。本轮前端通过独立请求快照/刷新和现有 run 事件恢复状态；没有宣称完整 configuration.required/updated/resolved SSE 事件族全部落地。未来事件必须携带 revision/stateVersion，不能用收到事件触发再次决定。

| 状态 | 展示与动作 |
|---|---|
| 无模型、未打开卡 | 轻量入口；可输入草稿 |
| 待确认连接器 | 完整卡，范围、任务等待、确认/拒绝与可选倒计时 |
| validating/applying（通用流程） | 明确阶段，禁止重复提交；保留 Stop |
| applied/dismissed/cancelled/expired | `ProcessRecord` 默认折叠的一行结果，可展开详情；已保存阶段失败可单独重试接续 |
| stale/needs_fix/failed | 保留错误与新预览入口，不用旧摘要再次批准 |
| 配置已保存、恢复未开始 | 如实显示排队或待处理，不能显示任务完成 |
| 工具权限 blocked | 独立权限卡，不重复完整配置卡 |
| 最终任务结束 | 单次最终助手答复，轨迹可回看 |

决定后隐藏可操作卡并归位到原 run，避免同时在历史和底部显示重复回执。展开状态 key 使用 request 稳定 ID。回执不能替代权限，也不能把已应用错误标为任务已完成。

异步请求捕获 sessionId/requestId，SSE 核对 session/run，再更新当前视图。会话切换不得混入旧卡、旧工具输出或文件。设置提交后通过既有加载入口刷新模型列表、会话设置和来源；避免设置页与对话显示两份状态。

目标事件与普通 DTO 一样脱敏，用 ID/version 去重；刷新以快照补齐，SSE 重放不能重新调用决定接口。表单 label、错误提示、状态播报、键盘展开、窄屏与 i18n 纳入验收。

### 12.1 复用组件与协议映射

保留 ModelConnectWizard、ConversationalConnectorCard 与 ProcessRecord 的兼容体验；新增 ConfigurationRequestCard 统一渲染归一化字段，QuickConfigurationMenu 提供七项登记动作入口，v3 卡片复用方案选择、受限 multiselect 和专用凭据 renderer。前端 actions 调用真实 preview/credentials/decision/query/continue/reversal；凭据与 handle 仅留组件内存，不进入对话状态。WorkSwarm ConnectTokenModal 的字段/状态处理作为参考，调用方使用本项目设置客户端与 credential-bridge，不引入其全局 store、路由、桌面壳或账户状态。

普通选项可以复用统一交互请求模型；凭据字段单独渲染和提交，不放入通用问答 answers。外部 credentials_required/auth_required/connected 映射为本项目请求/操作状态，connected 仅说明连接就绪，不能直接发送配置 applied 或 run.completed。多个连接依赖的 onAllConnected 只触发后续激活核对，不直接声明原任务恢复。

事件带本地 session/run/request 与 revision/stateVersion；来自上游的状态先经绑定和脱敏投影，再进入既有 SSE。决定处理完成后发统一 resolved 语义，使卡片收起并可回看；丢失事件由快照恢复，不以清掉 UI 代替服务端请求完成。

## 13. 撤销、迁移、上线与回退

已接入的模型选择与连接器撤销会创建反向提案，复核 receipt 后的最新资源版本并恢复原字段缺失/继承；其他动作没有因此自动获得撤销。已被引用资源不因一次撤销删除。外部验证、已发送请求和业务动作不能撤销。当前不提供删除共享服务、回滚网络请求或任意设置的万能撤销。

迁移采用新增表/可选字段与独立 schema 版本；不占用未经核对的全库版本号，不整体重写旧 run/事件。旧检查点和关联后续记录继续可读，历史语义不被篡改。

完整框架可另设发布开关，但该开关及默认值必须真实实现后才写入部署合同。关闭新增功能时应仍能读历史、取消等待和清理草稿，不能隐式回滚已保存配置。

删除会话清理其请求、未引用凭据草稿、通知和相关恢复记录，不顺带删除全局 provider。物理清理 CAS 遵循既有引用/保留策略。普通测试只按明确 project/session ID 清理。

降级前先抑制/排空待恢复操作、清理草稿并备份实际数据目录；旧二进制忽略新字段不等于调度安全。前后端同步发布，验证 schema、决定、快照和历史显示兼容。

日志只记录 request/run ID、版本、能力、范围、字段名、耗时与结果，不记录密钥、handle、认证 URL 或原始 provider 响应。正常配置等待不计作模型故障，验证失败、应用失败、恢复失败与用户拒绝分别统计。

## 14. 开发任务定义与完成条件

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

本轮本地与 Native 工作包已并行接入，实际交付状态见第 18 节。接下来先用 DEV-CFG-07 收口当前发布验收，再逐项贯通 DEV-CFG-08/09 的真实宿主；未达依赖门槛的设置不随本地版本默认开放。

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

当前相关测试入口还包括 configuration/request-service.test.ts、credential-bridge.test.ts、domain-adapters.integration.test.ts、http.integration.test.ts、native-activation.test.ts、runtime-contract.test.ts 与 agent-runtime.integration.test.ts。兼容相关测试入口包括 `services/api/src/configuration/explicit-connector.test.ts`、`runs/index.test.ts`、`runs/model-context.test.ts`、`store.test.ts`、`server.test.ts`、native-agent 测试和 `apps/web/tests/` 中的配置相关组件测试。新增文件必须进入包实际测试发现规则，不仅写一个无人执行的文件。

### 15.2 环境与命令

本轮 Windows 源码位于 `C:\Users\wyh\Documents\Deerflow\ScienceDiscovery-conversational-settings`，对应 WSL 隔离树 `/root/sciencediscovery-conversational-settings`。只同步源码，在 Linux 内安装依赖和生成构建产物；保留目标树中的 `data`、`.sciencediscovery-data*`、`node_modules`、`.git`、`.tmp`、`dist`。不使用主树 `/root/sciencediscovery` 或主服务 4310 的数据进行这些验证。

`dist` 必须排除。本轮曾因中途同步 Windows Runner `dist`，覆盖 Linux SEA 的 build-info，导致版本元数据不一致；补齐 Linux 产物后再同步源码仍会重新引入该问题。同步完成后才在 WSL 构建、生成 SEA 和运行测试，期间不再次同步。禁止未经确认使用 `rsync --delete`。

适用命令如下；这是开发验证要求，本轮完整 WSL 检查结果由根流程统一回填，状态 v10 单独列明待执行、通过或失败：

```bash
rsync -a \
  --exclude data --exclude '.sciencediscovery-data*' \
  --exclude node_modules --exclude .git --exclude .tmp --exclude dist \
  /mnt/c/Users/wyh/Documents/Deerflow/ScienceDiscovery-conversational-settings/ \
  /root/sciencediscovery-conversational-settings/
cd /root/sciencediscovery-conversational-settings
pnpm build
pnpm runner:binary
pnpm --filter @sciencediscovery/api test
pnpm --filter @sciencediscovery/web test
pnpm architecture:check
pnpm typecheck
pnpm test
```

优先用普通用户运行测试。在 WSL root 环境执行包含“不允许写目录”断言的完整套件时，需要让测试进程遵循目录 mode bits；本轮使用 `setpriv --bounding-set=-dac_override,-dac_read_search pnpm test`。这只是去除 root 的权限绕过能力，不跳过测试、不改产品逻辑或测试断言。完整结果仍以状态 v10 的最终记录为准。

资源受限时可设置 `pnpm_config_workspace_concurrency=1` 串行运行各包。历史全包回归曾因 executor 的真实 Docker/SSH 外部用例缺少 Docker 而失败；用户已明确跳过这一项，该决定继续适用于本轮。普通包命令复用 `SCIENCE_AGENT_DOCKER_SSH_TEST=1` 显式启用，否则输出带原因的 skip；共享测试计划显式选中该外部用例时仍实际执行。Docker/SSH 真实部署链路仍记为未验证，skip 不是通过；本轮新增生产代码需要重新验证，最终结果见状态 v10，不沿用历史成绩。

本轮浏览器验收使用 `http://127.0.0.1:4420`（Runner 4421，evolve 4423）。Windows 已有另一实例占用 4410，因此保留原实例并给新 WSL 构建另分配端口；核对 `/health` 的 Linux Runner 身份后才操作临时项目。不能仅凭相同端口或页面标题推定浏览器访问的是本轮代码。

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

上一轮四适配器与 Native 协调记录于设计/开发/状态 v17/v7/v7，v18/v8 明确下一阶段目标；本轮 v19/v9 更新已进入隔离分支调用路径的 P0/P1 七能力、v3 选择与 Native overlay。历史[状态 v7](https://github.com/wyhohyw/sciencediscovery/blob/f58c728b4395ed32f7fa1161636e87c9d6123f5d/docs/zh/explanation/conversational-settings-status.v7.md)保留；最新构建、WSL、真实模型、浏览器及崩溃恢复的最终结果见[状态 v10](conversational-settings-status.v10.md)。源码接线不代表已经合入主仓或全部验收。

已本地执行的核心、领域、凭据与真实 HTTP stub 验证见状态 v10；单元/模拟通过不等于真实 WorkSwarm 服务已接通。2026-10-16 的真实 DeepSeek/PubMed 同 run 记录只证明旧切片。新版本真实模型/浏览器闭环若未重跑，则继续明确标记待验证。

完整发布仍需列出适配器支持表、schema/迁移影响、CFG 断言、故障与并发证据、WSL 命令结果、真实/模拟边界和测试数据清理。JiuwenSwarm 宿主与外部条件写入缺口不因本轮端口代码和合同测试而消失。

## 17. JiuwenSwarm 已有能力与新增开发边界

### 17.1 评估口径与当前接入状态

本节将 WorkSwarm 产品与其 JiuwenSwarm 仓库按同一上游分析，同时把其依赖的 openJiuwen core 单独标明。已有能力指第 2.4 节固定源码中确认存在的模块，不表示 ScienceDiscovery 已经通过相应集成验收。

仓库默认安装标签为 workswarm0.2.6；凭据 Schema 和连接编排在该标签中已存在。较新 develop 的配置重载、恢复身份校验和 core 持久化顺序，仍需在实际依赖与补丁组合中逐项核对。当前 adapter 保留 `/agent/jiuwenswarm-config` 搜索设置，并新增认证后的 `/agent/configuration/capabilities` 握手；尚未实现实际配置 interrupt 宿主或条件写入 operations 后端。[本项目桥接](../../../services/adapter/src/sciencediscovery_adapter/agent_runs.py)、[旧标签凭据层](https://github.com/openJiuwen-ai/jiuwenswarm/blob/70aa6715992d9cb2c3ec81ee35991d13747e5ce0/jiuwenswarm/server/runtime/mcp/credential.py)

### 17.2 上游已经提供什么

| 已有能力及源码 | 已解决的问题 | 本项目仍需适配的边界 |
|---|---|---|
| core ConfirmInterruptRail / AskUserRail / InteractiveInput | 在工具边界请求确认或输入，并通过中断 ID 接收回答 | 配置提案与本地 request/run 绑定；普通答案不能传递明文凭据 |
| core ToolInterruptHandler 与宿主 checkpointer | 保存中断状态并重新进入待处理工具，再继续执行 | 确保持久化先于卡片；桥接工具重入只读已有决定；对接本地 Stop、队列和 run 终态 |
| WorkSwarm StructuredAskUserRail、问题卡与 continuation 校验 | 结构化选项、前端交互及响应身份校验 | 映射为本项目请求、卡片和 ProcessRecord；不直接复制上游全局 store 或账户上下文 |
| credential.py、token-schema.json、ConnectTokenModal | 凭据字段描述、缺失凭据提示、输入与保存/连接 | 字段归一化、权威存储归属、草稿与计划授权；保存并连接不能伪装成只读验证 |
| usePendingConnectorFlow | 顺序连接依赖，遇到凭据/认证要求引导用户，完成后调用后续步骤 | 当前连接编排不直接等于研究 run 恢复；需要接入激活核对与恢复桥接 |
| config/model/MCP 管理及 reload_runtime_after_config_save | 领域配置保存、连接和运行时配置更新 | 作用范围映射、条件写入与操作回执；核对目标任务实际工具目录，不能只看全局 reload 成功 |
| Smart Approval / 工具权限系统 | 在既有工具权限边界内自动审核或请求人工决定 | 不作为业务配置授权；保留本项目独立配置授权、白名单和服务端倒计时 |

固定源码入口：[core 中断 API](https://github.com/openJiuwen-ai/agent-core/blob/1fe6a03690766e64a686ac51e873495a617b5ca5/docs/zh/2.%E5%BC%80%E5%8F%91%E6%8C%87%E5%8D%97/API%E6%96%87%E6%A1%A3/openjiuwen.harness/rails/interrupt.md)、[恢复处理器](https://github.com/openJiuwen-ai/agent-core/blob/1fe6a03690766e64a686ac51e873495a617b5ca5/openjiuwen/core/single_agent/interrupt/handler.py)、[WorkSwarm 提问扩展](https://github.com/openJiuwen-ai/jiuwenswarm/blob/1372eda94970bf91d5dc0cb03d784e971f39b991/jiuwenswarm/agents/harness/common/rails/ask_user_rail.py)、[凭据层](https://github.com/openJiuwen-ai/jiuwenswarm/blob/1372eda94970bf91d5dc0cb03d784e971f39b991/jiuwenswarm/server/runtime/mcp/credential.py)、[连接流程](https://github.com/openJiuwen-ai/jiuwenswarm/blob/1372eda94970bf91d5dc0cb03d784e971f39b991/jiuwenswarm/channels/web/frontend/src/components/ConnectorMarket/usePendingConnectorFlow.tsx)、[配置重载](https://github.com/openJiuwen-ai/jiuwenswarm/blob/1372eda94970bf91d5dc0cb03d784e971f39b991/jiuwenswarm/server/runtime/gateway_adapter/config_adapter.py)、[Smart Approval](https://github.com/openJiuwen-ai/jiuwenswarm/blob/1372eda94970bf91d5dc0cb03d784e971f39b991/docs/en/SmartApproval.md)。

这些模块足以减少重复建设：不另造 Python 中断引擎、不重复维护连接器字段定义、不绕过领域服务直接改配置文件。需要补充的是产品合同与已有模块之间的连接，以及它们没有替本项目保证的事务和运行一致性。

### 17.3 我们需要新增什么

| 新增交付 | 为什么上游模块不足以直接替代 | 实施位置与工作包 | 可核对的产物 |
|---|---|---|---|
| 有限能力目录与统一提案 | 上游问题/配置接口不表达本项目完整的能力、Session 继承、差异和批准版本 | 本项目 registry、request-service；DEV-CFG-01/02/06 | capability 注册、脱敏请求、previewDigest、版本绑定与决定接口 |
| 字段和凭据桥接 | 上游凭据 UI 与本项目存储、认证、草稿生命周期不同 | 本项目前端 renderer/credential-bridge，必要时增强宿主凭据接口；DEV-CFG-03/05 | 字段映射、专用提交、唯一正式存储、取消与验证边界 |
| 外部配置条件提交 | 普通 config.set 的成功响应不提供本项目要求的全链路事务与重试保证 | 本项目 backend-control/operation-worker；宿主补条件写入、去重和查询；DEV-CFG-08 | 不可变 operationId、权威 receipt、未知状态核对与失败恢复 |
| 配置生效核对 | 上游连接完成或 reload 安排成功，不证明暂停的目标 run 已加载新工具 | 本项目 activation-coordinator 与运行适配；DEV-CFG-04/08 | 目标有效版本/工具目录指纹、生效回执、只含获准增量 |
| 研究 run 与中断桥接 | core Session/interrupt 与本项目 run、事件和 Stop 不是同一实体 | 本项目 runtime-bridge/现有 adapter；DEV-CFG-04/09 | request/run/Session/interrupt/checkpoint 绑定、同 run 输出、精确取消/恢复 |
| 依赖门槛与集成验证 | 源码存在或上游单测通过不代表安装组合可用 | 本项目部署清单、握手和合同测试；DEV-CFG-07/10 | 固定提交/补丁、支持矩阵、禁用原因与两执行器验收记录 |

其中本地 registry/request-service/credential-bridge/schema renderer/Native 协调已经接入；backend-control/worker、精确 interrupt bridge 已有合同与门控，实际宿主仍待接通。Native 已有检查点、模型向导和兼容连接器提案按第 2.1 节复用；不因新增 JiuwenSwarm 桥接就要求另一套模型注册表、凭据库或前端状态源。

### 17.4 用一个配置缺口解释还差哪一段

对于“原研究任务需要一个尚未连接的工具服务”，上游组件可以完成字段提示、连接/认证流程和工具级中断恢复。完整的 ScienceDiscovery 链路还需要把它们绑定在同一个获准变更中：

```text
主 Agent 提案 → 本项目生成差异、范围和版本
→ core 中断持久化 + 本地原 run 等待绑定 → 展示配置卡
→ 用户专用输入与决定 → 复用领域配置/凭据/连接服务
→ 核对远端提交事实和目标工具目录
→ 将决定交回绑定的中断 → 原研究 run 实际调用新工具
→ 配置回执折叠，研究结束后输出最终答复
```

本例用于说明目标链路，不代表当前已支持任意新工具安装。现有 PubMed 只需启用已就绪连接器，输入与连接阶段会更短；独立“启用工具”指令没有研究 run，也不人为补一个恢复任务。

如果只复用 usePendingConnectorFlow，能解决连接依赖的下一步；如果只接 ConfirmInterruptRail，能解决一个工具调用前的确认。只有同时补齐配置计划、持久绑定、生效核对和原 run 接续，才能兑现本 feature 的完整体验。

### 17.5 上游增强与本项目开发的职责归属

| 改动类型 | 优先归属 | 本项目如何使用 |
|---|---|---|
| 通用中断保存顺序、checkpointer 恢复缺陷 | core 上游；受控补丁作为过渡 | 固定包含修复的组合并跑本项目合同测试，不在前端用重发任务掩盖缺陷 |
| 配置服务缺少条件写入、幂等回执或凭据隔离接口 | WorkSwarm 宿主控制面扩展 | 本地窄桥接调用；如果仍有其他写入口绕过版本约束，则不开放相应覆盖型修改 |
| Session 继承、科研 run 绑定、配置范围、回执和授权策略 | ScienceDiscovery | 按本文公共服务及运行适配实现，保持 Native/JiuwenSwarm 的产品语义一致 |
| 卡片样式、领域说明、状态/错误映射 | ScienceDiscovery 前端 | 复用本项目组件，吸收上游字段协议和交互模式 |

交付时分别列出“直接使用的模块、适配代码、宿主增强、上游依赖补丁及本项目新增服务”，并关联 DEV-CFG 工作包和 CFG-T23～28。每项能力通过对应测试后才进入支持表；分析中的计划不计作已开发完成。

## 18. 本轮任务拆分、交付状态与后续工作

这里的“已接入”只说明实现进入本轮实际调用路径；最终验收以状态 v10 的 WSL 及闭环结果为准。“合同验证”不能在评审中改写成“已连接真实 JiuwenSwarm”。

| 工作包 | 本轮交付 | 当前状态 | 对应验收与下一步 |
|---|---|---|---|
| DEV-CFG-01 通用契约 | 七能力有限 registry、受限字段/ID 集合、v2/v3 脱敏 DTO 和严格输入；扩展见 DEV11/12 | 已接入 | CFG-T06/07/23；新增适配器继续显式登记字段/范围/生效规则 |
| DEV-CFG-02 配置服务 | request/revision/CAS、同步领域事务、正式存储复用、receipt/outbox、继承反向提案 | 已接入本地七领域持久保存；run overlay 独立 | CFG-T09/10/12/14/28；不宣称跨远端事务或多 API 共同写目录 |
| DEV-CFG-03 验证/凭据 | 加密草稿/上传幂等、精确绑定与租约、批准后协议验证、指定 profile 修复 | 已接入本地模型 | CFG-T04/06/23；WorkSwarm 所有的凭据资源仍需其宿主专用通道 |
| DEV-CFG-04 运行协调 | Native checkpoint 等待、激活/恢复持久阶段、租约、Stop、真实工具描述与装配 witness | 已接入 Native 连接器 | CFG-T11/13/17/18/27/28；模型不热切换，Jiuwen 实际恢复见 DEV09 |
| DEV-CFG-05 前端 | schema 卡片、专用凭据字段、预览/确认、scope/错误、生效/恢复状态与折叠记录 | 已接入 | CFG-T05/15/19/23；旧向导和专用卡继续兼容，浏览器闭环需单列证据 |
| DEV-CFG-06 Agent 接入 | inspect/propose 通用工具，有限参数、主 Agent 边界、无批准/密钥工具 | 已接入 | CFG-T07/16/22；保留 legacy 提案，不改变 Specialist 原有装配 |
| DEV-CFG-07 验收发布 | core/领域/HTTP/凭据/Native/外部故障/前端测试入口 | 新 P0/P1 联合验收由根流程收口；Docker 外部用例按用户决定明确跳过 | 历史成绩见状态 v7；最新构建、全套/专项、真实/模拟边界与临时 ID 清理见状态 v10，不能沿用旧成绩声称新能力通过 |
| DEV-CFG-08 外部控制面 | HttpBackendControl、不可变 operation、SQLite journal、query-only 超时恢复 worker | 基础合同已实现；宿主控制面待开发 | CFG-T24/28；本项目需统一 Web/TUI/config/model/MCP/凭据写入口的版本、去重与权威查询，并接生产调度 |
| DEV-CFG-09 JiuwenSwarm 接续 | 精确 request/run/Session/interrupt/checkpoint/generation 校验及 resumeKey 合同；固定 core 补丁提案与真实 handler 临时副本验证 | 基础合同已实现；上游提交顺序补丁待集成；宿主桥接待开发 | CFG-T25/27；需实际 rail 注册、Session/interrupt 持久绑定、精确 InteractiveInput 恢复、目标工具刷新和原 run 事件贯通 |
| DEV-CFG-10 依赖能力 | 固定基线常量、认证握手、设置/执行器能力矩阵与不支持原因 | 握手/门控已接入 | CFG-T26；旧安装组合不会凭版本名自动获得 same_run 或外部写入 |

后续采用两条开发线：产品扩展按第 19 节的公共合同 → 运行级覆盖/选择恢复 → P1/P2 适配器推进；执行底座继续依次完成 DEV-CFG-08 的实际宿主配置资源与 DEV-CFG-09 的 core interrupt/run 贯通。已有 Native 验收不替代 JiuwenSwarm 证据；每阶段独立给出正式存储归属、批准范围、实际生效与恢复结果。

历史 v2 Native PubMed 保留 30 秒独立授权策略与双重复核；新 v3 请求撤回旧计时并要求选项批准。旧真实 /runs 结果不扩展为新选择或 P1 的验收，最新重启/跨进程、真实模型与浏览器结果见状态 v10；JiuwenSwarm 对应后端仍待实际贯通。

## 19. P0/P1 开发合同、任务拆分与实施状态

### 19.1 输入依据与复用边界

产品依据为设计 v19 第 15 节及 [#278 人工维护者决策](https://github.com/openJiuwen-ai/sciencediscovery/issues/278#issuecomment-6238467927)：Agent 提出当前阻碍的选项，支持真实的保存应用、仅本次/不长期保存、拒绝并走替代路径；提交或关闭后的明确选择回到任务；Always allow 不取代配置决定。

继续复用现有 registry、schema normalizer、request/revision/digest、独立用户决定、credential bridge、领域正式存储、receipt/outbox、Native checkpoint 和 ProcessRecord。不会为新设置复制卡片、批准接口或密钥数据库。上述 v8 缺口已在本轮接入：注册驱动参数/目录、v3 方案绑定决定、Native 独立 overlay 与替代/拒绝结果。现有 v2 和 legacy 入口保持兼容，禁止通过旧端点批准新选项。

### 19.2 注册驱动的目录与缺口识别

在服务端有限注册中增加设置说明、适用任务/诊断分类、可见字段及参数约束、资源来源、可选保存范围、临时使用能力、生效模式和各后端要求。参数和选项元数据只来自受信任适配器，不接受 LLM 自定义 schema 或任意 config path。

目录按当前用户、Session、执行器和探测能力过滤。读取应返回脱敏当前值、继承来源、候选资源及不支持原因；不得用 inspect 顺便探测外部服务或返回凭据引用。供 Agent 的参数 schema 与服务端校验由同一注册生成，新增设置不再修改通用工具的参数并集。当前 values 已支持标量及受限 string[] ID 集合，normalizer、摘要、表单及领域校验已同步；不接受重复/未知 ID、逗号字符串或任意 JSON。

完整识别目标区分用户显式需求、确定性前置检查和工具结构化失败。当前已有注册目录与有限诊断 code 映射，尚未将任意真实工具/服务失败自动接入配置诊断；不把映射函数存在说成自动错误修复闭环。诊断映射到有限能力和具体目标；网络失败、无结果、限流、认证失败、模型不支持参数需分别处理。不能仅凭关键词启用设置，也不能在错误原因不明时不断建议改 Key、代理或配额。无模型/失效模型路径继续使用确定性入口，不依赖 LLM 自诊断。

### 19.3 可信选项与决定（已接入 v3）

以下 v3 字段已落到共享 schema 和实际端点；不是给 LLM 的执行或批准接口：

| 实际字段/记录 | 含义与约束 |
|---|---|
| reason / needKey | reason 为脱敏原因；needKey 由服务端按 task/能力/目标/相关基线生成。未新增任意结构化 need 或失败日志透传字段，也不是批准凭据 |
| options[] | Agent 提交已注册能力/目标；服务端按真实能力生成稳定 id，kind 仅 persisted_apply/run_overlay/alternative/dismiss，不接受 Agent 自定义执行计划 |
| 选择后的内部 plan | 与具体能力、目标、非敏感字段、保存/任务范围、生效和预算绑定；不是公共 option.plan 可写字段，内部计划不发给 Agent |
| alternativeId / description | 当前仅真实已有 Web 路径；说明覆盖差异，不包含命令、可执行补丁或任意工具调用序列 |
| selectedOptionId + previewDigest | 用户先选方案，服务端针对选中方案生成预览；摘要绑定选项、目标、参数、范围、预算、基线和版本 |
| decision | 绑定用户身份、请求、revision、optionId、digest 和操作幂等键；Agent 工具不能提交决定 |
| choiceReceipt | 选择类型、作用范围、是否有配置写入、原任务/检查点、生效与恢复结果；只含允许披露的信息 |

可在卡片中同时展示多个候选，但一次确认只批准所选方案。切换方案、修改字段、改变范围或基线变化均提升 revision，使旧摘要失效；选项不允许悄悄追加验证或费用。预览和提交时均复核替代工具的可用性；不具备真实能力的选项不展示或标注不可选及原因，不用虚假的 run-only 满足三个按钮。

保留 v2 apply/dismiss 的历史语义。新请求使用明确协议版本；旧客户端不认识选项时不能成功提交，服务端以方案/版本校验拒绝不完整旧载荷，不假定所有历史客户端都能正确显示新版卡片。旧决定接口不能把未选定方案的请求当成普通 apply。持久化和公共投影同步更新，刷新、重放和幂等键冲突按 optionId/版本校验。

### 19.4 运行级覆盖与凭据生命周期

新增独立的 run overlay 记录，不把 run-only 简单加入现有 Session settings setter。记录绑定 task/rootRun、原检查点、请求/option/revision/digest、Stop generation、能力及非敏感字段、基线资源版本和真实激活证据。历史基础 settingsSnapshot 不变，激活时只叠加已经批准的有限增量。

当前实现只开放 Native 已安装就绪 MCP 连接器的临时启用：复用 MCP 目录与真实工具 factory/witness，检查点恢复后确实能调用新增工具；普通下一任务及已有排队任务继续使用各自原配置。不先写 Session 再回滚，不让任务完成/取消后的清理覆盖用户新编辑。其他设置是否支持 overlay 由适配器和执行器声明，模型不因此支持热切换。关联接续继承 overlay 必须核对同一 task 身份；无可靠关联或无法生效时禁用该选项。

临时使用不等于不持久化恢复信息：非敏感 overlay、决定和回执已独立持久化；崩溃/重启最终结果见状态 v10。当前没有开放新临时凭据；未来若增加，临时凭据必须是后端隔离加密资源，只返回可用状态，限定 task/目标/用途和租约，终态后按策略清理且保留脱敏审计。不得复制到正式全局凭据库、聊天、事件或浏览器存储。没有临时凭据生命周期的适配器暂不开放对应 run-only 选项。

任务终止、Stop、过期及归档/删除使未提交的覆盖失效，持久租约和恢复动作同时校验 generation。进程重启恢复同一任务所需 overlay；终态后不激活、不复用。不能对结果未知的外部动作重放，也不能在临时选项下偷偷修改共享资源。

### 19.5 替代路径、关闭与原任务恢复

用户选择“改用 Web”时，先保存 choiceReceipt 并结束本次配置等待，不写 PubMed 设置；恢复时向原 Agent 提供脱敏结果：选中路径、已验证可用能力、原目标/约束、禁止重复请求的 needKey。Agent 再依据正常执行权限完成研究，不因选中替代方案自动批准网络、工具或安装动作。

“暂不配置”和有意点击关闭按钮返回明确的 dismiss 结果；关闭控件提前说明语义。浏览器刷新、SSE 断线、切换 Session 和折叠记录不提交决定。没有安全替代时如实报告阻碍，不擅自换来源；有其他可独立完成的工作可继续。

同一 task/needKey/目标/配置基线的拒绝不反复弹卡；新用户要求、明确重试或相关基线改变才允许重新评估。去重不能仅存浏览器状态，也不能永久封锁未来任务。Stop 是取消，不是 dismiss 后恢复。所有分支沿用持久阶段、重复提交/恢复幂等和真实工具装配检查，不新增原始用户消息或重放完成的副作用。

### 19.6 设置支持矩阵与分批适配

DEV-CFG-11 已盘点当前设置页、共享 schema、正式领域 API 与两个执行器；完整矩阵内嵌第 20 节，并保留独立矩阵来源。每行包含准确字段、scope/继承、凭据归属、风险与费用、校验/探测预算、生效模式、临时能力、反向操作和证据。下表保留分批领域及规则，P1 已接入 Native；P2～P4 尚未开放修改，不是全部已支持列表。

| 批次 | 候选设置与既有复用入口 | 需要专门实现的规则 |
|---|---|---|
| P1 Skill / Library | runtime settings 的 enabledSkillIds、enabledSkillLibraries、skillSelectionMode；现有安装目录/选择器与设置继承 | 只选择已安装内容，Skill 选择只支持 Project/Session；验证 ID/版本/白名单，明确新 run 装配时机，不因保存成功就宣称活动任务已加载 |
| P1 思考配置 | thinkingMode、thinkingEffort 和模型协议能力 | 所选模型 API variant 确有对应字段才可写；取值、费用与时效明确，不修改已冻结的活动模型 |
| P1 会话审阅 | reviewModelId、semanticReviewEnabled，以及 SessionReviewerSpecialistSettings 的 automaticReviewEnabled/level | 区分运行设置与会话审阅端点，复用现有校验；不把全局 Specialist enabled 开关混为会话修改 |
| P2 Web 服务 | web settings、providerApiKeys、现有搜索/抓取与代理策略领域服务 | Native/JiuwenSwarm 支持的引擎和参数不同；字段按真实后端过滤，Key 走专用通道，付费引擎不得因已有 Key 悄悄启用 |
| P2 已有 MCP 修复 | 已有 MCP 注册信息、连接/授权服务 | 明确服务资源的共享影响、协议与条件写入；先验证后替换；STDIO/安装命令不作为普通字符串直接运行 |
| P3 系统与环境 | proxy、SystemTimeoutSettings、SystemQuotaSettings、MemoryGraph/IdeaTree、Runner 环境服务 | 逐字段确认 scope、生效、资源开销与外部影响；系统级不能伪装 session/run-only，不自动放宽安全限制 |
| P4 新 MCP/OAuth/导入 | 已有 MCP Authorization、注册/安装及受控文件选择入口 | 完整授权回调绑定、来源/路径范围、凭据归属和副作用计划；宿主命令另有执行权限，不由配置选择绕过 |

识别到尚未支持的设置时返回明确原因和可行手动入口，不生成假成功卡片。产品支持矩阵必须分列“可识别 / 可提案 / 可保存 / 当前任务生效 / 后续任务生效 / 后端验证”，每项用实际证据更新。全量盘点完成前不对用户承诺所有设置均可自动修改。

### 19.7 可拆分工作包与依赖

以下按当前源码调用路径记录，最终验收以状态 v10 为准；DEV-CFG-01～10 的底座与宿主边界继续见第 18 节。

| 工作包 | 已接入产物与主要位置 | 当前状态与剩余验收 |
|---|---|---|
| DEV-CFG-11 设置盘点与能力发现 | registry 保存 descriptor；Agent schema 按实际注册生成；inspect 脱敏当前值/来源/资源/后端；settings-catalog 与第 20 节矩阵；diagnostic code 映射 | 已接入有限目录/映射；任意真实失败自动诊断仍未贯通；CFG-T29/33/34/35 最终结果见状态 v10 |
| DEV-CFG-12 通用选项合同 | v3 options/selectedOptionId/choiceReceipt/needKey、方案绑定 revision/digest、HTTP/幂等/公共投影；旧四能力 v2 兼容，P1 仅 v3 | 已接入；旧决定不能批准新选项，CFG-T30/36 最终结果见状态 v10 |
| DEV-CFG-13 运行级覆盖 | configuration_native_runs_v3/overlays_v3、精确 task/检查点/批准绑定、实际 MCP factory witness 与获准增量读取 | 已接 Native ready MCP，不含新凭据/模型 overlay；CFG-T31/27/28、崩溃/重启最终结果见状态 v10 |
| DEV-CFG-14 选择结果与恢复 | 选择 receipt、基线敏感 needKey、已有 Web 可用性/版本检查、dismiss/alternative 返回原目标与检查点 | 已接 Native 四类选择；Stop 不恢复，刷新不决定；CFG-T08/11/13/32 最终结果见状态 v10 |
| DEV-CFG-15 通用选项前端 | 统一 renderer、选项预览、范围/预算、multiselect/专用字段、主动关闭和折叠回执、中英文 | 已接入；浏览器与 CFG-T15/19/30/32 最终结果见状态 v10 |
| DEV-CFG-16 P1 适配器组 | skill.select、thinking.configure、review.configure；正式领域同步 hooks；Library pin/CAS；会话审阅队列快照与 sourceRunId、来源合批和最终 live gate | 已接 Native 后续新排队 run；不热切换，Jiuwen scoped 操作拒绝；CFG-T33 与真实消费结果见状态 v10 |
| DEV-CFG-17 扩展验收与后续适配 | P0/P1 核心/HTTP/UI/实际 /runs/后续运行与审阅专项、故障/重启入口，支持矩阵证据归档 | 验收由根流程收口，最终结果见状态 v10；P2～P4 和真实 Jiuwen 宿主继续待开发，不借旧成绩背书 |

底座开发线继续 DEV-CFG-08 → DEV-CFG-09，并由 DEV-CFG-10 门控。先接真实权威配置资源和所有相关写入口的版本/回执，再接精确中断、core 补丁与恢复。其合同可与 11/12 协调；未贯通前不开放 JiuwenSwarm 的新修改/同 run 能力，也不以本地测试代替实际宿主测试。

多人开发时由公共合同任务先交付类型和正常/错误状态，领域任务只改各自适配器与必要领域接口，前端复用统一 renderer，运行任务负责 overlay/恢复。最终由集成任务验证跨模块链路，避免每个领域各建一套决定或恢复接口。

### 19.8 退出条件与自动策略边界

新增验收编号对应设计第 12 节：

| 编号 | 必须证明的结果 |
|---|---|
| CFG-T29 | 普通任务表达能触发正确的已注册配置缺口；默认有效、网络偶发失败、否定表达不误弹 |
| CFG-T30 | 服务端校验选项及选择预览；篡改 optionId、旧摘要、并发变更和重复幂等键不能越权或重复写入 |
| CFG-T31 | run-only 真正调用获准连接器，Session/Project/Global 与下一任务不变；重启可恢复当前任务，Stop/终态不复活覆盖 |
| CFG-T32 | 替代路径、暂不配置、主动关闭均把正确结果交回原任务；离线/刷新/切换会话不误判拒绝；同 need 不循环弹卡 |
| CFG-T33 | P1 三组各用自然语言验证合法性、继承、正式设置页一致和实际生效模式；不要求用户写工具名或“确认后继续” |
| CFG-T34 | Web/服务凭据错误、验证失败、费用及后端不支持分类正确；候选失败不替换当前有效配置，敏感信息不泄漏 |
| CFG-T35 | 系统级设置的影响与批准一致；不虚构临时使用，不随意放宽配额、安全或网络限制 |
| CFG-T36 | Always allow 不产生配置授权；新增 option/scope 需匹配显式授权，旧 PubMed 倒计时不能批准新选项 |

P0 退出需覆盖持久应用、运行级覆盖、可用替代路径及拒绝四种选择的真实 Native /runs 链路，含刷新、Stop、重启和并发。P1 在其支持的生效模式验收，不强迫所有设置 same_run。测试说明分别记录实际模型/本地脚本模型、真实或离线工具、模拟/真实 Runner 和后端，商业 LLM 行为不能由固定脚本通过推定。

新增能力默认人工决定。保留已有独立授权的 PubMed 30 秒策略时，须限定旧选项、范围、目标和版本；升级到新选项合同需要显式迁移/重新授权，不能选择第一个选项当默认自动提交。维护者没有认可通用倒计时，新设置不继承工具 Always allow 或旧配置授权。聊天误贴 Key 的检测/脱敏另列凭据专项，不承诺识别任意秘密。

本版记录已接入源码，不在文档中预填新的通过数量或全量验收结论。构建、WSL 完整/专项、真实模型与浏览器、崩溃/重启和清理最终结果统一见状态 v10；脚本模型或模拟 Runner 通过不能写成真实商业模型/真实外部链路已验证。

## 20. 完整设置支持矩阵与领域盘点

本节完整内嵌[设置支持矩阵 v1](conversational-settings-capability-matrix.v1.md)正文并按 20.x 编号，方便单页评审，不要求跳转阅读；前置汇总表进一步分列提案、保存和生效维度。源码与实际调用路径优先；最终结果见[状态 v10](conversational-settings-status.v10.md)。独立矩阵保留历史来源，实际验收不得由目录存在推定。

先按产品支持维度分别核对；“已接入”仍不等于最终验收已通过，后端验证结果统一见状态 v10。

| 能力 / 设置组 | 目录识别 | 可提案 | 可保存正式配置 | 当前任务生效 | 后续新排队任务生效 | 后端与验证证据 |
|---|---|---|---|---|---|---|
| model.select | 已有模型/来源 | 是 | Session 绑定 | 不热切换 | 是 | 本地正式模型域；实际执行器和最终结果见状态 v10 |
| model.connect | 有限协议/连接字段 | 是 | 共享资源；首模型 Global 默认明示 | 不热切换，零模型走首次运行入口 | 是 | 本地凭据/模型域；真实/模拟验证分别见状态 v10 |
| model.repair_credential | 精确模型及凭据状态 | 是 | 指定 profile 覆盖 | 不把修复宣称为活动模型热切换 | 后续使用该 profile 的任务 | 本地隔离验证/正式凭据域；最终结果见状态 v10 |
| connector.enable | 安装/有效启用集合 | 是 | 保存方案写 Session；run-only/替代/拒绝不写 | Native ready MCP 的保存或 overlay 可同 run；已有 Web 替代沿原可用工具继续 | 只有 Session 保存方案使新任务默认启用；overlay 不传播 | Native 实际工厂/witness；Jiuwen 同 run 尚无实际宿主；最终结果见状态 v10 |
| skill.select | 安装 ID、库版本和来源 | 是，v3 | Project/Session | 不改变活动装配 | 是，保留已有排队快照 | Native 已接；Jiuwen scoped 选择拒绝；最终结果见状态 v10 |
| thinking.configure | 实际 variant/模型合法值 | 是，v3 且字段真实受支持 | Session | 不热切换 | 是 | Native 已接；Jiuwen scoped 激活未验收；最终结果见状态 v10 |
| review.configure | runtime 字段、会话偏好和全局依赖 | 是，v3 | Session runtime + 正式会话审阅域 | 不修改活动/已排队快照；全局开关仍实时约束实际审阅 | 是，automatic/level 额外入队冻结；全局禁用时偏好仍不激活 | Native 及真实审阅 scheduler 已接；Jiuwen scoped 操作拒绝；最终结果见状态 v10 |
| P2～P4、权限/全局开关及其他盘点项 | 目录列出字段/归属/未支持原因 | 未注册，不生成成功卡 | 本 feature 不写 | 无新承诺 | 无新承诺 | 继续使用正式设置入口；对应适配器及实际后端验收待开发 |

版本日期：2026-10-20。对应设计 v19 §15～16、开发 v9 §19 的 DEV-CFG-11/16，验收 CFG-T29/33/34/35。本文盘点当前正式设置入口，并区分已经接线的领域操作和后续候选。新增源码的 Windows 专项结果只用于开发反馈；WSL、浏览器与真实/脚本模型证据由集成状态记录，不能用本表替代。

“可识别”指 Agent 能从有限目录获知字段、适用场景和不支持原因，不保证任意自然语言均被正确理解。“可提案/保存”指已注册且复用正式存储；“本任务”指已冻结的活动 run，“后续任务”指保存后新排队的 run，不包括保存前已排队的任务。P1 目前仅接入 Native，JiuwenSwarm 的共享配置宿主与 scoped 激活未完成。

### 20.1 已接入的有限能力

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

### 20.2 已盘点但未开放 Agent 修改的设置

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

### 20.3 目录、参数和诊断如何复用

每项能力在注册时附上受信任的 label、适用任务、有限字段和参数规则、诊断分类、生效方式。通用 Agent 工具的 schema 根据**实际已注册能力**生成；通用工具中没有四/七种能力的参数并集维护表。P1 领域 normalizer 直接复用相同字段描述，并增加已安装资源、模型合法取值和作用范围检查。

诊断只接收结构化分类：缺模型、精确模型凭据缺失/认证拒绝、连接器未启用、Skill 被白名单排除、显式思考/审阅需求。未知故障、网络错误、超时、限流、无结果均不映射为修改 Key、代理或配额。目录提示是识别依据，不是用户授权，也不承诺已有全部工具错误都自动转换成诊断；新的工具领域仍需把可核实分类接到相应边界。

主 Agent 的工具仍只有 inspect 与 propose；没有批准、密钥读取、配置文件写入、安装命令或授权策略修改。P1 保存结果明确 next_run，不借用连接器的同 run 恢复承诺。JiuwenSwarm 未通过真实宿主验证的 scoped 操作，在目录中明确不可用，并在提案、预览和写入边界拒绝。

会话审阅的 `automaticReviewEnabled` / `level` 原来由执行器和后台审阅 scheduler 读取实时 Session 偏好，单改配置表无法兑现“旧排队任务不变”。本轮因此在正式入队时写入 `settingsSnapshot.sessionReviewer`，运行器使用该冻结值；自动审阅通过 Artifact version 的 `turnId` 及 Subagent 的 `parentTurnId` 精确绑定原 root Run，`ReviewerAuditTask.sourceRunId` 保留该归属，合批按 source Run/level 分隔，后台执行继续读取原 Run 冻结值。Deep 审阅复用原 Run 的模型/工具运行快照；全局 Reviewer Specialist 开关继续作为独立实时总闸。没有来源绑定的历史产物、已存在但缺少新增快照的旧 Run，以及手动发起审阅保留原有当前偏好语义，不按时间猜测所属 Run。已经明确绑定 `sourceRunId` 后若来源 Run 丢失，审阅失效收束，不能借用当前 Session 偏好/模型继续。忙碌检查、检查点和 claim 的异步持久化完成后，在实际调用审阅执行器前再次核实来源与实时全局开关；手动审阅同样不能在全局关闭后新启动。

仅本次连接器 overlay 生效后，inspect 分别返回活动任务的实际获准工具集和 Session 已保存工具集。活动集来自同 run、已应用且已激活、匹配批准 revision 的持久 witness；不以当前安装目录代替授权。这样“本次使用了 PubMed、Session 没有长期启用”不会在恢复后的第二次 inspect 被误判为仍需启用。

### 20.4 本轮专项证据与待验证项

新增 `p1-adapters.integration.test.ts` 覆盖：注册参数生成/跨领域拒绝、受限多选、Project/Session 继承、安装目录校验、库版本 CAS 与继承 pin 保留、协议/模型思考合法值、冻结任务不变、会话/全局审阅边界、正式页面编辑造成旧批准失效、脱敏 inspect、all 模式不重复提案、v3 选项和未验证后端门控。新增真实 ReviewerAuditCoordinator 用例证明：旧已排队 Run 冻结 `true/quick`，保存新偏好 `false/deep` 后，新 Run 得到 `false/deep`；旧 Run 的产物仍被实际执行 Quick 自动审阅，新 Run 的产物不触发自动审阅；旧 Run 的 settingsSnapshot 不变。

以上是测试覆盖说明，最终通过数量、WSL/浏览器端到端、脚本模型与真实 LLM、真实或离线工具、Runner 与 Jiuwen 后端的证据由集成记录逐项回填；未执行部分保持待验证。

## 21. 审查修订实施与验收（CFG-R01～R14）

本轮以设计 v22 第 17 节为准，保留 `f58c728b` 的可追溯审阅基线，在 `7ee6c1d0` 上整合。不得用删除安全比较来修复误阻塞，不得用通用确认按钮替代明确的自定义凭据接收主机授权。

1. **执行依赖与领域恢复（R01/R03/R08/R10）**：Store 提供语义化有效依赖与范围通知；framework/native activation 使用同一依赖证明。正式写入仍执行 CAS；恢复只看冻结执行所依赖的资源。连接器撤销全链路区分移除与新增；受信任恢复数据支持有界长集合。持久仓库提供待办/到期查询，批量有限且不扫描所有会话。
2. **目标安全与领域适配（R02/R11）**：统一标准化和分类端点；预设协议/主机有明确映射；用户 HTTP 路由接收专属端点同意并绑定精确目标，Agent 工具不暴露批准字段。验证和连接发送前再次检查。本机/私网按标准化主机字面量分类，独立确认后保留 HTTP 网关兼容，但须明确显示密钥明文传输风险；对话内提案的其余 HTTP 端点拒绝携带凭据，保留禁止重定向的连接测试。Provider 按合法身份复用，不覆盖共享凭据；凭据忙、未知模型取值分别处理。
3. **前端生命周期（R04/R05/R06/R07/R09/R11）**：请求组件 key 使用稳定 ID；中间预览成功先接收 revision，再上传凭据。密钥及焦点不因相同目标的 revision 更新丢失。无模型确定性入口产生统一持久请求；防止重复入口和重复提交。所有提交提前返回有终态；`resumption.started` 仍属于需要协调的状态，不能在原任务终态事件先于回执写入时停止轮询。列表合并不会回退或删除较新卡片；所有能力回执进入持久轨迹，过期说明和 Stop 入口明确。
4. **集成与交付（R12）**：逐项处理上游 Store、Runs、HTTP、Reviewer、MCP 工具和模型连接代码冲突，保留双方设计约束。删除本 feature 初始合入中的重复历史文档，仅保留当前完整版本与必要证据；重建 HTML 并验证锚点、单页阅读和链接。先专项测试，再完整 WSL 构建/测试与真实模型浏览器确认、暂停、同 run 恢复/完成、Stop、范围隔离和数据清理。所有结论分别记录通过、未验证与不适用，旧测试结果不能替代新分支结果。

回归测试应以实际失败场景构造，不只断言实现内部字段；主机未确认测试须观察网络未发出，恢复测试须有无关更改仍成功和相关更改拒绝两组对照。凭据重试测试要覆盖中间预览已成功、上传失败、同表单重试成功以及目标切换清空四个阶段。项目/全局通知同时检查其他会话收到更新和旧 run 快照不变。

CFG-R13 补齐原 CFG-01 的发现合同：`inspect_configuration.installedConnectors` 必须由实际注册目录与服务端状态生成，不只返回能力名称或当前启用 ID。通用发现和旧专用提案描述复用同一资源目录；分别表达 installed、ready、原 run 已装配、Session 已保存、相关策略许可和 same-run 支持。未就绪不可被宣传为能立即启用。集成脚本必须先读取发现结果再选中资源、创建提案和验证同 run 完成；真实模型未产生正确提案不能算端到端通过。

### 21.1 模型目的地授权合同

对话内配置请求视图的 `endpointAuthorization` 包含 `endpointTarget`、`host`、`classification`、`status` 和 `revision`。服务端按最终协议、variant 与实际请求 URL 生成目标标识，不信任显示名称。`trusted_preset` 仅匹配已登记的 OpenAI / Anthropic 标准 HTTPS wire 地址；其他 HTTPS、本机和私网均需用户明确同意。公网 HTTP 在有凭据时拒绝；免密探测也需单独确认。验证不跟随重定向。

用户专属 `POST /api/sessions/{sessionId}/configuration-requests/{requestId}/endpoint-consent` 接收 `{revision, endpointTarget}`，只记录同意，不发送验证请求。Agent 提案工具不接受此字段。目的地相同的后续凭据预览可继承同意到新 revision，目的地或字段目标变化必须重新确认；上传、批准验证及最终每次网络发送前均重新核对。模型凭据修复在实际发送前重读正式模型，不能将已确认的 Key 发往后来更换的地址。

既有设置页中用户手动保存模型后的测试按钮，保留原来的明确付费探测同意；它不被标成自动可信预设。该路径冻结用户所选目标、每次发送前复查；用户亲自填写的 HTTP 网关不额外增加风险复选框，见 CFG-R14。本轮不把设置页的手动模型管理改造成 Agent 提案；对话内 `model.connect` 和 `model.repair_credential` 始终使用上述专属同意接口。

### 21.2 手动模型连接回归（CFG-R14）

移除 v11 引入的黄色 HTTP 确认区及前端阻塞。手动获取列表直接使用用户输入，保存和重测保留既有付费探测同意；不默认授予 Agent 任何端点权限。

POST /api/models/{id}/test 保留 consent=true 和 compatibility，可选 expectedDestination={baseUrl, profileUpdatedAt} 只用于目标版本检查。客户端从本次表单与服务器返回模型比对后构造快照，不需要用户再次勾选。旧手动客户端只发送付费 consent 也能测试；空 body 仍返回明确提示。提供了错误/过期快照时返回 409；每次发送前复查保存版本和冻结目标，防止兼容性矩阵中途更换地址。

回归覆盖 HTTP 表单无需额外勾选即可获取列表、付费同意前不生成、失败保留草稿并能重试、改地址撤销既有付费同意、已有模型按钮只保留原确认、错误目标/过期版本/测试中修改拦截，以及原 Agent 目的地保护。浏览器与公开 API 的本地假服务仅证明产品路径，不代表真实 OpenCSI 可连接；真实网络故障记录与这些回归结果分开。
