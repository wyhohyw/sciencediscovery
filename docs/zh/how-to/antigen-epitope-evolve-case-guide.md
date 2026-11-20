# ScienceDiscovery 使用指南：抗原表位与抗体进化案例

本指南说明如何在 ScienceDiscovery 里完成一条案例：文献只查一次，算出复合物表位，再把起始 JSON 交给产品自带的 evolve。搜索、改写和打分都发生在同一次会话里。

## 1. 案例概述

用户给出一个抗原，并说明要看的是糖蛋白链还是 N 蛋白链。ScienceDiscovery 做四件事：

- **文献一次**：用文献搜索连接器查抗原–抗体复合物，以及抗原、抗体各自的 PDB，写出一份综述。之后的轮次不再查文献。
- **表位**：在选中的复合物上，抗原残基只要有任一非氢原子落在抗体链任一非氢原子的 4.5 Å 以内，就算表位。热点写成 `[A13,A14]` 这种链号加残基号。
- **进化**：把 `evolve_start.json` 交给会话里的 `create_evolve_run`。算法是 PUCT，模式是 `npu_job`。下一份候选由会话正在使用的模型根据父代 JSON 改写，只允许改指定字段。
- **打分**：评分程序跑 `antibody-protenix-pipeline`，分数是筛选脚本的 `screen_score`，也就是 (ipTM + 热点接触比例) / 2。更高更好。

`protenix_seeds` 固定为起始值 `42`，不进入可编辑字段。没有抗原–抗体复合物时停止，不对抗原单体做表面预测。

## 2. 模型配置

进入 **系统配置 → 模型注册表**，添加会话要使用的模型。evolve 的改写使用这个模型，不另起进程。

| 字段 | 填什么 |
|---|---|
| 显示名 | 自定义，例如 `opencsi-deepseek-v4.1-flash` |
| Base URL | `http://113.46.219.251:8080/v1` |
| 模型 ID | `DeepSeek-V4.1-Flash-1017` |
| API token | OpenCSI 的 key。加密存在后端，界面不显示明文 |

新建 Session 时选择这个模型。

## 3. 系统设置

### 3.1 连接器

文献只查一次。在 **系统配置 → Connectors** 里启用本次综述需要的文献库，例如 PubMed。不需要的连接器保持关闭。

### 3.2 技能

确认这两个内置技能可用：

- `antigen-epitope-evolve`：文献包、表位和 `evolve_start.json`。
- `antibody-protenix-pipeline`：真卡评分时由 evolve 的评分程序调用。

### 3.3 超时

一轮真卡评分包含 RFdiffusion、ProteinMPNN、Protenix 和筛选。把 **Runner 执行** 和 **Agent 单轮** 调到能覆盖一轮评分的时长。4 次展开会连续评分多次。

## 4. 创建 Project 与 Session

左侧 **新建 Project**：

- 名称：抗原的短名，例如 `6dzm-epitope-evolve`
- 描述：从该抗原的复合物表位出发，用 evolve 提高抗体设计分数

进 Project 后 **添加会话**。模型选第 2 节注册的那个。

## 5. 下发任务

在对话框输入：

```text
对这个抗原做一次抗原–抗体复合物文献综述，抽取糖蛋白链（或 N 蛋白链）上 4.5 Å 的复合物表位。
没有复合物就停止。有复合物就生成 evolve_start.json，然后只调用一次 create_evolve_run：
mode 为 npu_job，algorithm 为 puct，expansions 为 4，workers 为 1，thinking 为 disabled。
editableFields 是 hotspots、num_designs、diffuser_t、final_step、protenix_n_sample、protenix_use_msa，hotspots 放在第一位。
protenix_seeds 保持 42，不要放进可编辑字段。
评分程序调用 antibody-protenix-pipeline，最后一行 JSON 的 device 为 npu，score 为 screen_score。
目标是提高这个分数。
```

把“这个抗原”和“糖蛋白链或 N 蛋白链”换成具体名称。点击 **运行分析**。

## 6. 会话里应发生的事

Agent 按这个顺序做，并且只做一次文献搜索、只创建一次 evolve：

1. 选出文献搜索技能，把来源写到 `antigen_epitope/literature_sources.json`。
2. 只根据这个文献包写 `antigen_epitope/literature_review.md`。没有可用复合物就说明缺什么并停止。
3. 运行 `antigen-epitope-evolve` 的 `package` 子命令，得到 `antibody_pipeline/inputs/evolve_start.json`。
4. 调用 `create_evolve_run`。`startingPointText` 是这份 JSON 的全文。`evaluatorSource` 是实际调用 `antibody-protenix-pipeline` 的程序。
5. 等搜索自己结束。PUCT 选择父代，会话模型改写允许的字段，评分程序打分。更差的候选留在树上，但不会变成当前最好节点。

不要在会话外再起一个模型进程，也不要手写四组固定配置代替 `create_evolve_run`。

## 7. 查看结果

会话结束时应能看到：

- 文献综述和表位说明，包括热点、糖接触和未能写成热点的残基。
- 一次 evolve 搜索。状态成功时有基线分数、每次展开的字段改动和分数，以及最好节点。
- 最好节点的 `screen_score`。分数可以不高于基线；搜索是否跑通看 evolve 是否在会话里完成，而不是看分数是否上升。

真卡结果在抗体流水线的运行目录。`screen_score` 来自 `05_screening/protenix_screening_summary.csv`。
