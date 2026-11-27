# CDR 序列搜索 v4

2026-11-27。在 [v3](evolve-cdr.v3.md) 上落实用户确认的阶段 agent 方案，并修正源码对照发现的行为差异。参考 OpenDDE-Harness `fdab4f0ade76db41a300f1bab4c931540dc7a557` 及 v3 已锁定的 Ascend 补丁。v3 保留为历史设计；起点相同不能证明所有候选的目标函数、搜索轨迹或最终结果等价。

## 边界

保留 Python CDR 引擎、Node 控制面和现有 runner。阶段 agent 是搜索内部的有界调用，不创建主聊天会话。当前输入仍是单 binder 链、单 target 链、固定长度 CDR；不宣称覆盖 Harness 的 paired VH/VL、minibinder、post-filter。常驻推理、多卡分片和投机生成属于后续执行性能工作，本版不改变共享 NPU 的分配方式。

## 要求和验收

- **CDR4-001 模型传输**：兼容既有 `prompt -> text`；新增 messages/tools -> assistant message 的入口，保留 system、assistant tool calls、tool 回执及提供商要求的消息字段。所有轮次复用 run token、用量记账、停止门和 HTTP 重试，阶段设置总时间、token 和工具轮数上限。截断工具调用不得执行。
- **CDR4-002 阶段执行**：Analyze 一次，Reflect 按间隔（包括最后一轮），Quality 按配置门槛。使用锁定 Harness 的角色 system prompt、技能内容和 Analyze/Reflect 输出 schema。Analyze 最多 4 轮工具交互，Reflect 6，Quality 3；演化树工具每次反思最多 1 次。工具参数和最终结果均校验；反思工具不可用时返回真实 unavailable，不伪造证据。结构化反思转换为下一轮设计指令。旧的注入式纯文本 completion 仅作为测试/兼容入口，生产路径使用阶段执行器。
- **CDR4-003 工具及产物**：折叠临时目录清理前，将完整复合物（含 target 和侧链）、summary/full confidence、序列与链编号信息保存到会话工作区 `.evolve/cdr-artifacts/<内容摘要>/`；返回不透明结构 ID。模型只能通过当前运行的候选 ID 选择结构，不可指定任意文件路径。在远程 OpenDDE 解释器中适配 Harness 的 PLIP、epitope、ProTrek 后端；历史分析使用当前运行的真实候选及父代关系。developability 保留公开版 stub。工具调用不消耗候选预算，不生成折叠分数。
- **CDR4-004 种群提交**：整批质检成功后一次更新受约束种群；禁止逐条永久淘汰。去重、距离和复用约束顺序与 Harness 相同。参考 CDR 取第一次更新中按目标排序的首个有效准入成员（起点过门时为起点）；起点未过门时不得提前以其初始化参考。工作父代仍只用于种群为空时。
- **CDR4-005 配置和随机性**：解析并验证种群容量、距离/复用比例、质检开关/阈值、整轮重试次数、父代策略及温度、种子、停滞重设计和阶段参数覆盖。允许 uniform fraction 为零。父代按适应度排序后抽样；router 使用 `protein-design-router:{seed}:{cycle}`，生成种子使用 `seed + cycle`，折叠使用运行 seed。未知/不支持的模式不得静默假装生效。
- **CDR4-006 失败与预算**：设计无新候选、质检调用失败均在种群提交前重试整轮，次数取配置；不提前写入 tried 或改变种群。质检结果缺失/类型错误视为阶段失败。失败重试耗用的物理折叠需要记录。运行最多 `cycles` 个逻辑周期（默认 `ceil(expansions / candidates_per_cycle)`）；重试耗尽时依 `skip_failed_cycles` 跳过该周期，不伪造一次折叠来推进预算；整批物理折叠均无有效分数时重试耗尽必须失败，与 Harness 的 fatal batch failure 一致。`expansions` 仍约束正式候选数以兼容产品 API；失败周期可能导致正式候选数不足。停止/runner_lost 必须及时终止，不再发起质检/反思。
- **CDR4-007 目标函数**：生产折叠必须有有限 ESM-2 PLL，否则该候选无可比较分数，不能以省略 ESM 项的结构损失进入种群。同一运行的目标函数一致。保留显式结构损失函数用于历史单元测试，不允许生产路径隐式降级。
- **CDR4-008 验证**：原函数差分回归覆盖整批保留及首次准入参考；配置和非默认种子；质检重试不提交；真实 messages/tool 回合的 HTTP 测试；角色工具限制、schema/取消/预算；结构产物在临时目录外可读及路径边界；ESM 故障不得产出省略项分数。运行完整 evolve 测试、WSL build/test 和服务健康检查。NPU 实测与使用固定数据的离线测试分别报告。

## 实现安排

`completion.py` 提供结构化模型回合；CDR 阶段模块负责角色、技能、schema 和工具循环；CDR 工具模块负责当前运行候选绑定与 runner RPC；工作区 evaluator 负责完整结构持久化和固定的分析操作。Harness 的有限文本资产及 schema 在 evolve 包内锁定并保留出处，运行时不在线抓取技能。模型只提出建议，准入、种群和预算由引擎执行。

## 验证记录

- WSL 独立验证目录：`/root/sciencediscovery-cdr-harness-validation`，未覆盖运行数据。
- 最终 evolve 全量：503 通过、3 失败，200.03 秒。3 处失败均在未修改的 `5066a02b3` 源码上单独复现：`test_failed_measurement_is_shared_by_shards_but_retried_next_attempt`（旧测试替身不接受 `on_stage`）、`test_repeat_prompt_bounds_history_and_large_values_without_forgetting_edits`（长历史提示截断预期）、`test_a_candidate_that_rewrites_the_tests_at_run_time_gains_nothing`（临时目录出现在 JUnit case ID）。本次不修改这些独立模块。
- 完整 `pnpm build` 通过。项目级测试首次遇到过期 Runner SEA，重建后该项 2/2 通过；launcher 只读目录测试在移除 root 的 DAC 绕过能力后 11/11 通过。项目剩余测试结果单独报告，不将这些环境修正计作产品变更。
- 修改文档的链接检查和 wheel 资产检查通过：角色提示、完整反思 schema、技能 JSON 及出处声明均进入 wheel。
- 本地原 evolve 侧车确认 `running=0` 后经既有 launcher 重新初始化为 `ready`；API `/health` 为 `liveness=ok`。未创建烟测项目/会话，未使用共享 NPU 重新折叠。
- 分阶段消息/工具回合、完整结构跨执行保留、坐标投影和失败场景使用离线夹具验证；本记录不声称完成真实 ProTrek/PLIP/FoldMason 服务或 NPU 端到端实验。
- 最后适配审查补齐演化候选的 `chains`、`cycle`、`skill_id`，让 FoldMason 的代表结构选择按配置 CDR 比较；父代选择提示显式说明 score 越高越好。最终定向回归为 118/118 通过。
- 完整项目测试未全绿：桌面 `evolve-ui.test.tsx` 的算法数量仍断言 2，但本分支已有 3（该文件及产品 TS 源码未修改，单独复现相同失败）；executor 的 Docker 集成测试因没有 `docker` 可执行文件失败。额外的按包串行测试也报告了未改动 API 配置集成用例的超时。本次不把其他模块的基线/环境失败改成跳过或通过。
