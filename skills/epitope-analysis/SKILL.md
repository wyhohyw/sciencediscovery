---
name: epitope-analysis
description: Count antigen residues within a distance cutoff of antibody atoms, and attribute those contacts to supplied CDR ranges. Use on a local complex PDB or mmCIF. Not for affinity, docking, or chemical interaction types.
compatibility: Requires Python 3 standard library only. CDR ranges must be supplied; the script does not infer IMGT numbering.
metadata:
  version: 1.0.0
---

# 表位距离

对一份本地复合物数抗原原子和抗体原子的距离。默认 4.5 Å，只数重原子。几何邻近不是氢键、盐桥或亲和力。

CDR 必须由调用方给出，按链内残基出现顺序的 1-based 闭区间，不是 PDB 残基号，也不是 IMGT 编号。没有区间时脚本拒绝运行。

```sh
python "$SCIENCEDISCOVERY_SKILLS_DIR/epitope-analysis/scripts/epitope_contacts.py" complex.pdb --antibody-chains H --antigen-chains A --cdr-json cdr.json --hotspots A:45,A:52 --output epitope.json
```

`cdr.json` 的形状是 `{"H": [[1, 7], [26, 32]]}`。热点用结构里的链和残基号，写成 `A:45`。

脚本包是只读的。工作区里已有用户自己的同等脚本时，运行那一份。把 JSON 留在工作区。回复里写表位残基数、接触总数、热点覆盖和各 CDR 的接触数，并写明截断距离。
