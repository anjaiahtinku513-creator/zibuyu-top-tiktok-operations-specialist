import { randomUUID } from 'node:crypto';
import { readdir } from 'node:fs/promises';
import path from 'node:path';

import { readJson, writeJsonAtomic } from './state.mjs';

const tokenKeys = [
  'inputTokens',
  'cachedInputTokens',
  'outputTokens',
  'reasoningTokens',
];
const counter = (value) =>
  Number.isSafeInteger(value) && value >= 0 ? value : null;
const toolTypes = new Set([
  'command_execution',
  'mcp_tool_call',
  'web_search',
  'collab_tool_call',
]);

// Preserve top-level CLI receipts without summing them. The installed CLI's
// resume/cumulative semantics and child-agent coverage are not established.
export function createUsageTracker(
  metadata,
  startedAt = new Date().toISOString(),
) {
  const turns = [];
  const turnIds = new Set();
  const toolIds = new Set();
  let jsonEvents = 0;
  let unkeyedToolEvents = 0;
  let delegationObserved = false;
  return {
    observe(event) {
      if (!event || typeof event !== 'object') return;
      jsonEvents += 1;
      if (event.type === 'turn.completed') {
        const id = event.turn_id ?? event.id;
        if (id && turnIds.has(id)) return;
        if (id) turnIds.add(id);
        const usage = event.usage ?? {};
        const inputTokens = counter(usage.input_tokens);
        let cachedInputTokens = counter(usage.cached_input_tokens);
        if (
          inputTokens === null ||
          (cachedInputTokens !== null && cachedInputTokens > inputTokens)
        )
          cachedInputTokens = null;
        turns.push({
          eventIndex: jsonEvents,
          turnId: id ?? null,
          rawUsage: usage,
          inputTokens,
          cachedInputTokens,
          outputTokens: counter(usage.output_tokens),
          reasoningTokens: counter(
            usage.reasoning_output_tokens ??
              usage.output_tokens_details?.reasoning_tokens,
          ),
        });
      }
      if (
        ['item.started', 'item.completed'].includes(event.type) &&
        toolTypes.has(event.item?.type)
      ) {
        if (event.item.id) toolIds.add(event.item.id);
        else if (event.type === 'item.completed') unkeyedToolEvents += 1;
        if (event.item.type === 'collab_tool_call') delegationObserved = true;
      }
    },
    snapshot({
      endedAt = null,
      exitCode = null,
      signal = null,
      processStarted = false,
      sessionId = null,
    } = {}) {
      const usage = {};
      for (const key of tokenKeys) {
        usage[key] = turns.length === 1 ? turns[0][key] : null;
      }
      return {
        schemaVersion: 1,
        ...metadata,
        startedAt,
        endedAt,
        exitCode,
        signal,
        processStarted,
        sessionId,
        state: !endedAt ? 'running' : exitCode === 0 ? 'completed' : 'failed',
        durationMs: endedAt
          ? Math.max(0, Date.parse(endedAt) - Date.parse(startedAt))
          : null,
        completedTurns: turns.length,
        reportedUsage: [...turns],
        ...usage,
        observedToolCalls: jsonEvents ? toolIds.size + unkeyedToolEvents : null,
        delegationObserved,
        coverage: 'cli_top_level_turn_receipts',
        aggregationSemantics: 'unverified_no_token_totals',
        subagentUsageCoverage: 'unverified',
        validationRepairCount: null,
        // A crash/failure can consume tokens without returning a turn receipt.
        usageComplete: Boolean(
          endedAt &&
          exitCode === 0 &&
          turns.length &&
          turns.every(
            (turn) => turn.inputTokens !== null && turn.outputTokens !== null,
          ),
        ),
      };
    },
  };
}

export async function startUsageAttempt(runDir, phase, policy) {
  const attemptId = randomUUID();
  const file = path.join(runDir, 'web', 'usage', `${attemptId}.json`);
  const tracker = createUsageTracker({
    attemptId,
    phase,
    model: policy.model,
    reasoningEffort: policy.reasoningEffort,
  });
  // Telemetry cannot change whether a paid process was dispatched. A failed
  // write is reported to the caller without changing its production outcome.
  let persistenceError = null;
  async function persist(result) {
    try {
      await writeJsonAtomic(file, result);
    } catch (error) {
      persistenceError = error.code ?? 'usage_write_failed';
    }
    return persistenceError;
  }
  await persist(tracker.snapshot());
  return {
    observe: (event) => tracker.observe(event),
    async finish(outcome) {
      const result = tracker.snapshot({
        ...outcome,
        endedAt: new Date().toISOString(),
      });
      const error = await persist(result);
      return { usagePath: error ? null : file, usageError: error };
    },
  };
}

export function summarizeUsage(attempts, unreadableAttempts = 0) {
  const totals = {};
  for (const key of ['durationMs', 'observedToolCalls']) {
    totals[key] =
      !unreadableAttempts &&
      attempts.length &&
      attempts.every((attempt) => counter(attempt[key]) !== null)
        ? attempts.reduce((sum, attempt) => sum + attempt[key], 0)
        : null;
  }
  for (const key of tokenKeys) totals[key] = null;
  return {
    attempts,
    totals,
    unreadableAttempts,
    attemptCount: attempts.length + unreadableAttempts,
    usageComplete:
      !unreadableAttempts &&
      attempts.length > 0 &&
      attempts.every((attempt) => attempt.usageComplete),
    coverage: 'cli_top_level_turn_receipts',
    aggregationSemantics: 'unverified_no_token_totals',
    subagentUsageCoverage: 'unverified',
  };
}

export async function readUsage(runDir) {
  const folder = path.join(runDir, 'web', 'usage');
  let entries;
  try {
    entries = await readdir(folder);
  } catch (error) {
    if (error.code === 'ENOENT') return summarizeUsage([]);
    return summarizeUsage([], 1);
  }
  const attempts = [];
  let unreadable = 0;
  for (const entry of entries.filter((name) =>
    /^[0-9a-f-]{36}\.json$/i.test(name),
  )) {
    try {
      const item = await readJson(path.join(folder, entry));
      if (
        item?.schemaVersion !== 1 ||
        typeof item.attemptId !== 'string' ||
        typeof item.phase !== 'string' ||
        typeof item.startedAt !== 'string' ||
        !Array.isArray(item.reportedUsage)
      )
        throw new Error('invalid usage');
      attempts.push(item);
    } catch {
      unreadable += 1;
    }
  }
  attempts.sort((a, b) => a.startedAt.localeCompare(b.startedAt));
  return summarizeUsage(attempts, unreadable);
}
