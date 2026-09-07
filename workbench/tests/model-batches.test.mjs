import assert from 'node:assert/strict';
import { createHash, randomUUID } from 'node:crypto';
import {
  mkdir,
  mkdtemp,
  readFile,
  readdir,
  rm,
  writeFile,
} from 'node:fs/promises';
import os from 'node:os';
import path from 'node:path';
import test from 'node:test';
import { parseModelBatchPayload } from '../server/validation.mjs';
import {
  persistModelRuns,
  readModelBatch,
  modelBatchForDisplay,
  recoverIncompleteModelSubmissions,
} from '../server/model-batches.mjs';
import { readJson, writeJsonAtomic } from '../server/state.mjs';
import { freshHandoff } from '../server/publishing.mjs';

const payload = {
  sku: 'TEST-SKU',
  modelMode: 'preset',
  modelPresets: ['美1', '德3'],
  amazonUrls: {
    US: 'https://www.amazon.com/dp/TEST',
    DE: 'https://www.amazon.de/dp/TEST',
  },
  variants: [
    { id: 'variant-001', name: 'Black' },
    { id: 'variant-002', name: 'White' },
  ],
};
const policy = {
  model: 'gpt-5.5',
  reasoningEffort: 'high',
  conversationPolicy: 'new-session-per-production-batch',
};
const primaryId = 'zb-20260907-0000000001',
  secondId = 'zb-20260907-0000000002';
async function fixture(root, id = primaryId) {
  const intake = parseModelBatchPayload(payload);
  const runDir = path.join(root, id);
  await mkdir(runDir);
  for (const variant of intake.variants) {
    const bytes = Buffer.from(`original ${variant.name}`),
      relativePath = `uploads/${variant.id}/1.png`;
    await mkdir(path.dirname(path.join(runDir, relativePath)), {
      recursive: true,
    });
    await writeFile(path.join(runDir, relativePath), bytes);
    variant.images.push({
      relativePath,
      sha256: createHash('sha256').update(bytes).digest('hex'),
      size: bytes.length,
      originalName: '1.png',
      mimeType: 'image/png',
    });
  }
  return { root, primaryRunId: id, intake, policy, newRunId: () => secondId };
}
async function temporary(action) {
  const root = await mkdtemp(path.join(os.tmpdir(), 'zibuyu-model-batch-'));
  try {
    await action(root);
  } finally {
    await rm(root, { recursive: true, force: true });
  }
}

test('multi-model parser locks model identity, market and source independently; old requests still work', () => {
  const result = parseModelBatchPayload(payload);
  assert.deepEqual(
    result.models.map((model) => [model.preset, model.locale, model.size]),
    [
      ['美1', 'en-US', 'S'],
      ['德3', 'de-DE', '2XL'],
    ],
  );
  assert.equal(result.sourceUrls.DE, payload.amazonUrls.DE);
  const old = parseModelBatchPayload({
    ...payload,
    modelPresets: undefined,
    modelPreset: '德1',
    amazonUrl: 'https://www.amazon.de/',
  });
  assert.equal(old.models.length, 1);
  assert.equal(old.model.preset, '德1');
  const missing = parseModelBatchPayload({
    ...payload,
    amazonUrls: { US: payload.amazonUrls.US },
  });
  assert.equal(missing.sourceUrls.DE, '');
});

test('model arrays reject duplicates, invalid members, mixed custom and conflicting scalar selection', () => {
  for (const modelPresets of [
    [],
    ['德1', '德1'],
    ['德9'],
    [null],
    '德1',
    Array(7).fill('德1'),
  ])
    assert.throws(() => parseModelBatchPayload({ ...payload, modelPresets }));
  assert.throws(
    () => parseModelBatchPayload({ ...payload, modelPreset: '德1' }),
    /不一致/,
  );
  assert.throws(
    () => parseModelBatchPayload({ ...payload, modelMode: 'custom' }),
    /单独/,
  );
  assert.throws(
    () =>
      parseModelBatchPayload({
        ...payload,
        amazonUrl: 'https://www.amazon.com/',
      }),
    /分别/,
  );
});

test('two models times two colors persists isolated runs and keeps original images unchanged', () =>
  temporary(async (root) => {
    const created = await persistModelRuns(await fixture(root));
    assert.equal(created.runs.length, 2);
    const originals = await Promise.all(
      created.runs.map((run) => readJson(path.join(run.runDir, 'intake.json'))),
    );
    assert.deepEqual(
      originals.map((item) => item.model.locale),
      ['en-US', 'de-DE'],
    );
    assert.deepEqual(
      originals.map((item) => item.amazonUrl),
      [payload.amazonUrls.US, payload.amazonUrls.DE],
    );
    assert.ok(
      originals.every(
        (item) => item.models === undefined && item.variants.length === 2,
      ),
    );
    assert.ok(
      created.runs.every(
        (item) =>
          item.status.variantCount === 2 && item.status.sessionId === null,
      ),
    );
    const batch = await readModelBatch(created.runDir);
    assert.equal(batch.plannedVideoCount, 4);
    assert.equal(batch.allDelivered, false);
    for (const variant of originals[0].variants) {
      const relative = variant.images[0].relativePath;
      assert.deepEqual(
        await readFile(path.join(created.runDir, relative)),
        await readFile(path.join(created.runs[1].runDir, relative)),
      );
    }
    await writeFile(
      path.join(
        created.runs[1].runDir,
        originals[1].variants[0].images[0].relativePath,
      ),
      'changed copy',
    );
    assert.equal(
      (
        await readFile(
          path.join(
            created.runDir,
            originals[0].variants[0].images[0].relativePath,
          ),
        )
      ).toString(),
      'original Black',
    );
  }));

test('copy failure rolls back only new directories; ID collision never deletes an existing run', () =>
  temporary(async (root) => {
    const data = await fixture(root);
    await assert.rejects(
      persistModelRuns({
        ...data,
        copy: async () => {
          throw new Error('simulated disk failure');
        },
      }),
      /disk failure/,
    );
    assert.deepEqual(
      (await readdir(root)).filter((name) => name.startsWith('zb-')),
      [],
    );
    const again = await fixture(root);
    await mkdir(path.join(root, secondId));
    await writeFile(path.join(root, secondId, 'keep.txt'), 'existing');
    await assert.rejects(persistModelRuns(again), /EEXIST/);
    assert.equal(
      await readFile(path.join(root, secondId, 'keep.txt'), 'utf8'),
      'existing',
    );
  }));

test('repeated request returns original runs; changed payload cannot reuse that request ID', () =>
  temporary(async (root) => {
    const requestId = randomUUID();
    const first = await persistModelRuns({
      ...(await fixture(root)),
      requestId,
    });
    const repeated = await persistModelRuns({
      ...(await fixture(root, 'zb-20260907-0000000003')),
      requestId,
    });
    assert.equal(repeated.idempotent, true);
    assert.deepEqual(
      repeated.runs.map((run) => run.runId),
      first.runs.map((run) => run.runId),
    );
    assert.equal(
      (await readdir(root)).filter((name) => name.startsWith('zb-')).length,
      2,
    );
    const changed = await fixture(root, 'zb-20260907-0000000004');
    changed.intake.notes = 'different request';
    await assert.rejects(
      persistModelRuns({ ...changed, requestId }),
      /内容已变化/,
    );
  }));

test('group scope changes and incomplete siblings block publication before any external helper', () =>
  temporary(async (root) => {
    const created = await persistModelRuns(await fixture(root));
    const statusFile = path.join(created.runDir, 'web/status.json');
    await writeJsonAtomic(statusFile, {
      ...(await readJson(statusFile)),
      state: 'delivered',
    });
    await assert.rejects(freshHandoff(created.runDir), /同批次还有模特/);
    const sibling = path.join(created.runs[1].runDir, 'intake.json');
    const item = await readJson(sibling);
    item.model.preset = '德1';
    await writeJsonAtomic(sibling, item);
    await assert.rejects(readModelBatch(created.runDir), /范围发生变化/);
    const display = await modelBatchForDisplay(created.runDir);
    assert.equal(display.modelBatch, null);
    assert.match(display.modelBatchError, /范围发生变化/);
  }));

test('startup releases an interrupted request and removes only journal-owned staging', () =>
  temporary(async (root) => {
    await fixture(root);
    await mkdir(path.join(root, secondId));
    await writeFile(path.join(root, secondId, 'existing.txt'), 'not owned');
    const requestId = randomUUID(),
      receiptPath = path.join(root, '_intake-requests', `${requestId}.json`);
    await writeJsonAtomic(receiptPath, {
      state: 'pending',
      ownerProcessId: 2147483647,
      ownedRunIds: [primaryId],
      plannedRunIds: [primaryId, secondId],
      batchId: primaryId.replace('zb-', 'mb-'),
    });
    assert.deepEqual(await recoverIncompleteModelSubmissions(root), [
      requestId,
    ]);
    await assert.rejects(readFile(receiptPath), { code: 'ENOENT' });
    assert.equal(
      await readFile(path.join(root, secondId, 'existing.txt'), 'utf8'),
      'not owned',
    );
    const retry = await fixture(root);
    const result = await persistModelRuns({
      ...retry,
      requestId,
      newRunId: () => 'zb-20260907-0000000003',
    });
    assert.equal(result.runs.length, 2);
  }));

test('startup preserves active uploads and execution evidence', () =>
  temporary(async (root) => {
    await fixture(root);
    const requestId = randomUUID(),
      receiptPath = path.join(root, '_intake-requests', `${requestId}.json`);
    const receipt = {
      state: 'pending',
      ownerProcessId: process.pid,
      ownedRunIds: [primaryId],
      plannedRunIds: [primaryId],
    };
    await writeJsonAtomic(receiptPath, receipt);
    assert.deepEqual(await recoverIncompleteModelSubmissions(root), []);
    await writeJsonAtomic(path.join(root, primaryId, 'approval.json'), {
      evidence: 'preserve',
    });
    await writeJsonAtomic(receiptPath, {
      ...receipt,
      ownerProcessId: 2147483647,
    });
    assert.deepEqual(await recoverIncompleteModelSubmissions(root), []);
    assert.ok(await readJson(path.join(root, primaryId, 'approval.json')));
  }));
