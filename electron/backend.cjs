'use strict';

const { spawn } = require('child_process');
const fs = require('fs');
const http = require('http');
const path = require('path');

const BACKEND_PORT = 18765;
const HEALTH_PATH = '/api/health';
const HEALTH_RETRIES = 40;
const HEALTH_DELAY_MS = 250;

let backendProcess = null;

function getPythonCmd() {
  if (process.env.AIWRITER_PYTHON) {
    return { cmd: process.env.AIWRITER_PYTHON, argsPrefix: [] };
  }
  if (process.platform === 'win32') {
    return { cmd: 'py', argsPrefix: ['-3'] };
  }
  return { cmd: 'python3', argsPrefix: [] };
}

function readSettings(userDataPath) {
  const p = path.join(userDataPath, 'settings.json');
  try {
    const raw = fs.readFileSync(p, 'utf8');
    return JSON.parse(raw);
  } catch {
    return {};
  }
}

function waitForBackendReady() {
  return new Promise((resolve, reject) => {
    let attempts = 0;
    const tryOnce = () => {
      attempts += 1;
      const req = http.request(
        {
          hostname: '127.0.0.1',
          port: BACKEND_PORT,
          path: HEALTH_PATH,
          method: 'GET',
          timeout: 2000
        },
        (res) => {
          res.resume();
          if (res.statusCode && res.statusCode >= 200 && res.statusCode < 300) {
            resolve();
            return;
          }
          retry();
        }
      );
      req.on('error', retry);
      req.on('timeout', () => {
        req.destroy();
        retry();
      });
      req.end();

      function retry() {
        if (attempts >= HEALTH_RETRIES) {
          reject(new Error('后端在超时时间内未就绪，请确认已安装 Python 依赖：pip install -r backend/requirements.txt'));
          return;
        }
        setTimeout(tryOnce, HEALTH_DELAY_MS);
      }
    };
    tryOnce();
  });
}

/**
 * @param {object} opts
 * @param {string} opts.userDataPath
 * @param {string} opts.projectRoot - repo root (contains backend/)
 */
function startBackend({ userDataPath, projectRoot }) {
  return new Promise((resolve, reject) => {
    if (backendProcess && !backendProcess.killed) {
      resolve();
      return;
    }

    const settings = readSettings(userDataPath);
    const apiKey = (settings.deepseekApiKey || process.env.DEEPSEEK_API_KEY || '').trim();
    const model = (settings.deepseekModel || 'deepseek-chat').trim();
    const booksRoot = (settings.booksRoot || '').trim();

    const backendDir = path.join(projectRoot, 'backend');
    const { cmd, argsPrefix } = getPythonCmd();
    const args = [
      ...argsPrefix,
      '-m',
      'uvicorn',
      'app.main:app',
      '--host',
      '127.0.0.1',
      '--port',
      String(BACKEND_PORT)
    ];

    const env = {
      ...process.env,
      AIWRITER_USER_DATA: userDataPath,
      DEEPSEEK_API_KEY: apiKey,
      DEEPSEEK_MODEL: model,
      PYTHONUTF8: '1',
      PYTHONIOENCODING: 'utf-8'
    };
    if (booksRoot) {
      env.AIWRITER_BOOKS_ROOT = booksRoot;
    }

    try {
      backendProcess = spawn(cmd, args, {
        cwd: backendDir,
        env,
        windowsHide: true,
        stdio: ['ignore', 'pipe', 'pipe']
      });
    } catch (e) {
      reject(e);
      return;
    }

    const logLine = (buf, label) => {
      const s = String(buf).trimEnd();
      if (s) console.log(`[backend ${label}]`, s);
    };
    backendProcess.stdout.on('data', (d) => logLine(d, 'out'));
    backendProcess.stderr.on('data', (d) => logLine(d, 'err'));

    backendProcess.on('error', (err) => {
      console.error('[backend] spawn error:', err);
    });

    backendProcess.on('exit', (code, signal) => {
      console.log('[backend] exit', code, signal || '');
      backendProcess = null;
    });

    waitForBackendReady()
      .then(() => {
        console.log('[backend] ready on port', BACKEND_PORT);
        resolve();
      })
      .catch((err) => {
        stopBackend();
        reject(err);
      });
  });
}

function stopBackend() {
  if (!backendProcess || backendProcess.killed) {
    backendProcess = null;
    return;
  }
  try {
    backendProcess.kill();
  } catch (e) {
    console.error('[backend] stop error:', e);
  }
  backendProcess = null;
}

function getBackendBaseUrl() {
  return `http://127.0.0.1:${BACKEND_PORT}`;
}

module.exports = {
  startBackend,
  stopBackend,
  getBackendBaseUrl,
  BACKEND_PORT
};
