import assert from 'node:assert/strict';
import test from 'node:test';
import path from 'node:path';
import os from 'node:os';
import { mkdtemp, rm, writeFile, utimes } from 'node:fs/promises';
import { createHash } from 'node:crypto';
import {
  readJson,
  readStatus,
  writeJsonAtomic,
  transitionStatus,
} from '../server/state.mjs';
import {
  attemptOutputPath,
  readProductionEvidence,
  reconcilePaidOutcome,
  reconcilePaidRun,
  readCurrentPhaseResult,
} from '../server/production-reconciliation.mjs';
const hash = (v) => createHash('sha256').update(v).digest('hex');

test('shared recovery reads the current attempt final and preserves legacy recovery', () => {
  const dir = path.resolve('web');
  const id = '11111111-1111-4111-8111-111111111111';
  assert.equal(
    attemptOutputPath(dir, 'shared', id),
    path.join(dir, 'attempts', id, 'final-shared.json'),
  );
  assert.equal(
    attemptOutputPath(dir, 'shared', null),
    path.join(dir, 'final-shared.json'),
  );
  assert.throws(
    () => attemptOutputPath(dir, 'shared', '../old'),
    /执行标识无效/,
  );
});

async function fixture(t, leaves = false) {
  const dir = await mkdtemp(path.join(os.tmpdir(), 'zb-reconcile-'));
  t.after(() => rm(dir, { recursive: true, force: true }));
  const intake = {
    runId: 'test',
    sku: 'sku',
    model: { preset: '德2' },
    variants: [{ id: 'black' }, { id: 'white' }],
  };
  const prepareResult = { phase: 'prepare', artifacts: [] };
  const variantIds = intake.variants.map((v) => v.id);
  const artifactManifest = [];
  const ledgers = [];
  for (const ids of leaves ? variantIds.map((id) => [id]) : [variantIds]) {
    const base = leaves ? path.join('recovery/prepared/releases', ids[0]) : '.';
    const compile = {
      batch_compile_id: ids.join('-'),
      sku_family_id: 'sku',
      variants: ids.map((id) => ({ variant_id: id })),
    };
    const compilePath = path.join(base, 'batch-compile.json');
    await writeJsonAtomic(path.join(dir, compilePath), compile);
    const bytes = Buffer.from(JSON.stringify(compile, null, 2) + '\n');
    artifactManifest.push({
      path: compilePath,
      sha256: hash(bytes),
      size: bytes.length,
    });
    const jobs = [];
    for (const id of ids) {
      const videoPath = path.join(dir, `${id}.mp4`);
      await writeFile(videoPath, `video-${id}`);
      jobs.push({
        variant_id: id,
        model_name: '德2',
        state: 'succeeded',
        record_id: id === 'black' ? 123 : 456,
        completion_reported: true,
        rendered_quality_status: 'keep',
        download: {
          state: 'succeeded',
          local_path: videoPath,
          bytes: Buffer.byteLength(`video-${id}`),
          sha256: hash(`video-${id}`),
        },
      });
    }
    const ledgerPath = path.join(dir, base, 'ledger.json');
    await writeJsonAtomic(ledgerPath, {
      run_id: leaves ? `test:${ids[0]}` : 'test',
      sku_family_id: 'sku',
      batch_compile_id: compile.batch_compile_id,
      batch_compile_sha256: hash(bytes),
      jobs,
    });
    ledgers.push(ledgerPath);
  }
  await writeJsonAtomic(path.join(dir, 'intake.json'), intake);
  await writeJsonAtomic(
    path.join(dir, 'web/result-prepare.json'),
    prepareResult,
  );
  await writeJsonAtomic(path.join(dir, 'approval.json'), {
    runId: 'test',
    variantIds,
    artifactManifest,
    authorizationFingerprint: hash(
      JSON.stringify({ intake, prepareResult, variantIds, artifactManifest }),
    ),
  });
  return { dir, ledgers };
}

for (const leaves of [false, true])
  test(`known successful records recover to review with ${leaves ? 'per-color' : 'root'} ledgers`, async (t) => {
    const { dir } = await fixture(t, leaves);
    await writeJsonAtomic(path.join(dir, 'web/runtime.json'), {
      phase: 'paid',
      state: 'failed',
      exitCode: 1,
      attemptId: 'current',
    });
    await writeJsonAtomic(path.join(dir, 'web/result-paid.json'), {
      outcome: 'blocked',
      summary: 'old compile error',
      attemptId: 'old',
    });
    const result = await reconcilePaidRun(dir);
    assert.equal(result.outcome, 'needs_review');
    assert.equal(result.artifacts.length, 2);
    const status = await readStatus(dir);
    assert.equal(status.stageKey, 'quality_and_delivery');
    assert.equal(status.execution.state, 'failed');
    assert.equal(status.estimatedRemainingSeconds, null);
    assert.equal(status.etaLabel, '待验收');
    assert.equal(
      (await readCurrentPhaseResult(dir, 'paid')).attemptId,
      'current',
    );
    assert.equal(
      await readJson(path.join(dir, 'web/paid-dispatch.json')),
      null,
    );
  });

test('changed media blocks delivery while the accepted record remains known', async (t) => {
  const { dir } = await fixture(t);
  await writeFile(path.join(dir, 'white.mp4'), 'corrupted');
  const result = reconcilePaidOutcome(await readProductionEvidence(dir));
  assert.equal(result.outcome, 'blocked');
  assert.equal(result.stageKey, 'quality_and_delivery');
  assert.equal(result.artifacts.length, 1);
});

for (const mutation of [
  'compile',
  'authorization',
  'duplicate',
  'model',
  'ledger_hash',
  'missing_record',
  'unknown',
])
  test(`untrusted or uncertain evidence cannot clear submission uncertainty: ${mutation}`, async (t) => {
    const { dir, ledgers } = await fixture(t);
    if (mutation === 'compile')
      await writeJsonAtomic(path.join(dir, 'batch-compile.json'), {});
    else if (mutation === 'authorization')
      await writeJsonAtomic(path.join(dir, 'web/result-prepare.json'), {});
    else {
      const ledger = await readJson(ledgers[0]);
      if (mutation === 'duplicate') ledger.jobs[1].variant_id = 'black';
      if (mutation === 'model') ledger.jobs[0].model_name = '德3';
      if (mutation === 'ledger_hash') ledger.batch_compile_sha256 = 'wrong';
      if (mutation === 'missing_record') ledger.jobs[0].record_id = null;
      if (mutation === 'unknown') ledger.jobs[0].state = 'submission_unknown';
      await writeJsonAtomic(ledgers[0], ledger);
    }
    assert.equal(await reconcilePaidRun(dir), null);
  });

test('old paid results stay hidden after both successful and failed newer attempts', async (t) => {
  const { dir } = await fixture(t);
  const file = path.join(dir, 'web/result-paid.json');
  await writeJsonAtomic(file, {
    attemptId: 'old',
    summary: 'old preflight error',
  });
  for (const state of ['running', 'completed', 'failed']) {
    await writeJsonAtomic(path.join(dir, 'web/runtime.json'), {
      phase: 'paid',
      attemptId: 'new',
      state,
    });
    assert.equal(await readCurrentPhaseResult(dir, 'paid'), null);
  }
  await utimes(file, new Date('2026-01-01'), new Date('2026-01-01'));
  await writeJsonAtomic(path.join(dir, 'web/runtime.json'), {
    phase: 'paid',
    state: 'failed',
    startedAt: '2026-01-02T00:00:00Z',
  });
  assert.equal(await readCurrentPhaseResult(dir, 'paid'), null);
});

test('paused and completed states never display a running countdown', async (t) => {
  const { dir } = await fixture(t);
  for (const state of [
    'blocked',
    'submission_unknown',
    'failed',
    'needs_input',
  ]) {
    const status = await transitionStatus(dir, {
      state,
      stageKey: 'quality_and_delivery',
    });
    assert.equal(status.estimatedRemainingSeconds, null);
    assert.equal(status.etaLabel, '待处理');
  }
  const done = await transitionStatus(dir, {
    state: 'delivered',
    stageKey: 'quality_and_delivery',
  });
  assert.equal(done.etaLabel, '已完成');
});
