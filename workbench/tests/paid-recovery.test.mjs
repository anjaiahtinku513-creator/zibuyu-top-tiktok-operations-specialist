import assert from 'node:assert/strict';
import { createHash, randomUUID } from 'node:crypto';
import { mkdtemp, readFile, rm, writeFile } from 'node:fs/promises';
import os from 'node:os';
import path from 'node:path';
import test from 'node:test';
import { verifyAuditedPaidRecovery } from '../server/paid-recovery.mjs';
import { writeJsonAtomic } from '../server/state.mjs';
const hash = (bytes) => createHash('sha256').update(bytes).digest('hex');

test('only explicit audited local preflight failures may get a new dispatch identity; history remains intact', async () => {
  const root = await mkdtemp(path.join(os.tmpdir(), 'zb-paid-recovery-'));
  try {
    const intake = { runId: 'unit-only', sku: 'test' };
    const previous = {
      variantIds: ['black'],
      modelPreset: '德2',
      scope: 'generation',
      plannedVideoCount: 1,
      authorizationFingerprint: 'old',
    };
    const approval = { ...previous, authorizationFingerprint: 'new' };
    await writeJsonAtomic(path.join(root, 'intake.json'), intake);
    await writeJsonAtomic(path.join(root, 'web/session.json'), {
      sessionId: 'original-session',
    });
    await writeJsonAtomic(path.join(root, 'web/paid-dispatch.json'), {
      authorizationFingerprint: 'old',
    });
    const previousClaim = await readFile(
      path.join(root, 'web/paid-dispatch.json'),
      'utf8',
    );
    const data = {
      approval: previous,
      intake,
      paid_result: { outcome: 'blocked' },
      paid_runtime: { state: 'completed', exitCode: 0 },
      paid_dispatch: { authorizationFingerprint: 'old' },
      block: {
        preflight: {
          paidSubmissionAttempted: false,
          popboomGenerateCalled: false,
          popboomUploadCalled: false,
          existingLedgerFound: false,
        },
      },
      paid_events: [
        { type: 'thread.started', thread_id: 'original-session' },
        { type: 'turn.completed' },
      ]
        .map((x) => JSON.stringify(x))
        .join('\n'),
    };
    const evidence = [];
    for (const [role, value] of Object.entries(data)) {
      const file = path.join(root, 'recovery/original', `${role}.json`);
      await writeJsonAtomic(file, value);
      if (role === 'paid_events') await writeFile(file, value);
      evidence.push({
        role,
        path: path.relative(root, file),
        sha256: hash(await readFile(file)),
      });
    }
    const request = {
      id: randomUUID(),
      runId: intake.runId,
      sessionId: 'original-session',
      kind: 'audited_pre_submission_repair',
      authorizationFingerprint: 'new',
      previousAuthorizationFingerprint: 'old',
      authorizationSource: 'explicit_user_instruction',
      userInstruction: '修复后继续制作',
      audit: {
        conclusion: 'confirmed_no_popboom_submission',
        commandReview: 'local_read_validation_progress_only',
      },
      evidence,
    };
    const requestPath = path.join(root, 'web/paid-recovery.json');
    await writeJsonAtomic(requestPath, request);
    const evidenceHash = (role) => evidence.find((e) => e.role === role).sha256;
    for (const [role, file] of [
      ['paid_events', 'codex-events-paid.jsonl'],
      ['paid_result', 'result-paid.json'],
    ])
      await writeFile(
        path.join(root, 'web', file),
        await readFile(path.join(root, `recovery/original/${role}.json`)),
      );
    const reviewed = {
      [evidenceHash('paid_events')]: {
        runId: intake.runId,
        sessionId: 'original-session',
        dispatchSha256: evidenceHash('paid_dispatch'),
        resultSha256: evidenceHash('paid_result'),
      },
    };
    const verify = (value = approval, id = request.id) =>
      verifyAuditedPaidRecovery(root, id, value, reviewed);
    await assert.rejects(
      verifyAuditedPaidRecovery(root, request.id, approval),
      /独立命令审计/,
    );
    const valid = await verify();
    assert.equal(
      valid.claimPath,
      path.join(root, 'web/paid-dispatches', `${request.id}.json`),
    );
    assert.equal(
      await readFile(path.join(root, 'web/paid-dispatch.json'), 'utf8'),
      previousClaim,
    );
    await assert.rejects(
      verify({
        ...approval,
        variantIds: ['white'],
      }),
      /改变原制作范围/,
    );
    await writeJsonAtomic(requestPath, { ...request, userInstruction: '' });
    await assert.rejects(verify(), /明确授权/);
    await writeJsonAtomic(requestPath, request);
    await writeJsonAtomic(valid.claimPath, { started: true });
    await assert.rejects(verify(), /已派发/);
    const replacementId = randomUUID();
    await writeJsonAtomic(requestPath, { ...request, id: replacementId });
    await assert.rejects(verify(approval, replacementId), /已有恢复派发/);
    await writeJsonAtomic(requestPath, request);
    await rm(valid.claimPath);
    await writeJsonAtomic(valid.redemptionPath, { recoveryId: request.id });
    await assert.rejects(verify(), /已经使用过/);
    await rm(valid.redemptionPath);
    await writeFile(
      path.join(root, 'web/codex-events-paid.jsonl'),
      `${data.paid_events}\n{"type":"new-attempt"}`,
    );
    await assert.rejects(verify(), /新的付费执行证据/);
    await writeFile(
      path.join(root, 'web/codex-events-paid.jsonl'),
      data.paid_events,
    );
    await writeJsonAtomic(
      path.join(root, 'recovery/original/paid_result.json'),
      { outcome: 'submission_unknown' },
    );
    await assert.rejects(verify(), /证据已变化/);
    request.evidence.find((e) => e.role === 'paid_result').sha256 = hash(
      await readFile(path.join(root, 'recovery/original/paid_result.json')),
    );
    await writeJsonAtomic(requestPath, request);
    reviewed[evidenceHash('paid_events')].resultSha256 =
      evidenceHash('paid_result');
    await writeFile(
      path.join(root, 'web/result-paid.json'),
      await readFile(path.join(root, 'recovery/original/paid_result.json')),
    );
    await assert.rejects(verify(), /不是已确认的提交前失败/);
  } finally {
    await rm(root, { recursive: true, force: true });
  }
});
