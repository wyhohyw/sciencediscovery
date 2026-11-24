---
name: pubmed-lookup
description: Fetch a few PubMed records or one open-access full text through NCBI E-utilities and Europe PMC. Use for a known PMID, a narrow biomedical query, or an open-access article. Not for a deduplicated literature package; that is literature-searcher.
compatibility: Uses web_fetch against public NCBI and Europe PMC endpoints. No API key. Keep to about three NCBI requests per second.
metadata:
  version: 1.0.0
---

# PubMed 查询

这是按 PMID 或一个很窄的检索式取题录、摘要或开放全文。要一份去重后的文献包时用 `literature-searcher`，不要用本技能代替它。没有 `web_fetch` 时停下来说明。

## 检索和题录

```text
https://eutils.ncbi.nlm.nih.gov/entrez/eutils/esearch.fcgi?db=pubmed&term=PD-1+nanobody&retmode=json&retmax=10&sort=relevance
```

PMID 在 `esearchresult.idlist`。字段限定写成 `PD-1[Title]`、`Zak KM[Author]`、`2020:2026[dp]`。

```text
https://eutils.ncbi.nlm.nih.gov/entrez/eutils/esummary.fcgi?db=pubmed&id=26602187,28280600&retmode=json
```

读 `title`、`source`、`pubdate`、`authors[].name`、`elocationid`。

摘要：

```text
https://eutils.ncbi.nlm.nih.gov/entrez/eutils/efetch.fcgi?db=pubmed&id=26602187&rettype=abstract&retmode=text
```

## 开放全文

```text
https://www.ebi.ac.uk/europepmc/webservices/rest/search?query=PD-1+nanobody+AND+OPEN_ACCESS:y&format=json&pageSize=10&resultType=lite
```

只对 `isOpenAccess` 为 `Y` 的记录取全文。全文地址是 `https://www.ebi.ac.uk/europepmc/webservices/rest/<PMCID>/fullTextXML`，`PMCID` 已带 `PMC` 前缀。其余文献停在摘要。
