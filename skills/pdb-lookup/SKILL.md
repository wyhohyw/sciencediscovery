---
name: pdb-lookup
description: Look up one PDB entry, its chains, or a short text or sequence search through the public RCSB JSON APIs. Use for a known PDB ID, a complex, or a chain sequence. Not for literature review, docking, or folding.
compatibility: Uses web_fetch against public RCSB endpoints. No API key.
metadata:
  version: 1.0.0
---

# PDB 查询

会话里有 `web_fetch` 时，用下面这些不需要密钥的地址。一次问题通常是一次检索加一次条目读取。不要把整份 JSON 贴进回复，只引用用到的字段。没有 `web_fetch` 时停下来说明，不要改用网页搜索代替条目接口。

## 检索

`https://search.rcsb.org/rcsbsearch/v2/query?json=` 后面接 URL 编码的 JSON。全文检索：

```json
{"query":{"type":"terminal","service":"full_text","parameters":{"value":"PD-1"}},"return_type":"entry","request_options":{"paginate":{"start":0,"rows":10}}}
```

序列检索把 `service` 换成 `sequence`，`parameters` 用 `value`、`identity_cutoff`、`evalue_cutoff`、`sequence_type`（`protein`）。结果里的 PDB ID 在 `result_set[].identifier`。

## 读一条

`https://data.rcsb.org/rest/v1/core/entry/<PDB>`

只取 `struct.title`、`exptl[].method`、`rcsb_entry_info.resolution_combined`、`citation[0]` 的题目、期刊、年份和 DOI。

单条聚合物实体：`https://data.rcsb.org/rest/v1/core/polymer_entity/<PDB>/<实体号>`。序列在 `entity_poly.pdbx_seq_one_letter_code`，链字母在 `rcsb_polymer_entity_container_identifiers.auth_asym_ids`。

坐标用 `bash` 里的 `curl -o` 保存，不要用 `web_fetch` 拉整份结构：

```sh
curl -fsSL -o 5GGS.cif https://files.rcsb.org/download/5GGS.cif
```

FASTA 在 `https://www.rcsb.org/fasta/entry/<PDB>`。
