---
name: developability-filter
description: Scan antibody sequences for CDR motifs, cysteines, glycosylation sequons, and charge counts. Use when an explicit sequence and CDR ranges are available. Not a measured pI, TAP, solubility, or humanness score.
compatibility: Requires Python 3 standard library only. Does not call SoluProt, BioPhi, or a structure-based TAP model.
metadata:
  version: 1.0.0
---

# 序列可开发性扫描

只扫描调用方给出的氨基酸序列。脚本不计算实测 pI，不跑 TAP、SoluProt 或 BioPhi。偶数个半胱氨酸也不证明它们已经配对。

```sh
python "$SCIENCEDISCOVERY_SKILLS_DIR/developability-filter/scripts/developability_scan.py" candidates.json --output developability.json
```

输入是 `{"candidates":[{"name":"c1","chains":[{"id":"H","sequence":"ACDE","cdr_regions":[[1,3]]}]}]}`。`cdr_regions` 是该链上 1-based 闭区间。

脚本报告：CDR 内的 NG、NS、DG、DP、甲硫氨酸和色氨酸；N-X-S/T 糖基化序列（X 不是 P）；半胱氨酸位点；CDR 内连续 4 个疏水残基；Asp/Glu、Lys/Arg、His 的个数。

回复按候选引用这些命中。不要把电荷个数写成 pI，也不要在缺少序列或 CDR 区间时补一种总体风险等级。
