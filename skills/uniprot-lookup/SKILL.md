---
name: uniprot-lookup
description: Look up one reviewed UniProt protein by gene, name, or accession and read its sequence, domains, topology, glycosylation, and PDB cross-references. Use for an accession or extracellular boundary. Not for structure download or literature review.
compatibility: Uses web_fetch against the public UniProt REST API. No API key.
metadata:
  version: 1.0.0
---

# UniProt 查询

用 `web_fetch` 访问 `https://rest.uniprot.org`。每次请求都带 `fields`。不要拉整条未筛选的记录。没有 `web_fetch` 时停下来说明。

## 找编号

```text
https://rest.uniprot.org/uniprotkb/search?query=gene:PDCD1+AND+organism_id:9606+AND+reviewed:true&fields=accession,protein_name,length&format=tsv&size=5
```

常用条件：`gene:`、`protein_name:`、`accession:`、`organism_id:`（人是 9606，小鼠是 10090）、`reviewed:true`。用 `AND` 连接。

## 读一条

```text
https://rest.uniprot.org/uniprotkb/Q15116?fields=accession,protein_name,length,sequence,ft_domain,ft_signal,ft_chain,ft_topo_dom,ft_transmem,ft_carbohyd,ft_disulfid,xref_pdb&format=json
```

位点是前体序列上的 1-based 编号，含信号肽。只要序列时用 `https://rest.uniprot.org/uniprotkb/<编号>.fasta`。

## 切胞外区

用 `ft_signal` 和 `ft_topo_dom` 里标成 Extracellular 的起止切序列。不要用某条 PDB 构建的残基范围代替，那份构建可能带标签或截短。
