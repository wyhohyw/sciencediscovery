# 4910 备份分支 NPU Skill 审查

- 日期：2026-09-17
- 分支：`yuheng_wang/sciencediscovery@backup/4910-running-20260916`
- 固定提交：`bc41e28c4a0ae9f3e623d8ff7db4b793f9a9708b`
- 范围：GND、Protenix skill 及核实其调用契约所必需的 Runner/API 代码。未审查其他产品改动。
- 方式：从私仓 fetch 后导出独立源码快照；静态调用链审查和无模型 Python 探针。未修改该分支、未部署、未运行真实模型。

## 结论

这个分支并存两条路线：

| Skill | 实现 | 评价 |
|---|---|---|
| `gnd-nanobody-mlm` 1.2.0 | 冻结脚本 + `run_shell(background=true)` + Execution ID + 状态/日志/完成通知 | 调用和等待方向正确，适合作为迁移参考，但资源注入、恢复和远程流程还不完整 |
| `antibody-protenix-pipeline` | `run_npu_job` Broker + 周期性 Shell sleep TICK | 对既有 Broker 部署有明确防错约束，尚未采用新的统一执行和监控路线 |

建议复用 GND 的托管执行方式，保留 Protenix 的科学输入校验、禁止重复提交和终态后验收产物原则；不要原样照搬整个 skill。

## 需要修正的问题

### F1 · P1：GND 声明的资源注入没有在此提交中闭环

定位：`skills/gnd-nanobody-mlm/SKILL.md:17`；`scripts/gnd_infer.py:135`；`services/runner/src/executor.ts:716`。

skill 声称 `SCIENCE_AGENT_NPU_GND_*` / `GND_*` 会注入沙箱，脚本也只从这些变量读取代码、模型和 tokenizer 目录。但 `buildSandboxLaunch` 使用 `--clearenv`，显式装配的是基础环境、CANN、网络和 Session profile；未找到 GND 变量从宿主配置进入该环境的实现，也未找到对应宿主模型目录的只读挂载。

触发：全新 Session 按此 skill 操作，运维只在 Runner 宿主设置 GND 环境变量和模型路径。脚本不会继承这些变量，`operator_paths()` 将空值解析成当前目录，随后因缺少模型代码/权重失败；仅追加环境变量仍不能让未挂载的宿主路径可见。

建议：实现 Runner 管理的资源配置、只读挂载和沙箱路径注入，并在提交前验证代码、权重、tokenizer、设备均可用。保留现有“不允许 Agent 猜宿主路径”的规则。如果 4910 实际运行包包含仓库外补丁，需将该补丁和部署契约纳入版本管理；本审查不声称已检查实际运行包。

### F2 · P2：托管执行漏处理 `unknown`

定位：`skills/gnd-nanobody-mlm/SKILL.md:90`；`services/api/src/shell-executions.ts:26`、`:123`；`services/runner/src/execution-manager.ts:37`。

skill 只列出 `completed/failed/cancelled/interrupted`，但通用执行在 API/Runner 重启以及部分无法确认结果的异常路径会进入 `unknown`。`interrupted` 来自旧 Broker 的状态习惯，不能替代 `unknown`。

触发：任务已提交，控制端重启或观测异常，Agent 收到 `unknown`，既没有明确恢复分支，也无法按现有成功/失败规则收尾。

建议：明确 `queued/running` 持续观察，`completed` 验收，`failed/cancelled` 报错，`unknown` 保留原 ID、禁止重放和自动换服务器，核查原 Runner 状态后再决定。不要承诺现有版本能在所有断线场景自动重新接管。

### F3 · P2：多条 FASTA 会被拼接，改变预测对象

定位：`skills/gnd-nanobody-mlm/scripts/gnd_infer.py:56`、`:99`。

`first_fasta_record` 返回整个文件，`parse_masked_sequence` 删除所有 FASTA 标题后直接连接序列。

本地复现：`>nanobody_1\nQV<mask>\n>nanobody_2\nWYL\n` 被解析为 `QV<mask>WYL`，没有报错。模型随后会预测人为拼接序列，影响序列位置和上下文。

建议：当前契约只接受一条序列，多记录时明确拒绝并要求选择 record ID；不要静默拼接，也不要未经说明默认第一条。

## 迁移到远程运行前必须补齐

### R1：Runner 选择和文件传输

GND `SKILL.md:16,77` 硬编码 `runner_id="local"`。Protenix 的环境准备也固定 local，且 `workspace-bindings.ts:306` 的 Broker submit 本身调用 `resolveExecutionTarget("local")`，工具 schema 没有 `runner_id`。因此，产品支持远程连接不等于这两个 skill 已支持远程执行；仅修改 Protenix 提示词也无法让 Broker 自动路由到远端。

迁移应解析用户指定的实际 Runner ID，绑定该 Runner 的 environment/workspace，显式完成输入传输、资源探测、执行、结果回传和产物登记。复用 v0.2 设计里的统一 Shell Execution 路线。

### R2：启动后的 checkpoint 写入位置

GND `SKILL.md:24,86` 要求接受任务后写 `gnd_pipeline/sandbox_job.json`，但没有给出能在长任务运行期间完成的控制面写入机制。

`ExecutionManager` 按同一 Workspace 排队，并在运行和快照期间持有 lease（`execution-manager.ts:56`）。若照之前习惯再发一个 `run_shell` 写 checkpoint，该 Shell 必须等模型退出后才能执行。它不能作为运行期间已经持久化的恢复依据。

建议：以控制面的执行记录作为权威绑定，提交响应中的 ID 直接用于观察。需要补充 workflow 元数据时放在不受计算 Workspace 写锁影响的控制面存储中；工作区 JSON 可作为最终镜像。skill 应明确 checkpoint 尚未完成时也不能丢弃提交 ID。

### R3：产物登记和运行身份

GND `SKILL.md:25` 的“完成自动登记”不能直接推广到所有运行目标。其显式 `declare_artifact` 兜底可以保留；远端输出应先回传本地，再按实际路径登记。`packages/provenance/src/recorder.ts:837` 明确跳过远端 Workspace 的本地产出登记。

此外，GND 收集了 `run_name`，但 `gnd_infer.py` 的输出仍固定为 `gnd_pipeline/00_gnd_predictions.*`，没有按 run_name 分目录。连续运行会覆盖当前工作区文件；迁移时应明确覆盖策略，并用执行 ID/快照版本绑定本次产物，避免将上次文件当作本次结果。

## 等待与监控的取舍

- **保留 GND**：后台提交、单次提交、`execution_status`、增量 `execution_logs`、按本次执行 ID 区分完成通知。
- **删除冗余参数**：`background=true` 时 `wait_ms=30000` 不会形成前台等待；可以删掉以免误导。
- **迁移 Protenix 的 TICK**：当前 TICK 等待的是新建的 sleep Shell，而不是 NPU 作业。100 秒 TICK 最坏还会延迟下一次 Broker 完成观察约 100 秒，并产生额外执行/通知。新路线应直接观察计算 Execution。
- **不能混用 ID**：Broker Job ID 不能直接传给 `execution_status`，也不能绑定到只接受 Shell Execution 的 timer；需先迁移提交路径。
- **同步上层编排**：`antigen-x-nanobody-design-to-manufacture/SKILL.md:31` 仍要求 `run_python` TICK，与当前 Protenix skill 的 `run_shell` TICK 不一致；路线迁移必须同步调用方和 bootstrap。

## 已验证与未验证

本地运行 `review_probes.py`，验证了：

1. IMGT gap 上打 mask 会被正确拒绝。
2. 越出 Workspace 的输入路径会被正确拒绝。
3. 多记录 FASTA 被静默拼接，复现 F3。
4. 缺少资源变量时，在模型加载前失败，支持 F1 的脚本侧分析。

未运行 MindSpore/GND 实际推理、远程传输、真实断线恢复或 131 新版 Runner E2E。F1 的启动环境、R2 的排队行为来自固定提交的调用链和已有代码契约审查，不冒充实机验证。

下一步建议先补齐 F1/F2/F3 和远程身份绑定，再按 [NPU skill 设计 v0.2](npu-skill-execution-monitoring.v0.2.md) 在 131 的隔离 Runner 上验证单次真实计算、完成通知、结果回传及断线不重复提交。
