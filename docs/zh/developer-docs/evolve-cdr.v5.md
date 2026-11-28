# CDR 序列搜索 v5

2026-11-28。在 [v4](evolve-cdr.v4.md) 的阶段 agent、工具及搜索语义基础上，修复用户确认的 runner 参数长度问题。v4 的要求继续适用，本版只补充固定程序和完整历史的传输方式。

## 问题

evolve 侧压缩固定 Python 程序及候选 JSON 后，API 仍把整个程序 base64 编码后放进 `python3 -c` 参数。压缩率无法保证最终参数长度：完整重设计历史达到数百个候选时，压缩后的参数仍可能超出 Linux 的单参数限制，导致演化树分析无法启动。

## 要求和验收

- **CDR5-001 程序传输**：API 生成的 shell 使用固定长度的 Python 引导参数，从 stdin 读取 base64 源码并编译执行。源码和候选内容不得进入进程参数。使用带引号且无法与 base64 正文冲突的 here-document；runner 既有 shell stdin 传输及沙箱入口继续适用。
- **CDR5-002 语义保留**：保留完整候选历史和原候选 JSON，不截断、不丢弃序列、指标或父代关系。保留 `__name__ == "__main__"`、future import、Unicode 及特殊字符的行为，并保留 stdout、stderr 和失败退出码。evolve 的压缩只作为传输体积优化，不承担参数长度保证。
- **CDR5-003 验证**：回归必须执行 API 实际生成的 shell，覆盖压缩后经 API base64 编码仍超过 128 KiB 的完整历史夹具、普通程序和失败程序。验证历史数量及内容摘要、future import、入口保护和字符完整性。运行相关 API/evolve 回归、完整 WSL build/test；已确认的基线或环境失败分别报告。离线 Linux 进程验证与真实 NPU 端到端实验分别报告。

## 实现安排

`runner-proxy.ts` 负责将程序从 Python 命令参数移至 stdin。`npu_job.py` 保留可选源码压缩并明确其体积优化职责。runner 的 ephemeral shell 已通过 stdin 接收整个脚本，因此无需增加远程文件路径、产物清理或新的 runner RPC。

## 验证记录

- 在独立 WSL 目录 `/root/sciencediscovery-cdr-harness-validation` 验证，待提交的两份生产源码与最终被测文件的 SHA-256 一致。
- API `runner-proxy.test.ts` 与 `evolve-thinking-entry.test.ts` 联合复测 13/13 通过，191.53 秒。包含 2,000 个候选的完整历史，其压缩程序经 API base64 编码仍超过 128 KiB；通过真实 Bash/Python 执行并核对完整 JSON 摘要、Unicode、future import、入口保护及失败退出码。公共 evolution 离线流程覆盖 PUCT、OpenEvolve、HTTP 524、capped-empty 记账、取消及再次运行。
- Python 程序封装相关定向测试 5/5 通过。evolve 全量 509 通过、3 失败，318.57 秒；失败仍为 v4 已确认的 `test_failed_measurement_is_shared_by_shards_but_retried_next_attempt`、`test_repeat_prompt_bounds_history_and_large_values_without_forgetting_edits`、`test_a_candidate_that_rewrites_the_tests_at_run_time_gains_nothing`。
- `pnpm build` 通过。完整 `pnpm test` 首次被过期 Runner SEA 挡住；重建后 `pnpm binary:test` 38/38 通过。包级测试在 root 权限绕过下的 launcher 只读目录用例失败；移除该能力后 launcher 173/173 通过。继续全量包级测试仍遇到未改动桌面 `evolve-ui.test.tsx` 的 `3 !== 2` 算法数量断言，以及未改动 runner direct-execution 的等待超时；后者单独复测 20/22 通过，两个失败均为 `Remote execution did not terminate`。不宣称完整项目测试全绿。
- API evolution 扩展回归首次 228/230 通过：公共 journey 先因独立目录缺 Git 元数据、随后因旧启动字段断言失败，补齐环境后的最终联合复测通过；`availability.test.ts` 的认证错误断言得到 `SocketError: other side closed`，在恢复本轮修改前的 `d9a0d7a65` 生产源码后单独复现同一失败。本次保留该独立模块及其测试。
- 修改文档的 Markdown 和链接检查、API 回归测试的 TypeScript 检查通过。使用独立临时数据目录两次启动构建后的 API，`/health` 均为 `liveness=ok`，随后关闭并清理临时数据。未重启用户现有服务，未使用真实 NPU 执行模型或外部分析后端。
