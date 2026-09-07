import path from 'node:path';

import { transitionStatus } from './state.mjs';

function readArg(name, fallback = '') {
  const index = process.argv.indexOf(`--${name}`);
  return index >= 0 ? process.argv[index + 1] ?? fallback : fallback;
}

const runDir = path.resolve(readArg('run-dir'));
const stageKey = readArg('stage');
const currentTask = readArg('task');
const state = readArg('state', 'running');
const note = readArg('note');

if (!runDir || !stageKey || !currentTask) {
  throw new Error('Required: --run-dir, --stage, --task');
}

const status = await transitionStatus(runDir, {
  stageKey,
  state,
  currentTask,
  note,
});

process.stdout.write(`${JSON.stringify(status)}\n`);
