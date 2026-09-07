import { spawn } from 'node:child_process';
import path from 'node:path';
import { fileURLToPath } from 'node:url';

const projectRoot = path.dirname(path.dirname(fileURLToPath(import.meta.url)));
const children = new Set();
let stopping = false;

function launch(label, script, args = [], env = {}) {
  const child = spawn(process.execPath, [script, ...args], {
    cwd: projectRoot,
    env: { ...process.env, ...env },
    stdio: 'inherit',
    windowsHide: true,
  });
  children.add(child);
  child.once('exit', (code, signal) => {
    children.delete(child);
    if (!stopping) {
      console.error(`${label} stopped (${signal || code || 0}).`);
      stop(code || 1);
    }
  });
  return child;
}

function stop(exitCode = 0) {
  if (stopping) return;
  stopping = true;
  for (const child of children) child.kill('SIGTERM');
  const timer = setTimeout(() => process.exit(exitCode), 1200);
  timer.unref();
}

process.once('SIGINT', () => stop(0));
process.once('SIGTERM', () => stop(0));

launch('Local bridge', path.join(projectRoot, 'server', 'index.mjs'));
launch('Web interface', path.join(projectRoot, 'node_modules', 'vinext', 'dist', 'cli.js'), [
  'dev',
]);
