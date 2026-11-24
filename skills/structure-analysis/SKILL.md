---
name: structure-analysis
description: Run PLIP on one local protein complex and save its interaction report. Use when hydrogen bonds, salt bridges, hydrophobic contacts, or pi-stacking are needed. Not a substitute for a distance contact count.
compatibility: Requires a local PLIP command (PLIP_COMMAND, plipcmd.py, or plipcmd). Python 3 standard library writes a temporary PDB when the input is mmCIF.
metadata:
  version: 1.0.0
---

# 结构相互作用

对一份已经含有靶点和结合链的复合物运行 PLIP。一次一份文件。结合链用 `--binder-chains` 传给 PLIP 的蛋白-蛋白模式。找不到 PLIP 时写明命令缺失并停止；改用 `epitope-analysis` 做距离计数，不要从置信度推断氢键或盐桥。

```sh
python "$SCIENCEDISCOVERY_SKILLS_DIR/structure-analysis/scripts/structure_interactions.py" complex.cif --binder-chains D --output interactions.json
```

完整的 PLIP 文本留在 JSON 指向的报告文件里。回复只引用 JSON 里的计数和报告路径。相互作用只对这次分析的那一份结构成立。
