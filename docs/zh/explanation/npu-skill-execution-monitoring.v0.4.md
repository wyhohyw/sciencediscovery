# Protenix Skill 主仓迁移设计 v0.4

- 状态：Draft；设计已调整，迁移代码和新增验收尚未执行。
- 日期：2026-09-17。
- **贡献目标**：`openJiuwen/sciencediscovery` 主仓。
- 最新核查基线：`main@9a481c49b20c8ceec7a79fc48f3300b8776010f8`，本次通过 Git fetch 更新后读取源码。
- [v0.3](./npu-skill-execution-monitoring.v0.3.md) 保留为历史设计；本版对其修改范围和资源方案作明确收敛。
- 参考材料：4910 备份分支 `bc41e28` 的 GND 调用方式、本地 `420d545` 的适配探索；两者均不是完备实现或主仓能力依据。

## 1. 本版结论与修正

优先使用主仓已经存在的沙箱、科学环境、冻结 skill 包、远程 Workspace、后台 Execution 和完成通知，实现 Protenix skill 的新调用路线。

此前将“模型资源注入、WorkflowRun、重启 reconcile、进度 UI”放入整体必需改造，扩大了首个贡献的范围。现修正为：

1. **首个贡献默认修改 skill、入口脚本、bootstrap、直接调用方及必要测试/文档。** 不预设新增 Runner 模型专用变量或挂载接口。
2. 模型代码优先通过科学环境的正规安装入口提供，或作为目标 Workspace 中已准备并固定版本的代码；大权重和数据库通过已有文件准备路径放到目标 Workspace 后访问。必须验证实际规模的成本，不能因为路径可见就宣称整个模型链已适配。
3. 如果真实需求是直接复用 Workspace 外的宿主共享权重目录、保持只读且不复制，则这是现有能力边界之外的独立候选框架贡献。先确认需求和实测瓶颈，再设计通用资源绑定，不从 GND 专用变量倒推 Runner 需求。
4. 一个 Protenix 全流程由一个 Execution 承载，复用现有完成通知。新 WorkflowRun/多步骤自动续跑、重启 reconcile、结构化进度 UI 分开评估，不作为完成单次调用迁移的默认前置。

v0.3 的主仓优先、远程目标固定、未知状态不重放、最终结果验收等原则继续有效；其中“必须新增资源注入”和“先实现全部 P1/P2 才能迁移”的范围判断由本版替代。

## 2. 主仓能力核查

以下均来自固定的主仓提交，不从 4910 或本地分支推断。

| 需求 | 主仓已有能力与证据 | 本次决定 |
|---|---|---|
| 科学代码/依赖 | `environment_install` 支持管理环境和本地 wheel；`environmentPrefixBindArguments` 将所选环境只读挂到 `/opt/science-env`，同时保留原 prefix 路径 | 使用正规安装入口；不把手工改环境前缀当作资源接口 |
| Skill 脚本与小资源 | `buildSandboxLaunch` 只读挂载冻结包；远程 `prepareExecutionTarget` 先同步并校验包 | 入口用 `$SCIENCEDISCOVERY_SKILLS_DIR/...`；不打包大型权重 |
| 输入、代码目录与权重文件 | 目标 Workspace 挂到 `/workspace`；已准备在其中的文件可被模型读取 | 可以作为不改 Runner 的资源准备路径；Workspace 是可写的，不声称它等价于共享只读模型仓 |
| 远程文件流转 | `workspace_transfer` 在授权 Workspace 之间按明确映射传输，目录会展开，核对内容和逐文件结果 | 显式准备输入/资源和回传结果；不是任意宿主目录导入工具 |
| 环境变量 | 每次 `run_shell` 是新进程；官方文档要求 export 与命令放在同一脚本 | 入口脚本根据已验证的沙箱路径设置所需变量，再启动模型；无需模型专用 Runner 注入 |
| NPU | Runner 根据所选设备探测并映射到连续沙箱编号，注入 CANN 所需环境 | 直接复用；不自行 bwrap、挂设备或推断宿主卡号 |
| 长任务等待 | `background`、`wait_ms`、`execution_status/logs/cancel`、完成通知 | 直接复用；取消 sleep TICK，不造新等待器 |
| 原始执行身份 | 控制面已有 Execution ID、owner、runner/workspace/turn、状态和接受信息 | 单任务迁移直接保留这些信息，不要求启动后另开 Shell 写 checkpoint |
| 宿主任意目录只读挂载 | 当前公开 permission schema 的 mount source 只有 `workspace`；Shell 入口只接受一个 read-write Workspace mount，其他 bind 由框架固定构造 | 未发现可配置任意宿主共享资产目录的通用接口；作为独立候选需求，不能谎称现有配置即可完成 |

源码定位（仓库相对路径，均为上述主仓提交）：

- `packages/schema/src/permission.ts:19`：PermissionMount 的 source 范围。
- `services/runner/src/executor.ts:418`：科学环境 prefix 的只读挂载。
- `services/runner/src/executor.ts:701`：沙箱 mount/environment 构造；`--clearenv` 后显式注入框架环境。
- `services/runner/src/executor.ts:1099`：Shell 入口验证、Workspace/环境/skill/NPU 装配。
- `packages/workspace/src/workspace.ts:881`：Workspace transfer schema；`:1291` 附近为托管 Shell 提交与等待。
- `services/api/src/workspace-transfers.ts:89`：按已提交快照读取并展开文件映射，不能把任意宿主路径当源 Workspace。
- `services/api/src/agent-run/workspace-bindings.ts`：目标 Runner 解析、远程 skill 包准备和执行身份路由。
- `docs/zh/explanation/sandbox-execution.md:193`：无跨调用 export，变量与命令放在同一脚本。

源码中的 `envProfile`、内部 `bindArgs` 或 macOS 的 `readPaths` 参数不等于 Linux 产品已开放任意资源挂载配置。NPU 目标为 Linux Runner，应沿真实可达的 API→Runner 调用链判断能力。

## 3. 第一阶段的贡献范围

### 3.1 Skill 及脚本

- Protenix 模型启动从 `run_npu_job` 改为托管 `run_shell(scriptPath, background=true)`。
- 在冻结 skill 包中提供薄入口，保留 RFdiffusion → ProteinMPNN → Protenix → screening → codon 的科学流程、参数和筛选规则。
- 入口使用所选科学环境的 Python；代码、权重、数据库路径来自明确的资源配置且必须在当前沙箱内可访问。取消必须由旧 Broker 注入资源的假设。
- 在同一次脚本进程内设置模型需要的环境变量和缓存目录，再以前台方式执行并等待所有子进程；失败必须传播退出码。
- 更新 SKILL.md、AGENT_BOOTSTRAP.md 和目标主仓中实际存在的直接调用方，消除 Broker-only、固定 local、sleep TICK 和禁止所有沙箱 NPU 调用的冲突规则。
- 不移植 GND 的推理算法、专用资源变量、FASTA 解析等实现；4910 的其他产品功能不属于本次贡献。

### 3.2 资源配置与准备

资源位置必须显式区分科学环境已安装包、冻结包内小资源、目标 Workspace 文件。资源清单是 **skill 层拟议配置**，不是新增 run_shell 参数；字段名在适配脚本时统一。

允许清单引用经过验证的 Workspace 相对代码/权重/数据库路径，或通过已安装包解析的资源位置。不得沿用旧 Broker 的“一律拒绝 Agent 配置模型路径”同时又要求 Runner 自动注入的死结，也不得接受随意猜测的宿主绝对路径。

资源准备方式按实际来源选择：

1. 所需依赖可安装为包：通过 `environment_install`，可使用平台支持的本地 wheel；不通过 Shell 修改只读托管环境。
2. 资源已在授权 Workspace：同目标核验后复用，跨 Runner 通过 `workspace_transfer` 准备。相同路径字符串不代表同一文件。
3. 资源需下载/上传：使用已有且获准的文件/网络机制准备，固定来源、版本或校验信息；网络未授权或离线时不能假定可下载。
4. 资源只存在于任意宿主目录：不能把它伪装成 Workspace 路径，也不能用 symlink 绕过沙箱；明确报告尚未准备，评估受支持的导入/复制方式或 §5 的共享资源需求。

大权重放入 Workspace 会受到磁盘配额、快照/CAS 和远程传输成本影响。首次运行前记录总大小、可用空间、准备耗时、快照/提交流程耗时；失败或成本不可接受时，不以增大超时、绕过快照或扩大宿主挂载作为默认修复。当前没有完成这一规模验证。

不把大型权重塞进冻结 skill 包，也不承诺已有跨 Session 共享权重缓存。第一次准备完成后，同一目标 Workspace 可复用已确认资源，必须核对版本。

### 3.3 调用、远程与等待

1. 依据用户指定服务器和 Session 实际 Runner 目录，固定 `runner_id`、`environment_id`、目标 Workspace；不得默认 local。
2. 输入、资源和配置在模型启动前准备完成。检查依赖、资源可读、缓存可写、NPU 可见及短小真实计算。
3. 使用冻结的 Protenix 入口后台提交一次，保留返回的 Execution ID。后台等待由框架管理，不在脚本内 nohup。
4. 短等使用 `execution_status(wait_ms<=30000)`；长任务使用已有完成通知。日志按 cursor 增量读取，不提交 sleep Shell。
5. 直接使用 `queued/running/completed/failed/cancelled/unknown` 状态。unknown 表示待确认，保留原 ID，不自动换服务器或重跑。
6. 完成后核对提交回执、科学输出和 manifest。远端结果先回传再显式登记产物；不假定主仓普通 Shell 自动登记。

主仓 `run_shell` 已持久化底层执行记录，但尚不等价于完整的 pipeline 自动恢复协议。首阶段不引入新 WorkflowRun schema；通过已有记录保留身份并验证通知归属。丢失原任务关联且无法明确恢复时报告待确认，不匹配“任意最近 Shell”。

Protenix 全链默认由同一脚本完成，阶段之间无需 Agent 调度。独立后续制造/其他模型步骤不在首阶段承诺自动续跑。Agent 回合完成与计算完成要在说明中区分；现有 UI 若不能准确呈现，记录为独立问题，不靠 sleep 保活掩盖。

## 4. 首阶段验收

| 编号 | 场景 | 预期 |
|---|---|---|
| U01 | 仅使用固定主仓构建及迁移 skill | 不依赖 4910/local 私有 Runner 补丁或专用变量 |
| U02 | 环境已安装包、Workspace 资源、同脚本 export | 模型能访问正确资源；只声明实际具备的只读边界 |
| U03 | 缺文件、版本不符、缓存不可写、错误宿主路径 | 在长模型执行前明确失败，不尝试 Broker/host_bridge 回退 |
| U04 | 真实模型资产规模 | 记录磁盘、配额、传输、快照成本；可运行性和可接受成本分别评估 |
| U05 | 131 通过产品远程连接执行单设计完整链 | 环境、冻结包、输入、NPU、结果均属于同一目标 Runner；原服务不受影响 |
| U06 | 后台计算长于单次等待预算 | 等待结束不停止任务；无 sleep TICK；只提交一次 |
| U07 | 成功、失败、取消、unknown 和其他短任务通知 | 观察原 ID，正确解释状态，不误续跑、不自动重放 |
| U08 | 远端完成、结果传输/登记失败 | 只重试交付，科学结果通过验收后才报告任务完成 |
| U09 | 原科学参数与输出契约回归 | 阶段顺序、参数/阈值、数量要求和结果格式符合预期；不要求随机计算逐字节相同 |
| U10 | Skill/入口/bootstrap/调用方规则一致 | 无 Broker-only 与托管沙箱启动规则冲突 |

U01～U10 均待执行。此前 131 的矩阵算子、模块测试和 GND 解析探针不能替代这些验收；源码核查不等于真实 Protenix 运行成功。

## 5. 独立候选框架贡献

以下不纳入首阶段的默认修改清单，仅在确认需求后设计和提交：

| 候选能力 | 触发条件 | 设计约束 |
|---|---|---|
| 通用共享只读资源绑定 | 必须直接复用宿主既有代码/权重/大型数据库，或 Workspace 方案的规模测试不可接受 | 按 Runner 配置、授权和审计；通用资源身份及沙箱路径，不硬编码 Protenix/GND；不开放任意宿主目录 |
| 进程重启/网络异常后的 reconcile | 用户要求自动重新确认原任务并恢复观察 | 查询原 Runner/Execution；unknown 不重放；明确进程是否仍存活的实际保证 |
| Workflow/step 与幂等续跑 | 多个独立 Execution 跨回合自动串接 | 关联持久化、重复通知去重；不为了单脚本内部阶段造调度器 |
| 结构化阶段进度与 UI | 已有日志/Execution 展示不足以表达科学阶段和最终交付 | 在主仓现有通知/界面能力上增量补充，保持单一状态来源 |

v0.3 中相应字段、P1/P2 和故障测试可作为这些独立议题的草案，不视为已批准实施的完整框架改造。

## 6. 实施前剩余事项

先核对 Protenix 代码/权重/数据库实际布局、可安装包形式、规模和离线条件，完成 U02～U04 的资源准备可行性验证，再决定首阶段是否存在需要框架补丁的真实阻塞。若有，先提交明确证据和最小通用方案；若没有，保持 Runner/API 不改。

本版只修正设计和能力判断，未更改主仓、4910 分支、Runner 或部署配置。
