'use strict';

/**
 * 在 backend/ 下调用 PyInstaller，生成 dist/aiwriter-backend/（onedir）。
 * 需已安装: pip install -r backend/requirements.txt -r backend/requirements-build.txt
 */
const { spawnSync } = require('child_process');
const fs = require('fs');
const path = require('path');

const root = path.join(__dirname, '..');
const backendDir = path.join(root, 'backend');
const spec = path.join(backendDir, 'aiwriter-backend.spec');

if (!fs.existsSync(spec)) {
  console.error('[build-backend] Missing spec:', spec);
  process.exit(1);
}

const win = process.platform === 'win32';
/** AIWRITER_PYTHON 或 PATH 上的解释器；【py -3】在部分环境里会错误地把 -3 传给 python.exe */
const candidates = [];
if (process.env.AIWRITER_PYTHON) {
  candidates.push([process.env.AIWRITER_PYTHON, []]);
}
if (win) {
  candidates.push(['python', []], ['py', ['-3']]);
} else {
  candidates.push(['python3', []], ['python', []]);
}

let r = { error: new Error('no candidate'), status: -1 };
let lastCmd = '';
for (const [cmd, prefix] of candidates) {
  lastCmd = [cmd, ...prefix].filter(Boolean).join(' ');
  r = spawnSync(cmd, [...prefix, '-m', 'PyInstaller', '--noconfirm', '--clean', 'aiwriter-backend.spec'], {
    cwd: backendDir,
    stdio: 'inherit',
    env: { ...process.env, PYTHONUTF8: '1' },
    shell: false
  });
  if (r.error) continue;
  break;
}

if (!r || r.error) {
  console.error('[build-backend] Cannot run interpreter. Last tried:', lastCmd || String(candidates));
  if (r && r.error) console.error('[build-backend]', r.error.message || r.error);
  process.exit(1);
}
if (r.status !== 0) {
  process.exit(r.status || 1);
}

const outDir = path.join(backendDir, 'dist', 'aiwriter-backend');
const exe = path.join(outDir, win ? 'aiwriter-backend.exe' : 'aiwriter-backend');
if (!fs.existsSync(exe)) {
  console.error('[build-backend] Expected binary missing:', exe);
  process.exit(1);
}
console.log('[build-backend] OK →', outDir);
