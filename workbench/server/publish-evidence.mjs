import { createHash } from 'node:crypto';
import { readFile, readdir, realpath } from 'node:fs/promises';
import path from 'node:path';

function canonical(value) {
  if (Array.isArray(value)) return value.map(canonical);
  if (value && typeof value === 'object') return Object.fromEntries(Object.keys(value).sort().map((key) => [key, canonical(value[key])]));
  return value;
}
export const evidenceDigest = (value) => createHash('sha256').update(JSON.stringify(canonical(value))).digest('hex');

const nonempty = (value) => typeof value === 'string' && value.trim().length > 0;
const phaseName = /^publish-[a-z0-9-]+$/;

function receiptTime(value) {
  if (typeof value !== 'string') return null;
  const m = /^(\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2})(?:\.\d{1,3})?(Z|[+-]\d{2}:?\d{2})$/.exec(value);
  if (!m || /^-00:?00$/.test(m[2])) return null;
  const wall = Date.parse(`${m[1]}Z`);
  if (!Number.isFinite(wall) || new Date(wall).toISOString().slice(0, 19) !== m[1]) return null;
  if (m[2] !== 'Z') {
    const digits = m[2].slice(1).replace(':', '');
    if (+digits.slice(0, 2) > 23 || +digits.slice(2) > 59) return null;
  }
  const time = Date.parse(value);
  return Number.isFinite(time) && time <= Date.now() + 60_000 ? time : null;
}

// Written by the process runner, not accepted from ledger or model assertions.
export function archivePublishEvent(event, phase, attemptId, receivedAt) {
  if (!phaseName.test(phase) || !event || typeof event !== 'object' || Array.isArray(event)) return event;
  // The runner supplies these fields, overwriting any same-named CLI claim.
  // item.result is neither extracted nor rewritten.
  return { ...event, _zibuyuReceipt: { phase, attemptId, receivedAt } };
}

export async function readPublishEvents(dir) {
  const folder = path.resolve(dir, 'publishing', 'web');
  // Refuse a redirected evidence directory, and do not follow file symlinks.
  const resolved = await realpath(folder).catch(() => null);
  if (!resolved || path.normalize(resolved).toLowerCase() !== folder.toLowerCase()) return [];
  const entries = await readdir(folder, { withFileTypes: true }).catch(() => []);
  const files = entries.filter((entry) => entry.isFile() && /^codex-events-publish-[a-z0-9-]+\.jsonl$/.test(entry.name)).map((entry) => path.join(folder, entry.name)).sort();
  const texts = await Promise.all(files.map(async (file) => ({ file, text: await readFile(file, 'utf8').catch(() => '') })));
  return texts.flatMap(({ file, text }) => text.split('\n').flatMap((line, index) => {
    try {
      const event = JSON.parse(line);
      if (!event || typeof event !== 'object' || Array.isArray(event)) return [];
      Object.defineProperty(event, '_zibuyuEventRef', { value: `${file}:${index + 1}`, enumerable: false });
      return [event];
    } catch { return []; }
  }));
}

function argumentsObject(value) {
  if (typeof value !== 'string') return value;
  try { return JSON.parse(value); } catch { return null; }
}
export function verifiedPublishCall(action, evidence, events, tool = 'check_publish') {
  if (!action || !evidence || !Array.isArray(events) || !['check_publish', 'publish_video'].includes(tool) || evidence.tool !== tool) return null;
  const callId = evidence.callId || evidence.call_id;
  const attemptId = evidence.attemptId || evidence.attempt_id;
  if (!nonempty(callId) || !nonempty(attemptId) || !evidence.result || typeof evidence.result !== 'object') return null;
  const matches = events.filter((event) => {
    if (!event || typeof event !== 'object') return false;
    const item = event.item;
    if (event.type !== 'item.completed' || item?.type !== 'mcp_tool_call' || item.status !== 'completed' || item.error || item.result?.isError) return false;
    if (![item.id, item.call_id].some((id) => nonempty(id) && id === callId)) return false;
    const receipt = event._zibuyuReceipt;
    if (receipt?.attemptId !== attemptId || !phaseName.test(receipt?.phase) || receiptTime(receipt?.receivedAt) === null) return false;
    if (String(item.server).toLowerCase() !== 'popboom' || ![tool, `mcp__PopBoom__${tool}`].includes(item.tool)) return false;
    if (!item.result || evidenceDigest(item.result) !== evidenceDigest(evidence.result)) return false;
    const args = argumentsObject(item.arguments);
    if (!args || typeof args !== 'object' || Array.isArray(args)) return false;
    if (tool === 'publish_video') return action.request && evidenceDigest(args) === evidenceDigest(action.request);
    const expected = { schedule_id: action.scheduleId || action.schedule_id, log_id: action.logId || action.log_id };
    const supplied = ['schedule_id', 'log_id'].filter((key) => args[key] != null);
    return supplied.length > 0 && Object.keys(args).every((key) => ['schedule_id', 'log_id'].includes(key)) && supplied.every((key) => nonempty(String(args[key])) && expected[key] != null && String(args[key]) === String(expected[key]));
  });
  return matches.length === 1 ? matches[0] : null;
}
