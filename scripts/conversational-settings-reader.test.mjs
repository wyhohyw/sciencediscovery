import assert from 'node:assert/strict';
import { readFile } from 'node:fs/promises';
import test from 'node:test';
import { currentReadingSection } from './docs/conversational-settings-reading.mjs';

test('overview stays active until the first chapter crosses the reading line', () => {
  assert.equal(currentReadingSection([{ id: 'design-00', top: 121 }]), null);
  assert.equal(currentReadingSection([{ id: 'design-00', top: 120 }]), 'design-00');
});

test('scrolling selects the last crossed chapter and retains it in chapter gaps', () => {
  const chapters = [
    { id: 'design-01', top: -1500 },
    { id: 'design-02', top: -100 },
    { id: 'design-03', top: 500 },
  ];
  assert.equal(currentReadingSection(chapters), 'design-02');
  assert.equal(currentReadingSection(chapters.map(c => ({ ...c, top: c.top - 500 }))), 'design-03');
});

test('reading line can follow a different fixed header height', () => {
  assert.equal(currentReadingSection([{ id: 'design-01', top: 110 }], { line: 100 }), null);
  assert.equal(currentReadingSection([{ id: 'design-01', top: 110 }], { line: 115 }), 'design-01');
});

test('a short final chapter is selected at page bottom', () => {
  assert.equal(currentReadingSection([
    { id: 'status-06', top: -800 },
    { id: 'status-07', top: 300 },
  ], { atBottom: true }), 'status-07');
});

test('closed historical documents never displace the visible current chapter', () => {
  const chapters = [
    { id: 'status-07', top: -100 },
    { id: 'archive-design-00', top: 0, visible: false },
  ];
  assert.equal(currentReadingSection(chapters), 'status-07');
  assert.equal(currentReadingSection(chapters, { atBottom: true }), 'status-07');
  assert.equal(currentReadingSection([{ id: 'hidden', top: 0, visible: false }], { atBottom: true }), null);
});

test('an opened historical document participates in the same reading order', () => {
  assert.equal(currentReadingSection([
    { id: 'status-07', top: -500 },
    { id: 'archive-design-01', top: 50, visible: true },
    { id: 'archive-design-02', top: 700, visible: true },
  ]), 'archive-design-01');
  assert.equal(currentReadingSection([]), null);
});

test('generated offline HTML contains every chapter in order before any JavaScript runs', async () => {
  const page = await readFile(new URL('../docs/zh/explanation/conversational-settings.html', import.meta.url), 'utf8');
  const dataMarker = '<script type="application/json" id="document-data">';
  const data = JSON.parse(page.split(dataMarker)[1].split('</script>')[0]);
  const body = page.slice(page.indexOf('<body>'), page.indexOf(dataMarker));
  const chapterIds = Array.from(body.matchAll(/<section class="document-chapter" id="([^"]+)"/g), match => match[1]);
  assert.deepEqual(chapterIds, data.flatMap(doc => doc.sections.map(section => section.id)));
  assert.equal(new Set(chapterIds).size, chapterIds.length);
  assert.match(body, /<div id="reader-view">/);
  assert.doesNotMatch(body, /chapter-pagination|DOCUMENT_CONTENT/);
  assert.equal(Array.from(body.matchAll(/<details class="archive-document">/g)).length, data.filter(doc => doc.archived).length);
  for (const doc of data) for (const section of doc.sections) {
    assert.ok(body.includes(`<h2 id="${section.id}-title">`));
    for (const heading of section.outline) assert.ok(body.includes(`id="${heading.id}"`));
  }
});

test('review diagrams are distinct, accessible SVGs embedded without network dependencies', async () => {
  const base = new URL('../docs/zh/explanation/', import.meta.url);
  const page = await readFile(new URL('conversational-settings.html', base), 'utf8');
  const body = page.slice(page.indexOf('<body>'), page.indexOf('<script type="application/json"'));
  const figures = Array.from(body.matchAll(/<figure class="review-figure"><img src="data:image\/svg\+xml;base64,([^"]+)" alt="([^"]+)" width="(\d+)" height="(\d+)"><figcaption>[\s\S]*?<a href="(assets\/conversational-settings\/[^"<>]+\.svg)" download>/g));
  assert.equal(figures.length, 6);
  assert.equal(new Set(figures.map(figure => figure[1])).size, 6);
  for (const [, encoded, description, width, height, file] of figures) {
    const svg = Buffer.from(encoded, 'base64').toString('utf8');
    assert.equal(svg, (await readFile(new URL(file, base), 'utf8')).replaceAll('\r\n', '\n'));
    assert.match(description, /^图 [1-6]：/);
    assert.equal(width, '1040');
    assert.ok(Number(height) > 500);
    assert.match(svg, /<title>图 [1-6]：/);
    assert.doesNotMatch(svg, /<(?:script|foreignObject|image|use)\b|\bon\w+\s*=|(?:href|src)\s*=/i);
  }
  assert.doesNotMatch(body, /<img src="assets\//);
});

test('current documents expose the implemented local layer and distinguish blocked upstream integration from historical evidence', async () => {
  const page = await readFile(new URL('../docs/zh/explanation/conversational-settings.html', import.meta.url), 'utf8');
  const data = JSON.parse(page.split('<script type="application/json" id="document-data">')[1].split('</script>')[0]);
  const current = new Map(data.filter(doc => !doc.archived).map(doc => [doc.id, doc]));
  assert.equal(current.get('design').filename, 'conversational-settings.v23.md');
  assert.equal(current.get('development').filename, 'conversational-settings-development.v13.md');
  assert.equal(current.get('status').filename, 'conversational-settings-status.v11.md');
  assert.equal(current.get('research').filename, 'conversational-settings-research.v2.md');
  assert.equal(current.get('design').sections.length, 18);
  assert.equal(current.get('development').sections.length, 22);
  assert.equal(current.get('status').sections.length, 8);
  const implementation = current.get('development').sections.find(section => section.id === 'development-18');
  assert.ok(implementation);
  for (const id of ['DEV-CFG-01', 'DEV-CFG-08', 'DEV-CFG-09', 'DEV-CFG-10']) assert.ok(implementation.searchText.includes(id));
  assert.match(implementation.searchText, /宿主桥接待开发/);
  assert.match(current.get('status').raw, /历史真实模型用例/);
  assert.ok(current.get('status').raw.includes('同一通用 revision/previewDigest 决定服务'));
  assert.match(page, /href="#development-18"/);
  const delivery = current.get('design').sections.find(section => section.id === 'design-16');
  const matrix = current.get('development').sections.find(section => section.id === 'development-20');
  const p1 = current.get('development').sections.find(section => section.id === 'development-19');
  assert.ok(delivery && matrix && p1);
  for (const capability of ['model.select', 'model.connect', 'model.repair_credential', 'connector.enable', 'skill.select', 'thinking.configure', 'review.configure']) {
    assert.ok(matrix.searchText.includes(capability));
  }
  for (const field of ['providerApiKeys', 'runnerExecTimeoutMs', 'allowedDomains', 'privateKeyPath']) assert.ok(matrix.searchText.includes(field));
  for (const id of ['DEV-CFG-11', 'DEV-CFG-12', 'DEV-CFG-13', 'DEV-CFG-14', 'DEV-CFG-15', 'DEV-CFG-16', 'DEV-CFG-17']) assert.ok(p1.searchText.includes(id));
  assert.match(p1.searchText, /任意真实失败自动诊断仍未贯通/);
  assert.match(delivery.searchText, /旧 PubMed 的 30 秒预授权不迁移/);
  assert.match(page, /阅读页 v13/);
  assert.match(page, /href="#design-16"/);
  assert.match(page, /href="#development-20"/);
  assert.equal(data.length, 4);
  assert.equal(data.filter(doc => doc.archived).length, 0);
  assert.match(page, /f58c728b4395ed32f7fa1161636e87c9d6123f5d/);
  assert.ok(current.get('design').sections.find(section => section.id === 'design-17').searchText.includes('CFG-R12'));
  assert.ok(current.get('development').sections.find(section => section.id === 'development-21'));
});
