// Chapters are supplied in document order; closed history is explicitly hidden.
export function currentReadingSection(chapters, { line = 120, atBottom = false } = {}) {
  const visible = chapters.filter(chapter => chapter.visible !== false);
  if (atBottom) return visible.at(-1)?.id ?? null;
  let current = null;
  for (const chapter of visible) {
    if (chapter.top > line) break;
    current = chapter.id;
  }
  return current;
}
