'use strict';

/**
 * 作家后台等指标页：每日 8:00 / 20:00 各跑一轮 **DOM 抓取**（最多各 1 次/日），结果追加到
 * D 盘 Analytics/metrics/dom-scrape.jsonl（见 resolveAnalyticsRoot）。
 * 使用持久 session 分区：先「打开登录页」完成网站登录后再启用定时任务。
 * 页面内需稳定选择器：推荐给目标节点加 data-aiwriter-metric="key"，或在设置里配置 { key, selector } 列表。
 */

const fs = require('fs');
const path = require('path');
const { BrowserWindow } = require('electron');
const { resolveSnapshotRoot, resolveAnalyticsRoot } = require('./paths.cjs');
const { buildDomScrapeExpression } = require('./dom-scrape-inject.cjs');

const PERSIST_PARTITION = 'persist:aiwriter-metrics-snapshot';
const DEFAULT_SNAPSHOT_URL =
  'https://fanqienovel.com/main/writer/data?bookId=7628439872088329241';

/** @type {import('electron').BrowserWindow | null} */
let captureWin = null;
/** @type {import('electron').BrowserWindow | null} */
let loginWin = null;
let intervalHandle = null;
let catchUpDone = false;
/** @type {import('electron').App | null} */
let appRef = null;
let captureBusy = false;

function settingsPath() {
  if (!appRef) return null;
  return path.join(appRef.getPath('userData'), 'settings.json');
}

function statePath() {
  if (!appRef) return null;
  const ar = resolveAnalyticsRoot(appRef);
  if (ar) {
    return path.join(ar, 'state', 'snapshot-agent-state.json');
  }
  return path.join(appRef.getPath('userData'), 'snapshot-agent-state.json');
}

function migrateSnapshotAgentState() {
  if (!appRef) return;
  const ar = resolveAnalyticsRoot(appRef);
  if (!ar) return;
  const newP = path.join(ar, 'state', 'snapshot-agent-state.json');
  const oldP = path.join(appRef.getPath('userData'), 'snapshot-agent-state.json');
  if (fs.existsSync(newP) || !fs.existsSync(oldP)) return;
  try {
    fs.mkdirSync(path.dirname(newP), { recursive: true });
    fs.copyFileSync(oldP, newP);
    console.log('[AI-writer] Migrated snapshot-agent-state.json to', newP);
  } catch (e) {
    console.warn('[AI-writer] Snapshot state migrate failed', e);
  }
}

function readJsonSafe(p, fallback) {
  try {
    return JSON.parse(fs.readFileSync(p, 'utf8'));
  } catch {
    return fallback;
  }
}

function normalizeMetricsDomSelectors(raw) {
  let arr = [];
  if (Array.isArray(raw)) arr = raw;
  else if (typeof raw === 'string' && raw.trim()) {
    try {
      arr = JSON.parse(raw);
    } catch {
      arr = [];
    }
  }
  return arr
    .filter((x) => x && typeof x.key === 'string' && typeof x.selector === 'string')
    .map((x) => ({
      key: String(x.key).trim().slice(0, 120),
      selector: String(x.selector).trim().slice(0, 400)
    }))
    .filter((x) => x.key && x.selector);
}

function loadSettingsMerged() {
  const sp = settingsPath();
  if (!sp || !fs.existsSync(sp)) {
    return {
      snapshotAgentEnabled: false,
      snapshotPageUrl: DEFAULT_SNAPSHOT_URL,
      metricsDomSelectors: []
    };
  }
  const s = readJsonSafe(sp, {});
  return {
    snapshotAgentEnabled: Boolean(s.snapshotAgentEnabled),
    snapshotPageUrl: (s.snapshotPageUrl && String(s.snapshotPageUrl).trim()) || DEFAULT_SNAPSHOT_URL,
    metricsDomSelectors: normalizeMetricsDomSelectors(s.metricsDomSelectors)
  };
}

function readState() {
  const p = statePath();
  if (!p) return { slots: {} };
  return readJsonSafe(p, { slots: {} });
}

function writeState(st) {
  const p = statePath();
  if (!p) return;
  fs.mkdirSync(path.dirname(p), { recursive: true });
  fs.writeFileSync(p, JSON.stringify(st, null, 2), 'utf8');
}

function localDateString(d) {
  const y = d.getFullYear();
  const m = String(d.getMonth() + 1).padStart(2, '0');
  const day = String(d.getDate()).padStart(2, '0');
  return `${y}-${m}-${day}`;
}

function cleanupOldDayFolders(snapshotRoot) {
  if (!snapshotRoot || !fs.existsSync(snapshotRoot)) return;
  const today = localDateString(new Date());
  let names;
  try {
    names = fs.readdirSync(snapshotRoot, { withFileTypes: true });
  } catch {
    return;
  }
  for (const ent of names) {
    if (!ent.isDirectory()) continue;
    const name = ent.name;
    if (!/^\d{4}-\d{2}-\d{2}$/.test(name)) continue;
    if (name < today) {
      try {
        fs.rmSync(path.join(snapshotRoot, name), { recursive: true, force: true });
      } catch (e) {
        console.warn('[snapshot-agent] cleanup failed', name, e.message || e);
      }
    }
  }
}

function todaySlotsSet() {
  const st = readState();
  const today = localDateString(new Date());
  const arr = st.slots && Array.isArray(st.slots[today]) ? st.slots[today] : [];
  return new Set(arr);
}

function markSlotDone(slot) {
  const today = localDateString(new Date());
  const st = readState();
  st.slots = st.slots && typeof st.slots === 'object' ? st.slots : {};
  const cur = Array.isArray(st.slots[today]) ? st.slots[today].slice() : [];
  if (!cur.includes(slot)) cur.push(slot);
  st.slots[today] = cur;
  writeState(st);
}

function appendDomScrapeRecord(record) {
  const ar = resolveAnalyticsRoot(appRef);
  if (!ar) return;
  const dir = path.join(ar, 'metrics');
  fs.mkdirSync(dir, { recursive: true });
  const linePath = path.join(dir, 'dom-scrape.jsonl');
  fs.appendFileSync(linePath, `${JSON.stringify(record)}\n`, 'utf8');
}

function ensureCaptureWindow() {
  if (captureWin && !captureWin.isDestroyed()) return captureWin;
  captureWin = new BrowserWindow({
    show: false,
    width: 1440,
    height: 900,
    webPreferences: {
      partition: PERSIST_PARTITION,
      contextIsolation: true,
      nodeIntegration: false
    }
  });
  captureWin.on('closed', () => {
    captureWin = null;
  });
  return captureWin;
}

/**
 * @param {'morning' | 'evening'} slot
 */
/**
 * @param {'morning' | 'evening'} slot
 * @param {{ force?: boolean }} [opts] force=true 时忽略「未启用」开关（用于试拍）
 */
async function captureSnapshot(slot, opts) {
  const force = Boolean(opts && opts.force);
  if (captureBusy) return { ok: false, skipped: true, reason: 'busy' };
  const settings = loadSettingsMerged();
  if (!settings.snapshotAgentEnabled && !force) return { ok: false, skipped: true, reason: 'disabled' };

  const ar = resolveAnalyticsRoot(appRef);
  if (!ar) return { ok: false, error: 'no_analytics_root' };

  const slots = todaySlotsSet();
  if (!force && slots.has(slot)) return { ok: true, skipped: true, reason: 'already_captured' };

  captureBusy = true;
  const win = ensureCaptureWindow();
  const url = settings.snapshotPageUrl;
  const selectorsJson = JSON.stringify(settings.metricsDomSelectors || []);
  const scrapeExpr = buildDomScrapeExpression(selectorsJson);

  try {
    await new Promise((resolve, reject) => {
      const done = () => {
        win.webContents.removeListener('did-finish-load', onOk);
        win.webContents.removeListener('did-fail-load', onFail);
        clearTimeout(tid);
        resolve();
      };
      const onOk = () => {
        done();
      };
      const onFail = (_e, code, desc, _u, isMain) => {
        if (isMain) {
          win.webContents.removeListener('did-finish-load', onOk);
          win.webContents.removeListener('did-fail-load', onFail);
          clearTimeout(tid);
          reject(new Error(desc || String(code)));
        }
      };
      const tid = setTimeout(done, 15000);
      win.webContents.once('did-finish-load', onOk);
      win.webContents.once('did-fail-load', onFail);
      win.loadURL(url).catch(reject);
    });

    // SPA 常见：首屏后再等一会儿再 evaluate
    await new Promise((r) => setTimeout(r, 5500));

    let payload;
    try {
      payload = await win.webContents.executeJavaScript(scrapeExpr);
    } catch (e) {
      const err = e.message || String(e);
      appendDomScrapeRecord({
        kind: 'writer-dom-scrape',
        slot,
        url,
        scrapedAt: new Date().toISOString(),
        ok: false,
        error: err
      });
      return { ok: false, error: err };
    }

    const outPath = path.join(ar, 'metrics', 'dom-scrape.jsonl');
    appendDomScrapeRecord({
      kind: 'writer-dom-scrape',
      slot,
      url,
      scrapedAt: new Date().toISOString(),
      ok: true,
      payload: payload && typeof payload === 'object' ? payload : { raw: payload }
    });
    markSlotDone(slot);
    console.log('[metrics-dom] appended scrape →', outPath);
    return { ok: true, path: outPath, mode: 'dom' };
  } catch (e) {
    const err = e.message || String(e);
    appendDomScrapeRecord({
      kind: 'writer-dom-scrape',
      slot,
      url,
      scrapedAt: new Date().toISOString(),
      ok: false,
      error: err
    });
    return { ok: false, error: err };
  } finally {
    captureBusy = false;
  }
}

function shouldRunMorningCatchUp(now) {
  const h = now.getHours();
  if (h < 8) return false;
  return !todaySlotsSet().has('morning');
}

function shouldRunEveningCatchUp(now) {
  const h = now.getHours();
  if (h < 20) return false;
  return !todaySlotsSet().has('evening');
}

/** 保存设置里勾选启用后，允许再跑一次启动补拍逻辑 */
function notifySettingsMayHaveEnabledSnap() {
  catchUpDone = false;
  setTimeout(() => {
    runCatchUpOnStartup().catch((e) => console.warn('[snapshot-agent] post-settings', e));
  }, 2500);
}

async function runCatchUpOnStartup() {
  if (catchUpDone) return;
  const settings = loadSettingsMerged();
  if (!settings.snapshotAgentEnabled) return;
  catchUpDone = true;
  const snapshotRoot = resolveSnapshotRoot(appRef);
  if (snapshotRoot) cleanupOldDayFolders(snapshotRoot);
  const now = new Date();
  try {
    if (shouldRunMorningCatchUp(now)) {
      await captureSnapshot('morning');
    }
  } catch (e) {
    console.warn('[snapshot-agent] catch-up morning', e.message || e);
  }
  try {
    if (shouldRunEveningCatchUp(now)) {
      await captureSnapshot('evening');
    }
  } catch (e) {
    console.warn('[snapshot-agent] catch-up evening', e.message || e);
  }
}

function tickSchedule() {
  const settings = loadSettingsMerged();
  const snapshotRoot = resolveSnapshotRoot(appRef);
  if (snapshotRoot) cleanupOldDayFolders(snapshotRoot);
  if (!settings.snapshotAgentEnabled) return;

  const now = new Date();
  const h = now.getHours();
  const m = now.getMinutes();

  if (h === 8 && m <= 2 && !todaySlotsSet().has('morning')) {
    captureSnapshot('morning').catch((e) => console.warn('[snapshot-agent] morning', e.message || e));
  }
  if (h === 20 && m <= 2 && !todaySlotsSet().has('evening')) {
    captureSnapshot('evening').catch((e) => console.warn('[snapshot-agent] evening', e.message || e));
  }
}

function openLoginWindow() {
  const settings = loadSettingsMerged();
  const url = settings.snapshotPageUrl;
  if (loginWin && !loginWin.isDestroyed()) {
    loginWin.focus();
    loginWin.loadURL(url).catch(() => {});
    return loginWin;
  }
  loginWin = new BrowserWindow({
    width: 1180,
    height: 860,
    show: true,
    title: '登录作家后台（关闭窗口后仍保留登录状态）',
    webPreferences: {
      partition: PERSIST_PARTITION,
      contextIsolation: true,
      nodeIntegration: false
    }
  });
  loginWin.loadURL(url).catch((e) => console.error(e));
  loginWin.on('closed', () => {
    loginWin = null;
  });
  return loginWin;
}

function getSnapshotInfo() {
  const root = appRef ? resolveSnapshotRoot(appRef) : null;
  const ar = appRef ? resolveAnalyticsRoot(appRef) : null;
  const st = readState();
  const today = localDateString(new Date());
  const s = loadSettingsMerged();
  return {
    snapshotRoot: root,
    metricsDomJsonl: ar ? path.join(ar, 'metrics', 'dom-scrape.jsonl') : '',
    enabled: s.snapshotAgentEnabled,
    snapshotPageUrl: s.snapshotPageUrl,
    metricsDomSelectorCount: (s.metricsDomSelectors && s.metricsDomSelectors.length) || 0,
    today,
    todaySlots: st.slots && st.slots[today] ? st.slots[today] : []
  };
}

/**
 * @param {{ app: import('electron').App }} opts
 */
function initSnapshotScheduler(opts) {
  appRef = opts.app;
  migrateSnapshotAgentState();
  if (intervalHandle) clearInterval(intervalHandle);
  intervalHandle = setInterval(tickSchedule, 30 * 1000);
  setTimeout(() => {
    runCatchUpOnStartup().catch((e) => console.warn('[snapshot-agent] startup', e));
  }, 6000);
}

function disposeSnapshotScheduler() {
  if (intervalHandle) {
    clearInterval(intervalHandle);
    intervalHandle = null;
  }
  if (captureWin && !captureWin.isDestroyed()) {
    captureWin.destroy();
    captureWin = null;
  }
  if (loginWin && !loginWin.isDestroyed()) {
    loginWin.destroy();
    loginWin = null;
  }
}

module.exports = {
  initSnapshotScheduler,
  disposeSnapshotScheduler,
  openLoginWindow,
  captureSnapshot,
  getSnapshotInfo,
  notifySettingsMayHaveEnabledSnap,
  DEFAULT_SNAPSHOT_URL,
  PERSIST_PARTITION
};
