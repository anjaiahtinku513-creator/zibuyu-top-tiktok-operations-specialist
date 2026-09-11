import assert from 'node:assert/strict';
import test from 'node:test';
import { createHash } from 'node:crypto';
import { mkdtemp, mkdir, writeFile, readFile, rm } from 'node:fs/promises';
import path from 'node:path';
import os from 'node:os';
import { createWorkQueue } from '../server/work-queue.mjs';
import { persistModelRuns } from '../server/model-batches.mjs';
import {
  sharedRequestFor,
  initializeShared,
  sealShared,
  readVerifiedShared,
  attachShared,
  verifyAttachedShared,
} from '../server/shared-preparation.mjs';
import { readJson, readStatus, writeJsonAtomic } from '../server/state.mjs';

process.env.ZIBUYU_EXECUTOR_MODE = 'mock';
const runner = await import('../server/codex-runner.mjs');
const { createMockSharedResult } =
  await import('../server/shared-preparation-mock.mjs');
const delay = (ms) => new Promise((resolve) => setTimeout(resolve, ms));
const deferred = () => {
  let resolve;
  const promise = new Promise((yes) => {
    resolve = yes;
  });
  return { promise, resolve };
};
const hash = (bytes) => createHash('sha256').update(bytes).digest('hex');

async function fixture({ mixed = false } = {}) {
  const root = await mkdtemp(path.join(os.tmpdir(), 'zb-shared-'));
  const primaryRunId = 'zb-20260907-0000000001',
    primary = path.join(root, primaryRunId);
  await mkdir(path.join(primary, 'uploads'), { recursive: true });
  const bytes = Buffer.from([137, 80, 78, 71, 13, 10, 26, 10]);
  await writeFile(path.join(primary, 'uploads', 'black.png'), bytes);
  let id = 1;
  const created = await persistModelRuns({
    root,
    primaryRunId,
    policy: runner.CODEX_EXECUTION_POLICY,
    newRunId: () => `zb-20260907-${String(++id).padStart(10, '0')}`,
    intake: {
      sku: 'TEST-ONLY',
      notes: '',
      output: {},
      models: ['德1', '德2', mixed ? '美1' : '德3'].map((preset) => ({
        preset,
        marketCode: preset.startsWith('美') ? 'US' : 'DE',
        locale: preset.startsWith('美') ? 'en-US' : 'de-DE',
      })),
      sourceUrls: {
        DE: 'https://www.amazon.de/dp/EXAMPLE?th=1',
        US: 'https://www.amazon.com/dp/EXAMPLE?th=1',
      },
      variants: [
        {
          id: 'black',
          name: 'Black',
          images: [{ relativePath: 'uploads/black.png', sha256: hash(bytes) }],
        },
      ],
    },
  });
  return {
    root,
    created,
    request: await sharedRequestFor(primary),
    cleanup: () =>
      rm(root, { recursive: true, force: true, maxRetries: 5, retryDelay: 30 }),
  };
}

test('bounded queue starts three, retains duplicate promise, and excludes the same resource', async () => {
  const queue = createWorkQueue({ concurrency: 3 });
  const gates = Array.from({ length: 5 }, deferred),
    started = [];
  const run = (n) => async () => {
    started.push(n);
    await gates[n].promise;
  };
  const a = queue.enqueue('a', run(0), 'model-a');
  assert.equal(queue.enqueue('a', run(4)), a);
  const b = queue.enqueue('b', run(1), 'model-a');
  const c = queue.enqueue('c', run(2), 'model-c');
  const d = queue.enqueue('d', run(3), 'model-d');
  const e = queue.enqueue('e', run(4), 'model-e');
  await delay(0);
  assert.deepEqual(started, [0, 2, 3]);
  gates[0].resolve();
  await delay(0);
  assert.deepEqual(started, [0, 2, 3, 1]);
  for (const gate of gates) gate.resolve();
  await Promise.all([a, b, c, d, e]);
});

test('one shared producer precedes three simultaneous model preparations; retries do not rewrite the shared manifest', async () => {
  const f = await fixture();
  try {
    const work = runner.enqueuePreparationBatch(f.created.runs);
    assert.equal(runner.enqueuePreparationBatch(f.created.runs), work);
    let maxActive = 0;
    const sample = setInterval(() => {
      maxActive = Math.max(
        maxActive,
        runner.getExecutorInfo().activePreparationJobs,
      );
    }, 5);
    try {
      await work;
    } finally {
      clearInterval(sample);
    }
    assert.equal(maxActive, 3);
    const manifestPath = path.join(f.request.sharedDir, 'manifest.json');
    const before = await readFile(manifestPath, 'utf8');
    for (const run of f.created.runs) {
      const status = await readStatus(run.runDir);
      assert.equal(status.state, 'awaiting_paid_approval');
      assert.equal(status.preparation.state, 'completed');
      assert.ok(await verifyAttachedShared(run.runDir));
      const events = await readFile(
        path.join(run.runDir, 'web', 'events.jsonl'),
        'utf8',
      );
      assert.ok(!events.includes('模拟执行：three_view_generation'));
    }
    await runner.enqueuePreparationBatch(f.created.runs);
    assert.equal(await readFile(manifestPath, 'utf8'), before);
  } finally {
    await f.cleanup();
  }
});

test('market receipts stay isolated and local shared copies detect source or content changes', async () => {
  const f = await fixture({ mixed: true });
  try {
    await initializeShared(f.request);
    await sealShared(f.request, await createMockSharedResult(f.request));
    for (const run of f.created.runs) {
      const receipt = await attachShared(run.runDir, f.request);
      const intake = await readJson(path.join(run.runDir, 'intake.json'));
      const markets = receipt.files.filter((file) => file.kind === 'market');
      assert.equal(markets.length, 1);
      assert.equal(markets[0].marketCode, intake.model.marketCode);
      assert.equal(markets[0].sourceUrl, intake.amazonUrl);
    }
    const run = f.created.runs[0];
    const receipt = await readJson(
      path.join(run.runDir, 'shared-input', 'manifest.json'),
    );
    await writeFile(path.join(run.runDir, receipt.files[0].path), 'changed');
    await assert.rejects(verifyAttachedShared(run.runDir), /副本已变化/);
    const intake = await readJson(path.join(run.runDir, 'intake.json'));
    intake.amazonUrl += '&psc=1';
    await writeJsonAtomic(path.join(run.runDir, 'intake.json'), intake);
    await assert.rejects(verifyAttachedShared(run.runDir), /失效/);
  } finally {
    await f.cleanup();
  }
});

test('damaged shared artifact blocks all children without generating replacement images', async () => {
  const f = await fixture();
  try {
    await initializeShared(f.request);
    const result = await createMockSharedResult(f.request);
    await sealShared(f.request, result);
    await writeFile(
      path.join(f.request.sharedDir, result.threeViews[0].path),
      'corrupt',
    );
    await assert.rejects(readVerifiedShared(f.request), /损坏/);
    await assert.rejects(
      runner.enqueuePreparationBatch(f.created.runs),
      /损坏/,
    );
    for (const run of f.created.runs) {
      assert.equal((await readStatus(run.runDir)).state, 'blocked');
      assert.equal((await readStatus(run.runDir)).sessionId, null);
    }
  } finally {
    await f.cleanup();
  }
});

test('incomplete visual identity audit cannot seal a reusable reference', async () => {
  const f = await fixture();
  try {
    await initializeShared(f.request);
    const result = await createMockSharedResult(f.request);
    const qaFile = path.join(f.request.sharedDir, result.threeViews[0].qaPath);
    const qa = await readJson(qaFile);
    delete qa.identityCueAudit.hair_present;
    await writeJsonAtomic(qaFile, qa);
    await assert.rejects(sealShared(f.request, result), /人体身份审计/);
  } finally {
    await f.cleanup();
  }
});

test('missing shared receipt fails closed instead of falling back to repeat preparation', async () => {
  const f = await fixture();
  try {
    const run = f.created.runs[0];
    await writeJsonAtomic(path.join(run.runDir, 'web', 'preparation.json'), {
      mode: 'shared',
      state: 'ready',
    });
    await assert.rejects(verifyAttachedShared(run.runDir), /公共准备引用缺失/);
    await assert.rejects(
      runner.enqueueCodexPhase(run.runDir, 'prepare'),
      /公共准备引用缺失/,
    );
    assert.equal((await readStatus(run.runDir)).sessionId, null);
  } finally {
    await f.cleanup();
  }
});
