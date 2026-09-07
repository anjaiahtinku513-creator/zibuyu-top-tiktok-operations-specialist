import assert from 'node:assert/strict';
import test from 'node:test';
import { EventEmitter } from 'node:events';
import { mkdtemp, rm, writeFile } from 'node:fs/promises';
import { PassThrough, Writable } from 'node:stream';
import os from 'node:os';
import path from 'node:path';
import {
  codexFailureFromEvent,
  classificationFailureText,
} from '../server/codex-errors.mjs';
import { runCodexProcess } from '../server/codex-runner.mjs';

const rejection = JSON.stringify({
  type: 'error',
  error: {
    type: 'invalid_request_error',
    code: 'invalid_json_schema',
    message: "Invalid schema: 'uniqueItems' is not permitted.",
    param: 'text.format.schema',
  },
  status: 400,
});

test('nested API schema failures produce the actual actionable classification error', () => {
  for (const event of [
    { type: 'error', message: rejection },
    { type: 'turn.failed', error: { message: rejection } },
  ]) {
    const failure = codexFailureFromEvent(event);
    assert.equal(failure.code, 'invalid_json_schema');
    assert.match(failure.message, /uniqueItems/);
    assert.match(
      classificationFailureText({ exitCode: 1, failure }),
      /结果格式配置不兼容/,
    );
  }
});

test('nonfatal skill warnings and incomplete events are not terminal failures', () => {
  assert.equal(
    codexFailureFromEvent({
      type: 'item.completed',
      item: { type: 'error', message: 'Skills shortened' },
    }),
    null,
  );
  assert.equal(codexFailureFromEvent({ type: 'turn.failed', error: {} }), null);
  assert.match(classificationFailureText({ exitCode: 1 }), /退出码 1/);
});

test('plain terminal errors are readable and bounded', () => {
  const failure = codexFailureFromEvent({
    type: 'turn.failed',
    error: { message: 'Connection\n failed ' + 'x'.repeat(800) },
  });
  assert.equal(failure.message.length, 500);
  assert.match(classificationFailureText({ failure }), /Connection failed/);
});

test('CLI captures the current invocation failure without reusing previous appended log errors', async () => {
  const root = await mkdtemp(path.join(os.tmpdir(), 'zibuyu-error-process-'));
  const previous = process.env.CODEX_CLI_PATH;
  try {
    const cli = path.join(root, 'fake-codex.exe');
    await writeFile(cli, 'injected process only');
    process.env.CODEX_CLI_PATH = cli;
    for (const fails of [true, false]) {
      const processSpawner = () => {
        const child = new EventEmitter();
        child.stdout = new PassThrough();
        child.stderr = new PassThrough();
        child.stdin = new Writable({
          write(_chunk, _encoding, done) {
            done();
          },
          final(done) {
            const event = fails
              ? { type: 'turn.failed', error: { message: rejection } }
              : { type: 'turn.completed' };
            child.stdout.end(`${JSON.stringify(event)}\n`);
            child.stderr.end();
            done();
            process.nextTick(() => child.emit('close', fails ? 1 : 0, null));
          },
        });
        process.nextTick(() => child.emit('spawn'));
        return child;
      };
      const execution = await runCodexProcess({
        runDir: root,
        phase: 'classify',
        prompt: 'fixture',
        imagePaths: [],
        processSpawner,
      });
      assert.equal(execution.exitCode, fails ? 1 : 0);
      assert.equal(
        execution.failure?.code ?? null,
        fails ? 'invalid_json_schema' : null,
      );
    }
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
