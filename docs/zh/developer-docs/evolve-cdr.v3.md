# CDR 序列搜索 v3

后续阶段 agent 和搜索语义修正见 [v4](evolve-cdr.v4.md)。本页保留为历史设计。

2026-11-25。在 [v2](evolve-cdr.v2.md) 上对齐同事 CRLF2 的搜索策略细节：技能路由、`cdr-full-redesign`、父代均匀混合。折叠公式、5 Å 门、固定长度和「没过门不进种群」与 v2 相同。仍不改 `/data1/gs_agent/opendde`。

## 相对 v2

v2 已有 ESM-2、SolubleMPNN、对话点突变和压缩 binder 骨架。v3 补齐：

1. **技能路由**默认 `agent`：有对话模型时，每轮由模型在合法技能里选一个 `skill_id`（提示里带 prior 权重）；解析失败或没有对话模型时，退回 `protein-design-router:{seed}:{cycle}` 加权抽样。起点 JSON 可写 `router_selection_strategy` 为 `weighted` 强制只用加权抽样。
2. **默认权重**对齐 CRLF2：`cdr-point-mutation` 0.5、`cdr-full-redesign` 0.1、`esm2-guided-mutation` 0.2、`antibody-inverse-folding` 0.2。`skill_weights` 仍可覆盖。没有对话模型时去掉两个 LLM 技能；没有父代骨架时去掉 SolubleMPNN。
3. **全量重设计**：抽中 `cdr-full-redesign` 时，对话模型必须给每个可设计 CDR 位点恰好一次赋值（允许与父代相同的占位），引擎去掉无改动后再折叠。候选之间应产生不同序列。
4. **父代选择**：适应度先在已打分成员上做 min-max 归一化到 [0, 1]，再算 `exp((x - 1) / T)`，之外混入 `parent_fitness_uniform_fraction`（默认 0.1），与 harness `ParentSampler._fitness` 一致。温度按 cycle 几何插值，`T = 1.0 × 0.2^(cycle / (cycles - 1))`，`cycles = ceil(expansions / 每轮条数)`，与 `ParentSampler.temperature` 一致。
   agent 路由的提示沿用 harness 设计提示的结构：`SKILL SELECTION` 要求对照证据、瓶颈和搜索半径比较而不是看列表顺序；可选技能带 `backend` 与归一化 prior；再给父代分数与序列、全局最优、连续无改进周期数及停滞指导、近 6 个周期各技能的折叠数、准入数和最好分。同事的 CRLF2 配置没有设 `router_selection_strategy`，走 harness 默认的 `agent`。强制 full-redesign 的两个阈值（`bootstrap_full_redesign_cycles`、`stagnation_full_redesign_threshold`）harness 默认都是 0，这里不实现。
5. **runner 掉线**：POST 被拒（409，runner 未连接）时等待 30/60/120 秒重试，仍失败或 GET 状态读失败时结束搜索（`status=failed`，`stopReason=runner_lost`），这些失败不计入 expansions。
6. **谱系深度**：子节点深度 = 父节点深度 + 1，写入 `expanded.depth`。
7. **设计提示带证据**，对齐 harness `DESIGN_PROMPT`。v2 的提示只给可设计位点和父代序列，模型看不到自己上一步的结果，等于盲改。现在点突变和全量重设计的提示都附上：
    - **目标**：最小化 OpenDDE 折叠 loss（分数 = −loss，越高越好），loss 的组成项，以及「CDR 接触占比需超过 0.5 才过 5 Å 门」。
    - **父代指标**：`iptm`、`plddt`、`cdr_contact_fraction`、`framework_contact_fraction`、`esm2`、最大的几个 loss 分量，以及没过门时的门控原因。这些值评估器本来就返回，v2 在 `_measure_reading` 里全部丢掉了，现在保留在 `reading["metrics"]` 并挂到成员上。
    - **种群与历史**：已进种群的成员按分数排序列出（节点号、分数、深度、改动），以及最近 14 条已折叠的改动和它们的分数、门控原因，要求不要重复。
    - **搜索状态**：全局最优、连续无改进周期数和对应指导。
8. **analyze 与 reflect 两个阶段**：搜索开始前让对话模型读一次 CDR 图谱和靶点热点，产出一段不超过 120 词的说明，作为每轮提示的 `ANTIBODY HEADER / CDR MAP`；每轮折叠完后再让模型回看该轮的改动和分数，产出不超过 80 词的小结，作为下一轮的 `REFLECTION ON THE LAST CYCLE`。反思的频率由 `reflection_interval` 决定，见第 14 条。两个阶段的模型调用失败时退回 `none`，不影响搜索。

9. **设计阶段失败重试整轮**，对齐 harness `cycle_retry_limit`（默认 2）。对话模型没返回合法 JSON、或给出的候选全是已折叠过的序列时，这一轮还没有提交到种群，harness 会带着重新选父代、重新路由技能再跑一次，不消耗预算。重试两次仍然拿不到可折叠候选，才记一次失败的 expansion——harness 这时是跳过该轮（预算按轮计），我们的预算按折叠次数算，所以保留这一次扣减来保证循环必然终止。重试不推进 cycle 序号，也不累加「连续无改进」计数。
10. **每轮记一条 log**：`cycle N drew <skill> on node M`。`expanded` 事件没有技能字段，而技能组合是排查搜索行为的第一手信息。
11. **质量检查阶段**，对齐 harness `quality_cycle`。一批候选折叠完后，对门控通过且 `iptm ≥ 0.7` 的候选做一次批量质量检查（一次对话模型调用，逐个给出 `pass_check`），只有 `pass_check` 为真的候选才进种群。全量重设计（`cdr-full-redesign`）豁免，与 harness `_needs_quality_check` 一致。提示结构与 harness `QUALITY_CHECK_BATCH_PROMPT` 一致，包括 `<objective_developability_evidence>` 块。这一块的内容是 harness developability 工具的实际返回：开源版的后端 `servers/backends/developability_filter.py` 是 stub，每个候选返回 `{"available": false}`，harness 包装成 `{"summary": ..., "results": ...}`。同事 CRLF2 的运行日志也记录了 `available:false`（"confirmed on a re-query"），所以质检只依据序列判断。我们不补一个真实的 developability 后端，因为那会给质检比 harness 更多的证据，反而偏离对齐目标。质检调用本身失败（模型报错或返回缺少结果对象）时，这一批的候选全部不进种群并记 warn 日志。harness 此时重试整轮，我们不重试，以免同一批被重复折叠，这是有意偏差。
12. **受约束的精英种群**，对齐 harness `ConstrainedElitePopulation`，参数取 harness 默认值：容量 20；同序列去重；与已保留成员的 CDR 归一化编辑距离不低于 0.05（单链三 CDR 权重 0.25 / 0.25 / 0.50）；种群中同一位点的使用不超过 `ceil(20 × 0.75) = 15` 次；同一突变的使用不超过 `ceil(20 × 0.30) = 6` 次。突变标记按相对起点 CDR 的 difflib 对齐生成。每次有候选入选，就按分数从高到低重算保留集合，被挤掉的成员移出种群，最优值取自保留成员。被拒的候选在 `merged` 事件的 reason 里写明原因，如 `capacity_not_retained`、`cdr_distance_not_retained`。
13. **工作父代**，对齐 harness `WorkingParentTracker`。没过门但测得分数的候选也参与比较：先比 `cdr_contact_fraction`，再比分数。仅当种群里没有任何通过门控的成员时，下一轮以工作父代为父代；否则父代仍从种群抽取，与 harness `ParentSampler` 只看目标值一致。工作父代进入设计提示的 `WORKING PARENT` 段。起点没过门时，它就是初始工作父代。此前我们把没过门的候选整体排除，已修正。
14. **反思间隔**，对齐 harness `reflection_interval`。只有当 `(cycle + 1) % reflection_interval == 0` 时才让模型反思这一轮；其余轮次沿用最近一次的反思，提示标题为 `REFLECTION (latest reflected cycle)`。harness 默认值是 20，CRLF2 ds10 配置写的是 1，所以复现起点 `evo/start.json` 显式写了 `"reflection_interval": 1`。之前我们是硬编码每轮反思，数值碰巧与 ds10 一致，但没有读取配置；现在按配置执行。
15. **NPU 环境预检**，即“能用才用，不能用就说明原因”。每次搜索在第一个候选之前，NPU 任务先跑一次探针（评估脚本收到 `{"probe": true}`，在 `OPENDDE_PYTHON` 下执行）。逐项检查 NPU 卡、rdkit、biotite、折叠代码和检查点、ESM-2 包与权重、harness 模块、SolubleMPNN 权重、LigandMPNN 代码。缺失的组件按固定来源自动安装，全部装在 `/data1/gs_agent/sciencediscovery-deps`（与同事的 OpenDDE 目录平级，不写入其中）：pip 包 `fair-esm==2.0.0`、`rdkit==2025.9.3`、`biotite==1.4.0`（`--no-deps`，依赖沿用现有 venv）；权重 ESM-2 取 `biohub/esm2_t33_650M_UR50D` 在同事使用的 revision `08e4846e…`（经 `hf-mirror.com`，文件与同事主机上的逐字节一致），SolubleMPNN 取 `files.ipd.uw.edu`，`opendde_abag.pt` 取文档给出的 HF revision `eddd563c…`，并校验 SHA-256。代码（OpenDDE、OpenDDE-Harness、LigandMPNN）按复现方式安装：clone 上游固定 commit，再应用同事的本地修改（`git diff --binary HEAD`，补丁存于 `skills/evolve-design/patches/`，并内嵌在评估脚本里）。装完后核对 diff 的摘要，与同事的不一致就报错。这样做是因为同事的三份源码都有本地修改，直接 clone 会悄悄不一致。未跟踪的文件（`.bak_*` 备份、LigandMPNN 的调试输出和脚本）不复现，它们不在 import 路径上。某个组件的位置优先取同事的副本，不存在时才用平级目录。失败的安装返回真实原因（pip 的 `ERROR` 行、HTTP 状态或 SHA 不符）。下载遇到 429 限流会退避重试。主机实测：16 张 NPU，10 项检查全部通过；SolubleMPNN 权重、ESM-2 与检查点的安装结果见本节末尾的记录。

点突变仍用运行的对话模型出 1–3 处替换；不要求与同事 harness 使用同一家对话模型。

## 与同事 CRLF2 结果的对照

同事 `run_ds10.log`：10 轮每轮 5 条，共 51 次折叠，轨迹 2.193（起点）→ 1.958（c0）→ 1.811（c2）→ 1.774（c3）→ 1.550（c8）。技能上 c0–c7 和 c9 都是 `cdr-point-mutation`，只有 c8 用了一次 `esm2-guided-mutation`，赢家 `c0008_esm2_D115FW` 是在 c3 冠军（S54T + P110N + G113R）上再加一个 ESM 指导的 F115W。c2 和 c6 出现过 `cycle_retry`。

我们 49 次折叠（第 50 次因 WSL 关机未完成）最好 −1.6152，起点 −2.1928 与同事的 2.193 一致，说明打分、折叠、5 Å 门和 ESM-2 权重已经对齐。ESM-2 候选的挑选逻辑（正 LLR、按比值排序、先每个位点取一个再回填）也与 harness `_generate_esm2_guided` 逐条一致；同一父代产生同一批候选是 harness 本身的性质，不是偏差。剩余差距主要来自对话模型不同带来的技能组合差异：同事 10 轮里只抽中 1 次 ESM，我们抽中约 5 次。

## 剩余差异与存疑

- **analyze 调用频率（存疑）**：我们每次搜索开始时调用一次 analyze。harness 中调用它的位置还没有确认，是每轮、还是只在某些条件下，待确认后再定。
- **提示措辞**：各阶段提示只保证意思一致，不逐字对照。质检提示的结构已与 harness 一致，但候选摘要字段（`compact_candidate`）仍是我们自己的精简版本。
- **提示与工具/skill 的结构性差异**：harness 每个角色都绑定了 skill 和工具（`agents/profiles.py`）。analyze 用 protrek 检索，design 用 `generate_soluble_mpnn` / `generate_esm2_guided`，reflection 用 `analyze_*` 结构和 epitope 工具，quality 用 `check_antibody_developability`（开源版是 stub）。harness 的提示里因此有「调用 use_skill」「已注册的工具」这类说明。我们的 CDR 引擎是单次补全，没有工具循环，也不加载 skill 文档。生成类工具（ESM-2 引导、SolubleMPNN）已由 NPU 任务实现，缺的是 skill 文档内容；分析类工具（protrek、结构、epitope）尚无对应实现，且还需确认开源版是否可用。这部分不能只靠改措辞解决。
- **SolubleMPNN 的 `external` 报错（已核实，不是我们的问题）**：同事日志里 `No module named 'external'` 来自 harness 自带的工具后端（`servers/backends/soluble_mpnn.py` 在调用时导入 `external.ligandmpnn`）。我们的评估脚本在 `install_ligand` 里为 LigandMPNN 临时建了 `external/ligandmpnn` 的符号链接，并把它放进 `sys.path`，所以不走这条失败路径。这台主机上已在 NPU 环境中确认导入成功。
- **外部服务 protrek（未纳入预检）**：protrek 只被 harness 的 analyze 工具调用。我们的 CDR 引擎没有 analyze 工具，因此没有 skill 依赖它，预检也不检查它。若将来接入 analyze，再把它加入预检，并按“可选、不可用则标注原因、不阻塞搜索”处理。
- **本轮已修正的语义问题**：analyze 去掉了诱导性提问，加入 harness 的规则（不编造接触、1-based、CDR 只作标注）；设计提示补上了目标序列和热点，并加入「沿用给定父代、保持链 ID、序列互异」的约束；反思提示加入证据边界。
- **system prompt（部分残留）**：harness 每个角色（analyze、design、quality、reflect、parent selection）都有独立的 system 消息。我们的模型调用只有一条 user 消息。质检已先试验：把 harness 的 `QUALITY_CHECK_SYSTEM_PROMPT` 逐字放在质检提示最前面（不是真正的 system 角色）。其余角色和 system 角色的正式接入尚未做，需要改 `completion.py`，并改写 harness 面向工具式 agent 的提示。
- **developability（已对齐，不补后端）**：harness 的开源版 developability 后端是 stub，返回 `available:false`，同事的运行也是如此。我们的质检证据块同样发送 `{"available": false}`。若要增加真实的 pI / TAP / liability 后端，它会超出 harness 的证据范围，应作为明确标注的偏离单独决定。
- **速度（待定，未实现）**：当前每个候选是一个 NPU 任务，每次 `opendde pred` 重新加载模型，并且只用一张卡。harness 用常驻 predictor，并把候选分片到多张卡上。要在我们侧加速，需要 API 为单个 evolve 任务授予多张卡（`services/api/src/evolution/runner-proxy.ts` 目前由 API 按会话分配 `npuDevices`），而且这台主机的 NPU 与同事共用，需要先协调。我们目前没有投机折叠路径。若将来加入，门控须与 harness `_start_design_speculation` 一致：下一轮未超过总轮数、处于同一阶段、`(cycle + 1) % reflection_interval != 0`、父代选择策略不是 `llm`、已有打分种群和全局最优。ds10 的 `reflection_interval` 为 1，这一条使投机在每一轮都关闭，因此对当前的 CRLF2 对齐没有影响。
- **预算计法（保留）**：harness 设计阶段失败时按轮跳过，预算按轮计；我们按折叠次数计，见第 9 条。

## 打分与生成

打分、ESM-2 / SolubleMPNN 一批生成、压缩 `parent_backbone_pdb`、门控进种群，行为同 [v2](evolve-cdr.v2.md)。
