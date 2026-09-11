import assert from 'node:assert/strict';
import { spawn } from 'node:child_process';
import { once } from 'node:events';
import { randomUUID } from 'node:crypto';
import { mkdtemp, readFile, readdir, rm, writeFile } from 'node:fs/promises';
import os from 'node:os';
import path from 'node:path';
import { fileURLToPath } from 'node:url';
import test from 'node:test';

const project = path.dirname(path.dirname(fileURLToPath(import.meta.url)));
test(
  'multipart group submission, network retry, scoped approval and incomplete-group publishing use the full HTTP path',
  { timeout: 30000 },
  async () => {
    const root = await mkdtemp(path.join(os.tmpdir(), 'zibuyu-multi-http-'));
    const child = spawn(
      process.execPath,
      [path.join(project, 'server/index.mjs')],
      {
        cwd: project,
        windowsHide: true,
        env: {
          ...process.env,
          PORT: '0',
          ZIBUYU_RUNS_ROOT: root,
          ZIBUYU_EXECUTOR_MODE: 'mock',
          LOG_LEVEL: 'info',
        },
        stdio: ['ignore', 'pipe', 'pipe'],
      },
    );
    let stderr = '';
    child.stderr.on('data', (chunk) => {
      stderr += chunk;
    });
    try {
      const base = await new Promise((resolve, reject) => {
        const timer = setTimeout(
          () =>
            reject(new Error(`isolated mock server did not start: ${stderr}`)),
          8000,
        );
        child.once('error', (error) => {
          clearTimeout(timer);
          reject(error);
        });
        child.stdout.on('data', (chunk) => {
          const match = String(chunk).match(
            /Server listening at (http:\/\/127\.0\.0\.1:\d+)/,
          );
          if (match) {
            clearTimeout(timer);
            resolve(match[1]);
          }
        });
      });
      const session = await fetch(`${base}/api/session`);
      const { token } = await session.json();
      const cookie = session.headers.get('set-cookie').split(';')[0];
      const payload = {
        sku: 'HTTP-FIXTURE',
        modelMode: 'preset',
        modelPresets: ['美1', '德1'],
        amazonUrls: {
          US: 'https://www.amazon.com/dp/FIXTURE',
          DE: 'https://www.amazon.de/dp/FIXTURE',
        },
        variants: [
          { id: 'variant-001', name: 'Black' },
          { id: 'variant-002', name: 'White' },
        ],
      };
      const requestId = randomUUID();
      async function upload(invalidLast = false, id = requestId) {
        const form = new FormData();
        form.append('payload', JSON.stringify(payload));
        for (const [index, variant] of payload.variants.entries()) {
          const bytes =
            invalidLast && index === 1
              ? Buffer.from('invalid')
              : Buffer.from([137, 80, 78, 71, 13, 10, 26, 10]);
          form.append(
            `image__${variant.id}`,
            new Blob([bytes], { type: 'image/png' }),
            `${variant.name}.png`,
          );
        }
        return fetch(`${base}/api/runs`, {
          method: 'POST',
          headers: { 'x-zibuyu-token': token, 'x-zibuyu-request-id': id },
          body: form,
        });
      }
      const invalid = await upload(true, randomUUID());
      assert.equal(invalid.status, 400);
      assert.deepEqual(
        (await readdir(root)).filter((name) => name.startsWith('zb-')),
        [],
      );
      const createdResponse = await upload();
      assert.equal(
        createdResponse.status,
        202,
        JSON.stringify(await createdResponse.clone().json()),
      );
      const created = await createdResponse.json();
      assert.equal(created.runs.length, 2);
      const duplicate = await (await upload()).json();
      assert.equal(duplicate.idempotent, true);
      assert.deepEqual(
        duplicate.runs.map((run) => run.runId),
        created.runs.map((run) => run.runId),
      );
      assert.equal(
        (await readdir(root)).filter((name) => name.startsWith('zb-')).length,
        2,
      );
      const deadline = Date.now() + 12000;
      let details;
      do {
        details = await Promise.all(
          created.runs.map(async (run) =>
            (await fetch(`${base}/api/runs/${run.runId}`)).json(),
          ),
        );
        if (
          details.every(
            (detail) => detail.status.state === 'awaiting_paid_approval',
          )
        )
          break;
        await new Promise((resolve) => setTimeout(resolve, 100));
      } while (Date.now() < deadline);
      assert.ok(
        details.every(
          (detail) => detail.status.state === 'awaiting_paid_approval',
        ),
        JSON.stringify(details.map((detail) => detail.status)),
      );
      assert.notEqual(details[0].status.sessionId, details[1].status.sessionId);
      assert.equal(details[0].modelBatch.plannedVideoCount, 4);
      assert.deepEqual(
        details.map((detail) => detail.intake.model.locale),
        ['en-US', 'de-DE'],
      );
      const runDir = path.join(root, created.runId);
      const unreviewedRepair = await fetch(
        `${base}/api/runs/${created.runId}/resume-authorized-repair`,
        {
          method: 'POST',
          headers: {
            'content-type': 'application/json',
            'x-zibuyu-token': token,
          },
          body: JSON.stringify({ confirm: true, recoveryId: randomUUID() }),
        },
      );
      assert.equal(unreviewedRepair.status, 409);
      assert.equal((await readdir(runDir)).includes('approval.json'), false);
      // Session/runtime changes must reach an already-open detail view even
      // when workflow.updatedAt has not changed for a long preparation stage.
      const controller = new AbortController();
      const timeout = setTimeout(() => controller.abort(), 5000);
      try {
        const stream = await fetch(`${base}/api/runs/${created.runId}/events`, {
          signal: controller.signal,
          headers: { cookie },
        });
        assert.equal(stream.status, 200);
        const reader = stream.body.getReader();
        await reader.read();
        await writeFile(
          path.join(runDir, 'web/runtime.json'),
          JSON.stringify({
            phase: 'prepare',
            state: 'completed',
            source: 'exec',
            pid: null,
            lastEventAt: '2026-09-07T00:00:00Z',
            currentTask: 'SSE_RUNTIME_ONLY_UPDATE',
          }),
        );
        let output = '';
        while (!output.includes('SSE_RUNTIME_ONLY_UPDATE')) {
          const next = await reader.read();
          assert.equal(next.done, false);
          output += new TextDecoder().decode(next.value);
        }
        await reader.cancel();
      } finally {
        clearTimeout(timeout);
        controller.abort();
      }
      const resultFile = path.join(runDir, 'web/result-prepare.json');
      const result = JSON.parse(await readFile(resultFile, 'utf8'));
      await writeFile(path.join(runDir, 'fixture.json'), '{}');
      result.artifacts = [
        { kind: 'json', label: 'isolated mock artifact', path: 'fixture.json' },
      ];
      await writeFile(resultFile, JSON.stringify(result));
      const approve = await fetch(`${base}/api/runs/${created.runId}/approve`, {
        method: 'POST',
        headers: {
          'content-type': 'application/json',
          'x-zibuyu-token': token,
        },
        body: JSON.stringify({
          confirm: true,
          variantIds: ['variant-001', 'variant-002'],
        }),
      });
      assert.equal(approve.status, 202);
      const approval = JSON.parse(
        await readFile(path.join(runDir, 'approval.json'), 'utf8'),
      );
      assert.equal(approval.modelPreset, '美1');
      assert.equal(approval.plannedVideoCount, 2);
      await assert.rejects(
        readFile(path.join(root, created.runs[1].runId, 'approval.json')),
        { code: 'ENOENT' },
      );
      await new Promise((resolve) => setTimeout(resolve, 400));
      const snapshot = await fetch(
        `${base}/api/runs/${created.runId}/publishing/snapshot`,
        {
          method: 'POST',
          headers: {
            'content-type': 'application/json',
            'x-zibuyu-token': token,
            cookie,
          },
          body: '{}',
        },
      );
      assert.equal(snapshot.status, 409);
      assert.match((await snapshot.json()).error, /同批次还有模特/);
      const siblingFile = path.join(root, created.runs[1].runId, 'intake.json');
      const sibling = JSON.parse(await readFile(siblingFile, 'utf8'));
      sibling.model.preset = '德3';
      await writeFile(siblingFile, JSON.stringify(sibling));
      const readable = await fetch(`${base}/api/runs/${created.runId}`);
      assert.equal(readable.status, 200);
      assert.ok((await readable.json()).modelBatchError);
      assert.equal(
        (await (await fetch(`${base}/api/runs`)).json()).runs.length,
        2,
      );
    } finally {
      if (child.exitCode === null) {
        const exited = once(child, 'exit');
        child.kill();
        await exited;
      }
      await rm(root, {
        recursive: true,
        force: true,
        maxRetries: 5,
        retryDelay: 50,
      });
    }
  },
);
