(() => {
  'use strict';
  /* READING_POSITION */
  const documents = JSON.parse(document.getElementById('document-data').textContent);
  const sections = documents.flatMap(doc => doc.sections.map(section => ({ ...section, document: doc })));
  const byId = new Map(sections.map(section => [section.id, section]));
  const byAnchor = new Map(sections.flatMap(section => [
    [section.id, section], ...section.outline.map(heading => [heading.id, section]),
  ]));
  const $ = id => document.getElementById(id);
  const navGroups = $('nav-groups');
  const chapters = sections.map(section => ({
    section,
    element: section.index === 0 ? $(`document-${section.document.id}`) : $(section.id),
  }));
  const searchDialog = $('search-dialog');
  const searchInput = $('search-input');
  let activeSection;
  let readingFrame = 0;
  let toastTimer;

  function notify(message) {
    const toast = $('toast');
    toast.textContent = message;
    toast.hidden = false;
    clearTimeout(toastTimer);
    toastTimer = setTimeout(() => { toast.hidden = true; }, 2600);
  }

  function closeNavigation() {
    document.body.classList.remove('nav-open');
    $('nav-scrim').hidden = true;
    $('menu-button').setAttribute('aria-expanded', 'false');
  }

  function makeNav() {
    for (const doc of documents) {
      const group = document.createElement('details');
      group.className = 'nav-group';
      group.dataset.document = doc.id;
      group.dataset.primary = String(!doc.archived);
      group.open = doc.id === 'design';
      const label = document.createElement('summary');
      label.className = 'nav-group-label';
      const name = document.createElement('span');
      name.textContent = doc.label;
      const count = document.createElement('small');
      count.textContent = `${doc.sections.length - 1} 章`;
      label.append(name, count);
      group.append(label);
      doc.sections.forEach(section => {
        const link = document.createElement('a');
        link.className = 'nav-section-link';
        link.href = `#${section.id}`;
        link.dataset.section = section.id;
        const number = document.createElement('span');
        number.className = 'nav-index';
        number.textContent = section.index === 0 ? '·' : String(section.index).padStart(2, '0');
        const title = document.createElement('span');
        title.className = 'nav-title';
        title.textContent = section.title;
        link.append(number, title);
        group.append(link);
      });
      navGroups.append(group);
    }
  }

  function setActiveNavigation(id) {
    document.querySelectorAll('#document-nav [aria-current]').forEach(link => link.removeAttribute('aria-current'));
    const selected = id ? document.querySelector(`[data-section="${id}"]`) : document.querySelector('.overview-link');
    const activeGroup = selected?.closest('details.nav-group');
    if (activeGroup) {
      if (activeGroup.dataset.primary === 'true') {
        document.querySelectorAll('details.nav-group[data-primary="true"]').forEach(group => {
          group.open = group === activeGroup;
        });
      }
      activeGroup.open = true;
    }
    selected?.setAttribute('aria-current', 'location');
    // Scroll the navigation container only, never the reading surface.
    if (selected && window.innerWidth > 850) {
      const nav = $('document-nav');
      const item = selected.getBoundingClientRect();
      const bounds = nav.getBoundingClientRect();
      if (item.top < bounds.top + 8) nav.scrollTop += item.top - bounds.top - 8;
      else if (item.bottom > bounds.bottom - 8) nav.scrollTop += item.bottom - bounds.bottom + 8;
    }
  }

  function renderOutline(section) {
    const outline = $('article-outline');
    outline.replaceChildren();
    const label = document.createElement('p');
    label.className = 'outline-title';
    label.textContent = '本章目录';
    outline.append(label);
    if (!section.outline.length) {
      const empty = document.createElement('span');
      empty.className = 'outline-empty';
      empty.textContent = '本章无小节';
      outline.append(empty);
      return;
    }
    section.outline.forEach(entry => {
      const link = document.createElement('a');
      link.href = `#${entry.id}`;
      link.textContent = entry.title;
      link.dataset.level = String(entry.level);
      outline.append(link);
    });
  }

  function readHash() {
    try { return decodeURIComponent(location.hash.slice(1)); }
    catch { return ''; }
  }

  function setCurrentSection(section) {
    if (activeSection === section) return;
    activeSection = section;
    $('breadcrumb-current').textContent = section ? `${section.document.label} / ${section.title}` : '总览';
    setActiveNavigation(section?.id);
    $('article-outline').hidden = !section;
    $('download-source').textContent = `下载${section?.document.label ?? documents[0].label} Markdown`;
    if (!section) {
      document.title = '对话内按需配置 · ScienceDiscovery 文档';
      return;
    }
    renderOutline(section);
    document.title = `${section.title} · 对话内按需配置`;
  }

  function renderRoute() {
    const hash = readHash();
    const section = byAnchor.get(hash);
    if (hash !== 'overview' && !section) return;
    closeNavigation();
    if (!section) {
      window.scrollTo({ top: 0, behavior: 'instant' });
      setCurrentSection(null);
      requestReadingUpdate();
      return;
    }
    const target = hash === section.id && section.index === 0
      ? $(`document-${section.document.id}`) : $(hash) ?? $(section.id);
    const historyDocument = target.closest('details.archive-document');
    if (historyDocument) historyDocument.open = true;
    setCurrentSection(section);
    requestAnimationFrame(() => {
      target.scrollIntoView({ block: 'start', behavior: 'instant' });
      requestReadingUpdate();
    });
  }

  function updateReadingPosition() {
    const positions = chapters.map(({ section, element }) => ({
      id: section.id,
      top: element.getBoundingClientRect().top,
      visible: element.getClientRects().length > 0,
    }));
    const id = currentReadingSection(positions, {
      line: document.querySelector('.topbar').getBoundingClientRect().height + 40,
      atBottom: window.scrollY > 0 && window.scrollY + window.innerHeight >= document.documentElement.scrollHeight - 4,
    });
    const previous = activeSection?.id;
    const section = byId.get(id) ?? null;
    setCurrentSection(section);
    if (previous !== section?.id) {
      const hash = readHash();
      if (!(section && byAnchor.get(hash)?.id === section.id)) {
        try { history.replaceState(null, '', `#${id ?? 'overview'}`); }
        catch { /* Some local-file environments disallow history changes. */ }
      }
    }
    updateProgress();
  }

  function requestReadingUpdate() {
    if (readingFrame) return;
    readingFrame = requestAnimationFrame(() => {
      readingFrame = 0;
      updateReadingPosition();
    });
  }

  function updateProgress() {
    const height = document.documentElement.scrollHeight - window.innerHeight;
    const value = height > 0 ? Math.min(100, Math.max(0, window.scrollY / height * 100)) : 0;
    document.querySelector('.topbar').style.setProperty('--read-progress', `${value}%`);
  }

  function openSearch(initial = '') {
    searchInput.value = initial;
    renderSearch();
    if (!searchDialog.open) searchDialog.showModal();
    searchInput.focus();
    searchInput.select();
  }

  function addHighlightedText(container, value, query) {
    const index = value.toLocaleLowerCase().indexOf(query);
    if (index < 0) { container.textContent = value; return; }
    container.append(document.createTextNode(value.slice(0, index)));
    const mark = document.createElement('mark');
    mark.textContent = value.slice(index, index + query.length);
    container.append(mark, document.createTextNode(value.slice(index + query.length)));
  }

  function renderSearch() {
    const query = searchInput.value.trim().toLocaleLowerCase();
    const results = $('search-results');
    results.replaceChildren();
    const items = query
      ? sections.filter(section => `${section.originalTitle}\n${section.searchText}`.toLocaleLowerCase().includes(query))
          .sort((a, b) => Number(b.title.toLocaleLowerCase().includes(query)) - Number(a.title.toLocaleLowerCase().includes(query)))
      : sections.filter(section => ['design-03', 'design-08', 'development-06', 'development-09', 'status-06'].includes(section.id));
    $('search-summary').textContent = query ? `找到 ${items.length} 个相关章节` : '常用章节';
    for (const section of items) {
      const button = document.createElement('button');
      button.type = 'button';
      button.className = 'search-result';
      const meta = document.createElement('small');
      meta.textContent = `${section.document.archived ? '历史 · ' : ''}${section.document.label} · ${section.index === 0 ? '说明' : `第 ${section.index} 章`}`;
      const title = document.createElement('b');
      addHighlightedText(title, section.title, query);
      const snippet = document.createElement('span');
      const source = section.searchText.replace(/[#*`|_>\[\]]/g, ' ').replace(/\s+/g, ' ').trim();
      const match = query ? source.toLocaleLowerCase().indexOf(query) : -1;
      const start = match < 0 ? 0 : Math.max(0, match - 42);
      const excerpt = `${start ? '…' : ''}${source.slice(start, start + 130)}${source.length > start + 130 ? '…' : ''}`;
      addHighlightedText(snippet, excerpt, query);
      button.append(meta, title, snippet);
      button.addEventListener('click', () => {
        searchDialog.close();
        if (location.hash === `#${section.id}`) renderRoute();
        else location.hash = section.id;
      });
      results.append(button);
    }
    if (!items.length) {
      const empty = document.createElement('p');
      empty.className = 'search-empty';
      empty.textContent = '没有匹配的章节，试试较短的关键词。';
      results.append(empty);
    }
  }

  async function copyText(value) {
    try { await navigator.clipboard.writeText(value); return true; } catch { /* local files can deny clipboard access */ }
    const textarea = document.createElement('textarea');
    textarea.value = value;
    textarea.style.cssText = 'position:fixed;left:-10000px;top:0';
    document.body.append(textarea);
    textarea.select();
    let copied = false;
    try { copied = document.execCommand('copy'); } catch { /* manual copy dialog below */ }
    textarea.remove();
    return copied;
  }

  function downloadMarkdown() {
    const source = activeSection?.document ?? documents[0];
    const url = URL.createObjectURL(new Blob([source.raw], { type: 'text/markdown;charset=utf-8' }));
    const link = document.createElement('a');
    link.href = url;
    link.download = source.filename;
    document.body.append(link);
    link.click();
    link.remove();
    setTimeout(() => URL.revokeObjectURL(url), 1000);
  }

  function preparePrint() {
    const target = $('print-content');
    if (target.childElementCount) return;
    for (const source of documents.filter(source => !source.archived)) {
      const documentBlock = document.createElement('div');
      documentBlock.className = 'print-document';
      const title = document.createElement('h1');
      title.className = 'print-title';
      title.textContent = `ScienceDiscovery · ${source.label}`;
      const subtitle = document.createElement('p');
      subtitle.className = 'print-subtitle';
      subtitle.textContent = `对话内按需配置 · ${source.version} · ${source.note}`;
      documentBlock.append(title, subtitle);
      for (const section of source.sections) {
        const chapter = document.createElement('section');
        chapter.className = 'print-section';
        const heading = document.createElement('h2');
        heading.textContent = section.originalTitle;
        const article = document.createElement('div');
        article.className = 'prose';
        article.innerHTML = section.html;
        chapter.append(heading, article);
        documentBlock.append(chapter);
      }
      target.append(documentBlock);
    }
  }

  makeNav();
  renderRoute();
  requestReadingUpdate();
  window.addEventListener('hashchange', renderRoute);
  window.addEventListener('scroll', requestReadingUpdate, { passive: true });
  window.addEventListener('resize', requestReadingUpdate);
  window.addEventListener('load', requestReadingUpdate);
  $('article-content').addEventListener('toggle', requestReadingUpdate, true);
  document.addEventListener('click', event => {
    const link = event.target.closest('a[href^="#"]');
    if (!link || event.defaultPrevented || event.ctrlKey || event.metaKey || event.shiftKey || event.altKey) return;
    const href = link.getAttribute('href');
    let hash;
    try { hash = decodeURIComponent(href.slice(1)); } catch { return; }
    if (hash !== 'overview' && !byAnchor.has(hash)) return;
    event.preventDefault();
    if (location.hash === href) renderRoute();
    else location.hash = href;
  });
  $('search-launch').addEventListener('click', () => openSearch());
  $('close-search').addEventListener('click', () => searchDialog.close());
  searchInput.addEventListener('input', renderSearch);
  searchDialog.addEventListener('click', event => { if (event.target === searchDialog) searchDialog.close(); });
  document.addEventListener('keydown', event => {
    const editing = /INPUT|TEXTAREA/.test(document.activeElement?.tagName || '') || document.activeElement?.isContentEditable;
    if ((event.key === '/' && !editing) || ((event.ctrlKey || event.metaKey) && event.key.toLowerCase() === 'k')) {
      event.preventDefault();
      openSearch();
    }
  });
  $('menu-button').addEventListener('click', () => {
    const open = document.body.classList.toggle('nav-open');
    $('nav-scrim').hidden = !open;
    $('menu-button').setAttribute('aria-expanded', String(open));
  });
  $('nav-scrim').addEventListener('click', closeNavigation);
  $('document-nav').addEventListener('click', event => {
    if (event.target.closest('a') && window.innerWidth <= 850) closeNavigation();
  });
  $('article-content').addEventListener('click', async event => {
    const button = event.target.closest('.copy-code');
    if (!button) return;
    const code = button.closest('.code-block')?.querySelector('code');
    if (code && await copyText(code.textContent)) notify('代码已复制');
    else notify('浏览器未允许复制，请选中代码手动复制');
  });
  $('copy-link').addEventListener('click', async () => {
    const url = new URL(location.href);
    url.hash = activeSection?.id ?? 'overview';
    const link = url.href;
    if (await copyText(link)) notify('章节链接已复制');
    else {
      $('link-value').value = link;
      $('link-dialog').showModal();
      $('link-value').select();
    }
  });
  $('close-link').addEventListener('click', () => $('link-dialog').close());
  $('download-source').addEventListener('click', downloadMarkdown);
  $('print-button').addEventListener('click', () => { preparePrint(); window.print(); });
  window.addEventListener('beforeprint', preparePrint);
  const themeButton = $('theme-toggle');
  let theme = 'light';
  try { theme = localStorage.getItem('conversational-settings-theme') || (matchMedia('(prefers-color-scheme: dark)').matches ? 'dark' : 'light'); } catch { /* file access can deny storage */ }
  function applyTheme() {
    document.documentElement.dataset.theme = theme;
    themeButton.setAttribute('aria-label', `切换到${theme === 'dark' ? '浅色' : '深色'}主题`);
  }
  applyTheme();
  themeButton.addEventListener('click', () => {
    theme = theme === 'dark' ? 'light' : 'dark';
    applyTheme();
    try { localStorage.setItem('conversational-settings-theme', theme); } catch { /* file access can deny storage */ }
  });
  let demoDone = false;
  $('demo-button').addEventListener('click', () => {
    demoDone = !demoDone;
    $('demo-request').hidden = demoDone;
    $('demo-receipt').hidden = !demoDone;
    $('demo-receipt').querySelector('details').open = false;
    $('demo-agent-text').textContent = demoDone ? '已启用，原任务从检查点继续。' : '需要启用 PubMed，原任务已保存进度。';
    $('demo-runtime-state').textContent = demoDone ? '配置已应用 · 等待工具权限或继续任务' : '等待配置确认';
    $('demo-step').textContent = demoDone ? '02 / 02' : '01 / 02';
    $('demo-button').firstChild.textContent = demoDone ? '重新演示 ' : '确认启用（演示） ';
  });
})();
