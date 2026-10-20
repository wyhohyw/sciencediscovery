// Build the offline reading page from current specifications and historical baselines.
// Usage: node scripts/build-conversational-settings-page.mjs
import { readFile, writeFile } from 'node:fs/promises';
import { fileURLToPath } from 'node:url';
import { createHash } from 'node:crypto';
import { Script } from 'node:vm';
import { micromark } from '../node_modules/.pnpm/node_modules/micromark/index.js';
import { gfm, gfmHtml } from '../node_modules/.pnpm/node_modules/micromark-extension-gfm/index.js';

const root = new URL('../', import.meta.url);
const docsRoot = new URL('docs/zh/explanation/', root);
const sources = [
  { id: 'design', label: '完整设计 v18 · 任务选项与扩展', english: 'PRODUCT REVIEW · IMPLEMENTATION BOUNDARIES', filename: 'conversational-settings.v18.md', version: 'v18 / 维护者决策与扩展计划 · 2026.10.20', note: '保留四适配器和 Native 的既有实现边界；第 15 节对齐维护者选项要求，运行级覆盖、替代方案与更多设置为待开发目标，本次未修改产品代码。' },
  { id: 'development', label: '完整开发文档 v8', english: 'COMPLETE ENGINEERING GUIDE', filename: 'conversational-settings-development.v8.md', version: 'v8 / 选项、覆盖与工作包 · 2026.10.20', note: '第 1–18 节记录既有实现；第 19 节新增 DEV-CFG-11–17、目标合同、依赖和验收。运行级覆盖及新适配器尚未实现，实测仍以状态 v7 为准。' },
  { id: 'research', label: '竞品与底座调研 v2', english: 'PRODUCT PATTERNS & FRAMEWORK REUSE', filename: 'conversational-settings-research.v2.md', version: 'v2 / 结论已纳入设计与开发 · 2026.10.19', note: '比较 Codex、OpenCode、WorkBuddy 与 openJiuwen core / WorkSwarm。区分产品行为、源码能力、依赖版本与接入建议，不把上游组件存在当作本项目集成已通过。' },
  { id: 'status', label: '当前实现与实测 v7', english: 'IMPLEMENTATION & EVIDENCE', filename: 'conversational-settings-status.v7.md', version: 'v7 / 本轮验证单独记录 · 2026.10.20', note: '本轮构建、类型、架构及功能测试独立记录；Docker/SSH 外部用例按用户确认跳过，历史真实模型用例不替代新版本验收。' },
  { id: 'design-v11', label: '历史完整设计 v11', english: 'COMPLETE PRODUCT DESIGN · ARCHIVE', filename: 'conversational-settings.v11.md', version: 'v11 / 历史完整规格', archived: true, note: '历史完整设计，当前评审与实现边界见 v18；功能边界与技术约束保持可追溯。' },
  { id: 'interaction', label: '历史交互修订 v10', english: 'INTERACTION REVISION · ARCHIVE', filename: 'conversational-settings.v10.md', version: 'v10 / 历史增量修订', archived: true, note: '历史增量修订，已整合进完整设计 v11。当前阅读可直接从完整设计开始。' },
  { id: 'engineering', label: '历史恢复规格 v3', english: 'RECOVERY REVISION · ARCHIVE', filename: 'conversational-settings-development.v3.md', version: 'v3 / 历史增量修订', archived: true, note: '历史增量修订，已整合进完整开发文档 v4；保留供追溯。' },
  { id: 'archive-design', label: '历史产品设计 v1', english: 'PRODUCT DESIGN · ARCHIVE', filename: 'conversational-settings.v1.md', version: 'v1 / 2026.10.12 · 历史基线', archived: true, note: '历史设计基线，保留原文。完整设计 v11 已统一后续交互、作用范围和暂停恢复决策。' },
  { id: 'archive-development', label: '历史开发规格 v1', english: 'ENGINEERING GUIDE · ARCHIVE', filename: 'conversational-settings-development.v1.md', version: 'v1 / 2026.10.12 · 历史基线', archived: true, note: '历史目标架构，保留原文。请优先阅读完整开发文档 v8，其中已校正接口、状态和实现范围。' },
];

const escapeHtml = value => value.replaceAll('&', '&amp;').replaceAll('<', '&lt;').replaceAll('>', '&gt;').replaceAll('"', '&quot;');
const plain = value => value.replace(/<[^>]*>/g, '').replaceAll('&amp;', '&').replaceAll('&lt;', '<').replaceAll('&gt;', '>').replaceAll('&quot;', '"');

function splitDocument(markdown) {
  const parts = [{ title: '文档说明', markdown: '' }];
  let fence = null;
  for (const line of markdown.split('\n')) {
    const marker = line.match(/^\s*(`{3,}|~{3,})/);
    if (marker) {
      if (!fence) fence = marker[1][0];
      else if (marker[1][0] === fence) fence = null;
    }
    const heading = !fence && line.match(/^## (.+)$/);
    if (heading) parts.push({ title: heading[1], markdown: '' });
    else if (!/^# /.test(line) || fence) parts.at(-1).markdown += line + '\n';
  }
  if (fence) throw new Error('Unclosed Markdown code fence');
  return parts;
}

function architectureDiagram(source) {
  return `<figure class="architecture-figure">
    <figcaption><span class="tiny-label">SYSTEM MAP</span><strong>配置能力与运行系统的边界</strong></figcaption>
    <div class="architecture" role="img" aria-label="主 Agent 与用户卡片进入配置服务，配置服务经适配器写入已有领域存储，并经接续协调器连接运行队列。">
      <div class="architecture-entry"><div><small>AGENT</small><b>检查与提案</b></div><div><small>USER</small><b>输入与确认</b></div></div>
      <span class="diagram-arrow" aria-hidden="true">→</span>
      <div class="architecture-core"><small>CONTROL PLANE</small><b>Configuration<br>Service</b><span>校验 · 预览 · 应用</span></div>
      <span class="diagram-arrow" aria-hidden="true">→</span>
      <div class="architecture-outputs"><div><b>适配器与领域服务</b><small>已有设置 / 加密凭据 / SQLite</small></div><div><b>验证与接续协调器</b><small>受控探测 / 运行队列 / 事件</small></div></div>
    </div>
    <details class="diagram-source"><summary>查看完整 Mermaid 源码</summary><pre><code class="language-mermaid">${source}</code></pre></details>
  </figure>`;
}

async function embedReviewFigures(html) {
  const pattern = /<p><img src="(assets\/conversational-settings\/[0-9]{2}-[a-z-]+\.svg)" alt="([^"]*)"\s*\/?>\s*<\/p>/g;
  for (const match of Array.from(html.matchAll(pattern))) {
    const svg = (await readFile(new URL(match[1], docsRoot), 'utf8')).replaceAll('\r\n', '\n');
    if (/<(?:script|foreignObject|image|use)\b|\bon\w+\s*=|(?:href|src)\s*=/i.test(svg)) {
      throw new Error(`Review figure must be a self-contained static SVG: ${match[1]}`);
    }
    const dimensions = svg.match(/<svg\b[^>]*\bwidth="(\d+)"[^>]*\bheight="(\d+)"/);
    if (!dimensions) throw new Error(`Missing review figure dimensions: ${match[1]}`);
    const image = `<figure class="review-figure"><img src="data:image/svg+xml;base64,${Buffer.from(svg).toString('base64')}" alt="${match[2]}" width="${dimensions[1]}" height="${dimensions[2]}"><figcaption>${escapeHtml(plain(match[2]))}<a href="${match[1]}" download>下载矢量图 ↗</a></figcaption></figure>`;
    html = html.replace(match[0], image);
  }
  return html;
}

const documents = [];
for (const source of sources) {
  const raw = (await readFile(new URL(source.filename, docsRoot), 'utf8')).replaceAll('\r\n', '\n');
  const sections = await Promise.all(splitDocument(raw).map(async (part, index) => {
    const id = `${source.id}-${String(index).padStart(2, '0')}`;
    const title = part.title.replace(/^\d+\.\s*/, '');
    const outline = [];
    let html = micromark(part.markdown, { extensions: [gfm()], htmlExtensions: [gfmHtml()] });
    html = html.replace(/<h([3-6])>([\s\S]*?)<\/h\1>/g, (_, level, content) => {
      const headingId = `${id}-h${outline.length + 1}`;
      outline.push({ id: headingId, title: plain(content), level: Number(level) });
      return `<h${level} id="${headingId}">${content}</h${level}>`;
    });
    html = html.replace(/<pre><code class="language-mermaid">([\s\S]*?)<\/code><\/pre>/g, (_, value) => architectureDiagram(value));
    html = await embedReviewFigures(html);
    html = html.replace(/<table>/g, '<div class="table-scroll" tabindex="0" role="region" aria-label="可横向滚动的数据表"><table>').replace(/<\/table>/g, '</table></div>');
    html = html.replace(/<pre><code(?: class="language-([^"<>]+)")?>([\s\S]*?)<\/code><\/pre>/g, (_, language = 'text', value) => `<div class="code-block"><div class="code-toolbar"><span>${escapeHtml(language.toUpperCase())}</span><button type="button" class="copy-code" aria-label="复制 ${escapeHtml(language)} 代码">复制代码</button></div><pre><code${language !== 'text' ? ` class="language-${escapeHtml(language)}"` : ''}>${value}</code></pre></div>`);
    for (const target of sources) {
      html = html.replaceAll(`href="${target.filename}"`, `href="#${target.id}-00"`);
    }
    return { id, index, title, originalTitle: part.title, html, outline, searchText: part.markdown };
  }));
  documents.push({ ...source, raw, sha256: createHash('sha256').update(raw).digest('hex'), sections });
}

const expectedSections = { design: 16, development: 20, research: 11, status: 8, 'design-v11': 14, interaction: 4, engineering: 4, 'archive-design': 9, 'archive-development': 15 };
for (const doc of documents) if (doc.sections.length !== expectedSections[doc.id]) {
  throw new Error(`Document structure changed: ${doc.filename}; update chapter links before rebuilding.`);
}
const json = JSON.stringify(documents).replaceAll('<', '\\u003c');
const template = await readFile(new URL('scripts/docs/conversational-settings.template.html', root), 'utf8');
const styles = await readFile(new URL('scripts/docs/conversational-settings.css', root), 'utf8');
const readingPosition = await readFile(new URL('scripts/docs/conversational-settings-reading.mjs', root), 'utf8');
const script = (await readFile(new URL('scripts/docs/conversational-settings.js', root), 'utf8'))
  .replace('/* READING_POSITION */', readingPosition.replace(/^export function /m, 'function '));
if (template.split('<!-- DOCUMENT_DATA -->').length !== 2) throw new Error('Expected one document-data insertion point');
if (template.split('<!-- DOCUMENT_CONTENT -->').length !== 2) throw new Error('Expected one continuous-content insertion point');
const content = documents.map(doc => {
  const body = `<article class="reading-document" id="document-${doc.id}">
    <header class="reading-document-header"><div class="eyebrow">${escapeHtml(doc.english)}</div>
      <h1>${escapeHtml(doc.label)}</h1><p class="reading-document-meta">${escapeHtml(doc.version)} · ${doc.sections.length - 1} 章 · 连续全文</p>
      <aside class="document-notice${doc.archived ? ' is-archive' : ''}">${escapeHtml(doc.note)}</aside>
    </header>
    ${doc.sections.map(section => `<section class="document-chapter" id="${section.id}" aria-labelledby="${section.id}-title">
      <h2 id="${section.id}-title">${escapeHtml(section.originalTitle)}</h2><div class="prose">${section.html}</div>
    </section>`).join('\n')}
  </article>`;
  return doc.archived ? `<details class="archive-document"><summary>${escapeHtml(doc.label)} <span>历史全文 · 点击展开</span></summary>${body}</details>` : body;
}).join('\n');
const anchors = new Set(['overview', ...Array.from((template + content).matchAll(/\bid="([^"]+)"/g), match => match[1])]);
for (const doc of documents) for (const section of doc.sections) {
  anchors.add(section.id);
  for (const heading of section.outline) anchors.add(heading.id);
}
for (const html of [template, ...documents.flatMap(doc => doc.sections.map(section => section.html))]) {
  for (const match of html.matchAll(/href="#([^"]+)"/g)) {
    if (!anchors.has(match[1])) throw new Error(`Broken chapter link: #${match[1]}`);
  }
}
const output = template
  .replace('/* PAGE_STYLES */', styles)
  .replace('/* PAGE_SCRIPT */', script)
  .replace('<!-- DOCUMENT_CONTENT -->', content)
  .replace('<!-- DOCUMENT_DATA -->', `<script type="application/json" id="document-data">${json}</script>`);
for (const match of output.matchAll(/<script(?:\s[^>]*)?>([\s\S]*?)<\/script>/g)) {
  if (match[0].startsWith('<script type="application/json"')) continue;
  new Script(match[1]);
}
const destination = new URL('conversational-settings.html', docsRoot);
await writeFile(destination, output, 'utf8');
console.log(JSON.stringify({ output: fileURLToPath(destination), bytes: Buffer.byteLength(output), documents: documents.map(doc => ({ name: doc.filename, chapters: doc.sections.length - 1, sha256: doc.sha256 })) }, null, 2));
