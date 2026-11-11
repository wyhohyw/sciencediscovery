# 抗原复合物表位试跑

版本：1.0。基线是主仓 `openJiuwen-ai/sciencediscovery` 的 `main` `0763aba2f`。这一版只准备 `antibody-design` 的输入，并在本地记下按字段修改的轮次。它不提交 NPU 作业，也不在后续轮次里重新检索文献。

## 一次文献

用户给出抗原名称，以及要抽取的是糖蛋白链还是 N 蛋白链。`literature-searcher` 只运行一次，检索抗原–抗体复合物，以及抗原、抗体各自的 PDB。`report-writer` 只根据这次文献包写 `antigen_epitope/literature_review.md`。没有可用复合物时停止，不用抗原单体做表面预测。

## 表位

对选中的复合物，抗原残基只要有任一非氢原子落在抗体链任一非氢原子的 4.5 Å 以内，就算表位残基。只读第一模型和空白或 A 的交替构象。水和其他溶剂忽略。界面上的糖记在 `epitope.json`，不进入热点。

热点沿用抗原链原来的链号和残基号，写成 `[B45,B46]`。没有 CA、带插入码或不能被现有流水线解析的残基单独列出。0 个可写热点时脚本失败。超过 40 个时在警告里要求核对链是否认错。

`antibody_pipeline/config.json` 使用抽出的抗原链、同一复合物中的抗体链，以及这份热点。阈值、PDB、链和完整表位集合写在 `design_input.json` 的 `frozen` 里，后面的轮次不能改。

## 轮次

每一轮相对当前已接受的状态只改一个字段：

- L1：`hotspots`，而且必须是已算出表位的非空子集。
- L2：`num_designs`、`diffuser_t`、`final_step`、`protenix_n_sample`、`protenix_seeds`、`protenix_use_msa` 之一。`diffuser_t` 至少为 15，`final_step` 在 1 和 `diffuser_t` 之间。
- L3：`designed_sequences`。基线是空列表。抗体流水线返回真实序列后，先把这些序列写入账本的 `parent`，再允许修改，而且不能增减条数。`round` 不能用来凭空写下第一条序列。

分数由外部传入。脚本不生成抗体，也不编造分数。改了两个字段、改了锁定信息、或同一个状态没有更高分数时，这一轮记为拒绝，不成为新的当前状态。

多轮之后由 `summarize` 写出 PDB、链、每一轮的旧值新值和接受或拒绝原因。
