import assert from 'node:assert/strict';
import test from 'node:test';

import {
  buildCodexArguments,
  CODEX_EXECUTION_POLICY,
} from '../server/codex-runner.mjs';

const base = {
  imagePaths: ['C:\\input\\black.png'],
  outputSchemaPath: 'C:\\schemas\\result.json',
  outputPath: 'C:\\runs\\final.json',
};

test('every production batch starts a fresh gpt-5.5 high session', () => {
  const args = buildCodexArguments({ ...base, phase: 'prepare' });

  assert.deepEqual(
    args.slice(args.indexOf('--model'), args.indexOf('--model') + 2),
    ['--model', 'gpt-5.5'],
  );
  assert.ok(args.includes('model_reasoning_effort="high"'));
  assert.ok(!args.includes('resume'));
  assert.equal(CODEX_EXECUTION_POLICY.conversationPolicy, 'new-session-per-production-batch');
});

test('paid phase resumes only the current batch session with the same model policy', () => {
  const sessionId = '019c7abc-1234-7abc-8abc-1234567890ab';
  const args = buildCodexArguments({
    ...base,
    phase: 'paid',
    imagePaths: [],
    sessionId,
  });

  assert.ok(args.includes('resume'));
  assert.ok(args.includes('--all'));
  assert.ok(args.includes(sessionId));
  assert.ok(args.includes('gpt-5.5'));
  assert.ok(args.includes('model_reasoning_effort="high"'));
});

test('session isolation rejects cross-batch reuse and missing paid session IDs', () => {
  assert.throws(
    () =>
      buildCodexArguments({
        ...base,
        phase: 'prepare',
        sessionId: '019c7abc-1234-7abc-8abc-1234567890ab',
      }),
    /禁止复用/,
  );
  assert.throws(
    () => buildCodexArguments({ ...base, phase: 'paid' }),
    /缺少该批次/,
  );
});
