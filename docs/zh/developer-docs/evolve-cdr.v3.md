# CDR 序列搜索 v3

2026-11-25。在 [v2](evolve-cdr.v2.md) 上对齐同事 CRLF2 的搜索策略细节：技能路由、`cdr-full-redesign`、父代均匀混合。折叠公式、5 Å 门、固定长度和「没过门不进种群」与 v2 相同。仍不改 `/data1/gs_agent/opendde`。

## 相对 v2

v2 已有 ESM-2、SolubleMPNN、对话点突变和压缩 binder 骨架。v3 补齐：

1. **技能路由**默认 `agent`：有对话模型时，每轮由模型在合法技能里选一个 `skill_id`（提示里带 prior 权重）；解析失败或没有对话模型时，退回 `protein-design-router:{seed}:{cycle}` 加权抽样。起点 JSON 可写 `router_selection_strategy` 为 `weighted` 强制只用加权抽样。
2. **默认权重**对齐 CRLF2：`cdr-point-mutation` 0.5、`cdr-full-redesign` 0.1、`esm2-guided-mutation` 0.2、`antibody-inverse-folding` 0.2。`skill_weights` 仍可覆盖。没有对话模型时去掉两个 LLM 技能；没有父代骨架时去掉 SolubleMPNN。
3. **全量重设计**：抽中 `cdr-full-redesign` 时，对话模型必须给每个可设计 CDR 位点恰好一次赋值（允许与父代相同的占位），引擎去掉无改动后再折叠。候选之间应产生不同序列。
4. **父代选择**：适应度 softmax 之外混入 `parent_fitness_uniform_fraction`（默认 0.1），与 harness 一致；温度仍从 1.0 收到 0.2。

点突变仍用运行的对话模型出 1–3 处替换；不要求与同事 harness 使用同一家对话模型。

## 打分与生成

打分、ESM-2 / SolubleMPNN 一批生成、压缩 `parent_backbone_pdb`、门控进种群，行为同 [v2](evolve-cdr.v2.md)。
