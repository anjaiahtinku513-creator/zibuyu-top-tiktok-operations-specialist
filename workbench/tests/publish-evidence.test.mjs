import assert from 'node:assert/strict';
import test from 'node:test';
import { mkdtemp, mkdir, writeFile, rm, readFile } from 'node:fs/promises';
import os from 'node:os';
import path from 'node:path';
import { archivePublishEvent, evidenceDigest, readPublishEvents, verifiedPublishCall } from '../server/publish-evidence.mjs';

const receiptAt = '2020-07-12T11:00:00.123Z';
function fixture(tool = 'check_publish') {
  const result = { content: [{ type: 'text', text: 'synthetic complete response' }], structured_content: null };
  const action = { scheduleId: 'synthetic-schedule', request: { channel_id: 'synthetic-channel', video_title: 'complete synthetic caption', scheduled_time: '2030-07-12T07:00:00-04:00' } };
  const event = archivePublishEvent({ type: 'item.completed', item: { id: 'synthetic-call', type: 'mcp_tool_call', server: 'PopBoom', tool, arguments: tool === 'check_publish' ? { schedule_id: action.scheduleId } : action.request, status: 'completed', error: null, result } }, 'publish-reconcile', 'synthetic-attempt', receiptAt);
  const evidence = { tool, attemptId: 'synthetic-attempt', callId: 'synthetic-call', queriedAt: '2030-01-01T00:00:00Z', result: structuredClone(result) };
  action.verificationEvidence = evidence;
  return { action, evidence, event };
}

test('matches a complete real-shaped MCP query; canonical key order is immaterial', () => {
  const { action, evidence, event } = fixture();
  evidence.result = { structured_content: null, content: evidence.result.content };
  assert.equal(verifiedPublishCall(action, evidence, [event]), event);
  assert.equal(verifiedPublishCall(action, evidence, [event])._zibuyuReceipt.receivedAt, receiptAt);
});

test('no event or a local agent echo cannot establish query provenance', () => {
  const { action, evidence, event } = fixture();
  assert.equal(verifiedPublishCall(action, evidence, []), null);
  for (const changed of [{ ...event, type: 'item.started' }, { ...event, item: { ...event.item, type: 'agent_message' } }, { tool: 'check_publish', result: evidence.result }, { ...event, _zibuyuReceipt: undefined }]) {
    assert.equal(verifiedPublishCall(action, evidence, [changed]), null);
  }
});

test('wrong ID, raw-result hash, server, call ID, attempt, and tool do not pass', () => {
  const { action, evidence, event } = fixture();
  for (const itemPatch of [{ arguments: { schedule_id: 'other' } }, { result: { content: [] } }, { server: 'other' }, { id: 'other' }, { tool: 'publish_video' }, { status: 'failed' }, { error: { message: 'failure' } }, { result: { isError: true } }]) {
    assert.equal(verifiedPublishCall(action, evidence, [{ ...event, item: { ...event.item, ...itemPatch } }]), null);
  }
  assert.equal(verifiedPublishCall(action, { ...evidence, attemptId: 'other' }, [event]), null);
  assert.equal(verifiedPublishCall(action, { ...evidence, tool: 'local_echo' }, [event]), null);
  assert.equal(verifiedPublishCall(action, evidence, [event, structuredClone(event)]), null);
});

test('missing, invalid, naive, impossible or future runner times cannot be freshened by action time', () => {
  const { action, evidence, event } = fixture();
  for (const receivedAt of [null, '', 'yesterday', '2020-07-12 11:00:00', '2020-02-30T00:00:00Z', '2020-07-12T11:00:00-00:00', '2999-01-01T00:00:00Z']) {
    const changed = { ...event, _zibuyuReceipt: { ...event._zibuyuReceipt, receivedAt } };
    assert.equal(verifiedPublishCall(action, { ...evidence, queriedAt: new Date().toISOString() }, [changed]), null);
  }
  const old = verifiedPublishCall({ ...action, observedAt: new Date().toISOString() }, { ...evidence, queriedAt: new Date().toISOString() }, [event]);
  assert.equal(old._zibuyuReceipt.receivedAt, receiptAt);
});

test('log-only lookup and stringified CLI arguments preserve the actual ID kind', () => {
  const { action, evidence, event } = fixture();
  delete action.scheduleId;
  action.log_id = 'synthetic-log';
  event.item.arguments = JSON.stringify({ log_id: 'synthetic-log' });
  assert.equal(verifiedPublishCall(action, evidence, [event]), event);
  event.item.arguments = { log_id: 'synthetic-log', schedule_id: 'invented' };
  assert.equal(verifiedPublishCall(action, evidence, [event]), null);
});

test('creation proof binds exact outbound arguments and does not substitute for check_publish', () => {
  const { action, evidence, event } = fixture('publish_video');
  assert.equal(verifiedPublishCall(action, evidence, [event], 'publish_video'), event);
  assert.equal(verifiedPublishCall(action, evidence, [event]), null);
  assert.equal(verifiedPublishCall({ ...action, request: { ...action.request, video_title: 'different' } }, evidence, [event], 'publish_video'), null);
});

test('runner tagging is publishing-only, pure, preserves raw result, and overwrites CLI metadata claims', () => {
  const { event } = fixture();
  const before = structuredClone(event);
  const tagged = archivePublishEvent(event, 'publish-submit', 'local-attempt', receiptAt);
  assert.deepEqual(event, before);
  assert.equal(tagged.item.result, event.item.result);
  assert.deepEqual(tagged._zibuyuReceipt, { phase: 'publish-submit', attemptId: 'local-attempt', receivedAt: receiptAt });
  assert.equal(evidenceDigest(tagged.item.result), evidenceDigest(before.item.result));
  assert.equal(archivePublishEvent(event, 'paid', 'local', receiptAt), event);
  assert.equal(archivePublishEvent(null, 'publish-submit', 'local', receiptAt), null);
});

test('loader reads only fixed publishing log directory and pattern, retaining line provenance', async () => {
  const dir = await mkdtemp(path.join(os.tmpdir(), 'synthetic-publish-proof-'));
  try {
    const folder = path.join(dir, 'publishing', 'web');
    await mkdir(folder, { recursive: true });
    const { event } = fixture();
    await writeFile(path.join(folder, 'codex-events-publish-reconcile.jsonl'), `broken\n${JSON.stringify(event)}\nnull\n`);
    await writeFile(path.join(folder, 'codex-events-paid.jsonl'), JSON.stringify(event));
    await writeFile(path.join(dir, 'codex-events-publish-submit.jsonl'), JSON.stringify(event));
    const events = await readPublishEvents(dir);
    assert.equal(events.length, 1);
    assert.match(events[0]._zibuyuEventRef, /codex-events-publish-reconcile\.jsonl:2$/);
    assert.deepEqual(events[0], event);
    assert.deepEqual(await readPublishEvents(path.join(dir, 'missing')), []);
    const runner = await readFile(new URL('../server/codex-runner.mjs', import.meta.url), 'utf8');
    assert.match(runner, /archivePublishEvent\(event, phase, attemptId, receivedAt\)/);
    assert.match(runner, /const receivedAt = new Date\(\)\.toISOString\(\)/);
  } finally { await rm(dir, { recursive: true, force: true }); }
});
