# NPU Skill 设计：131 基线验证记录

- 对应设计：[v0.1](./npu-skill-execution-monitoring.v0.1.md)。
- 日期：2026-09-17。
- 状态：**现状核对及隔离基线测试完成；完整 pipeline 和拟议功能尚未验收**。
- 范围：验证已有部署和最小运行能力；不把待实现的设计验收项标记为通过。

## 1. 结论

新路线在 131 上具备可行的硬件基础：主仓代码的逐芯片探针能够区分可用/不可用芯片；最小 MindSpore 算子在只暴露一颗 NPU 的沙箱内完成，并以宿主进程表交叉核实实际卡号。主仓 Execution/API 管理模块的 15 个测试全部通过。

但是，131 当前服务仍是旧 Broker 部署，不具备本次主仓的完整 Execution 管理模块。不能只导入新版 skill 就投产，也不能把这次模块测试当作已部署服务端到端测试。

## 2. 部署与环境

检查时间：2026-09-17 09:27–09:39（Asia/Shanghai，证据中的时间使用 UTC）。

| 项目 | 实测值 |
|---|---|
| SSH 身份 | `lujiale@8.92.7.131`，非 root |
| 系统 | Linux aarch64 |
| API / Runner / Gateway | `127.0.0.1:18450 / 18451 / 18452`，health 正常 |
| 部署标识 | `ScienceDiscovery-78fa0d7-linux-aarch64`；标识来自目录/启动记录，未冒充 Git HEAD 核验 |
| 运行代码目录 | `/home/lujiale/.cache/science-agent/payload/ae46022e201cadbbbb2582cdab938ead/app` |
| 生产数据目录 | `/home/lujiale/ScienceDiscovery-deploy/data-78fa0d7` |
| Runner 报告版本 | `m4-isolation-only-v1` |
| Node | v22.19.0 |
| 当前 Broker | 已启用；`npu.smoke_test`、`antibody.protenix.v1` |
| 附带 Protenix skill | Broker only；仍含 `run_python` / sleep TICK 指令 |
| 附带 SKILL.md SHA-256 | `9c1702f3a27a18fb053da1ff6ee4aff5f54361047b5cb06d8421394faac55258` |
| 新 Execution 模块 | 运行包未包含 `runner/dist/execution-manager.js`、`api/dist/shell-executions.js` |
| 托管环境 | `antibody-npu-managed-clean`，ID `task-932d7c2f-0b0b-4ea4-8a69-4be166ec9932` |
| 复用环境 revision | `rev-4a6b8306-e8f6-4fdb-a28c-7be1bc3a9bbe`；本次只读挂载，不更改依赖 |
| 环境 Python / MindSpore | Python 3.12 / MindSpore 2.7.2 |
| 芯片 | 8 × Ascend 910B3；宿主卡 0、1 有其他计算进程，本次不用于测试 |
| 独立测试目录 | `/home/lujiale/sciencediscovery-tests/npu-design-20260917` |

上述 skill hash 是运行包附带文件的身份，**未枚举用户各 Session 已冻结/激活的 skill library revision**；不要把它当成所有 Session 当前使用的包 hash。

## 3. 主仓模块测试

从官方提交 `c1ab6327c6b2812e9fab26e62333b2c5ac118f3e` 提取相关模块，使用 Node 的 `stripTypeScriptTypes(mode=transform)` 转为 JavaScript，保留源码逻辑及 SHA-256 清单，在 131 的独立目录执行。测试不调用生产 API、不更改生产数据库。

```sh
node --test runner/execution-manager.test.js api/shell-executions.test.js
```

结果：**15 passed / 0 failed / 0 skipped**，约 0.73 秒。

覆盖了以下现有机制：

- 等待结束后后台工作继续；完成通知晚于 provenance 提交。
- 显式取消、进程清理先于终态、排队任务取消不启动。
- 同一 Workspace 写入串行，观察通道不被写锁阻塞；另一 Workspace 可独立运行。
- 提交响应丢失、API/Runner 重启后的 unknown，不重放命令。
- 身份不符、快照/ref 提交失败不虚报成功。
- 日志分页与截断、远端与本地回执作用域。

这些是**上游既有模块单元/组件测试**，部分 Runner 交互由测试替身驱动；没有执行完整 `pnpm build/test`、LLM Agent、Web UI 或生产 HTTP 提交链路。

证据：[测试日志](./evidence/npu-skill-131-20260917/upstream-module-tests.txt)、[来源哈希清单](./evidence/npu-skill-131-20260917/source-manifest.json)。

## 4. 芯片探针

使用同一官方提交的 `detectSandboxCapability` 和 `collectNpuInventory`，仅探测宿主 4、6 两颗芯片，没有探测正在运行其他模型的卡 0、1。

| 芯片 | 宿主读数 | 沙箱真实探针 | 判断 |
|---|---|---|---|
| 4 | 910B3、OK、AICore 0% | `sandboxUsable=true` | 可进入下一步算子验证 |
| 6 | 910B3、OK、AICore 0%、未列出计算进程 | `sandboxUsable=false` | 当时驱动拒绝，不纳入执行 |

卡 6 返回：`dcmi model initialized failed, because the device is used. ret is -8020`。这里只记录探测时的事实，不断言永久不可用或具体占用者。两卡同时执行及非连续多卡映射尚未通过实测；生成映射参数不等于运行成功。

现有 bwrap 不认识 `--disable-userns`，主仓能力探测选择 `option-unknown` 降级；仍使用新建 procfs、命名空间隔离、cap-drop。未为测试升级系统组件。

证据：[芯片与沙箱能力快照](./evidence/npu-skill-131-20260917/npu-probe.json)。

## 5. 真实 NPU 算子测试

测试 harness 复用官方 NPU 选择、环境变量、设备绑定与 baseline seccomp 生成函数；在独立 Workspace 中启动 bubblewrap。它不是部署中的 Runner HTTP 路由，也不是整个 pipeline。

隔离条件：`--unshare-all --unshare-user --cap-drop ALL`、新建 `/dev`、只绑定选中芯片与必需管理节点、只读托管环境、`--clearenv`、官方 arm64 baseline seccomp。没有切换到宿主 Python 运行模型，也没有为了通过测试取消 seccomp。

两次矩阵测试均退出 0。第二次显式限制 `max_device_memory=1GB`，并在算子完成后短暂保留进程，交叉核对宿主进程表；含观察时间共约 21.2 秒。

```text
VISIBLE_DEVICES=["davinci0"]
MINDSPORE_VERSION=2.7.2
TENSOR_RESULT=[[7.0, 10.0], [15.0, 22.0]]
SANDBOX_NPU_TENSOR_OK
```

输入 `[[1,2],[3,4]]` 自乘，输出经断言校验。宿主 `npu-smi` 中本次 Python PID `2529836` 位于 **NPU 4 / Chip 0**，记录显存 121 MiB；该 PID 属于本次 bwrap 的子进程。由此确认宿主卡 4 → 沙箱 device 0 的实际运算映射，不仅是文件节点重命名。

同时保留以下诊断，不将它们隐去：

- `getpwuid(): uid not found: 1024` 引出的 MindSpore/TE 检查 ERROR/CRITICAL。
- `/ascend/log` 不可写。
- Python 3.12 下 CANN 源码 SyntaxWarning，以及 NumPy 和 context API 的警告。

这些日志没有阻止本次矩阵计算，但完整模型未验证。需要在实际 Runner 的完整 runtime support、用户解析和缓存/日志目录配置下进一步核对，不能仅凭矩阵成功就忽略。

证据：[算子、进程归属和日志](./evidence/npu-skill-131-20260917/hardware-placement.json)。证据只保留本次测试的进程匹配行，排除无关用户的完整进程表。

## 6. 对设计的反馈

1. **平台与 skill 一起迁移。** 131 的旧服务缺少新 Execution 生命周期；下一步需在新端口、新数据目录部署隔离实例，再做 HTTP/Agent/UI 验证。
2. **坚持逐芯片探测。** 卡 6 的实际结果说明不能用宿主健康状态或低利用率替代沙箱可用性。
3. **保留 Python 版本门槛。** 本次 Python 3.12 环境证明算子可行，不能满足本地新版 skill 明确要求的 Python 3.11 全链路条件。
4. **不在运行中的 Workspace 另起 Shell 保存身份。** 既有测试确认写入串行、观察独立，控制面关联更适合保存 Execution 身份。
5. **资源与警告纳入完整链路验收。** 最小算子用显式小显存预算；模型阶段仍需自己的内存/缓存验证。

## 7. 尚未覆盖与现场影响

- 未执行 RFdiffusion → ProteinMPNN → Protenix → screening → codon 完整单设计链。
- 未测试拟议 WorkflowRun/step schema、控制面 checkpoint、reconcile、结构化进度和新版 UI；这些还未实现。
- 未在生产服务上模拟重启、断网或取消现有任务；本次恢复测试仅为独立模块测试。
- 未验证新部署下 LLM 自动唤醒与多阶段只续跑一次，也未测试远端产物传输和登记。
- 没有修改或重启生产 API/Runner/Gateway，没有安装依赖或改写 CANN/共享环境，没有创建生产项目/Session。
- 测试目录和日志保留，便于复核；它们不会被生产服务加载。
- 09:39 收尾检查：本次 bwrap/Python 测试进程均已退出；原 API/Runner 进程 PID 不变，三个 health 仍为 ok。独立测试目录总量约 276 KiB。

因此本次结论为：**迁移方向有实测依据，设计仍待评审，不能标记为已完成上线验收。**
