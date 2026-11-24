---
name: evolutionary-analysis
description: Summarize parent-child search lineages and, when FoldMason is installed, a structural-similarity guide tree. Use on an explicit candidate JSON. Not a claim of natural phylogeny or evolutionary time.
compatibility: Lineage uses Python 3 standard library. The guide tree needs the foldmason executable and at least two structures of one chain.
metadata:
  version: 1.0.0
---

# 搜索谱系

先做父代到子代的谱系。候选 JSON 至少有 `candidate_id`、`parent_id`、`score`，突变放在 `mutations`。谱系、突变是否在不同父代上重复出现、以及分数变化，都从这张表来。

```sh
python "$SCIENCEDISCOVERY_SKILLS_DIR/evolutionary-analysis/scripts/search_lineage.py" candidates.json --output lineage.json
```

FoldMason 只在本机有 `foldmason`、并且另外给出至少两份同一条链的结构时才运行。指南树是结构相似，不是自然系统发育，也不表示进化时间。命令失败时保留谱系，并把结构树标成未运行。

```sh
python "$SCIENCEDISCOVERY_SKILLS_DIR/evolutionary-analysis/scripts/search_lineage.py" candidates.json --structures structures --chain D --output lineage.json
```

`structures` 目录里的文件名（不含扩展名）必须等于 `candidate_id`。不要把候选数量本身说成收敛。
