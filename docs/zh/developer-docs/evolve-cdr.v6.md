# CDR 序列搜索 v6

2026-11-28。在 [v5](evolve-cdr.v5.md) 基础上，按用户要求实现同轮候选并行折叠，暂不实现模型常驻。v4 的阶段、事务及目标函数要求继续适用。

## 要求和验收

- **CDR6-001 并行边界**：以现有运行预算 `workers` 为上限，同一轮内的独立候选并行折叠；实际并发取请求值、当前 runner 已选卡数及待折叠候选数的最小值。单卡或 `workers=1` 使用串行路径。生成、父代选择、质检、种群提交和 Reflect 不跨轮并行，不新增持久模型进程。
- **CDR6-002 真实执行隔离**：API 冻结当前运行的 runner、已选卡、权限 epoch 及主工作区；sidecar 只请求槽位，不传物理卡编号或文件路径。每个槽位映射到一张授权卡和一个独立工作区，以避开 runner 的工作区执行队列。worker 工作区与主工作区不互为祖先，按主工作区及运行摘要隔离，不共享其他运行的数据。runner 的 managed execution 同时按工作区和 NPU 卡集合排队：跨运行、跨工作区的重叠选卡互斥，不重叠的选卡可并行；多卡请求依提交顺序等待前驱，避免锁循环。运行期间配置变化拒绝新提交；已接收任务的查询和取消仍走原 runner。
- **CDR6-003 结构证据汇集**：折叠任务完成后，通过 runner 的不可变快照及带大小、SHA-256 校验的流式上传，把指定结构 ID 的 manifest、summary/full confidence 和完整复合物复制到主工作区。只允许固定产物文件及当前运行绑定的 worker 来源。结构发布成功后才返回可用结果；整批收齐后方可执行后续阶段。大置信矩阵不进入搜索 Python 内存或模型上下文。正常运行结束后清理本运行 worker 工作区，主工作区保留证据；中断及无法确认安全删除的工作区保留并报告清理失败。
- **CDR6-004 顺序、预算和失败**：完成顺序不影响候选编号、准入顺序和种群结果。未提交周期不得更新 tried、历史或种群。停止、runner_lost 或执行终态未知时停止发出新任务，取消已发出的同批任务并收齐线程；失败周期重试仍遵循既有上限，并记录已尝试的物理折叠。只有明确未提交的创建请求可退避重试；查询或结构发布故障不得隐式重放已完成的折叠。普通失败候选保留无分数状态，禁止伪造数值。workers 上限按运行生效；runner 的卡集合互斥覆盖其 managed executions，不替代集群或外部进程的硬件调度。
- **CDR6-005 生成与折叠分离**：多 worker 的 ESM/MPNN 路径先生成完整候选组，再统一调度折叠，避免在生成 RPC 中提前串行折叠首个候选。生成任务不产生折叠分数或消耗候选预算；原串行生成兼容入口继续适用。
- **CDR6-006 验证**：用同步屏障证明候选真正重叠执行；验证不同卡及工作区、越界槽位和权限变化拒绝、结构流式汇集、产物边界、乱序完成的稳定提交、取消/runner_lost 阻止新增任务、重试及预算，以及单 worker 兼容路径。覆盖真实 runner 的工作区队列，避免只验证线程或请求数量。运行 WSL 相关回归、完整构建和测试；基线与环境失败单独报告，不以离线夹具声称已完成真实 NPU 多卡实验。

## 实现安排

Python CDR 引擎使用有界线程工作组，每个工作线程持有固定槽位，只派发当前轮任务；模型调用继续在主线程进行。API 提供运行内槽位容量与结构发布入口，生成受控 worker 工作区及卡选择，并复用 runner 现有 CAS 快照、流式传输和清理接口。runner 保留既有工作区互斥与版本提交，在 managed execution 增加卡集合的前驱队列。部署时同步更新 API、evolve、evaluator 和 runner；其他进程及未经过 managed execution 的硬件使用不在本次调度范围内。

## 使用配置

在现有运行预算中设置 `workers > 1`，并在当前 runner 选择两张或以上 NPU 卡；同轮候选数也应大于一。例如 `budget.workers=4`、已选四张卡、`candidates_per_cycle=8` 时，最多四个候选同时折叠。每次折叠仍独立启动模型。

## 验证记录

- 独立 WSL 目录 `/root/sciencediscovery-cdr-harness-validation`：Python CDR、阶段、评分及真实 HTTP 调用定向回归 97/97；API 槽位、结构发布、清理及真实 runner 队列集成回归 22/22；runner 执行管理回归 33/33。覆盖重叠选卡排队、不同卡同时执行、乱序的稳定提交、整轮重试、停止优先级、未知终态、未确认终止保留产物及 POST 回包丢失不重放折叠。
- evolve 全量 531 通过、3 失败，353.76 秒。剩余仍为此前已确认的 `test_failed_measurement_is_shared_by_shards_but_retried_next_attempt`、`test_repeat_prompt_bounds_history_and_large_values_without_forgetting_edits`、`test_a_candidate_that_rewrites_the_tests_at_run_time_gains_nothing`。
- 完整 WSL 构建通过；回归测试 TypeScript 检查、架构边界、Markdown 和修改文档的链接检查通过。重建 Runner SEA 后打包测试 38/38 通过。以正常文件访问权限运行完整 `pnpm test`，仍被桌面 `evolve-ui.test.tsx` 的 `3 !== 2` 算法数量断言挡住：桌面 1,181 通过、1 失败。不宣称完整项目测试全绿。
- evolve wheel 构建通过，并确认包含 `cdr_folds.py` 和既有阶段资产。使用独立临时数据目录两次启动构建后的 API，`/health` 均为 `liveness=ok`，随后关闭并清理临时数据；用户现有服务未重启。
- 保留并行开发期间新增的 `4d8bfe588` 和 `c8f9ce141`；后者已收录 Python 并行调度。本轮补齐 API/runner 配套和提交失败的防重放边界。以上为离线进程及协议验证，尚未使用真实 NPU 做多卡吞吐或模型结果实验。
