import assert from 'node:assert/strict';
import { mkdtemp, rm, writeFile } from 'node:fs/promises';
import { EventEmitter } from 'node:events';
import { PassThrough, Writable } from 'node:stream';
import os from 'node:os';
import path from 'node:path';
import test from 'node:test';
import {
  createUsageTracker,
  readUsage,
  startUsageAttempt,
  summarizeUsage,
} from '../server/usage.mjs';
import { runCodexProcess } from '../server/codex-runner.mjs';

const policy = { model: 'gpt-5.5', reasoningEffort: 'high' };
const completed = { endedAt: '2026-09-07T00:00:10.000Z', exitCode: 0 };
const begin = () =>
  createUsageTracker(
    { phase: 'prepare', ...policy },
    '2026-09-07T00:00:00.000Z',
  );

test('preserves distinct receipts without assuming additive resume usage; tools deduplicate', () => {
  const tracker = begin();
  const event = {
    type: 'turn.completed',
    turn_id: 'turn1',
    usage: { input_tokens: 100, cached_input_tokens: 40, output_tokens: 20 },
  };
  tracker.observe(event);
  tracker.observe(event);
  tracker.observe({
    type: 'turn.completed',
    turn_id: 'turn2',
    usage: { input_tokens: 200, cached_input_tokens: 80, output_tokens: 30 },
  });
  for (const type of ['item.started', 'item.completed'])
    tracker.observe({
      type,
      item: { id: 'tool1', type: 'mcp_tool_call', output: event },
    });
  const result = tracker.snapshot(completed);
  assert.equal(result.inputTokens, null);
  assert.deepEqual(
    result.reportedUsage.map((row) => row.inputTokens),
    [100, 200],
  );
  assert.deepEqual(
    result.reportedUsage.map((row) => row.cachedInputTokens),
    [40, 80],
  );
  assert.deepEqual(
    result.reportedUsage.map((row) => row.outputTokens),
    [20, 30],
  );
  assert.equal(result.reasoningTokens, null);
  assert.equal(result.observedToolCalls, 1);
  assert.equal(result.durationMs, 10000);
  assert.equal(result.usageComplete, true);
  assert.equal(result.subagentUsageCoverage, 'unverified');
});

test('missing, malformed and unfinished usage remains unknown, never zero', () => {
  assert.equal(begin().snapshot(completed).inputTokens, null);
  const tracker = begin();
  tracker.observe({
    type: 'turn.completed',
    usage: { input_tokens: 12, cached_input_tokens: 100, output_tokens: -1 },
  });
  tracker.observe({ type: 'turn.completed' });
  const result = tracker.snapshot({ ...completed, exitCode: 1 });
  assert.equal(result.inputTokens, null);
  assert.equal(result.cachedInputTokens, null);
  assert.equal(result.outputTokens, null);
  assert.equal(result.usageComplete, false);
});

test('attempts persist independently across phases and failures', async () => {
  const root = await mkdtemp(path.join(os.tmpdir(), 'zibuyu-usage-'));
  try {
    const first = await startUsageAttempt(root, 'prepare', policy);
    first.observe({
      type: 'turn.completed',
      usage: { input_tokens: 10, cached_input_tokens: 0, output_tokens: 3 },
    });
    await first.finish({ exitCode: 0 });
    const second = await startUsageAttempt(root, 'prepare', policy);
    await second.finish({ exitCode: 1 });
    const usage = await readUsage(root);
    assert.equal(usage.attemptCount, 2);
    assert.equal(
      usage.attempts.filter((row) => row.state === 'failed').length,
      1,
    );
    assert.equal(usage.totals.inputTokens, null);
    assert.equal(usage.usageComplete, false);
    assert.equal(
      summarizeUsage([usage.attempts[0]], 1).totals.outputTokens,
      null,
    );
  } finally {
    await rm(root, { recursive: true, force: true });
  }
});

test('CLI process integration records a fake local process without invoking Codex or PopBoom', async () => {
  const root = await mkdtemp(path.join(os.tmpdir(), 'zibuyu-usage-process-'));
  const previous = process.env.CODEX_CLI_PATH;
  try {
    const cli = path.join(root, 'fake-codex.exe');
    await writeFile(cli, 'not executable; injected process only');
    process.env.CODEX_CLI_PATH = cli;
    const processSpawner = (_command, args) => {
      assert.ok(args.includes('gpt-5.5'));
      const child = new EventEmitter();
      child.stdout = new PassThrough();
      child.stderr = new PassThrough();
      child.stdin = new Writable({
        write(_chunk, _encoding, done) {
          done();
        },
        final(done) {
          child.stdout.end(
            '{"type":"turn.completed","usage":{"input_tokens":21,"cached_input_tokens":7,"output_tokens":4}}\n',
          );
          child.stderr.end();
          done();
          process.nextTick(() => child.emit('close', 0, null));
        },
      });
      process.nextTick(() => child.emit('spawn'));
      return child;
    };
    const result = await runCodexProcess({
      runDir: root,
      phase: 'prepare',
      prompt: 'local fixture',
      imagePaths: [],
      processSpawner,
    });
    assert.equal(result.exitCode, 0);
    assert.ok(result.usagePath);
    const usage = await readUsage(root);
    assert.equal(usage.attempts[0].inputTokens, 21);
    assert.equal(usage.attempts[0].outputTokens, 4);
    assert.equal(usage.totals.inputTokens, null);
    assert.equal(usage.attempts[0].processStarted, true);
  } finally {
    if (previous === undefined) delete process.env.CODEX_CLI_PATH;
    else process.env.CODEX_CLI_PATH = previous;
    await rm(root, {
      recursive: true,
      force: true,
      maxRetries: 5,
      retryDelay: 20,
    });
  }
});
