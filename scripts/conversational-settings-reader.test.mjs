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
