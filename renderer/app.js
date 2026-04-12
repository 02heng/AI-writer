async function apiBase() {
  if (!window.aiWriter?.getBackendUrl) {
    throw new Error('Electron preload 未就绪');
  }
  return window.aiWriter.getBackendUrl();
}

/** 拼接后端根地址与 /api/... 路径，避免 base 误带 /api 或重复斜杠。 */
function joinBackendUrl(base, path) {
  const b = String(base ?? '')
    .trim()
    .replace(/\/+$/, '');
  const p = path.startsWith('/') ? path : `/${path}`;
  const root = b.replace(/\/api\/?$/i, '');
  return root + p;
}

function showAppDialog({ title, body, confirmMode = false, dangerConfirm = false }) {
  return new Promise((resolve) => {
    const shell = document.getElementById('app-dialog');
    const titleEl = document.getElementById('app-dialog-title');
    const bodyEl = document.getElementById('app-dialog-body');
    const okBtn = document.getElementById('app-dialog-ok');
    const cancelBtn = document.getElementById('app-dialog-cancel');
    const backdrop = document.getElementById('app-dialog-backdrop');
    if (!shell || !titleEl || !bodyEl || !okBtn || !cancelBtn) {
      if (confirmMode) resolve(window.confirm(String(body)));
      else {
        window.alert(String(body));
        resolve(undefined);
      }
      return;
    }
    const finish = (v) => {
      okBtn.onclick = null;
      cancelBtn.onclick = null;
      if (backdrop) backdrop.onclick = null;
      shell.hidden = true;
      shell.setAttribute('aria-hidden', 'true');
      resolve(v);
    };
    titleEl.textContent = title || (confirmMode ? '确认' : '提示');
    bodyEl.textContent = body;
    cancelBtn.hidden = !confirmMode;
    okBtn.textContent = confirmMode ? '确定' : '知道了';
    okBtn.className =
      confirmMode && dangerConfirm ? 'btn btn-primary reader-danger' : 'btn btn-primary';
    shell.hidden = false;
    shell.setAttribute('aria-hidden', 'false');
    okBtn.onclick = () => finish(confirmMode ? true : undefined);
    cancelBtn.onclick = () => finish(false);
    if (backdrop) {
      backdrop.onclick = () => finish(confirmMode ? false : undefined);
    }
    okBtn.focus();
  });
}

function showAppAlert(message, title) {
  return showAppDialog({ title: title || '提示', body: message, confirmMode: false });
}

function showAppConfirm(message, title, dangerConfirm = false) {
  return showAppDialog({
    title: title || '确认',
    body: message,
    confirmMode: true,
    dangerConfirm
  });
}

/** 替代 window.prompt，返回输入文本或 null（取消）。 */
function showAppPrompt(message, defaultValue = '', title = '输入', fieldLabel = '') {
  return new Promise((resolve) => {
    const shell = document.getElementById('app-prompt');
    const titleEl = document.getElementById('app-prompt-title');
    const hintEl = document.getElementById('app-prompt-hint');
    const labelEl = document.getElementById('app-prompt-label');
    const input = document.getElementById('app-prompt-input');
    const okBtn = document.getElementById('app-prompt-ok');
    const cancelBtn = document.getElementById('app-prompt-cancel');
    const backdrop = document.getElementById('app-prompt-backdrop');
    if (!shell || !input || !okBtn || !cancelBtn) {
      resolve(window.prompt(message, defaultValue));
      return;
    }
    const finish = (v) => {
      okBtn.onclick = null;
      cancelBtn.onclick = null;
      input.onkeydown = null;
      if (backdrop) backdrop.onclick = null;
      shell.hidden = true;
      shell.setAttribute('aria-hidden', 'true');
      resolve(v);
    };
    titleEl.textContent = title;
    hintEl.textContent = message || '';
    labelEl.textContent = fieldLabel || '输入';
    input.value = defaultValue ?? '';
    shell.hidden = false;
    shell.setAttribute('aria-hidden', 'false');
    okBtn.onclick = () => finish(input.value);
    cancelBtn.onclick = () => finish(null);
    if (backdrop) backdrop.onclick = () => finish(null);
    input.onkeydown = (ev) => {
      if (ev.key === 'Enter') {
        ev.preventDefault();
        finish(input.value);
      }
    };
    setTimeout(() => input.focus(), 30);
  });
}

async function fetchJson(path, options = {}) {
  const base = await apiBase();
  const res = await fetch(joinBackendUrl(base, path), {
    headers: { 'Content-Type': 'application/json', ...options.headers },
    ...options
  });
  const text = await res.text();
  let data = null;
  try {
    data = text ? JSON.parse(text) : null;
  } catch {
    data = text;
  }
  if (!res.ok) {
    const msg = typeof data === 'object' && data?.detail ? JSON.stringify(data.detail) : text;
    throw new Error(msg || res.statusText);
  }
  return data;
}

function escapeHtml(s) {
  return String(s)
    .replace(/&/g, '&amp;')
    .replace(/</g, '&lt;')
    .replace(/>/g, '&gt;')
    .replace(/"/g, '&quot;');
}

/** 后端未就绪或 themes.json 不可读时的兜底（与后端 _MINIMAL_THEMES 对齐思路） */
const FALLBACK_THEMES = [
  { id: 'general', label: '通用 / 不限定', description: '不额外强调题材。', system_addon: '' },
  { id: 'realism', label: '现实主义', description: '真实感社会背景。', system_addon: '' },
  { id: 'fantasy', label: '魔幻 / 西幻', description: '魔法与冒险。', system_addon: '' },
  { id: 'scifi', label: '科幻', description: '技术与社会推演。', system_addon: '' },
  { id: 'xianxia', label: '仙侠 / 修真', description: '东方修炼体系。', system_addon: '' },
  { id: 'horror', label: '悬疑 / 惊悚', description: '悬念与氛围。', system_addon: '' }
];

function selectedKbFiles() {
  return Array.from(document.querySelectorAll('.kb-cb:checked')).map((el) => el.value);
}

let libraryActiveName = '';
let readerBookId = '';
let readerChapterN = 0;
let readerToc = [];
let readerChapterNs = [];
let readerTocTotal = 0;
const CHAPTER_TOC_PAGE = 160;
const BOOKS_PAGE = 120;
const TRASH_PAGE = 120;
let booksLoaded = [];
let booksListMeta = { total: 0 };
let trashLoaded = [];
let trashListMeta = { total: 0 };

const THEME_KEY = 'aiw-ui-theme';

function initUiTheme() {
  const sel = document.getElementById('ui-theme');
  const saved = localStorage.getItem(THEME_KEY) || 'ember';
  document.documentElement.dataset.theme = saved;
  if (sel) sel.value = saved;
  sel?.addEventListener('change', () => {
    const v = sel.value || 'ember';
    document.documentElement.dataset.theme = v;
    localStorage.setItem(THEME_KEY, v);
  });
}

function formatFileMeta(mtime, size) {
  const d = new Date(mtime * 1000);
  const ds = d.toLocaleString();
  const kb = size < 1024 ? `${size} B` : `${(size / 1024).toFixed(1)} KB`;
  return `${ds} · ${kb}`;
}

function memoryBookId() {
  return document.getElementById('mem-scope-book')?.value?.trim() || '';
}

function renderReaderMarkdown(content) {
  const contentEl = document.getElementById('reader-content');
  if (!contentEl) return;
  contentEl.innerHTML = '';
  if (typeof window.renderMarkdownLite === 'function') {
    contentEl.appendChild(window.renderMarkdownLite(content));
  } else {
    const pre = document.createElement('pre');
    pre.className = 'reader-raw';
    pre.textContent = content;
    contentEl.appendChild(pre);
  }
}

function chapterNavNumbers() {
  if (readerChapterNs.length) return readerChapterNs.slice().sort((a, b) => a - b);
  return readerToc.map((x) => x.n).sort((a, b) => a - b);
}

function updateReaderNav() {
  const nav = document.getElementById('reader-chapter-nav');
  if (!nav) return;
  const hasBook = Boolean(readerBookId);
  const nums = chapterNavNumbers();
  const hasCh = readerChapterN > 0 && nums.length > 0;
  nav.hidden = !(hasBook && hasCh);
  const prev = document.getElementById('reader-prev-ch');
  const next = document.getElementById('reader-next-ch');
  const minN = nums.length ? nums[0] : 0;
  const maxN = nums.length ? nums[nums.length - 1] : 0;
  if (prev) prev.disabled = !hasCh || readerChapterN <= minN;
  if (next) next.disabled = !hasCh || readerChapterN >= maxN;
  const exp = document.getElementById('reader-export-txt');
  const del = document.getElementById('reader-delete-book');
  const showActs = Boolean(readerBookId) && nums.length > 0;
  if (exp) exp.hidden = !showActs;
  if (del) del.hidden = !showActs;
}

function scrollReaderToTop() {
  const el = document.getElementById('reader-content');
  if (el) el.scrollTop = 0;
}

function formatEta(ms) {
  if (ms == null || !Number.isFinite(ms) || ms < 0) return '';
  const s = Math.ceil(ms / 1000);
  if (s < 60) return `约 ${s} 秒`;
  const m = Math.floor(s / 60);
  const r = s % 60;
  return `约 ${m} 分 ${r} 秒`;
}

async function openBookChapter(bookId, chapterN) {
  const titleEl = document.getElementById('reader-doc-title');
  const metaEl = document.getElementById('reader-meta');
  const hintEl = document.getElementById('reader-book-hint');
  readerBookId = bookId;
  readerChapterN = chapterN;
  document.querySelectorAll('.library-item--chapter').forEach((el) => {
    el.classList.toggle('library-item--active', el.dataset.chapter === String(chapterN));
  });
  titleEl.textContent = `第 ${chapterN} 章`;
  metaEl.textContent = '加载中…';
  if (hintEl) hintEl.textContent = `书本 ID：${bookId}`;
  try {
    const data = await fetchJson(`/api/books/${encodeURIComponent(bookId)}/chapters/${chapterN}`);
    const sub = (data.title && String(data.title).trim()) || '';
    titleEl.textContent = sub ? `第 ${chapterN} 章 · ${sub}` : `第 ${chapterN} 章`;
    metaEl.textContent = `${data.content.length} 字`;
    renderReaderMarkdown(data.content);
    updateReaderNav();
    scrollReaderToTop();
  } catch (e) {
    metaEl.textContent = '';
    titleEl.textContent = '读取失败';
    document.getElementById('reader-content').innerHTML = `<p class="rail-hint">${escapeHtml(e.message)}</p>`;
  }
}

function renderChapterList(bookId) {
  const chBox = document.getElementById('chapter-list');
  const metaEl = document.getElementById('chapter-list-meta');
  const filterEl = document.getElementById('chapter-list-filter');
  if (!chBox) return;
  const q = (filterEl?.value || '').trim().toLowerCase();
  let rows = readerToc;
  if (q) {
    rows = readerToc.filter((row) => {
      const t = String(row.title || '').toLowerCase();
      return String(row.n).includes(q) || t.includes(q);
    });
  }
  chBox.innerHTML = '';
  if (!rows.length) {
    chBox.innerHTML =
      '<p class="rail-hint">无匹配章节。可调整筛选词，或先「加载更多章节」再筛。</p>';
  } else {
    for (const row of rows) {
      const btn = document.createElement('button');
      btn.type = 'button';
      btn.className = 'library-item library-item--chapter';
      btn.dataset.chapter = String(row.n);
      const t = row.title ? String(row.title) : '';
      btn.textContent = t ? `第 ${row.n} 章 · ${t}` : `第 ${row.n} 章`;
      btn.addEventListener('click', () => openBookChapter(bookId, row.n));
      chBox.appendChild(btn);
    }
  }
  if (metaEl) {
    metaEl.textContent = `全书 ${readerTocTotal} 章 · 已加载目录 ${readerToc.length} 条 · 当前显示 ${rows.length} 条`;
  }
}

async function loadMoreChapters() {
  const bookId = readerBookId;
  if (!bookId) return;
  const chMore = document.getElementById('chapter-list-more');
  try {
    const more = await fetchJson(
      `/api/books/${encodeURIComponent(bookId)}/toc?limit=${CHAPTER_TOC_PAGE}&offset=${readerToc.length}`
    );
    readerToc = readerToc.concat(more.toc || []);
    renderChapterList(bookId);
    if (chMore) chMore.hidden = readerToc.length >= readerTocTotal;
  } catch (e) {
    void showAppAlert(e.message || String(e));
  }
}

async function selectReaderBook(bookId, titleLabel) {
  readerBookId = bookId;
  readerChapterN = 0;
  readerToc = [];
  readerChapterNs = [];
  readerTocTotal = 0;
  const chBox = document.getElementById('chapter-list');
  const hintEl = document.getElementById('reader-book-hint');
  const chFilter = document.getElementById('chapter-list-filter');
  const chMore = document.getElementById('chapter-list-more');
  if (chFilter) {
    chFilter.disabled = false;
    chFilter.value = '';
  }
  if (hintEl) hintEl.textContent = titleLabel || bookId;
  document.querySelectorAll('.library-item--book').forEach((el) => {
    el.classList.toggle('library-item--active', el.dataset.book === bookId);
  });
  if (!chBox) return;
  chBox.innerHTML = '加载目录…';
  if (chMore) chMore.hidden = true;
  try {
    readerChapterNs = [];
    try {
      const nsData = await fetchJson(`/api/books/${encodeURIComponent(bookId)}/chapter-ns`);
      readerChapterNs = (nsData.ns || []).slice().sort((a, b) => a - b);
    } catch {
      /* 旧版后端无 /chapter-ns，下面从 toc 推导章节序号 */
    }
    const t0 = await fetchJson(
      `/api/books/${encodeURIComponent(bookId)}/toc?limit=${CHAPTER_TOC_PAGE}&offset=0`
    );
    readerToc = t0.toc || [];
    readerTocTotal = typeof t0.total === 'number' ? t0.total : readerToc.length;
    if (!readerChapterNs.length && readerToc.length) {
      readerChapterNs = [...new Set(readerToc.map((r) => r.n))].sort((a, b) => a - b);
    }
    renderChapterList(bookId);
    if (chMore) chMore.hidden = readerToc.length >= readerTocTotal;
    if (!readerChapterNs.length) {
      chBox.innerHTML = '<p class="rail-hint">本书尚无章节文件。</p>';
      if (chFilter) chFilter.disabled = true;
      if (document.getElementById('chapter-list-meta')) {
        document.getElementById('chapter-list-meta').textContent = '';
      }
      updateReaderNav();
      return;
    }
    const firstN = readerChapterNs[0];
    await openBookChapter(bookId, firstN);
  } catch (e) {
    chBox.innerHTML = `<p class="rail-hint">${escapeHtml(e.message)}</p>`;
    if (chFilter) chFilter.disabled = true;
  }
}

async function refreshReaderBooks(reset = true) {
  const box = document.getElementById('book-list');
  const metaEl = document.getElementById('book-list-meta');
  const moreBtn = document.getElementById('book-list-more');
  if (!box) return;
  if (reset) {
    booksLoaded = [];
    box.innerHTML = '加载中…';
  }
  try {
    const offset = reset ? 0 : booksLoaded.length;
    const q = (document.getElementById('book-list-search')?.value || '').trim();
    const data = await fetchJson(
      `/api/books?limit=${BOOKS_PAGE}&offset=${offset}&q=${encodeURIComponent(q)}`
    );
    const chunk = data.books || [];
    if (data.total != null && Number.isFinite(Number(data.total))) {
      booksListMeta.total = Number(data.total);
    } else if (reset) {
      booksListMeta.total = chunk.length;
    } else {
      booksListMeta.total = Math.max(booksListMeta.total, booksLoaded.length + chunk.length);
    }
    if (reset) booksLoaded = chunk.slice();
    else booksLoaded = booksLoaded.concat(chunk);
    if (!booksLoaded.length) {
      box.innerHTML =
        '<p class="rail-hint">暂无匹配书本。可清空搜索或使用「生成全书并入库」创建。</p>';
    } else {
      box.innerHTML = '';
      for (const b of booksLoaded) {
        const btn = document.createElement('button');
        btn.type = 'button';
        btn.className = 'library-item library-item--book';
        btn.dataset.book = b.id;
        btn.innerHTML = `<span>${escapeHtml(b.title || b.id)}</span><small>${b.chapter_count || 0} 章 · ${escapeHtml(b.id)}</small>`;
        btn.addEventListener('click', () => selectReaderBook(b.id, b.title || b.id));
        box.appendChild(btn);
      }
    }
    if (metaEl) metaEl.textContent = `共 ${booksListMeta.total} 本 · 已显示 ${booksLoaded.length}`;
    if (moreBtn) moreBtn.hidden = booksLoaded.length >= booksListMeta.total;
  } catch (e) {
    box.innerHTML = `<p class="rail-hint">无法加载书本：${escapeHtml(e.message)}</p>`;
    if (moreBtn) moreBtn.hidden = true;
  }
}

async function refreshLegacyFileList() {
  const box = document.getElementById('legacy-file-list');
  if (!box) return;
  box.innerHTML = '加载中…';
  try {
    const { files } = await fetchJson('/api/library/files');
    if (!files.length) {
      box.innerHTML = '<p class="rail-hint">无 flat 文件。</p>';
      return;
    }
    box.innerHTML = '';
    for (const f of files) {
      const btn = document.createElement('button');
      btn.type = 'button';
      btn.className = 'library-item';
      if (f.name === libraryActiveName) btn.classList.add('library-item--active');
      btn.dataset.name = f.name;
      btn.innerHTML = `<span>${escapeHtml(f.name)}</span><small>${formatFileMeta(f.mtime, f.size)}</small>`;
      btn.addEventListener('click', () => openLegacyLibraryFile(f.name));
      box.appendChild(btn);
    }
  } catch (e) {
    box.innerHTML = `<p class="rail-hint">${escapeHtml(e.message)}</p>`;
  }
}

async function openLegacyLibraryFile(name) {
  const titleEl = document.getElementById('reader-doc-title');
  const metaEl = document.getElementById('reader-meta');
  const contentEl = document.getElementById('reader-content');
  if (!contentEl) return;
  libraryActiveName = name;
  readerBookId = '';
  readerChapterN = 0;
  readerToc = [];
  readerChapterNs = [];
  readerTocTotal = 0;
  document.getElementById('reader-chapter-nav').hidden = true;
  const exp = document.getElementById('reader-export-txt');
  const del = document.getElementById('reader-delete-book');
  if (exp) exp.hidden = true;
  if (del) del.hidden = true;
  document.querySelectorAll('#legacy-file-list .library-item').forEach((el) => {
    el.classList.toggle('library-item--active', el.dataset.name === name);
  });
  titleEl.textContent = name;
  metaEl.textContent = '加载中…';
  contentEl.innerHTML = '';
  try {
    // #region agent log
    fetch('http://127.0.0.1:7358/ingest/ec74e965-0955-4757-aff0-bed113fed1c4', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json', 'X-Debug-Session-Id': 'd7648d' },
      body: JSON.stringify({
        sessionId: 'd7648d',
        hypothesisId: 'H5',
        location: 'app.js:openLegacyLibraryFile',
        message: 'library read',
        data: { name },
        timestamp: Date.now()
      })
    }).catch(() => {});
    // #endregion
    const data = await fetchJson(`/api/library/read?name=${encodeURIComponent(name)}`);
    metaEl.textContent = `${data.content.length} 字`;
    renderReaderMarkdown(data.content);
  } catch (e) {
    metaEl.textContent = '';
    titleEl.textContent = '读取失败';
    contentEl.innerHTML = `<p class="rail-hint">${escapeHtml(e.message)}</p>`;
  }
}

function appendTrashItemRow(box, it) {
  const wrap = document.createElement('div');
  wrap.className = 'trash-item';
  const label = document.createElement('div');
  label.className = 'rail-hint';
  label.style.marginBottom = '0.35rem';
  label.textContent = `${it.title || it.id} · ${it.folder || it.id}`;
  const row = document.createElement('div');
  row.style.display = 'flex';
  row.style.flexWrap = 'wrap';
  row.style.gap = '0.35rem';
  const br = document.createElement('button');
  br.type = 'button';
  br.className = 'btn btn-secondary';
  br.textContent = '还原';
  br.addEventListener('click', async () => {
    if (!(await showAppConfirm('还原此书到书库？', '还原书本'))) return;
    try {
      await fetchJson('/api/trash/books/restore', {
        method: 'POST',
        body: JSON.stringify({ folder: it.folder || it.id })
      });
      await refreshReaderShell();
    } catch (e) {
      void showAppAlert(e.message || String(e));
    }
  });
  const bp = document.createElement('button');
  bp.type = 'button';
  bp.className = 'btn btn-ghost reader-danger';
  bp.textContent = '永久删除';
  bp.addEventListener('click', async () => {
    if (!(await showAppConfirm('永久删除？不可恢复。', '危险操作', true))) return;
    try {
      await fetchJson('/api/trash/books/purge', {
        method: 'POST',
        body: JSON.stringify({ folder: it.folder || it.id })
      });
      await refreshTrashList(true);
      await refreshReaderBooks(true);
    } catch (e) {
      void showAppAlert(e.message || String(e));
    }
  });
  row.appendChild(br);
  row.appendChild(bp);
  wrap.appendChild(label);
  wrap.appendChild(row);
  box.appendChild(wrap);
}

async function refreshTrashList(reset = true) {
  const box = document.getElementById('trash-list');
  const metaEl = document.getElementById('trash-list-meta');
  const moreBtn = document.getElementById('trash-list-more');
  if (!box) return;
  if (reset) {
    trashLoaded = [];
    box.innerHTML = '加载中…';
  }
  try {
    const offset = reset ? 0 : trashLoaded.length;
    const q = (document.getElementById('trash-list-search')?.value || '').trim();
    const data = await fetchJson(
      `/api/trash/books?limit=${TRASH_PAGE}&offset=${offset}&q=${encodeURIComponent(q)}`
    );
    const chunk = data.items || [];
    if (data.total != null && Number.isFinite(Number(data.total))) {
      trashListMeta.total = Number(data.total);
    } else if (reset) {
      trashListMeta.total = chunk.length;
    } else {
      trashListMeta.total = Math.max(trashListMeta.total, trashLoaded.length + chunk.length);
    }
    if (reset) trashLoaded = chunk.slice();
    else trashLoaded = trashLoaded.concat(chunk);
    if (!trashLoaded.length) {
      box.innerHTML = '<p class="rail-hint">回收站为空或无匹配项。</p>';
    } else {
      box.innerHTML = '';
      for (const it of trashLoaded) appendTrashItemRow(box, it);
    }
    if (metaEl) metaEl.textContent = `共 ${trashListMeta.total} 项 · 已显示 ${trashLoaded.length}`;
    if (moreBtn) moreBtn.hidden = trashLoaded.length >= trashListMeta.total;
  } catch (e) {
    box.innerHTML = `<p class="rail-hint">${escapeHtml(e.message)}</p>`;
    if (moreBtn) moreBtn.hidden = true;
  }
}

async function refreshReaderShell() {
  await refreshReaderBooks();
  await refreshTrashList();
  await refreshMemBookOptions();
  await refreshLegacyFileList();
}

async function refreshMemBookOptions() {
  const sel = document.getElementById('mem-scope-book');
  if (!sel) return;
  const cur = sel.value;
  sel.innerHTML = '<option value="">全局记忆宫殿（跨书共享）</option>';
  try {
    let offset = 0;
    const lim = 400;
    let total = Infinity;
    while (offset < total) {
      const data = await fetchJson(`/api/books?limit=${lim}&offset=${offset}&q=`);
      total = data.total ?? 0;
      const chunk = data.books || [];
      for (const b of chunk) {
        const o = document.createElement('option');
        o.value = b.id;
        o.textContent = `${b.title || b.id} · ${b.chapter_count || 0} 章`;
        sel.appendChild(o);
      }
      offset += chunk.length;
      if (!chunk.length) break;
    }
  } catch (e) {
    console.warn('books for memory', e);
  }
  if (cur && Array.from(sel.options).some((op) => op.value === cur)) sel.value = cur;
}

function initTabs() {
  const tabs = document.querySelectorAll('.view-tab');
  const writePanel = document.getElementById('panel-write');
  const readPanel = document.getElementById('panel-read');
  tabs.forEach((tab) => {
    tab.addEventListener('click', () => {
      const panelId = tab.dataset.panel;
      tabs.forEach((t) => {
        const on = t.dataset.panel === panelId;
        t.classList.toggle('is-active', on);
        t.setAttribute('aria-selected', on ? 'true' : 'false');
      });
      writePanel?.classList.toggle('is-active', panelId === 'write');
      readPanel?.classList.toggle('is-active', panelId === 'read');
      if (panelId === 'read') {
        refreshReaderShell();
      }
    });
  });
}

async function refreshSeriesList() {
  const sel = document.getElementById('series-continue-select');
  if (!sel) return;
  const cur = sel.value;
  sel.innerHTML = '';
  const ph = document.createElement('option');
  ph.value = '';
  ph.textContent = '—— 选择书本或旧书系 ——';
  sel.appendChild(ph);
  try {
    let offset = 0;
    const lim = 400;
    let total = Infinity;
    const acc = [];
    while (offset < total) {
      const data = await fetchJson(`/api/books?limit=${lim}&offset=${offset}&q=`);
      total = data.total ?? 0;
      const chunk = data.books || [];
      acc.push(...chunk);
      offset += chunk.length;
      if (!chunk.length) break;
    }
    if (acc.length) {
      const og = document.createElement('optgroup');
      og.label = '书本（books/）';
      for (const b of acc) {
        const o = document.createElement('option');
        o.value = `book:${b.id}`;
        o.textContent = `${b.title || b.id} · ${b.chapter_count || 0} 章`;
        og.appendChild(o);
      }
      sel.appendChild(og);
    }
  } catch (e) {
    console.warn('books list', e);
  }
  try {
    const { series } = await fetchJson('/api/library/series');
    if (series?.length) {
      const og = document.createElement('optgroup');
      og.label = '旧库（out/ 前缀）';
      for (const s of series) {
        const o = document.createElement('option');
        o.value = `legacy:${s.prefix}`;
        o.textContent = `${s.prefix} · ${s.chapter_count} 章 · 末章 ${s.last_index}`;
        og.appendChild(o);
      }
      sel.appendChild(og);
    }
  } catch (e) {
    console.warn('series list', e);
  }
  if (cur && Array.from(sel.options).some((op) => op.value === cur)) {
    sel.value = cur;
  }
}

async function refreshHealth() {
  const el = document.getElementById('backend-status');
  if (!el) return null;
  try {
    const h = await fetchJson('/api/health');
    const ds = h.deepseek_configured ? '已配置 Key' : '未配置 Key';
    el.textContent = `后端正常 · ${ds}`;
    el.className = 'status-pill is-ok';
    const pathsEl = document.getElementById('paths-display');
    if (pathsEl && h.books_root) {
      const base = (pathsEl.dataset.basePaths || pathsEl.textContent || '').split('\n\n书本目录')[0].trim();
      pathsEl.dataset.basePaths = base;
      pathsEl.textContent = `${base}\n\n书本目录：\n${h.books_root}`;
    }
    return h;
  } catch (e) {
    el.textContent = `后端不可用 · ${e.message}`;
    el.className = 'status-pill is-bad';
    return null;
  }
}

async function refreshKbList() {
  const box = document.getElementById('kb-list');
  box.innerHTML = '';
  try {
    const { files } = await fetchJson('/api/kb');
    if (!files.length) {
      box.innerHTML = '<p class="rail-hint">暂无 .md，首次运行后会在 UserData/kb 生成示例文件。</p>';
      return;
    }
    for (const f of files) {
      const row = document.createElement('label');
      row.className = 'kb-item';
      const cb = document.createElement('input');
      cb.type = 'checkbox';
      cb.className = 'kb-cb';
      cb.value = f;
      cb.checked = f.includes('example');
      row.appendChild(cb);
      row.appendChild(document.createTextNode(f));
      box.appendChild(row);
    }
  } catch {
    box.innerHTML = '<p class="rail-hint">无法加载列表</p>';
  }
}

async function refreshPromptList() {
  const sel = document.getElementById('prompt-name');
  sel.innerHTML = '';
  try {
    const { files } = await fetchJson('/api/prompts');
    const list = files.length ? files : ['writer.md'];
    for (const f of list) {
      const o = document.createElement('option');
      o.value = f;
      o.textContent = f;
      sel.appendChild(o);
    }
  } catch {
    const o = document.createElement('option');
    o.value = 'writer.md';
    o.textContent = 'writer.md';
    sel.appendChild(o);
  }
}

let themesCache = [];

async function refreshThemes() {
  const sel = document.getElementById('theme-id');
  if (!sel) return;
  let list = [];
  for (let attempt = 0; attempt < 5; attempt += 1) {
    try {
      const { themes } = await fetchJson('/api/themes');
      if (Array.isArray(themes) && themes.length > 0) {
        list = themes;
        break;
      }
    } catch (e) {
      if (attempt === 4) console.warn('主题 API:', e);
    }
    await new Promise((r) => setTimeout(r, 280 * (attempt + 1)));
  }
  themesCache = list.length > 0 ? list : FALLBACK_THEMES;
  sel.innerHTML = '';
  for (const t of themesCache) {
    const o = document.createElement('option');
    o.value = t.id;
    o.textContent = t.label || t.id;
    sel.appendChild(o);
  }
  updateThemeDesc();
}

function updateThemeDesc() {
  const el = document.getElementById('theme-desc');
  const id = document.getElementById('theme-id')?.value;
  if (!el) return;
  const t = themesCache.find((x) => x.id === id);
  el.textContent = t?.description || '';
}

async function refreshRollup() {
  const ta = document.getElementById('memory-rollup');
  const bid = memoryBookId();
  try {
    const url = bid ? `/api/books/${encodeURIComponent(bid)}/memory/summary` : '/api/memory/rollup';
    const { text } = await fetchJson(url);
    if (ta) ta.value = text || '';
  } catch (e) {
    console.error(e);
  }
}

async function refreshMemList() {
  const box = document.getElementById('mem-list');
  if (!box) return;
  box.innerHTML = '加载中…';
  const bid = memoryBookId();
  const base = bid ? `/api/books/${encodeURIComponent(bid)}/memory/entries?limit=60` : '/api/memory/entries?limit=60';
  try {
    const { entries } = await fetchJson(base);
    if (!entries.length) {
      box.innerHTML = '<p class="rail-hint">暂无条目，可手动添加或使用「从本章萃取」。</p>';
      return;
    }
    box.innerHTML = '';
    for (const e of entries) {
      const div = document.createElement('div');
      div.className = 'mem-item';
      const head = document.createElement('div');
      head.className = 'mem-item-head';
      const left = document.createElement('div');
      left.innerHTML = `<strong>${escapeHtml(e.title)}</strong><div class="mem-item-meta">${escapeHtml(e.room)}${e.chapter_label ? ' · ' + escapeHtml(e.chapter_label) : ''}</div>`;
      const del = document.createElement('button');
      del.type = 'button';
      del.className = 'btn btn-ghost';
      del.textContent = '删除';
      del.dataset.id = String(e.id);
      head.appendChild(left);
      head.appendChild(del);
      const body = document.createElement('div');
      body.className = 'mem-snippet';
      body.textContent = e.body;
      div.appendChild(head);
      div.appendChild(body);
      del.addEventListener('click', async () => {
        if (!(await showAppConfirm('删除该条记忆？', '删除记忆'))) return;
        try {
          const delUrl = bid
            ? `/api/books/${encodeURIComponent(bid)}/memory/entries/${e.id}`
            : `/api/memory/entries/${e.id}`;
          await fetchJson(delUrl, { method: 'DELETE' });
          await refreshMemList();
        } catch (err) {
          void showAppAlert(err.message);
        }
      });
      box.appendChild(div);
    }
  } catch {
    box.innerHTML = '<p class="rail-hint">无法加载记忆列表</p>';
  }
}

document.addEventListener('DOMContentLoaded', async () => {
  initUiTheme();
  initTabs();

  document.getElementById('btn-library-refresh')?.addEventListener('click', () => {
    refreshReaderShell();
  });

  let bookSearchTimer = null;
  document.getElementById('book-list-search')?.addEventListener('input', () => {
    clearTimeout(bookSearchTimer);
    bookSearchTimer = setTimeout(() => refreshReaderBooks(true), 320);
  });
  document.getElementById('book-list-more')?.addEventListener('click', () => {
    void refreshReaderBooks(false);
  });

  let trashSearchTimer = null;
  document.getElementById('trash-list-search')?.addEventListener('input', () => {
    clearTimeout(trashSearchTimer);
    trashSearchTimer = setTimeout(() => refreshTrashList(true), 320);
  });
  document.getElementById('trash-list-more')?.addEventListener('click', () => {
    void refreshTrashList(false);
  });

  let chapterFilterTimer = null;
  document.getElementById('chapter-list-filter')?.addEventListener('input', () => {
    if (!readerBookId) return;
    clearTimeout(chapterFilterTimer);
    chapterFilterTimer = setTimeout(() => renderChapterList(readerBookId), 200);
  });
  document.getElementById('chapter-list-more')?.addEventListener('click', () => {
    void loadMoreChapters();
  });

  document.getElementById('btn-legacy-files-toggle')?.addEventListener('click', () => {
    const el = document.getElementById('legacy-file-list');
    if (!el) return;
    const open = el.classList.toggle('is-hidden');
    el.hidden = open;
    if (!open) refreshLegacyFileList();
  });

  document.getElementById('reader-prev-ch')?.addEventListener('click', () => {
    if (!readerBookId) return;
    const nums = chapterNavNumbers();
    if (!nums.length) return;
    const i = nums.indexOf(readerChapterN);
    if (i > 0) openBookChapter(readerBookId, nums[i - 1]);
  });

  document.getElementById('reader-next-ch')?.addEventListener('click', () => {
    if (!readerBookId) return;
    const nums = chapterNavNumbers();
    if (!nums.length) return;
    const i = nums.indexOf(readerChapterN);
    if (i >= 0 && i < nums.length - 1) openBookChapter(readerBookId, nums[i + 1]);
  });

  document.getElementById('reader-toc-focus')?.addEventListener('click', () => {
    document.getElementById('chapter-list')?.scrollIntoView({ behavior: 'smooth', block: 'start' });
  });

  document.getElementById('mem-scope-book')?.addEventListener('change', () => {
    refreshRollup();
    refreshMemList();
  });

  const pathsEl = document.getElementById('paths-display');
  if (window.aiWriter?.getPaths && pathsEl) {
    try {
      const p = await window.aiWriter.getPaths();
      pathsEl.hidden = false;
      pathsEl.classList.remove('is-hidden');
      pathsEl.textContent = `UserData:\n${p.userData}\n下载:\n${p.downloads}`;
    } catch (e) {
      console.error(e);
    }
  }

  if (window.aiWriter?.loadSettings) {
    const s = await window.aiWriter.loadSettings();
    document.getElementById('api-key').value = s.deepseekApiKey || '';
    document.getElementById('model-id').value = s.deepseekModel || 'deepseek-chat';
    const br = document.getElementById('books-root-path');
    if (br) br.value = s.booksRoot || '';
  }

  document.getElementById('btn-pick-books-dir')?.addEventListener('click', async () => {
    // #region agent log
    fetch('http://127.0.0.1:7358/ingest/ec74e965-0955-4757-aff0-bed113fed1c4', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json', 'X-Debug-Session-Id': 'd7648d' },
      body: JSON.stringify({
        sessionId: 'd7648d',
        hypothesisId: 'H2',
        location: 'app.js:btn-pick-books-dir',
        message: 'click',
        data: { hasPick: Boolean(window.aiWriter?.pickBooksDir) },
        timestamp: Date.now()
      })
    }).catch(() => {});
    // #endregion
    if (!window.aiWriter?.pickBooksDir) {
      void showAppAlert('当前页面未接入 Electron（或 preload 未加载），无法打开系统文件夹选择框。请用桌面版 AI Writer 启动。', '无法浏览文件夹');
      return;
    }
    try {
      const p = await window.aiWriter.pickBooksDir();
      if (p) document.getElementById('books-root-path').value = p;
    } catch (e) {
      console.error(e);
      void showAppAlert(e?.message || String(e), '选择文件夹失败');
    }
  });

  await refreshHealth();
  await refreshKbList();
  await refreshPromptList();
  await refreshThemes();
  await refreshRollup();
  await refreshMemList();
  await refreshSeriesList();

  document.getElementById('theme-id')?.addEventListener('change', () => updateThemeDesc());

  document.getElementById('btn-refresh-series')?.addEventListener('click', () => {
    refreshSeriesList();
  });

  document.getElementById('btn-pipeline-full')?.addEventListener('click', async () => {
    const title = document.getElementById('solo-title')?.value?.trim();
    if (!title) {
      void showAppAlert('请先填写题目 / 书名 / 灵感短语。', '缺少题目');
      return;
    }
    const rawN = parseInt(String(document.getElementById('solo-chapters')?.value || '8'), 10);
    const maxChapters = Number.isFinite(rawN) ? Math.min(25, Math.max(3, rawN)) : 8;
    const lengthScale = document.getElementById('solo-length')?.value || 'medium';
    const protagonistGender = document.getElementById('solo-gender')?.value || 'any';
    const btn = document.getElementById('btn-pipeline-full');
    const logEl = document.getElementById('pipeline-log');
    const gs = document.getElementById('gen-status');
    const prog = document.getElementById('pipeline-progress');
    const progBar = document.getElementById('pipeline-progress-bar');
    const progLabel = document.getElementById('pipeline-progress-label');
    const progEta = document.getElementById('pipeline-progress-eta');
    if (btn) btn.disabled = true;
    if (prog) {
      prog.hidden = false;
      prog.classList.remove('is-hidden');
    }
    if (progBar) progBar.style.width = '0%';
    if (progLabel) progLabel.textContent = '正在策划全书结构…';
    if (progEta) progEta.textContent = '';
    if (logEl) {
      logEl.hidden = false;
      logEl.textContent = '流式进度见上方进度条；完成后此处显示摘要。\n';
    }
    if (gs) gs.textContent = '一键流水线运行中…';
    const payload = {
      title,
      theme_id: document.getElementById('theme-id')?.value,
      max_chapters: maxChapters,
      length_scale: lengthScale,
      protagonist_gender: protagonistGender,
      use_long_memory: document.getElementById('cb-pipeline-memory')?.checked ?? true,
      kb_names: selectedKbFiles(),
      agent_profile: document.getElementById('pipeline-agent-profile')?.value || 'fast',
      run_reader_test: document.getElementById('pipeline-reader-test')?.checked ?? false
    };
    try {
      const base = await apiBase();
      const streamUrl = joinBackendUrl(base, '/api/pipeline/from-title/stream');
      const res = await fetch(streamUrl, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(payload)
      });
      if (!res.ok) {
        const t = await res.text();
        let hint = t || res.statusText;
        if (res.status === 404 && /Not Found|not found/i.test(hint)) {
          hint +=
            '\n\n提示：多为本机 18765 端口被旧版后端或其它程序占用。请关闭其它「python uvicorn」终端或冲突程序后，在设置里点「保存并重启后端」。';
        }
        throw new Error(hint);
      }
      const reader = res.body.getReader();
      const dec = new TextDecoder();
      let buf = '';
      let data = null;
      while (true) {
        const { done, value } = await reader.read();
        if (done) break;
        buf += dec.decode(value, { stream: true });
        const parts = buf.split('\n');
        buf = parts.pop() || '';
        for (const line of parts) {
          if (!line.trim()) continue;
          let ev;
          try {
            ev = JSON.parse(line);
          } catch {
            continue;
          }
          if (ev.event === 'phase' && progLabel) {
            progLabel.textContent = ev.message || '处理中…';
          }
          if (ev.event === 'planned' && progLabel) {
            progLabel.textContent = `策划完成 · 共 ${ev.total_chapters} 章 · 开始写作…`;
            if (progBar) progBar.style.width = '3%';
          }
          if (ev.event === 'chapter_begin') {
            if (progLabel) {
              progLabel.textContent = `第 ${ev.index}/${ev.total} 章「${ev.title || ''}」生成中…`;
            }
            if (progEta && ev.eta_ms != null) {
              progEta.textContent = `预计剩余 ${formatEta(ev.eta_ms)}`;
            }
            if (progBar && ev.total) {
              const pct = Math.min(99, Math.round((ev.done / ev.total) * 100));
              progBar.style.width = `${pct}%`;
            }
          }
          if (ev.event === 'chapter_end' && progBar && ev.total) {
            const pct = Math.min(100, Math.round((ev.done / ev.total) * 100));
            progBar.style.width = `${pct}%`;
            if (progEta) progEta.textContent = '';
          }
          if (ev.event === 'done') {
            data = ev.result;
          }
          if (ev.event === 'error') {
            throw new Error(ev.detail || '生成失败');
          }
        }
      }
      if (!data) throw new Error('未收到完成事件');
      const lines = [
        '—— 完成 ——',
        `书本 ID（续写、书库用）：${data.book_id || ''}`,
        `书名：${data.book_title}`,
        `梗概：${data.premise}`,
        `章节数：${data.chapters_planned}`,
        `策划文件：${data.plan_file}`,
        '',
        '已保存章节：',
        ...(data.saved_files || [])
      ];
      if (logEl) logEl.textContent = lines.join('\n');
      if (progBar) progBar.style.width = '100%';
      if (progLabel) progLabel.textContent = '已完成';
      if (progEta) progEta.textContent = '';
      const prem = document.getElementById('premise');
      if (prem && data.premise) prem.value = data.premise;
      if (gs) gs.textContent = '已入库，可到「书库阅读」打开；下方可续写。';
      await refreshSeriesList();
      await refreshMemBookOptions();
    } catch (e) {
      if (logEl) logEl.textContent = `失败：${e.message}`;
      if (gs) gs.textContent = '';
      if (progLabel) progLabel.textContent = '失败';
      void showAppAlert(e.message || String(e), '生成失败');
    } finally {
      if (btn) btn.disabled = false;
    }
  });

  document.getElementById('btn-continue-chapter')?.addEventListener('click', async () => {
    const raw = document.getElementById('series-continue-select')?.value?.trim();
    if (!raw) {
      void showAppAlert('请先刷新列表并选择一本书本或旧书系。', '未选择书系');
      return;
    }
    const logEl = document.getElementById('continue-log');
    const gs = document.getElementById('gen-status');
    const btn = document.getElementById('btn-continue-chapter');
    if (btn) btn.disabled = true;
    if (logEl) {
      logEl.hidden = false;
      logEl.textContent = '续写中：先生成章要点，再写正文（2 次 API）…\n';
    }
    if (gs) gs.textContent = '续写进行中…';
    try {
      const basePayload = {
        theme_id: document.getElementById('theme-id')?.value,
        use_long_memory: document.getElementById('cb-continue-memory')?.checked ?? true,
        kb_names: selectedKbFiles(),
        agent_profile: document.getElementById('pipeline-agent-profile')?.value || 'fast',
        run_reader_test: document.getElementById('pipeline-reader-test')?.checked ?? false
      };
      const cc = parseInt(String(document.getElementById('continue-chapter-count')?.value || '1'), 10);
      const chapterCount = Number.isFinite(cc) ? Math.min(20, Math.max(1, cc)) : 1;
      let payload = { ...basePayload, chapter_count: chapterCount };
      if (raw.startsWith('book:')) {
        payload.book_id = raw.slice(5);
      } else if (raw.startsWith('legacy:')) {
        payload.series_prefix = raw.slice(7);
        delete payload.agent_profile;
        delete payload.run_reader_test;
        delete payload.chapter_count;
      } else {
        void showAppAlert('选择项格式无效，请刷新列表后重选。', '选择无效');
        return;
      }
      const data = await fetchJson('/api/pipeline/continue', {
        method: 'POST',
        body: JSON.stringify(payload)
      });
      if (logEl) {
        if (data.chapters && Array.isArray(data.chapters)) {
          logEl.textContent = `—— 续写完成 ${data.chapters_written || data.chapters.length} 章 ——\n${data.chapters.map((c) => `第 ${c.chapter_index} 章 ${c.chapter_title || ''} → ${c.saved_file}`).join('\n')}\n书名：${data.book_title || ''}`;
        } else {
          logEl.textContent = `—— 续写完成 ——\n${data.saved_file}\n第 ${data.chapter_index} 章 ${data.chapter_title || ''}\n书名：${data.book_title}${data.book_id ? `\n书本 ID：${data.book_id}` : ''}`;
        }
      }
      if (gs) gs.textContent = '续写已保存，可在书库中阅读。';
      await refreshSeriesList();
      await refreshMemBookOptions();
      await refreshReaderBooks(true);
    } catch (e) {
      if (logEl) logEl.textContent = `失败：${e.message}`;
      if (gs) gs.textContent = '';
      void showAppAlert(e.message || String(e), '续写失败');
    } finally {
      if (btn) btn.disabled = false;
    }
  });

  document.getElementById('btn-refresh-kb').addEventListener('click', () => {
    refreshKbList();
  });

  document.getElementById('btn-save-settings').addEventListener('click', async () => {
    const btn = document.getElementById('btn-save-settings');
    btn.disabled = true;
    try {
      await window.aiWriter.saveSettings({
        deepseekApiKey: document.getElementById('api-key').value.trim(),
        deepseekModel: document.getElementById('model-id').value.trim() || 'deepseek-chat',
        booksRoot: document.getElementById('books-root-path')?.value?.trim() || ''
      });
      await refreshHealth();
      await refreshThemes();
      await refreshReaderShell();
    } catch (e) {
      void showAppAlert(e.message || String(e), '保存设置失败');
    } finally {
      btn.disabled = false;
    }
  });

  document.getElementById('reader-export-txt')?.addEventListener('click', async () => {
    if (!readerBookId) return;
    // #region agent log
    fetch('http://127.0.0.1:7358/ingest/ec74e965-0955-4757-aff0-bed113fed1c4', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json', 'X-Debug-Session-Id': 'd7648d' },
      body: JSON.stringify({
        sessionId: 'd7648d',
        hypothesisId: 'H4',
        location: 'app.js:export-txt',
        message: 'click',
        data: { readerBookId },
        timestamp: Date.now()
      })
    }).catch(() => {});
    // #endregion
    try {
      const base = await apiBase();
      const url = joinBackendUrl(
        base,
        `/api/books/${encodeURIComponent(readerBookId)}/export.txt`
      );
      const res = await fetch(url);
      if (!res.ok) throw new Error(await res.text());
      const text = await res.text();
      const cd = res.headers.get('Content-Disposition');
      const m =
        cd &&
        (/\bfilename\*=UTF-8''([^;\s]+)/i.exec(cd) || /\bfilename="([^"]+)"/i.exec(cd));
      let fname = `${readerBookId}.txt`;
      if (m) {
        try {
          fname = decodeURIComponent(m[1] || m[2] || fname);
        } catch {
          fname = m[1] || m[2] || fname;
        }
      }
      if (window.aiWriter?.saveTextFileAs) {
        const out = await window.aiWriter.saveTextFileAs({
          defaultFileName: fname,
          content: text
        });
        if (out?.canceled) return;
        if (!out?.ok) throw new Error(out?.error || '保存失败');
        return;
      }
      const blob = new Blob([text], { type: 'text/plain;charset=utf-8' });
      const u = URL.createObjectURL(blob);
      const a = document.createElement('a');
      a.href = u;
      a.download = fname;
      a.rel = 'noopener';
      document.body.appendChild(a);
      a.click();
      document.body.removeChild(a);
      URL.revokeObjectURL(u);
    } catch (e) {
      void showAppAlert(e.message || String(e), '导出失败');
    }
  });

  document.getElementById('reader-delete-book')?.addEventListener('click', async () => {
    if (!readerBookId) return;
    if (
      !(await showAppConfirm(
        '将本书移入回收站？可在左侧「回收站」还原。',
        '移入回收站'
      ))
    )
      return;
    try {
      await fetchJson(`/api/books/${encodeURIComponent(readerBookId)}`, { method: 'DELETE' });
      readerBookId = '';
      readerChapterN = 0;
      readerToc = [];
      readerChapterNs = [];
      readerTocTotal = 0;
      const cf = document.getElementById('chapter-list-filter');
      if (cf) {
        cf.value = '';
        cf.disabled = true;
      }
      const cmeta = document.getElementById('chapter-list-meta');
      if (cmeta) cmeta.textContent = '';
      document.getElementById('reader-content').innerHTML = '';
      document.getElementById('reader-doc-title').textContent = '选择书本与章节';
      document.getElementById('reader-meta').textContent = '';
      document.getElementById('reader-chapter-nav').hidden = true;
      document.getElementById('reader-export-txt').hidden = true;
      document.getElementById('reader-delete-book').hidden = true;
      document.getElementById('chapter-list').innerHTML = '';
      const chMore = document.getElementById('chapter-list-more');
      if (chMore) chMore.hidden = true;
      await refreshReaderShell();
    } catch (e) {
      void showAppAlert(e.message || String(e));
    }
  });

  document.getElementById('btn-outline').addEventListener('click', async () => {
    const premise = document.getElementById('premise').value.trim();
    if (!premise) return;
    const pre = document.getElementById('outline-out');
    pre.hidden = false;
    pre.textContent = '生成中…';
    try {
      const data = await fetchJson('/api/outline', {
        method: 'POST',
        body: JSON.stringify({
          premise,
          temperature: 0.7,
          theme_id: document.getElementById('theme-id').value
        })
      });
      pre.textContent = data.text || '';
    } catch (e) {
      pre.textContent = `错误：${e.message}`;
    }
  });

  const chapterOut = document.getElementById('chapter-out');
  const btnSave = document.getElementById('btn-save-md');
  const genStatus = document.getElementById('gen-status');

  document.getElementById('btn-generate').addEventListener('click', async () => {
    const brief = document.getElementById('chapter-brief').value.trim();
    if (!brief) return;
    genStatus.textContent = '请求模型中…';
    chapterOut.textContent = '';
    btnSave.disabled = true;
    try {
      const data = await fetchJson('/api/generate', {
        method: 'POST',
        body: JSON.stringify({
          user_message: brief,
          prompt_name: document.getElementById('prompt-name').value,
          kb_names: selectedKbFiles(),
          temperature: 0.8,
          stream: false,
          theme_id: document.getElementById('theme-id').value,
          use_long_memory: document.getElementById('cb-long-memory').checked,
          memory_max_chars: 4500
        })
      });
      chapterOut.textContent = data.text || '';
      btnSave.disabled = !chapterOut.textContent.trim();
      genStatus.textContent = '完成';
    } catch (e) {
      genStatus.textContent = '';
      chapterOut.textContent = `错误：${e.message}`;
    }
  });

  btnSave.addEventListener('click', async () => {
    const content = chapterOut.textContent.trim();
    if (!content) return;
    const name = await showAppPrompt(
      '将保存到 UserData/out 目录',
      'chapter-01',
      '保存章节',
      '文件名（不含路径）'
    );
    if (name == null || !String(name).trim()) return;
    try {
      await fetchJson('/api/save-chapter', {
        method: 'POST',
        body: JSON.stringify({ filename: String(name).trim(), content })
      });
      genStatus.textContent = '已保存到 UserData/out（可在「书库阅读」中打开）';
    } catch (e) {
      void showAppAlert(e.message);
    }
  });

  document.getElementById('btn-save-rollup')?.addEventListener('click', async () => {
    const text = document.getElementById('memory-rollup').value;
    const bid = memoryBookId();
    try {
      const url = bid ? `/api/books/${encodeURIComponent(bid)}/memory/summary` : '/api/memory/rollup';
      await fetchJson(url, {
        method: 'PUT',
        body: JSON.stringify({ text })
      });
      genStatus.textContent = '总摘要已保存';
    } catch (e) {
      void showAppAlert(e.message);
    }
  });

  document.getElementById('btn-mem-add')?.addEventListener('click', async () => {
    const room = document.getElementById('mem-room').value;
    const title = document.getElementById('mem-title').value.trim();
    const bodyText = document.getElementById('mem-body').value.trim();
    const chapter_label = document.getElementById('mem-chapter').value.trim() || null;
    const bid = memoryBookId();
    if (!title || !bodyText) {
      void showAppAlert('请填写标题与内容', '记忆条目');
      return;
    }
    try {
      const url = bid ? `/api/books/${encodeURIComponent(bid)}/memory/entries` : '/api/memory/entries';
      await fetchJson(url, {
        method: 'POST',
        body: JSON.stringify({ room, title, body: bodyText, chapter_label })
      });
      document.getElementById('mem-title').value = '';
      document.getElementById('mem-body').value = '';
      await refreshMemList();
      genStatus.textContent = '已添加记忆条目';
    } catch (e) {
      void showAppAlert(e.message);
    }
  });

  document.getElementById('btn-mem-refresh')?.addEventListener('click', () => {
    refreshMemList();
    refreshRollup();
  });

  document.getElementById('btn-mem-extract')?.addEventListener('click', async () => {
    const text = chapterOut.textContent.trim();
    if (text.length < 20) {
      void showAppAlert('请先生成或粘贴本章正文', '萃取记忆');
      return;
    }
    const rawLabel = await showAppPrompt(
      '用于在记忆列表中标注来源（可留空表示无）',
      '当前章',
      '章节标签',
      '标签（可选）'
    );
    const chapter_label = rawLabel != null && String(rawLabel).trim() ? String(rawLabel).trim() : null;
    const bid = memoryBookId();
    genStatus.textContent = '正在萃取记忆（调用模型）…';
    try {
      const url = bid ? `/api/books/${encodeURIComponent(bid)}/memory/extract` : '/api/memory/extract';
      await fetchJson(url, {
        method: 'POST',
        body: JSON.stringify({ text, chapter_label, temperature: 0.4 })
      });
      await refreshMemList();
      genStatus.textContent = '已萃取并写入「情节」房间';
    } catch (e) {
      genStatus.textContent = '';
      void showAppAlert(e.message);
    }
  });
});
