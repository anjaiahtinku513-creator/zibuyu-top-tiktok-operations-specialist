import path from 'node:path';

import { transitionStatus } from './state.mjs';

function readArg(name, fallback = '') {
  const index = process.argv.indexOf(`--${name}`);
  return index >= 0 ? (process.argv[index + 1] ?? fallback) : fallback;
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
  // Only the runner can open approval, after committing result-prepare.json.
  // A model's progress report can arrive before its structured output exists.
  stageKey:
    state === 'awaiting_paid_approval' ? 'preflight_validation' : stageKey,
  state: state === 'awaiting_paid_approval' ? 'running' : state,
  currentTask:
    state === 'awaiting_paid_approval'
      ? '正在整理准备产物与授权清单'
      : currentTask,
  note,
});

process.stdout.write(`${JSON.stringify(status)}\n`);
