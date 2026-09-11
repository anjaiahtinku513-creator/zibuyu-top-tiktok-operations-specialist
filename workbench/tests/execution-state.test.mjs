import assert from 'node:assert/strict';
import test from 'node:test';
import { EventEmitter } from 'node:events';
import { PassThrough, Writable } from 'node:stream';
import { mkdtemp, rm, writeFile, readFile, readdir } from 'node:fs/promises';
import path from 'node:path';
import os from 'node:os';
import {
  runCodexProcess,
  verifyPaidAuthorization,
  authorizationFingerprint,
  hasPaidDispatchEvidence,
} from '../server/codex-runner.mjs';
import { readStatus, writeJsonAtomic } from '../server/state.mjs';
import { createHash } from 'node:crypto';
const delay = (ms) => new Promise((resolve) => setTimeout(resolve, ms));

test('session and runtime are visible before the CLI exits and survive independent workflow progress writes', async () => {
  const root = await mkdtemp(path.join(os.tmpdir(), 'zb-live-session-'));
  const previous = process.env.CODEX_CLI_PATH;
  let child;
  try {
    const cli = path.join(root, 'fixture.exe');
    await writeFile(cli, 'fake process only');
    process.env.CODEX_CLI_PATH = cli;
    await writeJsonAtomic(path.join(root, 'web', 'status.json'), {
      state: 'running',
      stageKey: 'product_analysis',
      sessionId: null,
      updatedAt: 'unchanged',
    });
    const work = runCodexProcess({
      runDir: root,
      phase: 'prepare',
      imagePaths: [],
      prompt: 'fixture',
      processSpawner: () => {
        child = new EventEmitter();
        child.stdout = new PassThrough();
        child.stderr = new PassThrough();
        child.stdin = new Writable({
          write(_chunk, _encoding, done) {
            done();
          },
          final(done) {
            child.stdout.write(
              '{"type":"thread.started","thread_id":"01a07a33-3e3a-7061-b761-8ebc1bffe08a"}\n',
            );
            done();
          },
        });
        process.nextTick(() => child.emit('spawn'));
        return child;
      },
    });
    let current;
    for (let count = 0; count < 500; count++) {
      current = await readStatus(root);
      if (current.sessionId) break;
      await delay(10);
    }
    assert.equal(
      current.sessionId,
      '01a07a33-3e3a-7061-b761-8ebc1bffe08a',
      JSON.stringify({
        current,
        files: await readdir(path.join(root, 'web')),
        events: await readFile(
          path.join(root, 'web/codex-events-prepare.jsonl'),
          'utf8',
        ),
      }),
    );
    assert.equal(current.execution.state, 'running');
    await writeJsonAtomic(path.join(root, 'web', 'status.json'), {
      state: 'running',
      stageKey: 'script_and_voice',
      sessionId: null,
      updatedAt: 'unchanged',
    });
    assert.equal((await readStatus(root)).sessionId, current.sessionId);
    child.stdout.end();
    child.stderr.end();
    child.emit('close', 0, null);
    const execution = await work;
    assert.ok(execution.attemptId);
    assert.equal(
      path.basename(path.dirname(execution.outputPath)),
      execution.attemptId,
    );
    assert.notEqual(
      execution.outputPath,
      path.join(root, 'web/final-prepare.json'),
    );
    assert.equal(
      (await readStatus(root)).execution.attemptId,
      execution.attemptId,
    );
    assert.equal((await readStatus(root)).execution.state, 'completed');
  } finally {
    if (previous === undefined) delete process.env.CODEX_CLI_PATH;
    else process.env.CODEX_CLI_PATH = previous;
    await rm(root, {
      recursive: true,
      force: true,
      maxRetries: 5,
      retryDelay: 30,
    });
  }
});

test('paid authorization is rechecked against bytes, and even an empty dispatch log prevents recovery resubmission', async () => {
  const root = await mkdtemp(path.join(os.tmpdir(), 'zb-paid-check-'));
  try {
    const intake = { runId: 'test', variants: [{ id: 'black' }] },
      prepareResult = { artifacts: [{ kind: 'json', path: 'data.json' }] };
    await writeJsonAtomic(path.join(root, 'intake.json'), intake);
    await writeJsonAtomic(
      path.join(root, 'web', 'result-prepare.json'),
      prepareResult,
    );
    await writeFile(path.join(root, 'data.json'), '{}');
    const artifactManifest = [
      {
        path: 'data.json',
        size: 2,
        sha256: createHash('sha256').update('{}').digest('hex'),
      },
    ];
    const approval = {
      runId: 'test',
      variantIds: ['black'],
      artifactManifest,
      authorizationFingerprint: authorizationFingerprint({
        intake,
        prepareResult,
        variantIds: ['black'],
        artifactManifest,
      }),
    };
    await verifyPaidAuthorization(root, approval);
    assert.equal(await hasPaidDispatchEvidence(root), false);
    await writeFile(path.join(root, 'web', 'codex-events-paid.jsonl'), '');
    assert.equal(await hasPaidDispatchEvidence(root), true);
    await writeFile(path.join(root, 'data.json'), '[]');
    await assert.rejects(verifyPaidAuthorization(root, approval), /产物已变化/);
  } finally {
    await rm(root, { recursive: true, force: true });
  }
});
