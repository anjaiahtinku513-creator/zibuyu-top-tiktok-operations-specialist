import assert from 'node:assert/strict';
import test from 'node:test';
import { mkdtemp, rm, mkdir, readFile } from 'node:fs/promises';
import os from 'node:os';
import path from 'node:path';
import { writeJsonAtomic } from '../server/state.mjs';
import {
  CAPTION_TRANSPORT,
  normalizeIntent,
  scheduleIso,
  plannedSlot,
  assertCaption,
  digest,
  assertSlots,
  validateProposal,
  observedAction,
  freshHandoff,
  saveIntent,
  publishingDetail,
  approvePublishing,
  reconcilePublishing,
  recoverPublishing,
} from '../server/publishing.mjs';

test('account-local slots use actual New York and Berlin winter/summer offsets', () => {
  assert.equal(
    scheduleIso('2027-01-10', '07:00', 'America/New_York'),
    '2027-01-10T07:00:00-05:00',
  );
  assert.equal(
    scheduleIso('2027-07-10', '07:00', 'America/New_York'),
    '2027-07-10T07:00:00-04:00',
  );
  assert.equal(
    scheduleIso('2027-01-10', '18:00', 'Europe/Berlin'),
    '2027-01-10T18:00:00+01:00',
  );
  assert.equal(
    scheduleIso('2027-07-10', '18:00', 'Europe/Berlin'),
    '2027-07-10T18:00:00+02:00',
  );
});
test('extra colors cannot silently roll over; explicitly chosen carryover preserves times', () => {
  assert.throws(() => plannedSlot({ date: '2027-01-31' }, 3), /明确选择/);
  assert.deepEqual(
    plannedSlot({ date: '2027-01-31', spreadAcrossDays: true }, 3),
    { date: '2027-02-01', time: '07:00' },
  );
});
test('intake validates calendar and refuses invented account aliases', () => {
  assert.throws(() => normalizeIntent({ accountCode: '美7' }), /账号/);
  assert.throws(
    () => normalizeIntent({ accountCode: '美1', date: '2027-02-30' }),
    /日期/,
  );
  assert.equal(
    normalizeIntent({ accountCode: '德2', pid: '123', date: '2027-01-01' })
      .spreadAcrossDays,
    false,
  );
});
test('captions retain five distinct tags and reject the prohibited brand', () => {
  assert.doesNotThrow(() =>
    assertCaption('A complete caption #Outfit #Style #Knits #Autumn #Cardigan'),
  );
  assert.throws(() => assertCaption('A #One #One #Three #Four #Five'), /五个/);
  assert.throws(
    () => assertCaption('A #ImilyBela #Two #Three #Four #Five'),
    /非品牌/,
  );
});
test('content binding is deterministic but any outbound edit changes approval hash', () => {
  assert.equal(
    digest({ b: 2, a: { d: 4, c: 3 } }),
    digest({ a: { c: 3, d: 4 }, b: 2 }),
  );
  assert.notEqual(
    digest({ caption: 'original', time: '07:00' }),
    digest({ caption: 'changed', time: '07:00' }),
  );
});
test('past times, occupied slots and uncertain daily allocations block scheduling', () => {
  const row = {
    accountCode: '美1',
    localDate: '2027-01-01',
    request: { scheduled_time: '2027-01-01T07:00:00-05:00' },
  };
  assert.throws(() => assertSlots([row], [], Date.parse('2028-01-01')), /过去/);
  assert.throws(
    () => assertSlots([row], [row], Date.parse('2026-01-01')),
    /已有发布/,
  );
  assert.throws(
    () => assertSlots([row], [row, row, row], Date.parse('2026-01-01')),
    /三条/,
  );
});
test('model-written claims cannot unlock the unresolved full-caption interface', () => {
  assert.equal(CAPTION_TRANSPORT.verified, false);
  assert.throws(
    () =>
      validateProposal(
        {
          captionMapping: {
            field: 'video_title',
            evidenceSource: 'invented',
            evidenceExcerpt: 'trust me',
            maxLength: 10000,
          },
        },
        { items: [] },
        {},
      ),
    /仍待验证/,
  );
});
test('an ID or empty evidence never proves scheduled, and unknown stays unknown', () => {
  assert.equal(
    observedAction({
      scheduleId: 's1',
      state: 'scheduled',
      observedAt: 'invalid',
      verificationEvidence: {},
    }).state,
    'receipt_received',
  );
  assert.equal(
    observedAction({ state: 'dispatching' }).state,
    'submission_unknown',
  );
  assert.equal(observedAction({ state: 'claimed' }).state, 'not_dispatched');
});
test('only a matching explicit platform readback can establish scheduled or published', () => {
  const base = {
    scheduleId: 's1',
    observedAt: new Date().toISOString(),
    verificationEvidence: { schedule_id: 's1', status: 'scheduled' },
  };
  assert.equal(observedAction(base).state, 'scheduled');
  assert.equal(
    observedAction({
      ...base,
      verificationEvidence: { schedule_id: 'different', status: 'published' },
    }).state,
    'receipt_received',
  );
  assert.equal(
    observedAction({
      ...base,
      verificationEvidence: { schedule_id: 's1', status: 'published' },
    }).state,
    'published',
  );
});
async function fixture(t) {
  const dir = await mkdtemp(path.join(os.tmpdir(), 'zibuyu-publish-test-'));
  t.after(async () => {
    const resolved = path.resolve(dir);
    assert.ok(resolved.startsWith(path.resolve(os.tmpdir()) + path.sep));
    assert.ok(path.basename(resolved).startsWith('zibuyu-publish-test-'));
    await rm(resolved, { recursive: true, force: true });
  });
  await mkdir(path.join(dir, 'web'));
  await writeJsonAtomic(path.join(dir, 'web', 'status.json'), {
    state: 'running',
    modelPreset: '美1',
  });
  return dir;
}
test('saving intent during production does not start external preparation', async (t) => {
  const dir = await fixture(t);
  await saveIntent(dir, { accountCode: '美1', pid: '123', date: '2027-01-01' });
  const detail = await publishingDetail(dir);
  assert.equal(detail.production.state, 'running');
  assert.equal(detail.manifest, null);
  assert.equal(detail.actions.length, 0);
});
test('production state alone and a stale handoff cannot bypass the official batch gate', async (t) => {
  const dir = await fixture(t);
  await writeJsonAtomic(path.join(dir, 'publish-handoff.json'), {
    readiness: true,
    items: [{ status: 'succeeded' }],
  });
  await assert.rejects(freshHandoff(dir), /尚未完成/);
  await writeJsonAtomic(path.join(dir, 'web', 'status.json'), {
    state: 'delivered',
  });
  await assert.rejects(freshHandoff(dir));
  const detail = await publishingDetail(dir);
  assert.equal(detail.snapshot, null);
});
test('approval cannot be forged before the final manifest is prepared', async (t) => {
  const dir = await fixture(t);
  await assert.rejects(
    approvePublishing(dir, path.dirname(dir), {
      confirm: true,
      manifestHash: 'fake',
    }),
    /尚未准备/,
  );
  await assert.rejects(
    readFile(path.join(dir, 'publishing', 'dispatch-claim.json')),
    { code: 'ENOENT' },
  );
});
test('duplicate approval is idempotent; unknown without receipt cannot be resubmitted', async (t) => {
  const dir = await fixture(t);
  await writeJsonAtomic(path.join(dir, 'publishing', 'approval.json'), {
    manifestHash: 'original',
  });
  await writeJsonAtomic(path.join(dir, 'publishing', 'ledger.json'), {
    actions: [{ actionId: 'a', state: 'dispatching' }],
  });
  const results = await Promise.all(
    [1, 2].map(() =>
      approvePublishing(dir, path.dirname(dir), {
        confirm: true,
        manifestHash: 'original',
      }),
    ),
  );
  assert.ok(results.every((result) => result.idempotent));
  await assert.rejects(saveIntent(dir, { accountCode: '美2' }), /已有发布动作/);
  await assert.rejects(reconcilePublishing(dir), /尚无可核对/);
});
test('restart retains uncertain submissions instead of dispatching them again', async (t) => {
  const root = await fixture(t);
  const dir = path.join(root, 'batch');
  await writeJsonAtomic(path.join(dir, 'publishing', 'status.json'), {
    state: 'submitting',
  });
  await recoverPublishing(root);
  assert.equal(
    JSON.parse(await readFile(path.join(dir, 'publishing', 'status.json')))
      .state,
    'submission_unknown',
  );
});
