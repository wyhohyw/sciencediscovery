# CDR 序列搜索 v1

2026-11-23。`/evolve-design --algorithm cdr` 在现有 evolve 侧车上增加一种算法，不替换 PUCT 或 OpenEvolve。

## 候选

起点是 JSON，不是 Python。必填 `sequence`（氨基酸）、`cdr_regions`（含端点的 1-based 区间）和 `target_sequence`。可选 `chain`（默认 `D`）、`target_chain`（默认 `A`）、`target_hotspots`（靶链上的 1-based 位点）、`candidates_per_cycle`（默认 4）。`expansions` 是打分的子代条数。最后一段 `cdr_regions` 当作 CDR3。

每一轮按适应度温度从已得分的种群里选父代，向模型要 1–3 个 CDR 点突变。落在框架上、重复、或不改变残基的提议记为拒绝，不进种群。序列长度不变。

## 打分

沿用 `npu_job`：一条序列一次执行，进程结束即退出，不要求常驻 OpenDDE。固定程序是 `skills/evolve-design/scripts/cdr_opendde_evaluator.py`。它先导入 rdkit，再调用 `opendde_cli()` 的 `pred`。直接 `python -m runner.cli` 不会进入这个命令，而且在 910C 上若先加载 torch_npu 再加载 rdkit 会段错误。临时目录删除前读取 `summary_confidence`、`full_data` 和结构。

结构项按 `vhh-confidence-contact8-proxy-v2` 计算，权重为 `plddt` 1、`i_plddt` 1、`pae` 0.1、`i_pae` 0.5、`i_ptm` 1、`con` 0.1、`i_con` 0.1、`rg` 0.1、`dgram_cce` 0.01。这些项已经是惩罚。`score = -loss`，越高越好。pLDDT 和 iptm 大于 1 时先除以 100。PAE 取对称均值再除以 31。接触项用模型给出的 8 Å 接触概率。一次折叠没有 ESM-2 的伪对数似然，这一项记为 `esm2: omitted`，权重为 0，损失只含结构项。缺 `full_data` 字段时直接失败，不拿 `ranking_score` 或 `gpde` 顶替。

折叠成功后还有 5 Å 重原子门控。界面里 CDR 残基占比必须大于 0.5。给出 `target_hotspots` 时，CDR3 还要和至少一个热点有 5 Å 接触；不给热点时这一项直接通过。门控失败记 `ran: true`、`gate_passed: false`，该子代不进种群。序列不是氨基酸，或折叠失败，记 `ran: false`。

运行该程序的环境要有 `OPENDDE_CODE_DIR`、`OPENDDE_CHECKPOINT`，以及在需要时的 `OPENDDE_PYTHON`。未设置 `OPENDDE_ROOT_DIR` 时，用检查点所在目录的上一级，这样 `common/components.cif` 走本机缓存，不访问外网。NPU 沙箱不继承宿主机环境；runner 进程里的 `OPENDDE_*` 和 `LAYERNORM_TYPE` 会在勾选了 NPU 卡时放进沙箱。代码、检查点和 conda 环境目录还要用 `SCIENCE_AGENT_SANDBOX_RO_BINDS` 挂进去。这台 910C 自带的 bubblewrap 0.4.1 没有 `--clearenv`。`/tmp/cdr-bwrap/bin/bwrap` 是在这台机器上编出来的 0.11.0，没有替换系统里的那一份。沙箱还要挂上 `/data1/gs_agent/opendde` 和 `/root/miniconda3/envs/protenix`，因为 OpenDDE 的 Python 是指向后者的符号链接。这台机器上没有 Node，产品的 runner 还没在这里启动。

## 旁边的技能

`pdb-lookup`、`uniprot-lookup`、`pubmed-lookup`、`epitope-analysis`、`structure-analysis`、`evolutionary-analysis`、`developability-filter` 是独立技能。它们不进入这条搜索的 `score`。

## 不在这版

ESM-2 穷举、SolubleMPNN、运行中改权重、从事件日志恢复种群。控制面重启后 CDR 运行记为失败，不能续跑。因为一次折叠不含 ESM-2，这里的损失数字不能和含该项的历史最优直接比较。
