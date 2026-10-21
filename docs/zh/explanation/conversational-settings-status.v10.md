# 对话内按需配置 · 合入前修订与实测 v10

- 日期：2026-10-21；分支：`codex/conversational-settings-review-fixes`。
- 产品依据：[设计 v21](conversational-settings.v21.md)第 17 节；工程合同：[开发 v11](conversational-settings-development.v11.md)第 21 节，CFG-R01～R14。
- 上游整合基线：`7ee6c1d0`；修复前审查验收候选 E：`05efd5476e800fb66c6dcc92b80be5aac7bd0849`。原审阅基线 `f58c728b` 留作历史追溯，不代替当前验证。
- CFG-R14 手动 HTTP 修复候选 F：`20acc0df682eca8fcccbdd1efdcc4cd53662a014`；本轮结果见第 1.1 节。以下 E 的原始证据属于修复前基线，分别保留原候选和计数。
- E 的公共 HTTP 十步旅程、真实 Flash/浏览器场景与第 3 节根回归属于前轮记录；不能用它们替代 F 的手动连接回归。

## 1. 本轮修订范围

落实 R01～R13：恢复前仅比较真实运行依赖，同时保留写入时的版本检查；自定义模型目标由用户通过专属入口确认；失效或已卸载连接器仍可撤销；凭据中间预览立即接收新 revision，上传失败可原地重试，同一目标更新保留输入，目标变化清空旧密钥并停止上传。

同一通用 revision/previewDigest 决定服务继续约束通用配置请求，兼容协议保留原有隔离规则。

无模型入口统一为持久通用请求，防止重复入口和原输入重复提交；所有提交提前返回都有回执结果。全能力回执进入对话轨迹，列表按 revision/stateVersion 单调合并；`resumption.started` 持续轮询到持久终态，避免原任务完成事件先到、回执停留在“继续运行中”。过期请求明确保留原任务暂停并提供 Stop。另补跨会话通知、有界待办调度、provider 复用、真实模型能力取值和可信连接器目录。

目标授权按最终协议及精确 wire 地址绑定。卡片在密钥输入前显示目标及风险；本地/私网地址不等于可信，HTTP 携 Key 会明文传输，只有独立用户确认后才保留本地网关兼容。公网或未识别为本地/私网的 HTTP 端点不能携 Key。免密探测同样需要目标同意；不自动跟随重定向，Agent 不获得用户批准或凭据接口。

E 还修正最新上游整合中的 Reviewer 回执回归：移除入队阶段导致无回执的全局关闭早退，保留会话选项和真正执行前的全局关闭检查。关闭时仍能留下可见的失败/跳过原因，不因此调用审阅模型。子 Agent 流测试按同一个逻辑工具执行核对单调 running 进度和唯一 completed，保留权限、输出、持久化及无重复执行断言。

### 1.1 CFG-R14 手动 HTTP 连接修复与验收

F 恢复正式设置页的手动 HTTP 网关路径：列表预览和保存前独立确认目标及明文风险；保存后测试绑定 baseUrl/updatedAt，地址、协议、variant 或代理变化后重新确认。费用确认保持独立。模型对象冻结，测试中修改正式模型会阻止后续矩阵探测；旧空 body 返回明确的刷新/确认提示。Agent 对话提案的公网 HTTP 携凭据禁止规则没有放宽。

| F 的实际验证 | 结果与边界 |
| --- | --- |
| 标准 WSL 根回归 | 同一不可变候选，从头执行 pnpm test：3688 tests，3687 pass，0 fail，1 skip；唯一跳过项为用户已允许跳过的 Docker/SSH 外部环境用例 |
| 类型与架构 | pnpm typecheck 退出 0；pnpm architecture:check 退出 0，72 tests 通过；不以早期候选结果替代 |
| 专项 | WSL 表单/已有模型按钮 52/52；HTTP 缺确认/错地址/过期版本、测试途中修改与保存凭据 2/2；客户端序列化回归也进入本次完整套件 |
| 公开 API 用户旅程 | test/api/manual-model-connection-review.mjs，8/8：预览、创建、登记、探测、持久化读取与精确清理；只使用 loopback 假服务和虚构 Key |
| 真实浏览器 | 1280×720：HTTP 确认默认未选、列表按钮受限、确认后列表成功、保存成功、刷新后记录仍在、已有模型再次测试返回可用；全局默认模型未改。未验证其他宽度、DPR 或真实手机 |
| 根地址补测 | 不带 /v1、带尾斜杠的本地网关从 /models 列表到 /chat/completions 探测通过，按基础 URL 原样组装，不自动为自定义地址添加 /v1 |

保留环境失败史：首次标准根流程在 Runner SEA 冷启动健康检查超时，原断言/预算重试 2/2 通过；第二次流程中 3 项 Runner 执行等待超时。改为显式 --test-concurrency=1，保持测试文件串行、允许沙箱子进程使用原有 4 核，未放宽产品、断言或超时。完整 Runner 285/285 通过后，从头重跑并得到上表的最终根回归；不是用局部重跑拼接全仓通过。旧日志保留为 manual-http-first-root-sea-timeout.log 与 manual-http-second-root-runner-deadlines.log。

浏览器曾遇到测试控制连接延迟，恢复后才执行上述交互。用户报告仍失败时，原标签页使用旧 bundle index-CwjwBKLx.js 并显示 Failed to fetch；经用户明确授权刷新后，已核对为修复版 index-CvF-uN3Z.js，旧错误通知消失。刷新曾被自动权限审核因未保存输入风险阻止，获得本次用户授权后才执行。

API/浏览器测试资源均按精确 ID 清理，本地假服务已停止；用户原有 3 个模型和测试前已有 3 个项目保留，API 4420 / Runner 4421 健康均为 200。浏览器模型 f5ca1045-7a8b-4e89-8644-ca5bea1f982d 与 provider c587b371-75eb-4d72-85b5-14d174d26b13 已删除。

F 的证据：Windows .tmp/manual-http-20acc0df-evidence/（三张截图、browser-verification.json、full-validation-counts.json）；WSL .tmp/manual-model-connection-20acc0df/report.md；完整日志在 .tmp/configuration-review-validation/。证据仅本地保存，不含用户 Key。真实 OpenCSI 的生成请求曾被自动权限审核拒绝，本轮未重试，也未用产品接口间接执行；这些本地结果不宣称该商业网关生成已通过。

## 2. 修复前 E 基线的专项与端到端证据

| 范围 | 候选与本轮结果 | 证明边界 |
| --- | --- | --- |
| 公共 HTTP 用户旅程 | E，`test/api/conversational-settings-review.mjs`，10/10 PASS | 实际产品路由、目标授权、凭据通道、幂等、持久回执、任务完成、失败修复及精确清理；仅外部模型 wire 使用本地 stub，不代表浏览器或商业模型通过 |
| 真实 Flash + PubMed | E，浏览器提交普通研究需求、选择仅本次并确认，原 run completed | 一次真实 PubMed 调用成功返回 3 条；无子 Agent，保存的连接器选择及后续运行连接器快照仍仅 web |
| 真实 Flash + Skill | E，浏览器提交配置需求并确认，再提交后续验证任务 | 当前 run 保留旧空快照，下一 run 精确启用 `literature-searcher`；不是修改已冻结运行的工具集 |
| 浏览器回执收敛 | E，三项任务均由浏览器提交，完成后先观察/截图，再展开回执，最后刷新 | 无需 reload 即显示终态；卡片默认一行且可展开，刷新后保留，3 runs completed、2 durable notices，无重复任务 |
| 原 API 失败项复跑 | E 及对应隔离测试环境，9/9 PASS、0 skip | 保留原业务、权限、隔离、拒绝后无副作用与输出断言；这是失败项专项，不是完整 API 或根回归通过 |
| 启动 fixture 与配置卡/API | D `8ea9ff16`，Windows 36/36、0 skip；Web 类型检查通过 | 2 项 mounted 启动测试与 34 项卡片/API 测试；准确归属于该次专项，不把早期计数相加当成 E 全量 |

E 的真实模型断言位于 `.tmp/configuration-review-05efd547-evidence/configuration-review-live-assertions.json`，其中 `targetSha` 与候选 E 一致：

- PubMed 原 run：`d138ee73-7639-4700-b183-fab79fcb64af`，经历 blocked 后以同一 ID 完成。实际审计 `ff11530a-a2ec-4fdc-8149-5e038a2dfab9` 为 succeeded，结果 3 条，turnId 与该原 run 一致。
- Skill 配置 run：`65e36838-8059-4680-916b-f9c5b91e1f1c`；下一验证 run：`6730dd51-c83f-4a35-a734-64af83725a28`。三项任务均完成，原选择没有被仅本次覆盖写回。
- JSON 明确核对同一原 run、run-only 不保存、无子 Agent、Skill 新快照和 2 条持久通知。浏览器自动显示 completed 的结论来自本次“先观察截图、再展开、最后刷新”的实际步骤，不仅由 API 已完成反推。

用户提示没有额外要求“弹卡后继续”。所有确认经真实浏览器操作，未用脚本代点批准接口来冒充浏览器验收。本次“自动终态收敛”指回执轮询，**不表示 30 秒自动批准/Always allow 的真实模型浏览器验收已通过**。

Reviewer/Thinking 的本轮覆盖限于领域、协议和运行回归；没有将上述 PubMed/Skill 结果扩写为这两项的商业模型界面通过。桌面证据限实际使用的视口，不宣称手机、缩放和全部断点已测。

## 3. 修复前 E 基线的 WSL 完整验证

标准根回归必须在同一已提交候选、同一源码清单和对应构建产物下完整执行，以最终命令退出结果为准。独立包、失败项重跑或真实模型成功不能拼成一次不存在的全仓通过。

保留本轮失败与归因：

- C `57ea0787` 的完整根回归在 Web 1045 项中有 1 项失败（1044 通过）：启动 fixture 把配置列表返回成 `[]`，真实合同为 `{ items: [] }`，导致会话刷新提前退出、运行监听器未安装。D 仅修测试 DTO，增加有界就绪及准确 Session/Run 订阅断言；未删 MCP terminal、失败清状态、设置刷新和移除 Specialist 的原断言。
- C 的独立 API 完整运行原始结果为 1483 项、1474 通过、9 失败、0 skip，不能写成完整通过。9 项分别涉及：2 项隔离副本缺 Git 元数据（并补齐固定 Evolve 虚拟环境）、1 项真实 Reviewer 回执回归、1 项合法的上游流进度语义，以及 5 项 `/usr` 沙箱无法执行指向 `/opt/node` 的 Node。上述原失败项随后 9/9 专项通过，仍保留原始完整失败日志。
- E 的首次标准根执行在 SEA 打包相关检查中因旧 `unknown` 缓存身份失败后停止；失败记录归档为 `final-05efd547-stale-sea-failed`。随后在**同一 E SHA**重新打包匹配产物并重新执行标准根命令，不修改测试期望来接受旧缓存，也不把首次失败记录抹去。

测试环境修正没有放宽产品门槛。隔离运行副本通过真实 Git 元数据文件及精确 `includeIf` 配置识别候选和核对 clean，不向所有子仓全局强塞 `GIT_DIR`。源码清单与冻结 SHA 对齐。对于 Node 布局，在私有 mount namespace 内向 `/usr` 提供与 `/opt/node/bin/node` 经 `cmp` 相同的可执行文件，宿主原链接未改，沙箱 `/usr` 可见范围和权限断言未放宽。以上不是 Docker/SSH 例外，也不是允许忽略失败。

本轮用户明确允许跳过的仅为 Docker/SSH 外部环境用例；实际跳过数量如下。全仓类型与架构检查均在 E 上完成；最终文档检查独立记录，不加入根测试计数。

最终标准根结果：

代码候选：`05efd5476e800fb66c6dcc92b80be5aac7bd0849`。下列命令按顺序执行并全部 **exit 0**；命令前后 HEAD 未变、tracked-clean，运行副本核对了 1499 个源文件的精确哈希。

| 最终命令 | 实际结果 |
| --- | --- |
| `pnpm test`（包含完整构建、binary:test 及所有包测试） | 32 个 TAP 汇总，共 3684 项：3683 pass、0 fail、1 skipped、0 cancelled |
| 同一根命令内的 API 完整包 | 1483/1483 pass，0 fail、0 skip；不是把此前失败项复跑拼接为全量 |
| `pnpm typecheck` | 全仓及 `tsconfig.tests.json` 通过，exit 0 |
| `pnpm architecture:check` | 72/72 pass、0 fail、0 skip；291 个 package 源文件边界通过 |

唯一跳过项原文：`a real SSH machine without a runner is deployed to, connected, and used`，标注 `Docker/SSH external test: set SCIENCE_AGENT_DOCKER_SSH_TEST=1 to run`，属于用户已授权的外部环境例外。

最终日志位于隔离 WSL `.tmp/configuration-review-validation/final-test.log`、`final-typecheck.log`、`final-architecture.log`。本次根运行在私有挂载环境中使用相同 Node 二进制；测试进程按单 CPU 串行运行，工作区串行调度，并移除 root 的 DAC 绕过能力，使权限断言保持真实。标准命令、用例和退出码未替换，所有失败记录仍保留。

后续提交只收录文档、阅读页来源文案与生成 HTML，不改变上述生产代码；最终阅读页、Markdown lint 和本地链接独立检查，其结果写入文档提交说明，不计入上述 3684 项。

最终文档检查：阅读页 9/9 通过，Markdown lint 与本地链接均检查 147 份文档并通过。页面内嵌来源与当前 Markdown 一致；离线 file: 页面未另做浏览器视觉验收，不由生成测试推定所有屏幕效果。

## 4. 修复与回归对应

| 合同 | 实现与验收重点 |
| --- | --- |
| R01、R03 | 无关设置不阻止原任务接续，真实相关依赖仍阻止；离线/卸载后的连接器撤销不要求重新建链 |
| R02、R04 | 用户专属精确目标同意；HTTP 风险显示；上传、探测及派发前复核；接受中间 revision；失败重试保留输入；目标变化即清空并禁止向新目标上传旧 Key |
| R05～R07 | 过期保留原任务暂停并提供 Stop；统一持久无模型引导；原输入防重复；每个提交早退有明确结果 |
| R08、R09 | 跨会话通知、全能力持久轨迹、revision/stateVersion 单调合并；started 持续协调直到终态，next_run 无原任务不永久轮询；E 有回执自动终态浏览器证据 |
| R10、R11 | 索引待办查询和公平有界调度；provider 复用；凭据忙与过期区分；未知模型不承诺全部 thinking 值；中文选项可区分 |
| R12 | 最新 main 冲突整合，保留 Reviewer 可见回执及真实执行门槛；工具流按唯一逻辑执行与单调进度检查，不减少权限、输出和持久化要求 |
| R13 | 已安装、ready、原 run 实际装配、Session 保存、策略许可与支持模式分别发现；模型先读可信目录再提案。E 的普通 PubMed 需求实际触发卡片并完成同一 run |

矩阵表达代码合同及其证据入口，不等于每个合同都完成了商业模型/浏览器/所有环境组合。具体测试层级以第 2～3 节为准。

## 5. 未交付边界与拟议拆分

当前七项为模型选择、模型连接、凭据修复、连接器启用，以及 Skill、思考、会话审阅的有限适配，不表示全部设置可自动修改。Native 的同 run 接续与仅本次覆盖只面向已安装、ready、策略允许且可实际装配的连接器；其它设置按声明在后续运行生效，不承诺模型热切换。

JiuwenSwarm 的实际条件写入宿主、部署组合与精确 interrupt 恢复仍受能力门控；合同或模拟通过不代表实际宿主接通。P2～P4、新 MCP 安装、系统代理和任意文件写入不在本轮支持范围。同一过期检查点原地换提案仍未实现，需明确 Stop 后重试。真实浏览器的 Reviewer/Thinking、30 秒自动批准、拒绝、Stop 与重启故障组合未在本次真实模型旅程全部重跑，不能用受控回归替代这些场景的实际验收。

PubMed stdio 初始化曾超过握手预算，独立测量与既有 reload 后恢复 ready，之后才执行真实检索；未放宽连接超时、网络限制或凭据规则。该启动可靠性现象保留为环境限制，后续成功不抹去失败史。

**拟议合入拆分尚未执行，也没有据此创建多个 PR 或合入上游：**第一包为公共 v3/版本/幂等/凭据/目标授权及 Native P0 垂直链路，安全、Stop、持久回执与验收不得延期到后续补齐；第二包为依赖公共机制的 P1 Skill/思考/会话审阅和 next_run 证据；真实 Jiuwen 宿主及 P2～P4 为后续工作。当前提交链是实现与纠错记录，不是已经独立验收的多个可直接合入包。若实际拆分，每个新候选需重新验证并同步其文档和能力开放范围。

## 6. 服务、证据与数据清理

保留隔离运行目录及数据，API `4420`、Runner `4421`；本轮未操作其它实例。实际研究边车按用例隔离启动，不因端口预设就宣称 Evolve `4423` 健康。

E 的真实模型 scratch 项目 `727a9b1e-7a3b-40fa-bda1-e31abcb788db`、会话 `7bb20485-388a-439e-a48a-819e24e978f7` 已按精确 ID 清理并确认不存在；公共 HTTP 旅程也完成其拥有项目、模型及 provider 的清理。原有 **2 个项目、3 个模型及凭据**保持不变；清理时 API/Runner health 均为 200。这是当时的健康观测，不保证服务永远健康。

本轮主证据：

- E 公共 HTTP 报告：隔离 WSL `.tmp/configuration-review-fc721059-341f-4430-9c0d-efdb21da25d8/report.md`，10/10 PASS。
- E 真实模型与截图：`.tmp/configuration-review-05efd547-evidence/`；包含 SHA 绑定的 `configuration-review-live-assertions.json`、三次浏览器提交元数据，以及 `configuration-review-card-pending.png`、`configuration-review-completed.png`、`configuration-review-skill-next-run.png`。
- 原始失败、专项与最终完整日志：隔离 WSL `.tmp/configuration-review-validation/`，按候选和执行轮次区分，保留 C API、SEA 失败和最终重跑记录。不要以根目录可被覆盖的同名 JSON 代替 SHA 归档。

证据存于本地忽略目录，不当作已提交的产品资源；不提交 API token、模型 Key、credential handle 或未脱敏日志。临时数据清理不删除验收记录。

## 7. 历史追溯

本轮候选演进为 `0cf443b2`（主体整合）、`42857fe8`（可信发现与回归）、`049fe9be`（HTTP 风险提示）、`57ea0787`（接续回执终态轮询）、`8ea9ff16`（mounted fixture 合同）、E `05efd547`（Reviewer 回执及上游进度整合）。早期候选和环境失败各自保留，不将不同候选的局部成功相加成为最终 E 的全仓通过。

[原审阅基线状态 v8](https://github.com/wyhohyw/sciencediscovery/blob/f58c728b4395ed32f7fa1161636e87c9d6123f5d/docs/zh/explanation/conversational-settings-status.v8.md)保留历史真实模型用例，仅作为历史证据。当前完整设计、开发、调研、支持矩阵、状态及单页 HTML 共同用于评审；历史全文通过固定 Git 链接追溯。最终文档提交不改变生产实现，保留各次执行报告的原始 targetSha，不回写为文档提交的 SHA。
