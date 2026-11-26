# CDR 序列搜索 v3

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
8. **analyze 与 reflect 两个阶段**：搜索开始前让对话模型读一次 CDR 图谱和靶点热点，产出一段不超过 120 词的说明，作为每轮提示的 `ANTIBODY HEADER / CDR MAP`；每轮折叠完后再让模型回看该轮的改动和分数，产出不超过 80 词的小结，作为下一轮的 `REFLECTION ON THE LAST CYCLE`。harness 的 `reflection_interval` 是 1，即每轮都反思。两个阶段的模型调用失败时退回 `none`，不影响搜索。

点突变仍用运行的对话模型出 1–3 处替换；不要求与同事 harness 使用同一家对话模型。

## 打分与生成

打分、ESM-2 / SolubleMPNN 一批生成、压缩 `parent_backbone_pdb`、门控进种群，行为同 [v2](evolve-cdr.v2.md)。
