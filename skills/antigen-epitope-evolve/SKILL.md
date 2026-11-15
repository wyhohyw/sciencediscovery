---
name: antigen-epitope-evolve
description: From one antigen name, search the literature once, take a 4.5 angstrom epitope from an antibody complex, and start one evolve search. Use when the user wants that antigen-to-antibody-design loop. Not for a second literature search, and not for a model process of your own.
compatibility: Requires Python 3 standard library only. Literature search and the antibody model run stay in their own skills.
metadata:
  version: 1.2.0
---

# 抗原表位和抗体设计

用户给一个抗原名。你查一次文献，算出表位，然后只发起一次 evolve。查完不要再查。

## 文献

先问抗原名。用户已经给了就直接做。选 `literature-searcher`，查这个抗原的抗原抗体复合物，以及单独的抗原、抗体 PDB。来源记在 `antigen_epitope/literature_sources.json`。选 `report-writer`，只根据这份来源写 `antigen_epitope/literature_review.md`。写明复合物 PDB、抗原链、抗体链。没有这样的复合物就停，说缺什么。不要改去预测单独抗原的表面。

## 表位

把复合物 PDB 放到 `antigen_epitope/complex.pdb`，再跑：

```sh
python "$SCIENCEDISCOVERY_SKILLS_DIR/antigen-epitope-evolve/scripts/antigen_epitope.py" package \
  --pdb antigen_epitope/complex.pdb \
  --antigen-chain B \
  --antibody-chains H,L \
  --output-dir antibody_pipeline/inputs \
  --workspace-input-dir antibody_pipeline/inputs \
  --pdb-id 1ABC \
  --literature-id antigen_epitope/literature_sources.json \
  --run-name trial-antigen \
  --cutoff 4.5
```

链号、PDB、run name 换成综述里的。cutoff 保持 4.5。表位是抗原和抗体非氢原子距离小于 4.5 Å 的残基。失败就报 `epitope.json` 里的 error，然后停。

`framework_pdb` 用 `/home/lujiale/antibody_pipeline/resources/presets/6DZM/3dwt_HLT.pdb`。不要用复合物里拆出来的抗体链。`target_pdb` 用 `/workspace/target_antigen.pdb`。

## 搜索

读 `antibody_pipeline/inputs/evolve_start.json`。选了 NPU runner 时，`evaluatorSource` 用 `real_npu_evaluator.py` 的全文；否则用 `mock_npu_evaluator.py` 的全文。只调用一次 `create_evolve_run`，然后结束这次对话。

```text
create_evolve_run(
  mode="npu_job",
  algorithm="puct",
  statement="把抗体设计的分数做高。",
  howScored="分数越高越好。热点必须是表位的子集。",
  startingPointText=<evolve_start.json 的全文>,
  editableFields=["hotspots", "num_designs", "diffuser_t", "final_step", "protenix_n_sample", "protenix_use_msa"],
  evaluatorSource=<上面选定的脚本全文>,
  expansions=4,
  workers=1,
  thinking="disabled"
)
```

`hotspots` 放在第一位。种子保持 `"42"`，不要放进可改字段。可改范围：`num_designs` 1 到 8，`protenix_n_sample` 1 到 4，`diffuser_t` 15 到 30，`final_step` 从 1 到 `diffuser_t`，`protenix_use_msa` 是布尔值。短验证用 1 个设计、1 个样本、MSA 关。正式测试用 MSA 开、`num_designs` 4、`protenix_n_sample` 1。

搜索结束时，最佳设计的序列和结构会记成产物。不要另起模型进程，也不要自己再跑一轮流水线。
