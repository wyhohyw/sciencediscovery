# 抗原复合物表位试跑

版本：1.5。打分用的热点名单固定为候选里的 `epitope_residues`，也就是最初抽出的完整表位。搜索仍可修改 `hotspots`，那只传给 RFdiffusion，用来引导这一轮的设计。流水线结束后，评测脚本用完整表位重新筛选，`hotspot_prob` 的分母是这份初始表位。1.4 及更早的说明留在下面。

版本：1.4。NPU 搜索的墙钟是 6 小时，整轮 token 上限 1,800,000，单个候选最长 1 小时。`num_designs` 收到 1 到 8，`protenix_n_sample` 收到 1 到 5。`diffuser_t` 仍是 15 到 40。超出范围的候选仍然不启动流水线，也不占节点。1.3 及更早的说明留在下面。

版本：1.3。搜索模型每次看到的提示写明取值范围：`num_designs` 1 到 16，`protenix_n_sample` 1 到 8，`diffuser_t` 15 到 40，`final_step` 从 1 到 `diffuser_t`，`protenix_use_msa` 为布尔值，热点必须是表位的非空子集。评测脚本拒绝超出范围的候选时不启动流水线；evolve 把这次拒绝记成未评分，不占节点。1.2 及更早的说明留在下面。

版本：1.2。基线是主仓 `openJiuwen-ai/sciencediscovery` 的 `main` `0763aba2f`。每一次新任务都按同一顺序做完：先为本任务写文献综述并声明为产物，再从这份综述取出 PDB、抗原链和抗体链，再下载并处理这个结构，然后才打包表位和发起一次 evolve。本任务的综述是后续步骤的依据。同一个任务里的后续 evolve 轮次不再重新检索文献。1.1 及更早的说明留在下面。

版本 1.1 只准备 `antibody-design` 的输入，并在本地记下按字段修改的轮次。它不提交 NPU 作业，也不在后续轮次里重新检索文献。1.0 把四轮证明的短配置写成了后续测试的配置；1.1 把两者分开。

## 一次文献

用户给出抗原名称，以及要抽取的是糖蛋白链还是 N 蛋白链。`literature-searcher` 只运行一次，检索抗原–抗体复合物，以及抗原、抗体各自的 PDB。`report-writer` 只根据这次文献包写 `antigen_epitope/literature_review.md`。没有可用复合物时停止，不用抗原单体做表面预测。

## 表位

对选中的复合物，抗原残基只要有任一非氢原子落在抗体链任一非氢原子的 4.5 Å 以内，就算表位残基。只读第一模型和空白或 A 的交替构象。水和其他溶剂忽略。界面上的糖记在 `epitope.json`，不进入热点。

热点沿用抗原链原来的链号和残基号，写成 `[B45,B46]`。没有 CA、带插入码或不能被现有流水线解析的残基单独列出。0 个可写热点时脚本失败。超过 40 个时在警告里要求核对链是否认错。

`antibody_pipeline/config.json` 使用抽出的抗原链、同一复合物中的抗体链，以及这份热点。阈值、PDB、链和完整表位集合写在 `design_input.json` 的 `frozen` 里，后面的轮次不能改。

## 迭代

`evolve_start.json` 是交给现有 evolve 的那一份 JSON。`editableFields` 只包含 `hotspots`、`num_designs`、`diffuser_t`、`final_step`、`protenix_n_sample`、`protenix_use_msa`。文献、PDB、链、阈值、完整表位和 `protenix_seeds` 留在对象里，但不放进可编辑字段。种子固定为起始值 `42`。`hotspots` 放在列表第一位。

评分程序是 `mock_npu_evaluator.py`。它按 RFdiffusion、ProteinMPNN、Protenix、screening 的名字走一遍，打印 `device` 为 `npu`、`mock` 为 true 的一行 JSON。分数由候选 JSON 决定，坏掉的热点更低。它不提交模型作业。`designed_sequences` 等真实序列出现后再加入可编辑字段。

四轮证明可以关掉 MSA，并把 `num_designs`、`protenix_n_sample` 都设为 1，`diffuser_t` 留在 15 到 30。实际测试不沿用这组限制：`protenix_use_msa` 为 true，`num_designs` 从 4 起，`protenix_n_sample` 仍为 1。种子继续固定为 `42`。
