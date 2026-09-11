import assert from 'node:assert/strict';
import test from 'node:test';
import {
  auditSchedule as audit,
  observedAction as observe,
  explicitInstant,
  normalizeCheckPublishResult,
  summarizePublishingActions,
  canContinueScheduling as canContinue,
} from '../server/publish-audit.mjs';
import {
  postScheduleAuditInstruction,
  publishingDetail,
  digest,
} from '../server/publishing.mjs';
import { archivePublishEvent } from '../server/publish-evidence.mjs';
import { mkdtemp, rm, mkdir, writeFile } from 'node:fs/promises';
import { writeJsonAtomic } from '../server/state.mjs';
import os from 'node:os';
import path from 'node:path';

const now = Date.parse('2026-09-08T02:00:00Z');
const caption =
  'Room to move. #OversizedTee #CasualStyle #EasyOutfit #DailyWear #FallStyle';
const videoUrl = 'https://media.example.com/approved.mp4';
function eventsFor(action) {
  return [
    ['check_publish', action.verificationEvidence],
    ['publish_video', action.creationEvidence],
  ].flatMap(([tool, evidence]) =>
    evidence
      ? [
          archivePublishEvent(
            {
              type: 'item.completed',
              item: {
                id: evidence.callId,
                type: 'mcp_tool_call',
                server: 'PopBoom',
                tool,
                status: 'completed',
                arguments:
                  tool === 'check_publish'
                    ? { schedule_id: action.scheduleId || action.schedule_id }
                    : structuredClone(action.request),
                result: structuredClone(evidence.result),
              },
            },
            'publish-submit',
            evidence.attemptId,
            evidence.queriedAt,
          ),
        ]
      : [],
  );
}
const auditSchedule = (action, at = now, events = eventsFor(action)) =>
  audit(action, at, events);
const observedAction = (action, at = now, events = eventsFor(action)) =>
  observe(action, at, events);
const canContinueScheduling = (actions, at = now) =>
  canContinue(actions, at, actions.flatMap(eventsFor));
function fixture(fields = {}, evidence = {}) {
  return {
    actionId: 'a1',
    state: 'receipt_received',
    scheduleId: 's1',
    logId: 'l1',
    claimedAt: '2026-09-08T01:58:00Z',
    dispatchStartedAt: '2026-09-08T01:59:00Z',
    request: {
      scheduled_time: '2026-09-09T07:00:00-04:00',
      channel_id: 'c1',
      product_id: '1730268153835590528',
      video_title: caption,
      video_url: videoUrl,
    },
    verificationEvidence: {
      tool: 'check_publish',
      attemptId: 'attempt1',
      callId: 'query1',
      queriedAt: '2026-09-08T01:59:30Z',
      result: {
        schedule_id: 's1',
        log_id: 'l1',
        status: 'pending',
        scheduled_time: '2026-09-09T07:00:00-04:00',
        channel_id: 'c1',
        product_id: '1730268153835590528',
        video_title: caption,
        video_url: videoUrl,
        ...fields,
      },
      ...evidence,
    },
  };
}
function textResult(fields = {}) {
  const value = {
    schedule_id: 's1',
    log_id: 'l1',
    status: 'pending',
    scheduled_time: '2026-09-09 07:00:00 -0400',
    channel_id: 'c1',
    product_id: '1730268153835590528',
    video_title: caption,
    video_url: videoUrl,
    ...fields,
  };
  return {
    content: [
      {
        type: 'text',
        text: `⏳ 定时发布状态: ${value.status === 'pending' ? '等待发布' : value.status}\n\n📋 排期 ID: ${value.schedule_id}\n📋 发布记录 ID: ${value.log_id}\n📺 渠道 ID: ${value.channel_id}\n🎬 标题: ${value.video_title}\n🕐 计划发布时间: ${value.scheduled_time}\n🛍️ 商品 ID: ${value.product_id}\n🎥 视频 URL: ${value.video_url}\n📦 商品标题: Oversized tee\n\n💡 视频已上传至 TikTok，系统将在指定时间自动发布`,
      },
    ],
  };
}

test('query with matching request passes; pending is a platform fact separate from audit', () => {
  const result = observedAction(fixture(), now);
  assert.equal(result.platformState, 'pending');
  assert.equal(result.state, 'scheduled');
  assert.equal(result.postScheduleAudit.status, 'passed');
  assert.equal(
    result.postScheduleAudit.observed.scheduledInstant,
    '2026-09-09T11:00:00.000Z',
  );
});

test('12-hour regression: same wall clock with +08:00 is not -04:00', () => {
  const result = observedAction(
    fixture({ scheduled_time: '2026-09-09T07:00:00+08:00' }),
    now,
  );
  assert.equal(result.state, 'scheduled');
  assert.equal(result.postScheduleAudit.status, 'mismatch');
  const { expected, observed } = result.postScheduleAudit;
  assert.equal(
    Date.parse(expected.scheduledInstant) -
      Date.parse(observed.scheduledInstant),
    12 * 60 * 60 * 1000,
  );
  assert.equal(summarizePublishingActions([result]).state, 'schedule_mismatch');
});

test('Z, compact offset, fractional seconds and different offsets accept the same instant', () => {
  for (const scheduled_time of [
    '2026-09-09T11:00:00Z',
    '2026-09-09T19:00:00+08:00',
    '2026-09-09 07:00:00 -0400',
    '2026-09-09T11:00:00.000Z',
  ]) {
    assert.equal(
      auditSchedule(fixture({ scheduled_time }), now).status,
      'passed',
      scheduled_time,
    );
  }
});

test('timezone-free or malformed times remain reviewable without inventing an instant', () => {
  for (const scheduled_time of [
    '2026-09-09T07:00:00',
    '2026-09-09 07:00:00',
    '',
    undefined,
    '2026-02-30T07:00:00Z',
    '2026-09-09T07:00:00+14:30',
    '2026-09-09T07:00:00-00:00',
    '2026-09-09 07:00:00 -0000',
  ]) {
    const result = observedAction(fixture({ scheduled_time }), now);
    assert.equal(result.state, 'scheduled');
    assert.equal(result.postScheduleAudit.status, 'needs_review');
    assert.equal(result.postScheduleAudit.observed.scheduledInstant, null);
  }
  const action = fixture();
  action.request.scheduled_time = '2026-09-09T07:00:00';
  assert.equal(auditSchedule(action, now).status, 'needs_review');
  assert.equal(explicitInstant('2026-09-09T24:00:00Z'), null);
});

test('creation echoes, unmarked local data and prewritten audit claims cannot prove a query', () => {
  for (const tool of ['publish_video', 'manifest', undefined]) {
    const action = fixture({}, { tool });
    action.postScheduleAudit = { status: 'passed' };
    const result = observedAction(action, now);
    assert.equal(result.postScheduleAudit.status, 'needs_review');
    assert.equal(result.state, 'receipt_received');
  }
  const local = fixture({}, { result: { request: fixture().request } });
  assert.equal(auditSchedule(local, now).status, 'needs_review');
});

test('missing, future, invalid and timezone-free observation timestamps do not pass', () => {
  for (const queriedAt of [
    undefined,
    '',
    'invalid',
    '2026-09-08T01:59:30',
    '2026-09-08T02:00:01Z',
  ]) {
    assert.equal(
      auditSchedule(fixture({}, { queriedAt }), now).status,
      'needs_review',
      String(queriedAt),
    );
  }
});

test('historical as-of success stays valid, but a query before submission or a new reconcile does not', () => {
  const action = fixture();
  assert.equal(
    auditSchedule(action, now + 90 * 24 * 60 * 60 * 1000).status,
    'passed',
  );
  assert.equal(
    auditSchedule(fixture({}, { queriedAt: '2026-09-08T01:58:59Z' }), now)
      .status,
    'needs_review',
  );
  action.verificationRequiredAfter = '2026-09-08T01:59:45Z';
  assert.equal(auditSchedule(action, now).status, 'needs_review');
  action.verificationEvidence.queriedAt = '2026-09-08T01:59:50Z';
  assert.equal(auditSchedule(action, now).status, 'passed');
});

test('a matched log ID cannot hide a different schedule ID; missing IDs never pass', () => {
  assert.equal(
    auditSchedule(fixture({ schedule_id: 'wrong' }), now).status,
    'mismatch',
  );
  assert.equal(
    observedAction(fixture({ schedule_id: 'wrong', status: 'published' }), now)
      .state,
    'receipt_received',
  );
  assert.equal(
    auditSchedule(fixture({ schedule_id: undefined, log_id: undefined }), now)
      .status,
    'needs_review',
  );
  const action = fixture();
  action.scheduleId = null;
  action.logId = null;
  assert.equal(auditSchedule(action, now).status, 'needs_review');
});

test('channel, PID and whole caption are compared exactly; missing fields are not copied from requests', () => {
  for (const fields of [
    { channel_id: 'c2' },
    { product_id: '1730268153835590529' },
    { video_title: caption.replace('#FallStyle', '') },
    { video_title: caption + ' ' },
  ]) {
    assert.equal(auditSchedule(fixture(fields), now).status, 'mismatch');
  }
  for (const key of ['channel_id', 'product_id', 'video_title']) {
    assert.equal(
      auditSchedule(fixture({ [key]: undefined }), now).status,
      'needs_review',
    );
  }
  assert.equal(
    auditSchedule(fixture({ product_id: 1730268153835590528 }), now).status,
    'needs_review',
  );
});

test('JSON wrappers, MCP JSON and field-text provide equivalent audit results', () => {
  const raw = fixture().verificationEvidence.result;
  for (const result of [
    raw,
    { data: raw },
    { result: { data: raw } },
    { content: [{ type: 'text', text: JSON.stringify(raw) }] },
    textResult(),
  ]) {
    const audit = auditSchedule(fixture({}, { result }), now);
    assert.equal(audit.status, 'passed', JSON.stringify(audit));
    assert.equal(audit.observed.caption, caption);
    assert.equal(audit.observed.scheduledInstant, '2026-09-09T11:00:00.000Z');
  }
});

test('MCP text preserves a multiline caption and ignores bare metadata words inside it', () => {
  const multiline = `${caption}\n计划发布时间: 2026-09-09T19:00:00+08:00`;
  const action = fixture(
    {},
    { result: textResult({ video_title: multiline }) },
  );
  action.request.video_title = multiline;
  assert.equal(auditSchedule(action, now).status, 'passed');
  const injected = textResult({
    video_title: `${caption}\n计划发布时间: 2026-09-09T07:00:00-04:00`,
  });
  injected.content[0].text = injected.content[0].text.replace(
    /^🕐 计划发布时间:.*\n/m,
    '',
  );
  const audit = auditSchedule(fixture({}, { result: injected }), now);
  assert.notEqual(audit.status, 'passed');
  assert.equal(audit.observed.scheduledTime, undefined);
  assert.ok(audit.observed.caption.includes('计划发布时间:'));
});

test('known text and JSON mismatch cases agree', () => {
  for (const fields of [
    { scheduled_time: '2026-09-09T07:00:00+08:00' },
    { scheduled_time: '2026-09-09 07:00:00' },
    { channel_id: 'wrong' },
    { product_id: 'wrong' },
    { video_title: caption.slice(0, 25) },
    { schedule_id: 'wrong' },
  ]) {
    assert.equal(
      auditSchedule(fixture(fields), now).status,
      auditSchedule(fixture({}, { result: textResult(fields) }), now).status,
    );
  }
});

test('conflicting structured and text values cannot pass; tool errors cannot pass', () => {
  const response = {
    structuredContent: fixture().verificationEvidence.result,
    ...textResult({ scheduled_time: '2026-09-09T07:00:00+08:00' }),
  };
  assert.equal(
    auditSchedule(fixture({}, { result: response }), now).status,
    'needs_review',
  );
  assert.equal(normalizeCheckPublishResult(response).issues.length, 1);
  assert.equal(
    auditSchedule(
      fixture(
        {},
        { result: { ...fixture().verificationEvidence.result, isError: true } },
      ),
      now,
    ).status,
    'needs_review',
  );
});

test('published fact is preserved when time is missing, wrong or a new reconcile needs fresh evidence', () => {
  for (const scheduled_time of [undefined, '2026-09-09T07:00:00+08:00']) {
    const action = observedAction(
      fixture({ status: 'published', scheduled_time }),
      now,
    );
    assert.equal(action.state, 'published');
    assert.notEqual(action.postScheduleAudit.status, 'passed');
    const summary = summarizePublishingActions([action]);
    assert.equal(summary.state, 'published');
    assert.ok(summary.note.includes('已由平台确认发布'));
    assert.ok(!summary.note.includes('排期已审核'));
  }
  const action = fixture({ status: 'published' });
  action.verificationRequiredAfter = '2026-09-08T01:59:45Z';
  assert.equal(observedAction(action, now).state, 'published');
  assert.equal(auditSchedule(action, now).status, 'needs_review');
});

test('continuation stops on any pending audit and never turns claimed into a dispatch', () => {
  const claimed = { state: 'claimed', actionId: 'a2' };
  assert.equal(canContinueScheduling([fixture(), claimed], now), true);
  for (const first of [
    fixture({ scheduled_time: undefined }),
    fixture({ scheduled_time: '2026-09-09T07:00:00+08:00' }),
    { state: 'dispatching' },
  ]) {
    assert.equal(canContinueScheduling([first, claimed], now), false);
    assert.equal(observedAction(claimed, now).state, 'claimed');
  }
  assert.ok(postScheduleAuditInstruction.includes('canContinueScheduling'));
  assert.ok(postScheduleAuditInstruction.includes('独立调用 check_publish'));
});

test('summary requires every necessary audit, including partial submission; published remains separate', () => {
  const scheduled = observedAction(fixture(), now);
  const published = observedAction(fixture({ status: 'published' }), now);
  assert.equal(
    summarizePublishingActions([scheduled, published]).postScheduleAudit.status,
    'passed',
  );
  assert.equal(
    summarizePublishingActions([scheduled, published]).state,
    'scheduled',
  );
  assert.equal(
    summarizePublishingActions([
      scheduled,
      observedAction({ state: 'claimed' }, now),
    ]).state,
    'schedule_needs_review',
  );
  assert.equal(
    summarizePublishingActions([]).postScheduleAudit.status,
    'needs_review',
  );
});

test('detail recomputes audit instead of trusting a stored scheduled success banner', async (t) => {
  const dir = await mkdtemp(path.join(os.tmpdir(), 'zibuyu-audit-test-'));
  t.after(async () => {
    assert.ok(
      path.resolve(dir).startsWith(path.resolve(os.tmpdir()) + path.sep),
    );
    assert.ok(path.basename(dir).startsWith('zibuyu-audit-test-'));
    await rm(dir, { recursive: true, force: true });
  });
  const action = fixture({ scheduled_time: '2026-09-09 07:00:00' });
  action.verificationEvidence.queriedAt = new Date(
    Date.now() - 1000,
  ).toISOString();
  action.dispatchStartedAt = new Date(Date.now() - 2000).toISOString();
  await writeAuditFiles(dir, [action], [action]);
  await writeJsonAtomic(path.join(dir, 'publishing', 'status.json'), {
    state: 'scheduled',
    note: 'old success',
  });
  const detail = await publishingDetail(dir);
  assert.equal(detail.status.state, 'schedule_needs_review');
  assert.equal(detail.actions[0].state, 'scheduled');
  assert.equal(detail.actions[0].postScheduleAudit.status, 'needs_review');
});

async function writeAuditFiles(dir, actions, rows) {
  const manifest = { rows };
  manifest.manifestHash = digest(manifest);
  await writeJsonAtomic(
    path.join(dir, 'publishing', 'manifest.json'),
    manifest,
  );
  await writeJsonAtomic(path.join(dir, 'publishing', 'approval.json'), {
    actionIds: rows.map((row) => row.actionId),
    manifestHash: manifest.manifestHash,
  });
  await writeJsonAtomic(path.join(dir, 'publishing', 'ledger.json'), {
    actions,
  });
  await mkdir(path.join(dir, 'publishing', 'web'), { recursive: true });
  await writeFile(
    path.join(dir, 'publishing', 'web', 'codex-events-publish-submit.jsonl'),
    actions
      .flatMap(eventsFor)
      .map((event) => JSON.stringify(event))
      .join('\n'),
  );
}

test('a self-asserted check_publish source or a changed ledger timestamp cannot forge fresh evidence', () => {
  const action = fixture();
  assert.equal(audit(action, now).status, 'needs_review');
  const events = eventsFor(action);
  action.verificationRequiredAfter = '2026-09-08T01:59:45Z';
  action.observedAt = '2026-09-08T01:59:50Z';
  action.verificationEvidence.queriedAt = action.observedAt;
  assert.equal(auditSchedule(action, now, events).status, 'needs_review');
  action.verificationEvidence.result.video_title = 'forged';
  assert.equal(
    auditSchedule(action, now, events).checks.find(
      (item) => item.field === 'source',
    ).status,
    'needs_review',
  );
});

test('video identity needs an exact URL or matching same-type ID from the real creation event', () => {
  assert.equal(
    auditSchedule(
      fixture({ video_url: 'https://media.example.com/wrong.mp4' }),
      now,
    ).status,
    'mismatch',
  );
  assert.equal(
    auditSchedule(fixture({ video_url: undefined }), now).status,
    'needs_review',
  );
  const action = fixture({ video_url: undefined, tiktok_file_id: 'file1' });
  action.creationEvidence = {
    tool: 'publish_video',
    callId: 'create1',
    attemptId: 'attempt1',
    queriedAt: '2026-09-08T01:59:10Z',
    result: { schedule_id: 's1', tiktok_file_id: 'file1' },
  };
  assert.equal(auditSchedule(action, now).status, 'passed');
  const events = eventsFor(action);
  action.verificationEvidence.result.tiktok_file_id = 'file2';
  const wrongEvents = eventsFor(action);
  assert.equal(auditSchedule(action, now, wrongEvents).status, 'mismatch');
  assert.equal(
    auditSchedule(action, now, events).checks.find(
      (item) => item.field === 'source',
    ).status,
    'needs_review',
  );
  const missingChain = fixture({
    video_url: undefined,
    tiktok_file_id: 'file1',
  });
  assert.equal(auditSchedule(missingChain, now).status, 'needs_review');
  const mixed = fixture({ video_url: undefined, tiktok_video_id: 'file1' });
  mixed.creationEvidence = action.creationEvidence;
  assert.equal(auditSchedule(mixed, now).status, 'needs_review');
});

test('missing optional cover/music/AI fields are not_returned and do not fail required checks', () => {
  const audit = auditSchedule(fixture(), now);
  assert.equal(audit.status, 'passed');
  for (const field of ['cover', 'music', 'ai'])
    assert.equal(
      audit.checks.find((item) => item.field === field).status,
      'not_returned',
    );
  assert.doesNotThrow(() =>
    normalizeCheckPublishResult({
      content: [
        {
          type: 'text',
          text: 'cover_timestamp_ms: 0\nmusic_id: none\nis_ai_generated: false',
        },
      ],
    }),
  );
});

test('whole-batch readback cannot pass with missing, extra, duplicate or altered ledger entries', async (t) => {
  const dir = await mkdtemp(path.join(os.tmpdir(), 'zibuyu-audit-scope-'));
  t.after(async () => {
    assert.ok(
      path.resolve(dir).startsWith(path.resolve(os.tmpdir()) + path.sep),
    );
    assert.ok(path.basename(dir).startsWith('zibuyu-audit-scope-'));
    await rm(dir, { recursive: true, force: true });
  });
  const first = fixture({ status: 'published' });
  first.verificationEvidence.queriedAt = new Date(
    Date.now() - 1000,
  ).toISOString();
  first.dispatchStartedAt = new Date(Date.now() - 2000).toISOString();
  const second = {
    ...fixture(),
    actionId: 'a2',
    state: 'claimed',
    scheduleId: null,
    logId: null,
    verificationEvidence: null,
  };
  const rows = [first, second];
  const altered = {
    ...first,
    request: { ...first.request, video_title: 'changed' },
  };
  for (const actions of [
    [first],
    [first, first],
    [first, second, { ...second, actionId: 'extra' }],
    [altered, second],
  ]) {
    await writeAuditFiles(dir, actions, rows);
    await writeJsonAtomic(path.join(dir, 'publishing', 'status.json'), {
      state: 'scheduled',
      note: 'old success',
    });
    const detail = await publishingDetail(dir);
    assert.notEqual(detail.status.postScheduleAudit.status, 'passed');
    assert.notEqual(detail.status.state, 'scheduled');
    assert.notEqual(detail.status.state, 'published');
    if (actions.length === 1)
      assert.equal(detail.actions[0].state, 'published');
  }
  await writeAuditFiles(dir, [first], rows);
  let detail = await publishingDetail(dir);
  assert.equal(detail.status.postScheduleAudit.total, 2);
  assert.equal(detail.status.postScheduleAudit.needsReview, 1);
  await rm(path.join(dir, 'publishing', 'ledger.json'));
  detail = await publishingDetail(dir);
  assert.equal(detail.status.state, 'schedule_needs_review');
  assert.equal(detail.status.postScheduleAudit.needsReview, 2);
  await writeAuditFiles(dir, [first, second], rows);
  const changed = {
    rows: [
      { ...first, request: { ...first.request, video_title: 'tampered' } },
      second,
    ],
    manifestHash: digest({ rows }),
  };
  await writeJsonAtomic(path.join(dir, 'publishing', 'manifest.json'), changed);
  assert.equal((await publishingDetail(dir)).coverage.status, 'mismatch');
  await writeJsonAtomic(path.join(dir, 'publishing', 'manifest.json'), {
    rows,
  });
  assert.equal((await publishingDetail(dir)).coverage.status, 'needs_review');
});

test('confirmed PopBoom Beijing request accepts bare query time with declared basis', () => {
  const action = fixture({ scheduled_time: '2026-09-09 19:00:00' });
  action.request.scheduled_time = '2026-09-09T19:00:00+08:00';
  const result = auditSchedule(action, now);
  assert.equal(result.status, 'passed');
  assert.equal(
    result.observed.timezoneBasis,
    'popboom_beijing_caption_v1:user_confirmed',
  );
  action.verificationEvidence.result.scheduled_time = '2026-09-09 07:00:00';
  assert.equal(auditSchedule(action, now).status, 'mismatch');
  action.verificationEvidence.result.scheduled_time = '2026-09-09 19:00:00';
  assert.equal(auditSchedule(action, now, []).status, 'needs_review');
});
