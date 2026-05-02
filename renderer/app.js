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
  { id: 'horror', label: '悬疑 / 惊悚', description: '悬念与氛围。', system_addon: '' },
  {
    id: 'ancient_romance',
    label: '古代言情',
    description: '古代背景下的情感与人物命运。',
    system_addon: ''
  }
];

/** 写作台侧边栏分组（可与 themes.json 单条可选 `category` 叠加，未标注则按 id 回落） */
const THEME_SIDEBAR_ROWS = [
  ['main', '一类'],
  ['plot', '二类'],
  ['character', '三类'],
  ['emotion', '四类'],
  ['backdrop', '五类']
];

const THEME_SIDEBAR_KEYS = new Set(THEME_SIDEBAR_ROWS.map((r) => r[0]));

const THEME_ID_SIDEBAR = {
  realism: 'backdrop',
  urban: 'backdrop',
  fantasy: 'main',
  xianxia: 'main',
  scifi: 'main',
  cyberpunk: 'backdrop',
  postapoc: 'backdrop',
  horror: 'emotion',
  romance: 'emotion',
  ancient_romance: 'emotion',
  cosmic: 'emotion',
  game: 'main',
  annals_fiction: 'main',
  artifact_pov: 'plot',
  soft_apocalypse: 'backdrop',
  ethnographic_weird: 'backdrop',
  weather_law: 'backdrop',
  culinary_weird: 'character',
  loop_literary: 'plot',
  dream_industry: 'emotion',
  rust_romanticism: 'emotion',
  sigil_economy: 'backdrop',
  mycelium_society: 'backdrop',
  grammar_magic: 'plot',
  emotion_tax: 'plot',
  tidal_memory: 'plot',
  night_republic: 'backdrop',
  parasitic_architecture: 'backdrop',
  wormhole_post: 'plot',
  probability_herds: 'plot',
  joke_materialize: 'plot',
  failed_archaeology: 'plot',
  silence_endemic: 'character',
  authenticity_war: 'character',
  reverse_domestication: 'character',
  outsourced_divinity: 'plot',
  map_fraud: 'backdrop',
  celestial_nursery: 'emotion',
  symmetry_trade: 'plot',
  moss_epoch: 'emotion',
  fanwork: 'main'
};

function sidebarKeyForThemeRow(t) {
  const raw = typeof t.category === 'string' ? t.category.trim().toLowerCase() : '';
  if (raw && THEME_SIDEBAR_KEYS.has(raw)) return raw;
  const id = String(t.id || '');
  return THEME_ID_SIDEBAR[id] || 'main';
}

function slug(s) {
  return String(s).replace(/[^a-zA-Z0-9_\u0080-\uFFFF-]/g, '_') || '_';
}

let themesCache = [];

let activeThemeSidebarKey = 'main';
let themeCascadeListenersBound = false;
let themeCascadeLayersMounted = false;
let themeCascadePreviewHoverId = null;
let themeCascadePositionRaf = 0;

function selectedKbFiles() {
  return Array.from(document.querySelectorAll('.kb-cb:checked')).map((el) => el.value);
}

let libraryActiveName = '';
let readerBookId = '';
let readerChapterN = 0;
/** 当前阅读区正文的原始 Markdown（用于复制本章） */
let readerChapterRaw = '';
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

/** 脑洞程度 0～1，默认 0.5；与后端 Pipeline / 手动生成 / 大纲一致 */
function readIdeationLevel() {
  const el = document.getElementById('ideation-level');
  if (!el) return 0.5;
  const v = parseFloat(String(el.value));
  if (!Number.isFinite(v)) return 0.5;
  return Math.min(1, Math.max(0, v));
}

function initIdeationSlider() {
  const range = document.getElementById('ideation-level');
  const valEl = document.getElementById('ideation-level-value');
  const sync = () => {
    if (valEl) valEl.textContent = readIdeationLevel().toFixed(2);
  };
  sync();
  range?.addEventListener('input', sync);
  range?.addEventListener('change', sync);
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
  const copyB = document.getElementById('reader-copy-chapter');
  const showActs = Boolean(readerBookId) && nums.length > 0;
  if (exp) exp.hidden = !showActs;
  if (del) del.hidden = !showActs;
  const canCopy = Boolean(String(readerChapterRaw || '').trim());
  if (copyB) copyB.hidden = !canCopy;
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

/** 复制用：去掉 **加粗** 及残留星号（模型常把对白写成 **「…」**） */
function stripMarkdownEmphasisForCopy(raw) {
  let s = String(raw);
  for (let i = 0; i < 48; i++) {
    const next = s.replace(/\*\*([\s\S]*?)\*\*/g, (_, inner) => inner);
    if (next === s) break;
    s = next;
  }
  s = s.replace(/\*{2,}/g, '');
  return s;
}

async function copyReaderTextToClipboard() {
  const text = stripMarkdownEmphasisForCopy(String(readerChapterRaw || '').trim());
  if (!text) {
    void showAppAlert('当前没有已加载的正文可复制。', '复制本章');
    return;
  }
  try {
    if (navigator.clipboard?.writeText) {
      await navigator.clipboard.writeText(text);
    } else {
      const ta = document.createElement('textarea');
      ta.value = text;
      ta.setAttribute('readonly', '');
      ta.style.position = 'fixed';
      ta.style.left = '-9999px';
      document.body.appendChild(ta);
      ta.select();
      document.execCommand('copy');
      document.body.removeChild(ta);
    }
    void showAppAlert('本章正文已复制到剪贴板。', '复制本章');
  } catch (e) {
    void showAppAlert(e?.message || String(e), '复制失败');
  }
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
    readerChapterRaw = typeof data.content === 'string' ? data.content : '';
    renderReaderMarkdown(data.content);
    updateReaderNav();
    scrollReaderToTop();
  } catch (e) {
    readerChapterRaw = '';
    metaEl.textContent = '';
    titleEl.textContent = '读取失败';
    document.getElementById('reader-content').innerHTML = `<p class="rail-hint">${escapeHtml(e.message)}</p>`;
    updateReaderNav();
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
  readerChapterRaw = '';
  readerChapterN = 0;
  readerToc = [];
  readerChapterNs = [];
  readerTocTotal = 0;
  // 书库所选书本与「写作 · 记忆范围」未联动时，用户易误以为在改本书记忆却在改全局。
  const memSel = document.getElementById('mem-scope-book');
  if (memSel && bookId && Array.from(memSel.options).some((op) => op.value === bookId)) {
    memSel.value = bookId;
    void refreshRollup();
    void refreshMemList();
  }
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
  readerChapterRaw = '';
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
    readerChapterRaw = typeof data.content === 'string' ? data.content : '';
    renderReaderMarkdown(data.content);
    updateReaderNav();
  } catch (e) {
    readerChapterRaw = '';
    metaEl.textContent = '';
    titleEl.textContent = '读取失败';
    contentEl.innerHTML = `<p class="rail-hint">${escapeHtml(e.message)}</p>`;
    updateReaderNav();
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

async function populateAnalyticsSupervisorBooks() {
  const sel = document.getElementById('analytics-supervisor-book');
  if (!sel) return;
  const cur = sel.value;
  sel.innerHTML = '<option value="">— 选择书本 —</option>';
  try {
    let offset = 0;
    const lim = 400;
    let total = Infinity;
    while (offset < total) {
      const data = await fetchJson(`/api/books?limit=${lim}&offset=${offset}&q=`);
      total = data.total ?? 0;
      for (const b of data.books || []) {
        const o = document.createElement('option');
        o.value = b.id;
        o.textContent = `${b.title || b.id} · ${b.chapter_count ?? 0} 章`;
        sel.appendChild(o);
      }
      offset += (data.books || []).length;
      if (!(data.books || []).length) break;
    }
  } catch (e) {
    console.warn('analytics supervisor books', e);
  }
  if (cur && Array.from(sel.options).some((op) => op.value === cur)) sel.value = cur;
}

function showAnalyticsSupervisorPayload(payload) {
  const titleEl = document.getElementById('analytics-preview-title');
  const metaEl = document.getElementById('analytics-preview-meta');
  const preview = document.getElementById('analytics-preview');
  if (!preview) return;
  if (titleEl) titleEl.textContent = '书本监督结果';
  if (metaEl) {
    if (payload?.saved?.rel_path) metaEl.textContent = `已保存：${payload.saved.rel_path}`;
    else if (payload?.metaLine) metaEl.textContent = payload.metaLine;
    else metaEl.textContent = '';
  }
  preview.innerHTML = '';
  if (payload?.integrity) {
    const h = document.createElement('h4');
    h.className = 'analytics-supervisor__sub';
    h.textContent = '完整性报告';
    preview.appendChild(h);
    const preI = document.createElement('pre');
    preI.className = 'analytics-json';
    preI.textContent = JSON.stringify(payload.integrity, null, 2);
    preview.appendChild(preI);
  }
  if (payload?.meta_review) {
    const h2 = document.createElement('h4');
    h2.className = 'analytics-supervisor__sub';
    h2.textContent = '元审查（监督智能体）';
    preview.appendChild(h2);
    const preM = document.createElement('pre');
    preM.className = 'analytics-json';
    preM.textContent = JSON.stringify(payload.meta_review, null, 2);
    preview.appendChild(preM);
  }
  if (!payload?.integrity && !payload?.meta_review) {
    preview.innerHTML = '<p class="rail-hint">无数据</p>';
  }
}

async function refreshAnalyticsFileListOnly() {
  const sectionsEl = document.getElementById('analytics-sections');
  if (!sectionsEl) return;
  try {
    const data = await fetchJson('/api/analytics/list');
    renderAnalyticsSections(data);
  } catch (e) {
    console.warn('analytics list refresh', e);
  }
}

async function refreshAnalyticsPanel() {
  const hint = document.getElementById('analytics-paths-hint');
  const sectionsEl = document.getElementById('analytics-sections');
  const titleEl = document.getElementById('analytics-preview-title');
  const metaEl = document.getElementById('analytics-preview-meta');
  const preview = document.getElementById('analytics-preview');
  if (!sectionsEl || !preview) return;
  await populateAnalyticsSupervisorBooks();
  try {
    const info = await fetchJson('/api/analytics/info');
    if (hint) {
      hint.textContent = `分析根目录：${info.analytics_root}\n快照库：${info.snapshots_dir}`;
    }
    const data = await fetchJson('/api/analytics/list');
    renderAnalyticsSections(data);
    if (titleEl) titleEl.textContent = '选择左侧文件';
    if (metaEl) metaEl.textContent = '';
    preview.innerHTML =
      '<p class="rail-hint">点击列表中的 Markdown、JSON、JSONL 或截图即可预览。</p>';
  } catch (e) {
    sectionsEl.innerHTML = '';
    if (hint) hint.textContent = `加载失败：${e?.message || e}`;
  }
}

function renderAnalyticsSections(data) {
  const sectionsEl = document.getElementById('analytics-sections');
  if (!sectionsEl) return;
  sectionsEl.innerHTML = '';
  for (const sec of data.sections || []) {
    const wrap = document.createElement('div');
    wrap.className = 'analytics-section';
    const h = document.createElement('h3');
    h.className = 'analytics-section-title';
    h.textContent = sec.title;
    wrap.appendChild(h);
    const list = document.createElement('div');
    list.className = 'analytics-file-list';
    const items = sec.items || [];
    if (!items.length) {
      const empty = document.createElement('p');
      empty.className = 'rail-hint';
      empty.textContent = '暂无条目';
      list.appendChild(empty);
    } else {
      for (const it of items) {
        if (it.is_dir) {
          const row = document.createElement('div');
          row.className = 'analytics-dir-label';
          row.textContent = `· ${it.name}`;
          list.appendChild(row);
          continue;
        }
        const btn = document.createElement('button');
        btn.type = 'button';
        btn.className = 'analytics-file-item';
        const kb = it.size != null && it.size > 0 ? ` · ${(it.size / 1024).toFixed(1)} KB` : '';
        btn.textContent = `${it.name}${kb}`;
        btn.dataset.rel = it.rel_path;
        btn.addEventListener('click', () => {
          void loadAnalyticsPreview(it.rel_path, it);
        });
        list.appendChild(btn);
      }
    }
    wrap.appendChild(list);
    sectionsEl.appendChild(wrap);
  }
}

async function loadAnalyticsPreview(relPath, meta) {
  const titleEl = document.getElementById('analytics-preview-title');
  const metaEl = document.getElementById('analytics-preview-meta');
  const preview = document.getElementById('analytics-preview');
  if (!preview || !relPath) return;
  if (titleEl) titleEl.textContent = meta?.name || relPath;
  const name = (meta?.name || relPath).toLowerCase();
  const isImg =
    name.endsWith('.png') ||
    name.endsWith('.jpg') ||
    name.endsWith('.jpeg') ||
    name.endsWith('.webp') ||
    name.endsWith('.gif');
  if (isImg) {
    const base = await apiBase();
    const url = joinBackendUrl(base, `/api/analytics/raw?rel=${encodeURIComponent(relPath)}`);
    if (metaEl) metaEl.textContent = relPath;
    preview.innerHTML = '';
    const img = document.createElement('img');
    img.className = 'analytics-preview-img';
    img.src = url;
    img.alt = relPath;
    preview.appendChild(img);
    return;
  }
  if (metaEl) metaEl.textContent = relPath;
  preview.innerHTML = '<p class="rail-hint">加载中…</p>';
  try {
    const res = await fetchJson(`/api/analytics/file?rel=${encodeURIComponent(relPath)}`);
    preview.innerHTML = '';
    if (res.kind === 'json') {
      const pre = document.createElement('pre');
      pre.className = 'analytics-json';
      pre.textContent = JSON.stringify(res.data, null, 2);
      preview.appendChild(pre);
    } else {
      const pre = document.createElement('pre');
      pre.className = 'analytics-text';
      pre.textContent = res.content || '';
      preview.appendChild(pre);
    }
  } catch (e) {
    preview.innerHTML = '';
    const p = document.createElement('p');
    p.className = 'rail-hint';
    p.textContent = e?.message || String(e);
    preview.appendChild(p);
  }
}

function initTabs() {
  const tabs = document.querySelectorAll('.view-tab');
  const writePanel = document.getElementById('panel-write');
  const readPanel = document.getElementById('panel-read');
  const teardownPanel = document.getElementById('panel-teardown');
  const analyticsPanel = document.getElementById('panel-analytics');
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
      teardownPanel?.classList.toggle('is-active', panelId === 'teardown');
      analyticsPanel?.classList.toggle('is-active', panelId === 'analytics');
      if (panelId === 'read') {
        refreshReaderShell();
      }
      if (panelId === 'analytics') {
        void refreshAnalyticsPanel();
      }
    });
  });
}

function initTeardownPanel() {
  // —— 子导航切换 ——
  const subTabs = document.querySelectorAll('.teardown-sub-tab');
  const subPanels = document.querySelectorAll('.teardown-sub-panel');
  subTabs.forEach((tab) => {
    tab.addEventListener('click', () => {
      const subId = tab.dataset.sub;
      subTabs.forEach((t) => {
        const on = t.dataset.sub === subId;
        t.classList.toggle('is-active', on);
        t.setAttribute('aria-selected', on ? 'true' : 'false');
      });
      subPanels.forEach((p) => {
        p.classList.toggle('is-active', p.id === `subpanel-${subId}`);
      });
    });
  });

  // —— 辅助函数 ——
  function setOutputMarkdownText(el, text) {
    if (!el) return;
    el.innerHTML = '';
    if (!text) {
      const p = document.createElement('p');
      p.className = 'rail-hint';
      p.textContent = '尚无输出';
      el.appendChild(p);
      return;
    }
    const pre = document.createElement('pre');
    pre.className = 'teardown-pre';
    pre.textContent = text;
    el.appendChild(pre);
  }

  function parseTags(raw) {
    return String(raw || '')
      .split(/[,，、;；\s]+/)
      .map((t) => t.trim())
      .filter(Boolean);
  }

  async function matchTagsForDisplay(tags, hintEl) {
    if (!hintEl) return;
    hintEl.innerHTML = '';
    if (!tags.length) return;
    try {
      const r = await fetchJson('/api/teardown/match-tags', {
        method: 'POST',
        body: JSON.stringify({ tags }),
      });
      for (const m of r.matched_themes || []) {
        const chip = document.createElement('span');
        chip.className = 'tag-match-chip';
        chip.textContent = `${m.label || m.id}`;
        hintEl.appendChild(chip);
      }
      for (const ut of r.unmatched_tags || []) {
        const chip = document.createElement('span');
        chip.className = 'tag-unmatch-chip';
        chip.textContent = `${ut}（将自动新建主题）`;
        hintEl.appendChild(chip);
      }
    } catch {
      // 静默失败
    }
  }

  // —— 拆开头标签实时匹配 ——
  let openingTagTimer = null;
  document.getElementById('opening-tags')?.addEventListener('input', () => {
    clearTimeout(openingTagTimer);
    openingTagTimer = setTimeout(() => {
      const tags = parseTags(document.getElementById('opening-tags')?.value);
      void matchTagsForDisplay(tags, document.getElementById('opening-matched-themes'));
    }, 500);
  });

  // —— 蒸馏标签实时匹配 ——
  let distillTagTimer = null;
  document.getElementById('distill-tags')?.addEventListener('input', () => {
    clearTimeout(distillTagTimer);
    distillTagTimer = setTimeout(() => {
      const tags = parseTags(document.getElementById('distill-tags')?.value);
      void matchTagsForDisplay(tags, document.getElementById('distill-matched-themes'));
    }, 500);
  });

  // ==================== 拆开头 ====================
  let lastOpeningText = '';
  const openingRunBtn = document.getElementById('btn-opening-run');
  const openingSaveKbBtn = document.getElementById('btn-opening-save-kb');
  const openingWriteMemBtn = document.getElementById('btn-opening-write-memory');
  const openingOutEl = document.getElementById('opening-output');
  const openingRunStatus = document.getElementById('opening-run-status');
  const openingSaveStatus = document.getElementById('opening-save-status');

  openingRunBtn?.addEventListener('click', async () => {
    const excerpt = document.getElementById('opening-excerpt')?.value?.trim() ?? '';
    if (excerpt.length < 80) {
      void showAppAlert('正文节选过短：请至少粘贴约 80 字。', '拆开头');
      return;
    }
    const book_title = document.getElementById('opening-title')?.value?.trim() ?? '';
    const author = document.getElementById('opening-author')?.value?.trim() ?? '';
    const tags = parseTags(document.getElementById('opening-tags')?.value);
    if (openingRunStatus) openingRunStatus.textContent = '调用模型中…';
    openingRunBtn.disabled = true;
    openingSaveKbBtn && (openingSaveKbBtn.disabled = true);
    openingWriteMemBtn && (openingWriteMemBtn.disabled = true);
    lastOpeningText = '';
    try {
      const r = await fetchJson('/api/teardown/opening', {
        method: 'POST',
        body: JSON.stringify({ excerpt, book_title, author, tags, temperature: 0.35 }),
      });
      lastOpeningText = typeof r?.text === 'string' ? r.text : '';
      setOutputMarkdownText(openingOutEl, lastOpeningText);
      if (openingSaveKbBtn) openingSaveKbBtn.disabled = !lastOpeningText;
      if (openingWriteMemBtn) openingWriteMemBtn.disabled = !lastOpeningText;
      if (openingRunStatus) openingRunStatus.textContent = lastOpeningText ? '完成' : '无正文返回';
    } catch (e) {
      setOutputMarkdownText(openingOutEl, '');
      if (openingRunStatus) openingRunStatus.textContent = '';
      void showAppAlert(e?.message || String(e), '拆开头失败');
    } finally {
      openingRunBtn.disabled = false;
    }
  });

  openingSaveKbBtn?.addEventListener('click', async () => {
    if (!lastOpeningText) {
      void showAppAlert('请先生成拆开头报告。', '保存');
      return;
    }
    let name = document.getElementById('opening-kb-filename')?.value?.trim() ?? '';
    if (!name) {
      const t = document.getElementById('opening-title')?.value?.trim() || '拆开头';
      name = `拆开头-${t.replace(/[\\/:*?"<>|]+/g, '_').slice(0, 60)}`;
    }
    if (openingSaveStatus) openingSaveStatus.textContent = '写入中…';
    openingSaveKbBtn.disabled = true;
    try {
      await fetchJson('/api/kb/write', {
        method: 'POST',
        body: JSON.stringify({ filename: name, content: lastOpeningText }),
      });
      if (openingSaveStatus) openingSaveStatus.textContent = '已写入知识库，可在写作台刷新 kb 列表并勾选。';
      await refreshKbList();
    } catch (e) {
      if (openingSaveStatus) openingSaveStatus.textContent = '';
      void showAppAlert(e?.message || String(e), '保存失败');
    } finally {
      openingSaveKbBtn.disabled = !lastOpeningText;
    }
  });

  openingWriteMemBtn?.addEventListener('click', async () => {
    if (!lastOpeningText) {
      void showAppAlert('请先生成拆开头报告。', '写入记忆');
      return;
    }
    const book_title = document.getElementById('opening-title')?.value?.trim() || '拆开头分析';
    if (openingSaveStatus) openingSaveStatus.textContent = '写入记忆宫殿中…';
    openingWriteMemBtn.disabled = true;
    try {
      await fetchJson('/api/teardown/write-memory', {
        method: 'POST',
        body: JSON.stringify({
          room: '风格',
          title: `拆开头分析：${book_title}`,
          body: lastOpeningText.slice(0, 8000),
        }),
      });
      if (openingSaveStatus) openingSaveStatus.textContent = '已写入记忆宫殿「风格」房间。';
    } catch (e) {
      void showAppAlert(e?.message || String(e), '写入记忆失败');
    } finally {
      openingWriteMemBtn.disabled = !lastOpeningText;
    }
  });

  // ==================== 蒸馏作者 ====================
  let lastDistillResult = null;
  const distillRunBtn = document.getElementById('btn-distill-run');
  const distillSaveSkillBtn = document.getElementById('btn-distill-save-skill');
  const distillWriteMemBtn = document.getElementById('btn-distill-write-memory');
  const distillOutEl = document.getElementById('distill-output');
  const distillRunStatus = document.getElementById('distill-run-status');
  const distillSaveStatus = document.getElementById('distill-save-status');

  // txt 文件上传（支持超大文件：智能采样头/中/尾各 2 万字）
  document.getElementById('distill-file')?.addEventListener('change', (ev) => {
    const file = ev.target.files?.[0];
    const infoEl = document.getElementById('distill-file-info');
    const excerptEl = document.getElementById('distill-excerpt');
    if (!file) {
      if (infoEl) infoEl.textContent = '';
      return;
    }
    const SAMPLE_SIZE = 20000; // 每段采样字符数
    const LARGE_THRESHOLD = 10 * 1024 * 1024; // 10MB 以上走采样

    if (file.size > LARGE_THRESHOLD) {
      // 超大文件：只读头/中/尾各约 SAMPLE_SIZE 字符
      if (infoEl) infoEl.textContent = `大文件检测（${(file.size / 1024 / 1024).toFixed(1)} MB），正在采样头/中/尾…`;
      const chunkBytes = SAMPLE_SIZE * 3; // 按 3 倍字节数读（中文 UTF-8 约 3 字节/字）
      const blobSlice = File.prototype.slice || File.prototype.mozSlice || File.prototype.webkitSlice;

      // 读取头部
      const readChunk = (start, size) => new Promise((resolve, reject) => {
        const r = new FileReader();
        r.onload = () => resolve(String(r.result || ''));
        r.onerror = reject;
        r.readAsText(blobSlice.call(file, start, Math.min(start + size, file.size)), 'utf-8');
      });

      (async () => {
        try {
          const mid = Math.floor(file.size / 2);
          const tail = Math.max(0, file.size - chunkBytes);
          const [head, middle, end] = await Promise.all([
            readChunk(0, chunkBytes),
            readChunk(mid - Math.floor(chunkBytes / 2), chunkBytes),
            readChunk(tail, chunkBytes),
          ]);
          const text = `${head}\n\n…（此处省略大量正文，以下为作品中部采样）…\n\n${middle}\n\n…（此处省略大量正文，以下为作品尾部采样）…\n\n${end}`;
          if (excerptEl) excerptEl.value = text;
          // 估算原始字符数（中文 UTF-8 约 3 字节/字符）
          const estChars = Math.round(file.size / 3);
          if (infoEl) infoEl.textContent = `已采样 ${file.name}（原始约 ${estChars.toLocaleString()} 字，采样 ${text.length.toLocaleString()} 字）`;
        } catch {
          if (infoEl) infoEl.textContent = '读取失败';
        }
      })();
    } else {
      // 普通文件：完整读取
      if (infoEl) infoEl.textContent = `加载 ${file.name}（${(file.size / 1024).toFixed(1)} KB）…`;
      const reader = new FileReader();
      reader.onload = () => {
        const text = String(reader.result || '');
        if (excerptEl) excerptEl.value = text;
        const charCount = text.length;
        const truncHint = charCount > 60000 ? '（超长文本，蒸馏时会自动截取头/中/尾样本）' : '';
        if (infoEl) infoEl.textContent = `已加载 ${file.name}（${charCount.toLocaleString()} 字）${truncHint}`;
      };
      reader.onerror = () => {
        if (infoEl) infoEl.textContent = '读取失败';
      };
      reader.readAsText(file, 'utf-8');
    }
  });

  distillRunBtn?.addEventListener('click', async () => {
    const author_name = document.getElementById('distill-author-name')?.value?.trim() ?? '';
    if (!author_name) {
      void showAppAlert('请填写作者署名。', '蒸馏作者');
      return;
    }
    const excerpt = document.getElementById('distill-excerpt')?.value?.trim() ?? '';
    if (excerpt.length < 200) {
      void showAppAlert('正文过短：请至少粘贴约 200 字（越长蒸馏越准确）。', '蒸馏作者');
      return;
    }
    const book_title = document.getElementById('distill-book-title')?.value?.trim() ?? '';
    const tags = parseTags(document.getElementById('distill-tags')?.value);
    if (distillRunStatus) distillRunStatus.textContent = '调用模型蒸馏中…';
    distillRunBtn.disabled = true;
    distillSaveSkillBtn && (distillSaveSkillBtn.disabled = true);
    distillWriteMemBtn && (distillWriteMemBtn.disabled = true);
    lastDistillResult = null;
    try {
      const r = await fetchJson('/api/teardown/distill-author', {
        method: 'POST',
        body: JSON.stringify({ excerpt, book_title, author_name, tags, temperature: 0.38 }),
      });
      lastDistillResult = r;
      const displayText = typeof r?.distill_text === 'string' ? r.distill_text : '';
      setOutputMarkdownText(distillOutEl, displayText);
      if (distillSaveSkillBtn) distillSaveSkillBtn.disabled = !r?.skill_content;
      if (distillWriteMemBtn) distillWriteMemBtn.disabled = !displayText;
      // 自动填充 SKILL 文件名
      const skillNameEl = document.getElementById('distill-skill-filename');
      if (skillNameEl && !skillNameEl.value) {
        skillNameEl.value = `作者-${author_name}风格`;
      }
      if (distillRunStatus) distillRunStatus.textContent = displayText ? '蒸馏完成' : '无正文返回';
      // 蒸馏完成后刷新一键全书区的蒸馏作者下拉列表
      void loadDistilledAuthors();
    } catch (e) {
      setOutputMarkdownText(distillOutEl, '');
      if (distillRunStatus) distillRunStatus.textContent = '';
      void showAppAlert(e?.message || String(e), '蒸馏失败');
    } finally {
      distillRunBtn.disabled = false;
    }
  });

  distillSaveSkillBtn?.addEventListener('click', async () => {
    if (!lastDistillResult?.skill_content) {
      void showAppAlert('请先完成蒸馏。', '保存 SKILL');
      return;
    }
    let name = document.getElementById('distill-skill-filename')?.value?.trim() ?? '';
    if (!name) {
      name = `作者-${lastDistillResult.author_name || '蒸馏'}风格`;
    }
    if (distillSaveStatus) distillSaveStatus.textContent = '写入中…';
    distillSaveSkillBtn.disabled = true;
    try {
      await fetchJson('/api/teardown/save-skill', {
        method: 'POST',
        body: JSON.stringify({ filename: name, content: lastDistillResult.skill_content }),
      });
      if (distillSaveStatus) distillSaveStatus.textContent = 'SKILL 已写入知识库，可在写作台刷新并勾选调用。';
      await refreshKbList();
    } catch (e) {
      if (distillSaveStatus) distillSaveStatus.textContent = '';
      void showAppAlert(e?.message || String(e), '保存 SKILL 失败');
    } finally {
      distillSaveSkillBtn.disabled = !lastDistillResult?.skill_content;
    }
  });

  distillWriteMemBtn?.addEventListener('click', async () => {
    if (!lastDistillResult?.distill_text) {
      void showAppAlert('请先完成蒸馏。', '写入记忆');
      return;
    }
    const authorName = lastDistillResult.author_name || '未知作者';
    if (distillSaveStatus) distillSaveStatus.textContent = '写入记忆宫殿中…';
    distillWriteMemBtn.disabled = true;
    try {
      // 提取虚拟作者卡片作为记忆条目
      const cardMatch = lastDistillResult.distill_text.match(
        /【虚拟作者[·.]蒸馏画像】[\s\S]*?(?=\n##|\n---|$)/
      );
      const memBody = cardMatch
        ? cardMatch[0].trim()
        : lastDistillResult.distill_text.slice(0, 3000);
      await fetchJson('/api/teardown/write-memory', {
        method: 'POST',
        body: JSON.stringify({
          room: '风格',
          title: `蒸馏作者：${authorName}`,
          body: memBody,
        }),
      });
      if (distillSaveStatus) distillSaveStatus.textContent = '已写入记忆宫殿「风格」房间。';
    } catch (e) {
      void showAppAlert(e?.message || String(e), '写入记忆失败');
    } finally {
      distillWriteMemBtn.disabled = !lastDistillResult?.distill_text;
    }
  });

  // ==================== 蒸馏作者 · 历史记录 & 合并 ====================
  const distillHistoryList = document.getElementById('distill-history-list');
  const distillHistoryHint = document.getElementById('distill-history-hint');
  const distillMergeBar = document.getElementById('distill-merge-bar');
  const distillMergeCount = document.getElementById('distill-merge-count');
  const distillMergeBtn = document.getElementById('btn-distill-merge');
  const distillHistoryRefresh = document.getElementById('btn-distill-history-refresh');

  // 记录选择状态
  const distillSelectedRecords = new Set();

  function parseTimestamp(ts) {
    const d = new Date(ts * 1000);
    return d.toLocaleString('zh-CN', { month: '2-digit', day: '2-digit', hour: '2-digit', minute: '2-digit' });
  }

  async function refreshDistillHistory() {
    const authorName = document.getElementById('distill-author-name')?.value?.trim() ?? '';
    if (!distillHistoryList) return;
    distillSelectedRecords.clear();
    distillHistoryList.innerHTML = '';

    if (!authorName) {
      if (distillHistoryHint) distillHistoryHint.textContent = '输入作者署名后自动加载历史';
      if (distillMergeBar) distillMergeBar.hidden = true;
      return;
    }

    if (distillHistoryHint) distillHistoryHint.textContent = '加载中…';
    try {
      const r = await fetchJson(`/api/teardown/distill-history?author_name=${encodeURIComponent(authorName)}`);
      const records = r.records || [];
      if (!records.length) {
        distillHistoryList.innerHTML = '';
        if (distillHistoryHint) distillHistoryHint.textContent = '此作者暂无蒸馏记录。蒸馏后会自动保存。';
        if (distillMergeBar) distillMergeBar.hidden = true;
        return;
      }

      if (distillHistoryHint) distillHistoryHint.textContent = '';
      distillHistoryList.innerHTML = '';

      for (const rec of records) {
        const isMerged = !!rec.merged;
        const row = document.createElement('div');
        row.className = 'distill-history-item' + (isMerged ? ' distill-history-item--merged' : '');

        // 合并记录不参与再次合并，用专用标记替代复选框
        if (isMerged) {
          const badge = document.createElement('span');
          badge.className = 'distill-merged-badge';
          badge.textContent = '已合并';
          badge.title = '此为合并蒸馏结果，用作虚拟作者时会优先使用';
          row.appendChild(badge);
        } else {
          const cb = document.createElement('input');
          cb.type = 'checkbox';
          cb.className = 'distill-history-cb';
          cb.value = rec.id;
          cb.addEventListener('change', () => {
            if (cb.checked) distillSelectedRecords.add(rec.id);
            else distillSelectedRecords.delete(rec.id);
            updateDistillMergeBar();
          });
          row.appendChild(cb);
        }

        const label = document.createElement('span');
        label.className = 'distill-history-label';
        label.textContent = `${rec.book_title || '未命名'} · ${parseTimestamp(rec.timestamp)}`;

        const viewBtn = document.createElement('button');
        viewBtn.type = 'button';
        viewBtn.className = 'btn btn-ghost btn-sm';
        viewBtn.textContent = '查看';
        viewBtn.addEventListener('click', async () => {
          try {
            const d = await fetchJson(`/api/teardown/distill-detail?author_name=${encodeURIComponent(authorName)}&record_id=${rec.id}`);
            if (d.text) {
              setOutputMarkdownText(distillOutEl, d.text);
              // 也填充 SKILL
              if (lastDistillResult) {
                lastDistillResult = { ...lastDistillResult, distill_text: d.text };
              }
            }
          } catch (e) {
            void showAppAlert(e?.message || String(e), '查看失败');
          }
        });

        row.appendChild(label);
        row.appendChild(viewBtn);
        distillHistoryList.appendChild(row);
      }

      // 如果只有 1 篇，提示可以蒸馏更多再合并
      if (records.length === 1) {
        if (distillHistoryHint) distillHistoryHint.textContent = '已蒸馏 1 篇。再蒸馏同一作者的其他作品后，可合并为综合画像。';
      } else {
        if (distillHistoryHint) distillHistoryHint.textContent = `已蒸馏 ${records.length} 篇。勾选 ≥2 篇可合并为综合画像。`;
      }
      updateDistillMergeBar();

    } catch {
      distillHistoryList.innerHTML = '';
      if (distillHistoryHint) distillHistoryHint.textContent = '无法加载蒸馏历史';
      if (distillMergeBar) distillMergeBar.hidden = true;
    }
  }

  function updateDistillMergeBar() {
    const count = distillSelectedRecords.size;
    if (distillMergeCount) distillMergeCount.textContent = `已选 ${count} 篇`;
    if (distillMergeBar) distillMergeBar.hidden = false;
    if (distillMergeBtn) distillMergeBtn.disabled = count < 2;
    if (distillMergeBtn) distillMergeBtn.textContent = count < 2 ? '合并蒸馏（需选 ≥2 篇）' : `合并 ${count} 篇蒸馏`;
  }

  distillHistoryRefresh?.addEventListener('click', refreshDistillHistory);

  // 作者名输入变化时自动加载历史
  let distillAuthorTimer = null;
  document.getElementById('distill-author-name')?.addEventListener('input', () => {
    clearTimeout(distillAuthorTimer);
    distillAuthorTimer = setTimeout(refreshDistillHistory, 800);
  });

  // 合并蒸馏按钮
  distillMergeBtn?.addEventListener('click', async () => {
    const authorName = document.getElementById('distill-author-name')?.value?.trim() ?? '';
    if (!authorName || distillSelectedRecords.size < 2) return;
    if (distillSaveStatus) distillSaveStatus.textContent = '合并蒸馏中（调用模型）…';
    distillMergeBtn.disabled = true;
    try {
      const r = await fetchJson('/api/teardown/merge-distill', {
        method: 'POST',
        body: JSON.stringify({
          author_name: authorName,
          record_ids: Array.from(distillSelectedRecords),
          temperature: 0.38,
        }),
      });
      const mergedText = r.merged_text || '';
      if (mergedText) {
        lastDistillResult = {
          ok: true,
          distill_text: mergedText,
          skill_content: r.skill_content || '',
          author_name: authorName,
        };
        setOutputMarkdownText(distillOutEl, mergedText);
        if (distillSaveSkillBtn) distillSaveSkillBtn.disabled = !r.skill_content;
        if (distillWriteMemBtn) distillWriteMemBtn.disabled = false;
        const skillNameEl = document.getElementById('distill-skill-filename');
        if (skillNameEl && !skillNameEl.value) {
          skillNameEl.value = `作者-${authorName}风格`;
        }
        if (distillSaveStatus) distillSaveStatus.textContent = `合并完成（综合自 ${r.merged_count || distillSelectedRecords.size} 篇蒸馏）。可保存 SKILL 或写入记忆。`;
      }
    } catch (e) {
      void showAppAlert(e?.message || String(e), '合并蒸馏失败');
      if (distillSaveStatus) distillSaveStatus.textContent = '';
    } finally {
      distillMergeBtn.disabled = distillSelectedRecords.size < 2;
    }
  });

  // ==================== 经典长篇拆书 ====================
  let lastReportText = '';
  const runBtn = document.getElementById('btn-teardown-run');
  const saveBtn = document.getElementById('btn-teardown-save-kb');
  const outEl = document.getElementById('teardown-output');
  const runStatus = document.getElementById('teardown-run-status');
  const saveStatus = document.getElementById('teardown-save-status');

  runBtn?.addEventListener('click', async () => {
    const excerpt = document.getElementById('teardown-excerpt')?.value?.trim() ?? '';
    if (excerpt.length < 80) {
      void showAppAlert('正文节选过短：请至少粘贴约 80 字再拆书。', '拆书');
      return;
    }
    const book_title = document.getElementById('teardown-title')?.value?.trim() ?? '';
    const mode = document.getElementById('teardown-mode')?.value ?? 'quick';
    const excerpt_note = document.getElementById('teardown-note')?.value?.trim() ?? '';
    if (runStatus) runStatus.textContent = '调用模型中…';
    runBtn.disabled = true;
    saveBtn && (saveBtn.disabled = true);
    lastReportText = '';
    try {
      const r = await fetchJson('/api/teardown/novel', {
        method: 'POST',
        body: JSON.stringify({
          excerpt,
          book_title,
          mode,
          excerpt_note,
          temperature: 0.35
        })
      });
      lastReportText = typeof r?.text === 'string' ? r.text : '';
      setOutputMarkdownText(outEl, lastReportText);
      if (saveBtn) saveBtn.disabled = !lastReportText;
      if (runStatus) runStatus.textContent = lastReportText ? '完成' : '无正文返回';
    } catch (e) {
      setOutputMarkdownText(outEl, '');
      if (runStatus) runStatus.textContent = '';
      void showAppAlert(e?.message || String(e), '拆书失败');
    } finally {
      runBtn.disabled = false;
    }
  });

  saveBtn?.addEventListener('click', async () => {
    if (!lastReportText) {
      void showAppAlert('请先生成拆书报告。', '保存');
      return;
    }
    let name = document.getElementById('teardown-kb-filename')?.value?.trim() ?? '';
    if (!name) {
      const t = document.getElementById('teardown-title')?.value?.trim() || '拆书';
      name = `拆书-${t.replace(/[\\/:*?"<>|]+/g, '_').slice(0, 60)}`;
    }
    if (saveStatus) saveStatus.textContent = '写入中…';
    saveBtn.disabled = true;
    try {
      await fetchJson('/api/kb/write', {
        method: 'POST',
        body: JSON.stringify({ filename: name, content: lastReportText })
      });
      if (saveStatus) saveStatus.textContent = '已写入知识库，可在写作台刷新 kb 列表并勾选。';
      await refreshKbList();
    } catch (e) {
      if (saveStatus) saveStatus.textContent = '';
      void showAppAlert(e?.message || String(e), '保存失败');
    } finally {
      saveBtn.disabled = !lastReportText;
    }
  });
}

async function refreshSeriesList() {
  const ids = ['series-continue-select', 'series-rewrite-select'];
  const selects = ids.map((id) => document.getElementById(id)).filter(Boolean);
  if (!selects.length) return;
  const saved = selects.map((s) => s.value);

  const builder = document.createElement('select');
  const ph = document.createElement('option');
  ph.value = '';
  ph.textContent = '—— 选择书本或旧书系 ——';
  builder.appendChild(ph);
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
      builder.appendChild(og);
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
      builder.appendChild(og);
    }
  } catch (e) {
    console.warn('series list', e);
  }

  const html = builder.innerHTML;
  selects.forEach((sel, i) => {
    sel.innerHTML = html;
    const cur = saved[i];
    if (cur && Array.from(sel.options).some((op) => op.value === cur)) {
      sel.value = cur;
    }
  });
}

async function refreshHealth() {
  const el = document.getElementById('backend-status');
  if (!el) return null;
  try {
    const h = await fetchJson('/api/health');
    const ds = h.deepseek_configured ? '已配置 Key' : '未配置 Key';
    const tw = h.teardown_framework === false ? ' · 拆书框架缺失' : '';
    el.textContent = `后端正常 · ${ds}${tw}`;
    el.className = 'status-pill is-ok';
    const pathsEl = document.getElementById('paths-display');
    if (pathsEl && h.books_root) {
      const base = (pathsEl.dataset.basePaths || pathsEl.textContent || '').split('\n\n书本目录')[0].trim();
      pathsEl.dataset.basePaths = base;
      let extra = `\n\n书本目录：\n${h.books_root}`;
      if (h.analytics_root) extra += `\n\n分析目录：\n${h.analytics_root}`;
      if (h.snapshots_dir) extra += `\n\n快照目录：\n${h.snapshots_dir}`;
      pathsEl.textContent = `${base}${extra}`;
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


function novelThemePayload() {
  const box = document.getElementById('theme-tags');
  if (!box) {
    return { theme_id: 'general', theme_ids: ['general'] };
  }
  const checked = [...box.querySelectorAll('input.theme-tag-cb:checked')]
    .map((i) => i.value)
    .filter(Boolean);
  const ids = checked.length ? checked : ['general'];
  return { theme_id: ids[0], theme_ids: ids };
}

function ensureMinimumThemeChecked(box) {
  if (!box) return;
  const n = box.querySelectorAll('input.theme-tag-cb:checked').length;
  if (n === 0) {
    const g = box.querySelector('input.theme-tag-cb[value="general"]');
    if (g) g.checked = true;
  }
}

function syncThemeCascadeNavActive() {
  const panel = document.getElementById('theme-cascade-panel');
  if (!panel) return;
  panel.querySelectorAll('.theme-cat-btn').forEach((btn) => {
    btn.classList.toggle('is-active', btn.dataset.cat === activeThemeSidebarKey);
  });
}

function mountThemeCascadeLayers() {
  if (themeCascadeLayersMounted) return;
  const backdrop = document.getElementById('theme-cascade-backdrop');
  const panel = document.getElementById('theme-cascade-panel');
  if (!backdrop || !panel) return;
  document.body.appendChild(backdrop);
  document.body.appendChild(panel);
  themeCascadeLayersMounted = true;
}

function positionThemeCascadePanel() {
  const trig = document.getElementById('theme-cascade-trigger');
  const panel = document.getElementById('theme-cascade-panel');
  if (!trig || !panel || panel.hidden) return;
  const r = trig.getBoundingClientRect();
  const margin = 10;
  const vw = window.innerWidth;
  const vh = window.innerHeight;
  const minW = 288;
  const width = Math.min(Math.max(minW, r.width), vw - margin * 2);

  let left = Math.round(r.left + (r.width - width) / 2);
  left = Math.max(margin, Math.min(left, vw - width - margin));

  panel.style.boxSizing = 'border-box';
  panel.style.width = `${width}px`;
  panel.style.left = `${left}px`;

  const belowTop = Math.round(r.bottom + 6);
  panel.style.top = `${belowTop}px`;
  panel.style.maxHeight = `${Math.floor(Math.max(220, vh - belowTop - margin))}px`;

  requestAnimationFrame(() => {
    if (panel.hidden) return;
    const ph = panel.getBoundingClientRect().height;
    const spaceBelow = vh - r.bottom - margin;
    const spaceAbove = r.top - margin;
    let top = belowTop;
    if (ph > spaceBelow + 12 && spaceAbove > spaceBelow) {
      top = Math.max(margin, Math.round(r.top - ph - 6));
    }
    panel.style.top = `${top}px`;
    panel.style.maxHeight = `${Math.floor(vh - top - margin)}px`;
  });
}

function scheduleThemeCascadePanelPosition() {
  const panel = document.getElementById('theme-cascade-panel');
  if (!panel || panel.hidden) return;
  if (themeCascadePositionRaf) cancelAnimationFrame(themeCascadePositionRaf);
  themeCascadePositionRaf = requestAnimationFrame(() => {
    themeCascadePositionRaf = 0;
    positionThemeCascadePanel();
  });
}

function refreshThemeCascadePreview() {
  const wrap = document.getElementById('theme-cascade-preview');
  const panel = document.getElementById('theme-cascade-panel');
  if (!wrap || !panel || panel.hidden) return;

  wrap.innerHTML = '';
  let titleText = '';
  let bodyText = '';
  if (themeCascadePreviewHoverId) {
    const t = themesCache.find((x) => x.id === themeCascadePreviewHoverId);
    titleText = t?.label || themeCascadePreviewHoverId;
    bodyText =
      (t?.description || '').trim() ||
      '暂无简介；仍可与其它题材叠加，系统会将已选题材的提示约束合并发送。';
  } else {
    const { theme_ids } = novelThemePayload();
    const labels = theme_ids.map((tid) => {
      const tt = themesCache.find((x) => x.id === tid);
      return tt?.label || tid;
    });
    titleText = `已选 ${theme_ids.length} 项`;
    const line =
      labels.length <= 10 ? labels.join('、') : `${labels.slice(0, 10).join('、')} 等`;
    bodyText = `${line}。

悬停某一标签可看该题材的写作要点。「脑洞程度」在下方单独一项里调节，与题材勾选无关。`;
  }
  const strong = document.createElement('strong');
  strong.textContent = titleText;
  const span = document.createElement('span');
  span.className = 'theme-cascade-preview-body';
  span.style.whiteSpace = 'pre-line';
  span.style.display = 'block';
  span.style.marginTop = '0.375rem';
  span.textContent = bodyText;
  wrap.appendChild(strong);
  wrap.appendChild(span);
}

function renderThemeCascadeGrid(resetHover = false) {
  const grid = document.getElementById('theme-cascade-grid');
  const box = document.getElementById('theme-tags');
  if (!grid || !box) return;
  const items = themesCache
    .filter((t) => sidebarKeyForThemeRow(t) === activeThemeSidebarKey)
    .slice()
    .sort((a, b) =>
      String(a.label || a.id || '').localeCompare(String(b.label || b.id || ''), 'zh')
    );
  const frag = document.createDocumentFragment();
  for (const t of items) {
    const tid = String(t.id);
    const btn = document.createElement('button');
    btn.type = 'button';
    btn.className = 'theme-tag-tile';
    btn.dataset.themeId = tid;
    btn.textContent = String(t.label || tid);
    const cb = document.getElementById(`theme-sync-${slug(tid)}`);
    if (cb?.checked) btn.classList.add('is-selected');
    frag.appendChild(btn);
  }
  if (resetHover) themeCascadePreviewHoverId = null;
  else if (themeCascadePreviewHoverId) {
    const stillHere = items.some((t) => String(t.id) === themeCascadePreviewHoverId);
    if (!stillHere) themeCascadePreviewHoverId = null;
  }
  grid.innerHTML = '';
  grid.appendChild(frag);
  refreshThemeCascadePreview();
}

function syncThemeCascadeChips() {
  const wrap = document.getElementById('theme-cascade-chips');
  const root = document.getElementById('theme-cascade');
  if (!wrap || !root) return;
  const { theme_ids } = novelThemePayload();
  wrap.innerHTML = '';
  for (const tid of theme_ids) {
    const t = themesCache.find((x) => x.id === tid);
    const lab = String(t?.label || tid || '');
    const chip = document.createElement('span');
    chip.className = 'theme-chip';
    const labSpan = document.createElement('span');
    labSpan.className = 'theme-chip-label';
    labSpan.textContent = lab;
    const xbtn = document.createElement('button');
    xbtn.type = 'button';
    xbtn.className = 'theme-chip-x';
    xbtn.textContent = '×';
    xbtn.dataset.themeId = tid;
    xbtn.setAttribute('aria-label', `移除 ${lab}`);
    chip.appendChild(labSpan);
    chip.appendChild(xbtn);
    wrap.appendChild(chip);
  }
  root.classList.toggle('has-selection', theme_ids.length > 0);
}

function setThemeCascadePanelOpen(open) {
  mountThemeCascadeLayers();
  const panel = document.getElementById('theme-cascade-panel');
  const backdrop = document.getElementById('theme-cascade-backdrop');
  const trig = document.getElementById('theme-cascade-trigger');
  const rootEl = document.getElementById('theme-cascade');
  const field = document.querySelector('.novel-theme-field');
  if (!panel || !trig || !rootEl) return;

  if (!open) {
    themeCascadePreviewHoverId = null;
  }

  if (backdrop) {
    backdrop.hidden = !open;
    backdrop.setAttribute('aria-hidden', open ? 'false' : 'true');
  }

  panel.hidden = !open;
  trig.setAttribute('aria-expanded', open ? 'true' : 'false');
  rootEl.classList.toggle('is-open', open);
  field?.classList.toggle('is-picker-open', open);

  if (open) {
    syncThemeCascadeNavActive();
    renderThemeCascadeGrid(true);
    scheduleThemeCascadePanelPosition();
    refreshThemeCascadePreview();
  }
}

function toggleThemeCascadePanel() {
  const panel = document.getElementById('theme-cascade-panel');
  if (!panel) return;
  setThemeCascadePanelOpen(!!panel.hidden);
}

function bindThemeCascadeOnce() {
  if (themeCascadeListenersBound) return;
  mountThemeCascadeLayers();
  const root = document.getElementById('theme-cascade');
  const trig = document.getElementById('theme-cascade-trigger');
  const panel = document.getElementById('theme-cascade-panel');
  const sidebar = panel?.querySelector('.theme-cascade-sidebar');
  const box = document.getElementById('theme-tags');
  const chipsWrap = document.getElementById('theme-cascade-chips');
  const backdrop = document.getElementById('theme-cascade-backdrop');
  const grid = document.getElementById('theme-cascade-grid');
  if (!root || !trig || !panel || !sidebar || !box || !chipsWrap || !backdrop) return;
  themeCascadeListenersBound = true;
  sidebar.innerHTML = '';
  for (const [key, lab] of THEME_SIDEBAR_ROWS) {
    const b = document.createElement('button');
    b.type = 'button';
    b.className = 'theme-cat-btn';
    b.dataset.cat = key;
    b.innerHTML = `<span>${escapeHtml(lab)}</span><span class="chev" aria-hidden="true">›</span>`;
    sidebar.appendChild(b);
  }
  sidebar.addEventListener('click', (e) => {
    const catBtn = e.target.closest('.theme-cat-btn');
    if (!catBtn) return;
    activeThemeSidebarKey = catBtn.dataset.cat || 'main';
    syncThemeCascadeNavActive();
    renderThemeCascadeGrid(true);
    scheduleThemeCascadePanelPosition();
  });
  panel.addEventListener('click', (e) => {
    const tile = e.target.closest('.theme-tag-tile');
    if (!tile || !tile.dataset.themeId) return;
    const cb = document.getElementById(`theme-sync-${slug(tile.dataset.themeId)}`);
    if (!cb) return;
    cb.checked = !cb.checked;
    cb.dispatchEvent(new Event('change', { bubbles: true }));
  });
  trig.addEventListener('click', () => toggleThemeCascadePanel());
  chipsWrap.addEventListener('click', (e) => {
    e.stopPropagation();
    const x = e.target.closest('.theme-chip-x');
    if (!x || !x.dataset.themeId) return;
    e.preventDefault();
    const cb = document.getElementById(`theme-sync-${slug(x.dataset.themeId)}`);
    if (cb) {
      cb.checked = false;
      cb.dispatchEvent(new Event('change', { bubbles: true }));
    }
  });

  grid?.addEventListener('mouseover', (e) => {
    const tile = e.target.closest('.theme-tag-tile');
    const id = tile?.dataset?.themeId;
    if (!id) return;
    themeCascadePreviewHoverId = id;
    refreshThemeCascadePreview();
  });
  grid?.addEventListener('mouseleave', () => {
    const ae = document.activeElement;
    if (ae?.closest?.('#theme-cascade-grid') && ae.matches('.theme-tag-tile')) return;
    themeCascadePreviewHoverId = null;
    refreshThemeCascadePreview();
  });
  grid?.addEventListener('focusin', (e) => {
    const tile = e.target.closest('.theme-tag-tile');
    if (tile?.dataset.themeId) {
      themeCascadePreviewHoverId = tile.dataset.themeId;
      refreshThemeCascadePreview();
    }
  });

  panel.addEventListener('focusout', (e) => {
    if (!root.classList.contains('is-open')) return;
    const next = e.relatedTarget;
    if (next && panel.contains(next)) return;
    themeCascadePreviewHoverId = null;
    refreshThemeCascadePreview();
  });

  /** 打开时：点在面板内或主题输入条上保持；其余任意处关闭（含半透明遮罩后方视觉上「透明」区域） */
  function themePickerShouldStayOpenForTarget(t) {
    if (!t || !(t instanceof Element)) return false;
    if (panel.contains(t)) return true;
    if (t.closest('#theme-cascade')) return true;
    return false;
  }

  function dismissThemePickerIfOutside(e) {
    if (!root.classList.contains('is-open') || panel.hidden) return;
    if (themePickerShouldStayOpenForTarget(e.target)) return;
    setThemeCascadePanelOpen(false);
  }

  document.addEventListener('mousedown', dismissThemePickerIfOutside, true);
  document.addEventListener('touchstart', dismissThemePickerIfOutside, { capture: true, passive: true });

  box.addEventListener('change', () => {
    ensureMinimumThemeChecked(box);
    syncThemeCascadeChips();
    if (!panel.hidden) {
      renderThemeCascadeGrid(false);
    }
    updateThemeDesc();
  });
  window.addEventListener('resize', scheduleThemeCascadePanelPosition);
  window.addEventListener('scroll', scheduleThemeCascadePanelPosition, true);

  document.addEventListener('keydown', (e) => {
    if (e.key !== 'Escape') return;
    if (root.classList.contains('is-open')) setThemeCascadePanelOpen(false);
  });
}

async function refreshThemes() {
  const box = document.getElementById('theme-tags');
  if (!box) return;
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
  bindThemeCascadeOnce();
  box.innerHTML = '';
  for (const t of themesCache) {
    const tid = String(t.id);
    const inp = document.createElement('input');
    inp.type = 'checkbox';
    inp.className = 'theme-tag-cb';
    inp.value = tid;
    inp.name = 'novel_theme';
    inp.id = `theme-sync-${slug(tid)}`;
    inp.checked = tid === 'general';
    box.appendChild(inp);
  }
  activeThemeSidebarKey = 'main';
  setThemeCascadePanelOpen(false);
  syncThemeCascadeNavActive();
  syncThemeCascadeChips();
  renderThemeCascadeGrid(true);
  updateThemeDesc();
}

function updateThemeDesc() {
  const el = document.getElementById('theme-desc');
  if (!el) return;
  const { theme_ids } = novelThemePayload();
  const bits = [];
  for (const tid of theme_ids) {
    const t = themesCache.find((x) => x.id === tid);
    const d = t?.description;
    if (d) bits.push(`${t.label || tid} — ${d}`);
    else if (t?.label) bits.push(t.label);
  }
  el.textContent = bits.join(' ');
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

/** 加载蒸馏作者列表到所有虚拟作者下拉框（一键全书/续写/重写） */
async function loadDistilledAuthors() {
  const selectors = [
    { id: 'pipeline-distilled-author', defaultText: null },
    { id: 'continue-distilled-author', defaultText: null },
    { id: 'rewrite-distilled-author', defaultText: null },
  ];
  const sels = selectors
    .map((s) => ({ ...s, el: document.getElementById(s.id) }))
    .filter((s) => s.el);
  if (!sels.length) return;

  // 保存当前选择
  const currents = sels.map((s) => s.el.value);
  // 清除除第一项以外的所有选项
  for (const s of sels) {
    while (s.el.options.length > 1) s.el.remove(1);
  }

  try {
    const resp = await fetchJson('/api/teardown/distill-authors');
    const list = resp?.authors || resp;
    if (Array.isArray(list)) {
      for (const item of list) {
        const name = item.author_name || '';
        if (!name) continue;
        const count = item.count || 0;
        const books = (item.books || []).slice(0, 3).join('、');
        const label = `${name}（${count} 篇${books ? '：' + books : ''}）`;
        for (let i = 0; i < sels.length; i++) {
          const opt = document.createElement('option');
          opt.value = name;
          opt.textContent = label;
          sels[i].el.appendChild(opt);
        }
      }
    }
    // 恢复之前的选择
    for (let i = 0; i < sels.length; i++) {
      if (currents[i]) sels[i].el.value = currents[i];
    }
  } catch {
    // 静默失败
  }
}

document.addEventListener('DOMContentLoaded', async () => {
  initUiTheme();
  initIdeationSlider();
  initTabs();
  initTeardownPanel();
  loadDistilledAuthors();

  document.getElementById('btn-refresh-distilled-authors')?.addEventListener('click', () => {
    void loadDistilledAuthors();
  });

  document.getElementById('btn-analytics-refresh')?.addEventListener('click', () => {
    void refreshAnalyticsPanel();
  });

  document.getElementById('btn-analytics-supervisor-report')?.addEventListener('click', async () => {
    const bid = document.getElementById('analytics-supervisor-book')?.value?.trim();
    const st = document.getElementById('analytics-supervisor-status');
    if (!bid) {
      void showAppAlert('请先在列表中选择一本书。', '书本监督');
      return;
    }
    if (st) st.textContent = '正在拉取完整性报告…';
    try {
      const rep = await fetchJson(`/api/books/${encodeURIComponent(bid)}/supervisor/report`);
      showAnalyticsSupervisorPayload({ integrity: rep, meta_review: null, metaLine: `书本 ${bid}` });
      if (st) {
        st.textContent = rep?.integrity_ok
          ? '结构检查通过（无策划缺章与序号空洞）'
          : `已生成报告：${(rep?.warnings || []).length} 条提示，请看右侧`;
      }
    } catch (e) {
      if (st) st.textContent = '';
      void showAppAlert(e?.message || String(e), '完整性报告失败');
    }
  });

  document.getElementById('btn-analytics-supervisor-review')?.addEventListener('click', async () => {
    const bid = document.getElementById('analytics-supervisor-book')?.value?.trim();
    const st = document.getElementById('analytics-supervisor-status');
    const save = Boolean(document.getElementById('cb-analytics-supervisor-save')?.checked);
    if (!bid) {
      void showAppAlert('请先在列表中选择一本书。', '书本监督');
      return;
    }
    if (st) st.textContent = '监督审查中（调用模型）…';
    try {
      const r = await fetchJson(`/api/books/${encodeURIComponent(bid)}/supervisor/review`, {
        method: 'POST',
        body: JSON.stringify({ max_run_lines: 40, save_to_analytics: save })
      });
      showAnalyticsSupervisorPayload({ ...r, metaLine: `书本 ${bid}` });
      if (st) {
        st.textContent = r?.saved?.rel_path
          ? `已写入 ${r.saved.rel_path}`
          : save
            ? '完成（应已保存，若目录不可写请见报错）'
            : '完成（未勾选保存则未写入 reviews）';
      }
      if (r?.saved?.rel_path) await refreshAnalyticsFileListOnly();
    } catch (e) {
      if (st) st.textContent = '';
      void showAppAlert(e?.message || String(e), '监督审查失败');
    }
  });

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
      let t = `UserData:\n${p.userData}\n下载:\n${p.downloads}`;
      if (p.snapshotRoot) t += `\n快照目录:\n${p.snapshotRoot}`;
      if (p.analyticsRoot) t += `\n分析目录:\n${p.analyticsRoot}`;
      pathsEl.textContent = t;
    } catch (e) {
      console.error(e);
    }
  }

  if (window.aiWriter?.loadSettings) {
    const s = await window.aiWriter.loadSettings();
    document.getElementById('api-key').value = s.deepseekApiKey || '';
    document.getElementById('model-id').value = s.deepseekModel || 'deepseek-v4-flash';
    const br = document.getElementById('books-root-path');
    if (br) br.value = s.booksRoot || '';
    const snapCb = document.getElementById('cb-snapshot-agent');
    if (snapCb) snapCb.checked = Boolean(s.snapshotAgentEnabled);
    const snapUrl = document.getElementById('snapshot-page-url');
    if (snapUrl) snapUrl.value = s.snapshotPageUrl || '';
    const selTa = document.getElementById('metrics-dom-selectors');
    if (selTa) {
      try {
        selTa.value = JSON.stringify(s.metricsDomSelectors || [], null, 2);
      } catch {
        selTa.value = '[]';
      }
    }
  }

  async function refreshSnapshotRailHint() {
    const el = document.getElementById('snapshot-rail-hint');
    if (!el || !window.aiWriter?.getSnapshotInfo) return;
    try {
      const info = await window.aiWriter.getSnapshotInfo();
      const logPath = info.metricsDomJsonl || info.snapshotRoot || '（未解析到路径）';
      const slots = (info.todaySlots || []).join(', ') || '无';
      const n = info.metricsDomSelectorCount ?? 0;
      el.textContent = `DOM 记录: ${logPath} · 自定义选择器 ${n} 条 · 今日已跑: ${slots}`;
    } catch {
      el.textContent = '';
    }
  }
  await refreshSnapshotRailHint();

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

  document.getElementById('btn-refresh-series')?.addEventListener('click', () => {
    refreshSeriesList();
  });

  document.getElementById('btn-pipeline-full')?.addEventListener('click', async () => {
    const title = document.getElementById('solo-title')?.value?.trim();
    if (!title) {
      void showAppAlert('请先填写题目 / 书名 / 灵感短语。', '缺少题目');
      return;
    }
    const MAX_PIPELINE_RUN = 1500;
    const MAX_PLANNED_TOTAL = 5000;
    const rawN = parseInt(String(document.getElementById('solo-chapters')?.value || '8'), 10);
    const maxChapters = Number.isFinite(rawN) ? Math.min(MAX_PIPELINE_RUN, Math.max(3, rawN)) : 8;
    const rawPlanned = String(document.getElementById('solo-planned-total')?.value || '').trim();
    let plannedTotalChapters;
    if (rawPlanned !== '') {
      const p = parseInt(rawPlanned, 10);
      if (Number.isFinite(p)) {
        plannedTotalChapters = Math.min(MAX_PLANNED_TOTAL, Math.max(3, p));
      }
    }
    const lengthScale = document.getElementById('solo-length')?.value || 'medium';
    const protagonistGender = document.getElementById('solo-gender')?.value || 'any';
    const btn = document.getElementById('btn-pipeline-full');
    const logEl = document.getElementById('pipeline-log');
    const gs = document.getElementById('gen-status');
    const prog = document.getElementById('pipeline-progress');
    const progBar = document.getElementById('pipeline-progress-bar');
    const progLabel = document.getElementById('pipeline-progress-label');
    const progEta = document.getElementById('pipeline-progress-eta');
    if (plannedTotalChapters !== undefined && plannedTotalChapters < maxChapters) {
      void showAppAlert('预定总章数须大于或等于本轮生成章数。', '参数无效');
      return;
    }
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
    const bookNote = document.getElementById('solo-book-note')?.value?.trim();
    const payload = {
      title,
      ...novelThemePayload(),
      ideation_level: readIdeationLevel(),
      max_chapters: maxChapters,
      length_scale: lengthScale,
      protagonist_gender: protagonistGender,
      use_long_memory: document.getElementById('cb-pipeline-memory')?.checked ?? true,
      kb_names: selectedKbFiles(),
      agent_profile: document.getElementById('pipeline-agent-profile')?.value || 'fast',
      run_reader_test: document.getElementById('pipeline-reader-test')?.checked ?? false,
      run_reader_driven_revision: document.getElementById('pipeline-reader-revision')?.checked ?? true,
      live_supervisor: document.getElementById('pipeline-live-supervisor')?.checked ?? false,
      final_supervisor: document.getElementById('pipeline-final-supervisor')?.checked ?? false,
      foreshadowing_sync_after_chapter: document.getElementById('pipeline-foreshadow-sync')?.checked ?? false,
      memory_episodic_keep_last: (() => {
        const v = parseInt(String(document.getElementById('pipeline-episodic-keep')?.value || '0'), 10);
        if (!Number.isFinite(v) || v <= 0) return null;
        return Math.min(500, v);
      })()
    };
    if (bookNote) {
      payload.user_book_note = bookNote;
    }
    if (plannedTotalChapters !== undefined) {
      payload.planned_total_chapters = plannedTotalChapters;
    }
    const distilledAuthorVal = document.getElementById('pipeline-distilled-author')?.value?.trim();
    if (distilledAuthorVal) {
      payload.distilled_author_name = distilledAuthorVal;
    }
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
          if (ev.event === 'supervisor_chapter' && logEl) {
            const sum = ev.review?.summary || ev.error || '';
            const line = `[监督·第${ev.index}章] ${sum}\n`;
            logEl.textContent += line;
          }
          if (ev.event === 'supervisor_final' && logEl) {
            const mr = ev.meta_review;
            const sum = mr?.summary || ev.error || '';
            logEl.textContent += `[监督·全书] ${sum}\n`;
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
      if (data.virtual_author) {
        const a = data.virtual_author;
        lines.push(
          '',
          '本书虚拟作者（叙事滤光，已写入记忆宫殿）：',
          `${a.gender || ''}，${a.age != null ? `${a.age}岁` : ''}，${a.city || ''}，${a.profession || ''}`,
          String(a.card || '').slice(0, 1200)
        );
      }
      if (data.user_book_note) {
        const u = String(data.user_book_note);
        lines.push('', '全书项目说明（摘要）：', u.length > 600 ? `${u.slice(0, 600)}…` : u);
      }
      const sf = data.supervisor_final;
      if (sf && !sf.error && sf.meta_review) {
        const mr = sf.meta_review;
        lines.push('', '—— 总监督（元审查）——', `健康分：${mr.health_score ?? '—'}`, `总评：${mr.summary || ''}`);
        const hints = mr.prompt_iteration_hints;
        if (Array.isArray(hints) && hints.length) {
          lines.push('提示词/迭代方向：', ...hints.map((h) => `  · ${h}`));
        }
      } else if (sf && sf.error) {
        lines.push('', '总监督未完成：', sf.error);
      }
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
      const cap = document.getElementById('continue-agent-profile')?.value || 'fast';
      const hint =
        cap === 'full'
          ? '续写中：要点/标题（如需）→ 完整多智能体链（每章多次 API）…\n'
          : '续写中：先生成章要点，再写正文（约 2 次 API）…\n';
      logEl.textContent = hint;
    }
    if (gs) gs.textContent = '续写进行中…';
    try {
      const basePayload = {
        ...novelThemePayload(),
        ideation_level: readIdeationLevel(),
        use_long_memory: document.getElementById('cb-continue-memory')?.checked ?? true,
        kb_names: selectedKbFiles(),
        agent_profile: document.getElementById('continue-agent-profile')?.value || 'fast',
        run_reader_test: document.getElementById('continue-reader-test')?.checked ?? false,
        run_reader_driven_revision: document.getElementById('continue-reader-revision')?.checked ?? true,
        live_supervisor: document.getElementById('continue-live-supervisor')?.checked ?? false,
        final_supervisor: document.getElementById('continue-final-supervisor')?.checked ?? false,
        continuation_arc_plan: document.getElementById('continue-arc-plan')?.checked ?? true,
        foreshadowing_sync_after_chapter: document.getElementById('continue-foreshadow-sync')?.checked ?? true,
        memory_episodic_keep_last: Math.min(
          500,
          Math.max(0, parseInt(String(document.getElementById('continue-episodic-keep')?.value || '48'), 10) || 0)
        )
      };
      const MAX_CONTINUE_CHAPTERS = 500;
      const cc = parseInt(String(document.getElementById('continue-chapter-count')?.value || '1'), 10);
      const chapterCount = Number.isFinite(cc)
        ? Math.min(MAX_CONTINUE_CHAPTERS, Math.max(1, cc))
        : 1;
      let payload = { ...basePayload, chapter_count: chapterCount };
      const continueDistilledAuthor = document.getElementById('continue-distilled-author')?.value?.trim();
      if (continueDistilledAuthor) payload.distilled_author_name = continueDistilledAuthor;
      if (raw.startsWith('book:')) {
        payload.book_id = raw.slice(5);
      } else if (raw.startsWith('legacy:')) {
        payload.series_prefix = raw.slice(7);
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
        let text = '';
        if (data.chapters && Array.isArray(data.chapters)) {
          text = `—— 续写完成 ${data.chapters_written || data.chapters.length} 章 ——\n${data.chapters.map((c) => `第 ${c.chapter_index} 章 ${c.chapter_title || ''} → ${c.saved_file}`).join('\n')}\n书名：${data.book_title || ''}`;
        } else {
          text = `—— 续写完成 ——\n${data.saved_file}\n第 ${data.chapter_index} 章 ${data.chapter_title || ''}\n书名：${data.book_title}${data.book_id ? `\n书本 ID：${data.book_id}` : ''}`;
        }
        const ls = data.live_supervisor;
        if (Array.isArray(ls)) {
          for (const row of ls) {
            const sum = row.review?.summary || row.error || '';
            text += `\n[监督·第${row.chapter}章] ${sum}`;
          }
        }
        const sf = data.supervisor_final;
        if (sf && !sf.error && sf.meta_review) {
          const mr = sf.meta_review;
          text += `\n\n—— 总监督 ——\n健康分：${mr.health_score ?? '—'}\n${mr.summary || ''}`;
        } else if (sf && sf.error) {
          text += `\n\n总监督未完成：${sf.error}`;
        }
        const arc = data.continuation_arc;
        if (arc && (arc.arc_notes || (arc.updated_indices && arc.updated_indices.length))) {
          text += `\n\n[中观续写规划] ${arc.arc_notes || ''}`;
          if (Array.isArray(arc.updated_indices) && arc.updated_indices.length) {
            text += `\n已写回 plan 的章序：${arc.updated_indices.join('、')}`;
          }
        }
        logEl.textContent = text;
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

  document.getElementById('btn-rewrite-chapter')?.addEventListener('click', async () => {
    const raw = document.getElementById('series-rewrite-select')?.value?.trim();
    if (!raw || !raw.startsWith('book:')) {
      void showAppAlert('请先点「刷新列表」，在「重写已有章节」里选择一本新书库书本（非旧 out 书系）。', '请选择书本');
      return;
    }
    const logEl = document.getElementById('rewrite-log');
    const gs = document.getElementById('gen-status');
    const btn = document.getElementById('btn-rewrite-chapter');
    if (btn) btn.disabled = true;
    if (logEl) {
      logEl.hidden = false;
      logEl.textContent = '重写中：按 plan 要点覆盖该章正文…\n';
    }
    if (gs) gs.textContent = '重写章节中…';
    try {
      const rawIdx = document.getElementById('rewrite-chapter-index')?.value?.trim();
      let chapter_index = null;
      if (rawIdx) {
        const n = parseInt(String(rawIdx), 10);
        if (!Number.isFinite(n) || n < 1) {
          void showAppAlert('章号须为正整数，或留空以重写末章。', '章号无效');
          return;
        }
        chapter_index = n;
      }
      const note = document.getElementById('rewrite-author-note')?.value?.trim() || '';
      const payload = {
        book_id: raw.slice(5),
        chapter_index,
        ...novelThemePayload(),
        ideation_level: readIdeationLevel(),
        use_long_memory: document.getElementById('cb-rewrite-memory')?.checked ?? true,
        kb_names: selectedKbFiles(),
        agent_profile: document.getElementById('rewrite-agent-profile')?.value || 'fast',
        run_reader_test: document.getElementById('rewrite-reader-test')?.checked ?? false,
        run_reader_driven_revision: document.getElementById('rewrite-reader-revision')?.checked ?? true,
        live_supervisor: document.getElementById('rewrite-live-supervisor')?.checked ?? false,
        ...(note ? { rewrite_author_note: note } : {})
      };
      const rewriteDistilledAuthor = document.getElementById('rewrite-distilled-author')?.value?.trim();
      if (rewriteDistilledAuthor) payload.distilled_author_name = rewriteDistilledAuthor;
      const data = await fetchJson('/api/pipeline/rewrite-chapter', {
        method: 'POST',
        body: JSON.stringify(payload)
      });
      if (logEl) {
        let text = `—— 重写完成 ——\n第 ${data.chapter_index} 章 ${data.chapter_title || ''}\n${data.saved_file || ''}\n书名：${data.book_title || ''}`;
        const ls = data.agent_log;
        if (ls && ls.steps && Array.isArray(ls.steps)) {
          text += `\n编排：${ls.profile || ''}（${ls.steps.map((s) => s.agent).join(' → ')}）`;
        }
        logEl.textContent = text;
      }
      if (gs) gs.textContent = '重写已保存，可在书库中阅读。';
      await refreshSeriesList();
      await refreshMemBookOptions();
      await refreshReaderBooks(true);
    } catch (e) {
      if (logEl) logEl.textContent = `失败：${e.message}`;
      if (gs) gs.textContent = '';
      void showAppAlert(e.message || String(e), '重写失败');
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
      let metricsDomSelectors = [];
      const rawSel = document.getElementById('metrics-dom-selectors')?.value?.trim() || '';
      if (rawSel) {
        let parsed;
        try {
          parsed = JSON.parse(rawSel);
        } catch {
          void showAppAlert('「自定义 CSS 选择器」须为合法 JSON 数组。', 'JSON 无效');
          return;
        }
        if (!Array.isArray(parsed)) {
          void showAppAlert('「自定义 CSS 选择器」须为 JSON 数组，例如 [{ "key": "x", "selector": ".y" }]。', '格式错误');
          return;
        }
        metricsDomSelectors = parsed.filter(
          (x) => x && typeof x.key === 'string' && typeof x.selector === 'string'
        );
      }
      await window.aiWriter.saveSettings({
        deepseekApiKey: document.getElementById('api-key').value.trim(),
        deepseekModel: document.getElementById('model-id').value.trim() || 'deepseek-v4-flash',
        booksRoot: document.getElementById('books-root-path')?.value?.trim() || '',
        snapshotAgentEnabled: document.getElementById('cb-snapshot-agent')?.checked ?? false,
        snapshotPageUrl: document.getElementById('snapshot-page-url')?.value?.trim() || '',
        metricsDomSelectors
      });
      await refreshHealth();
      await refreshThemes();
      await refreshReaderShell();
      await refreshSnapshotRailHint();
      if (pathsEl && window.aiWriter?.getPaths) {
        try {
          const p = await window.aiWriter.getPaths();
          pathsEl.textContent = `UserData:\n${p.userData}\n下载:\n${p.downloads}${
            p.snapshotRoot ? `\n快照目录:\n${p.snapshotRoot}` : ''
          }${p.analyticsRoot ? `\n分析目录:\n${p.analyticsRoot}` : ''}`;
        } catch {
          /* noop */
        }
      }
    } catch (e) {
      void showAppAlert(e.message || String(e), '保存设置失败');
    } finally {
      btn.disabled = false;
    }
  });

  document.getElementById('btn-snapshot-login')?.addEventListener('click', async () => {
    if (!window.aiWriter?.openSnapshotLogin) {
      void showAppAlert('请使用桌面版启动。', 'DOM 抓取');
      return;
    }
    try {
      await window.aiWriter.openSnapshotLogin();
    } catch (e) {
      void showAppAlert(e?.message || String(e), '打开登录页失败');
    }
  });

  document.getElementById('btn-snapshot-test-am')?.addEventListener('click', async () => {
    if (!window.aiWriter?.testSnapshotNow) return;
    try {
      const r = await window.aiWriter.testSnapshotNow('morning');
      if (r?.skipped) void showAppAlert(String(r.reason || '已跳过'), '试抓·早');
      else if (r?.ok) void showAppAlert(`已追加 JSONL：${r.path || ''}`, '试抓·早');
      else void showAppAlert(r?.error || JSON.stringify(r), '试抓·早');
      await refreshSnapshotRailHint();
    } catch (e) {
      void showAppAlert(e?.message || String(e), '试抓失败');
    }
  });

  document.getElementById('btn-snapshot-test-pm')?.addEventListener('click', async () => {
    if (!window.aiWriter?.testSnapshotNow) return;
    try {
      const r = await window.aiWriter.testSnapshotNow('evening');
      if (r?.skipped) void showAppAlert(String(r.reason || '已跳过'), '试抓·晚');
      else if (r?.ok) void showAppAlert(`已追加 JSONL：${r.path || ''}`, '试抓·晚');
      else void showAppAlert(r?.error || JSON.stringify(r), '试抓·晚');
      await refreshSnapshotRailHint();
    } catch (e) {
      void showAppAlert(e?.message || String(e), '试抓失败');
    }
  });

  document.getElementById('reader-copy-chapter')?.addEventListener('click', () => {
    void copyReaderTextToClipboard();
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
      readerChapterRaw = '';
      document.getElementById('reader-copy-chapter').hidden = true;
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
          ...novelThemePayload(),
          ideation_level: readIdeationLevel()
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
          ...novelThemePayload(),
          ideation_level: readIdeationLevel(),
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
