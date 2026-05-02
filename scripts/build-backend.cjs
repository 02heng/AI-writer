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
const py = process.env.AIWRITER_PYTHON || (win ? 'py' : 'python3');
const prefix = win ? ['-3'] : [];
const args = [...prefix, '-m', 'PyInstaller', '--noconfirm', '--clean', 'aiwriter-backend.spec'];

const r = spawnSync(py, args, {
  cwd: backendDir,
  stdio: 'inherit',
  env: { ...process.env, PYTHONUTF8: '1' }
});

if (r.error) {
  console.error('[build-backend]', r.error.message || r.error);
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
