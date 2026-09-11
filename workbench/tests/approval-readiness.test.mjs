import assert from 'node:assert/strict';
import { execFile } from 'node:child_process';
import { mkdtemp, readFile, rm } from 'node:fs/promises';
import os from 'node:os';
import path from 'node:path';
import { fileURLToPath } from 'node:url';
import { promisify } from 'node:util';
import test from 'node:test';
import ts from 'typescript';
import {
  readStatus,
  transitionStatus,
  writeJsonAtomic,
} from '../server/state.mjs';

const project = path.dirname(path.dirname(fileURLToPath(import.meta.url)));
const { outputText } = ts.transpileModule(
  await readFile(path.join(project, 'lib/zibuyu.ts'), 'utf8'),
  { compilerOptions: { module: ts.ModuleKind.ESNext } },
);
const { isPaidApprovalReady } = await import(
  `data:text/javascript;base64,${Buffer.from(outputText).toString('base64')}`
);
const readyDetail = () => ({
  intake: { runId: 'test-model-three' },
  status: {
    state: 'awaiting_paid_approval',
    execution: { state: 'completed' },
  },
  approval: null,
  prepareResult: {
    runId: 'test-model-three',
    phase: 'prepare',
    outcome: 'awaiting_paid_approval',
    artifacts: [{ kind: 'json', path: 'director-package.json' }],
  },
});

test('approval stays closed during the observed progress-before-result window', () => {
  const detail = readyDetail();
  detail.prepareResult = null;
  detail.status.execution.state = 'running';
  assert.equal(isPaidApprovalReady(detail), false);
  detail.prepareResult = readyDetail().prepareResult;
  assert.equal(isPaidApprovalReady(detail), false);
  detail.status.execution.state = 'completed';
  assert.equal(isPaidApprovalReady(detail), true);
  detail.approval = { approvedAt: '2026-09-07T06:16:44Z' };
  assert.equal(isPaidApprovalReady(detail), false);
});

test('another model result, missing local artifacts and failed preparation never open approval', () => {
  for (const mutate of [
    (d) => {
      d.prepareResult.runId = 'another-model';
    },
    (d) => {
      d.prepareResult.artifacts = [];
    },
    (d) => {
      d.prepareResult.artifacts = [
        { kind: 'link', path: 'https://example.com/reference' },
      ];
    },
    (d) => {
      d.prepareResult.outcome = 'blocked';
    },
    (d) => {
      d.status.execution.state = 'failed';
    },
    (d) => {
      d.modelBatchError = 'batch mismatch';
    },
  ]) {
    const detail = readyDetail();
    mutate(detail);
    assert.equal(isPaidApprovalReady(detail), false, JSON.stringify(detail));
  }
});

test('production progress CLI cannot mark approval ready before the runner commits the result', async () => {
  const root = await mkdtemp(path.join(os.tmpdir(), 'zb-approval-progress-'));
  try {
    await writeJsonAtomic(path.join(root, 'intake.json'), {
      runId: 'test-model-three',
      variants: [{ id: 'black' }],
    });
    await promisify(execFile)(
      process.execPath,
      [
        path.join(project, 'server/progress-cli.mjs'),
        '--run-dir',
        root,
        '--stage',
        'paid_approval',
        '--state',
        'awaiting_paid_approval',
        '--task',
        '准备阶段完成，等待付费生成确认',
      ],
      { windowsHide: true },
    );
    const pending = await readStatus(root);
    assert.equal(pending.state, 'running');
    assert.equal(pending.stageKey, 'preflight_validation');
    assert.match(pending.currentTask, /整理.*授权清单/);
    // The runner's commit path is still able to open approval normally.
    await writeJsonAtomic(
      path.join(root, 'web/result-prepare.json'),
      readyDetail().prepareResult,
    );
    await transitionStatus(root, {
      state: 'awaiting_paid_approval',
      stageKey: 'paid_approval',
    });
    assert.equal((await readStatus(root)).state, 'awaiting_paid_approval');
  } finally {
    await rm(root, { recursive: true, force: true });
  }
});
