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
  { id: 'status', label: '当前实现与实测', english: 'IMPLEMENTATION & EVIDENCE', filename: 'conversational-settings-status.v1.md', version: 'v1 / 更新于 2026.10.19', note: '隔离分支 a88d9283；真实模型验证日期为 2026.10.16。当前能力与未完成项以本文为准。' },
  { id: 'interaction', label: '交互修订 v10', english: 'CURRENT INTERACTION', filename: 'conversational-settings.v10.md', version: 'v10 / 原任务暂停与恢复', note: '当前交互规范。原生执行器的已安装连接器启用支持同一 run 恢复，其他实现边界见“当前实现与实测”。' },
  { id: 'engineering', label: '恢复规格 v3', english: 'CURRENT ENGINEERING', filename: 'conversational-settings-development.v3.md', version: 'v3 / 持久检查点', note: '当前暂停恢复规格。文中的通用 outbox 与未知副作用隔离尚未全部落地，详见“当前实现与实测”。' },
  { id: 'design', label: '产品设计基线 v1', english: 'PRODUCT DESIGN · ARCHIVE', filename: 'conversational-settings.v1.md', version: 'v1 / 2026.10.12 · 历史基线', archived: true, note: '历史设计基线，保留原文。实现状态、接续方式与作用范围已有后续修订，请先阅读“当前实现与实测”和交互 v10。' },
  { id: 'development', label: '开发规格基线 v1', english: 'ENGINEERING GUIDE · ARCHIVE', filename: 'conversational-settings-development.v1.md', version: 'v1 / 2026.10.12 · 历史基线', archived: true, note: '历史目标架构，保留原文。拟定的通用接口、表单与全部工作包不代表已经实现；恢复语义已由开发规格 v3 修订。' },
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

const documents = [];
for (const source of sources) {
  const raw = (await readFile(new URL(source.filename, docsRoot), 'utf8')).replaceAll('\r\n', '\n');
  const sections = splitDocument(raw).map((part, index) => {
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
    html = html.replace(/<table>/g, '<div class="table-scroll" tabindex="0" role="region" aria-label="可横向滚动的数据表"><table>').replace(/<\/table>/g, '</table></div>');
    html = html.replace(/<pre><code(?: class="language-([^"<>]+)")?>([\s\S]*?)<\/code><\/pre>/g, (_, language = 'text', value) => `<div class="code-block"><div class="code-toolbar"><span>${escapeHtml(language.toUpperCase())}</span><button type="button" class="copy-code" aria-label="复制 ${escapeHtml(language)} 代码">复制代码</button></div><pre><code${language !== 'text' ? ` class="language-${escapeHtml(language)}"` : ''}>${value}</code></pre></div>`);
    for (const target of sources) {
      html = html.replaceAll(`href="${target.filename}"`, `href="#${target.id}-00"`);
    }
    return { id, index, title, originalTitle: part.title, html, outline, searchText: part.markdown };
  });
  documents.push({ ...source, raw, sha256: createHash('sha256').update(raw).digest('hex'), sections });
}

const expectedSections = { status: 8, interaction: 4, engineering: 4, design: 9, development: 15 };
for (const doc of documents) if (doc.sections.length !== expectedSections[doc.id]) {
  throw new Error(`Document structure changed: ${doc.filename}; update chapter links before rebuilding.`);
}
const json = JSON.stringify(documents).replaceAll('<', '\\u003c');
const template = await readFile(new URL('scripts/docs/conversational-settings.template.html', root), 'utf8');
const styles = await readFile(new URL('scripts/docs/conversational-settings.css', root), 'utf8');
const script = await readFile(new URL('scripts/docs/conversational-settings.js', root), 'utf8');
if (template.split('<!-- DOCUMENT_DATA -->').length !== 2) throw new Error('Expected one document-data insertion point');
const anchors = new Set(['overview', ...Array.from(template.matchAll(/\bid="([^"]+)"/g), match => match[1])]);
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
  .replace('<!-- DOCUMENT_DATA -->', `<script type="application/json" id="document-data">${json}</script>`);
for (const match of output.matchAll(/<script(?:\s[^>]*)?>([\s\S]*?)<\/script>/g)) {
  if (match[0].startsWith('<script type="application/json"')) continue;
  new Script(match[1]);
}
const destination = new URL('conversational-settings.html', docsRoot);
await writeFile(destination, output, 'utf8');
console.log(JSON.stringify({ output: fileURLToPath(destination), bytes: Buffer.byteLength(output), documents: documents.map(doc => ({ name: doc.filename, chapters: doc.sections.length - 1, sha256: doc.sha256 })) }, null, 2));
