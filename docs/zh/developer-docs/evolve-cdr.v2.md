# CDR 序列搜索 v2

2026-11-25。在 [v1](evolve-cdr.v1.md) 上补 ESM-2、SolubleMPNN，以及对话点突变。折叠、5 Å 门、固定长度和「没过门不进种群」不变。不改 `/data1/gs_agent/opendde` 里同事的代码和权重，搜索去调用已经装好的客户端。

策略对齐（agent 路由、`cdr-full-redesign`、父代均匀混合）见 [v3](evolve-cdr.v3.md)。

运行中改权重、从事件日志恢复种群，仍然不做。

## 已安装的模型

910C 上已经有这两份权重，不用再下载。

- ESM-2 是 33 层（650M），在 `/data1/gs_agent/opendde/weights/esm2`。同事源码里未设置 `ESM_MODEL_NAME` 时的默认名字是 `facebook/esm2_t12_35M_UR50D`，那份不在这台机器上。调用必须指到这份 650M 目录，并离线加载。
- SolubleMPNN 权重是 `/data1/gs_agent/opendde/weights/soluble_mpnn/solublempnn_v_48_020.pt`。代码在同级的 `LigandMPNN`，用的是 `soluble_mpnn`，不是配体条件模型。同事的客户端按 `external.ligandmpnn` 导入，现场没有这个包目录，所以作业里临时把 `LigandMPNN` 链到这个名字，不改 `/data1/gs_agent/opendde`。`venv_pd` 能导入 `opendde_harness`。

## 打分

结构项和权重与 v1 相同。另加 ESM-2 平均伪对数似然，权重 `0.1`。该项通常是负数。贡献是 `-0.1 * pll`，加进损失，所以越不像天然序列，损失越大。`score = -loss`，越高越好。公式名改为 `vhh-confidence-contact8-proxy-esm2-v2`。

没有拿到有限的 `pll` 时，仍记 `esm2: omitted`，公式保持 v1 的名字，不拿别的模型的 `ranking_score` 顶替。

同一次 NPU 作业里生成并折叠，不拆成三次搜索。作业解释器没有这些包，所以被抽中的模型用 `OPENDDE_PYTHON` 起子进程，跑完就退出，显存随进程释放。折叠仍是单独的子进程，因为 910C 上必须先导入 rdkit 再加载 OpenDDE。

一轮只抽一个技能，种子串是 `protein-design-router:{seed}:{cycle}`。默认权重对齐同事 CRLF2（不含全量重设计）：对话点突变 `0.5`、ESM-2 `0.2`、SolubleMPNN `0.2`。起点 JSON 可写 `skill_weights` 覆盖。没有对话模型时去掉点突变；没有父代骨架时去掉 SolubleMPNN。

1. 抽中对话点突变时，引擎用运行的对话模型一次要一批 1–3 处 CDR 替换，再逐条发 NPU 只折叠、不生成。
2. 抽中 ESM-2 时，这次作业做深度突变扫描，按原版一次取出最多 `num_sequences` 条：对数似然比大于等于 0 的 CDR 替换按比值从高到低排序，先各取一个不同位点，名额还有剩再取同一位点的下一个氨基酸。作业折叠这一批的第一条，其余留在 `portfolio`，后面的作业只折叠，不再生成。
3. 抽中 SolubleMPNN 时，这次作业用父代骨架按温度 0.1 采样。一轮采样不够互不相同的序列时，种子加一再采样，最多三轮。框架位点不动，CDR 可以改多个残基。
4. 子进程做 OpenDDE 折叠。折叠结束后再起一个 ESM-2 子进程，把伪对数似然加进这一次的损失。

ESM/MPNN 那一批如果都是已经折过的序列，同一次作业改跑另一个允许的技能，仍然一次生成一整批，不从排序里补下一条。两个技能都没有新序列时，这一轮记为没有突变。SolubleMPNN 采样失败时记下 `soluble_error`，并改用 ESM-2 的这一批。

起点那一次只做折叠和伪对数似然。折叠得到的骨架只保留 binder 链上的 N、CA、C、O，再 gzip 压缩后放进结果，下一轮作为 `parent_backbone_pdb` 传回去。靶链不放进去，避免大复合物把作业参数撑爆。

## 提议

提议按 OpenDDE harness 的一轮一批，不把比值列表当成游标逐条往下取。同一条父序列上，ESM-2 再调用一次得到的是同一批；已经折过的序列从这一批里丢掉，不因此去取下一名。SolubleMPNN 靠温度采样和加种子的重试得到不同序列。`candidates_per_cycle` 小于 2 时按 harness 的默认 8 条生成，因为 1 会让 ESM-2 每次都只剩那条最高比值。正的比值只说明序列更像天然序列，不说明结合更好；子代仍然要折叠、过 5 Å 门，才进种群。

SolubleMPNN 用父代骨架重写 CDR，框架残基固定。它的 `mpnn_score` 是被设计残基上的平均负对数概率，不是这次搜索的 `score`。没有父代骨架时不做这一步。ESM/MPNN 这一轮没有可用序列时，记为没有突变；点突变由引擎单独走对话模型，不在 NPU 作业里补。
