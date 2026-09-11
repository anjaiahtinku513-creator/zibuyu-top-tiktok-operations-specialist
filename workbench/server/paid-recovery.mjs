import path from 'node:path';
import { access, readFile, readdir } from 'node:fs/promises';
import { createHash } from 'node:crypto';
import { readJson } from './state.mjs';
import { REVIEWED_PRE_SUBMISSION_ATTEMPTS } from './reviewed-pre-submission-attempts.mjs';

const hash = (bytes) => createHash('sha256').update(bytes).digest('hex');
const fail = (message) => {
  throw new Error(message);
};
const equalIds = (a, b) =>
  JSON.stringify(
    [...(a || [])].sort((left, right) =>
      String(left).localeCompare(String(right)),
    ),
  ) ===
  JSON.stringify(
    [...(b || [])].sort((left, right) =>
      String(left).localeCompare(String(right)),
    ),
  );

// Only an explicit, hash-bound audit of a *pre-submission* block may use a new
// dispatch identity. Ordinary interrupted/unknown paid attempts still cannot retry.
export async function verifyAuditedPaidRecovery(
  runDir,
  recoveryId,
  approval,
  reviewedAttempts = REVIEWED_PRE_SUBMISSION_ATTEMPTS,
) {
  if (!/^[a-f0-9-]{36}$/.test(recoveryId || '')) fail('付费恢复标识无效');
  const request = await readJson(path.join(runDir, 'web/paid-recovery.json'));
  const intake = await readJson(path.join(runDir, 'intake.json'));
  const session = await readJson(path.join(runDir, 'web/session.json'));
  if (
    request?.id !== recoveryId ||
    request.kind !== 'audited_pre_submission_repair' ||
    request.runId !== intake.runId ||
    request.sessionId !== session?.sessionId ||
    request.authorizationFingerprint !== approval?.authorizationFingerprint ||
    request.authorizationSource !== 'explicit_user_instruction' ||
    !request.userInstruction?.trim() ||
    request.audit?.conclusion !== 'confirmed_no_popboom_submission' ||
    request.audit?.commandReview !== 'local_read_validation_progress_only'
  )
    fail('缺少本次修复续跑的明确授权或提交前审计');
  const claimPath = path.join(
    runDir,
    'web/paid-dispatches',
    `${recoveryId}.json`,
  );
  try {
    await access(claimPath);
    fail('本次付费恢复已派发，必须核对记录，不能再次提交');
  } catch (error) {
    if (error.code !== 'ENOENT') throw error;
  }

  const evidence = {};
  const evidenceHashes = {};
  for (const item of request.evidence || []) {
    const absolute = path.resolve(runDir, item.path);
    if (!absolute.startsWith(path.resolve(runDir, 'recovery') + path.sep))
      fail('恢复证据必须保存在本单历史目录');
    const bytes = await readFile(absolute);
    if (hash(bytes) !== item.sha256) fail('原始提交前审计证据已变化');
    evidenceHashes[item.role] = item.sha256;
    evidence[item.role] =
      item.role === 'paid_events'
        ? bytes.toString('utf8')
        : JSON.parse(bytes.toString('utf8'));
  }
  const reviewed = reviewedAttempts[evidenceHashes.paid_events];
  if (
    !reviewed ||
    reviewed.runId !== intake.runId ||
    reviewed.sessionId !== session.sessionId ||
    reviewed.dispatchSha256 !== evidenceHashes.paid_dispatch ||
    reviewed.resultSha256 !== evidenceHashes.paid_result
  )
    fail('该执行记录尚未完成独立命令审计，不能以自报未提交恢复');
  for (const [file, expected] of [
    ['web/codex-events-paid.jsonl', evidenceHashes.paid_events],
    ['web/paid-dispatch.json', evidenceHashes.paid_dispatch],
    ['web/result-paid.json', evidenceHashes.paid_result],
  ]) {
    if (hash(await readFile(path.join(runDir, file))) !== expected)
      fail('原失败后已有新的付费执行证据，必须先核对');
  }
  const laterDispatches = await readdir(
    path.join(runDir, 'web/paid-dispatches'),
  ).catch((error) => {
    if (error.code === 'ENOENT') return [];
    throw error;
  });
  if (laterDispatches.length)
    fail('此失败之后已有恢复派发，不能再次恢复旧尝试');
  const redemptionPath = path.join(
    runDir,
    'web/paid-recovery-redemptions',
    `${reviewed.dispatchSha256}.json`,
  );
  try {
    await access(redemptionPath);
    fail('这次旧付费尝试已经使用过修复续跑');
  } catch (error) {
    if (error.code !== 'ENOENT') throw error;
  }
  const previous = evidence.approval;
  if (
    !previous ||
    previous.authorizationFingerprint !==
      request.previousAuthorizationFingerprint ||
    previous.authorizationFingerprint === approval.authorizationFingerprint ||
    !equalIds(previous.variantIds, approval.variantIds) ||
    previous.modelPreset !== approval.modelPreset ||
    previous.scope !== approval.scope ||
    previous.plannedVideoCount !== approval.plannedVideoCount ||
    JSON.stringify(evidence.intake) !== JSON.stringify(intake)
  )
    fail('修复续跑不能改变原制作范围或沿用旧产物指纹');
  if (
    evidence.paid_result?.outcome !== 'blocked' ||
    evidence.paid_runtime?.state !== 'completed' ||
    evidence.paid_runtime?.exitCode !== 0 ||
    evidence.paid_dispatch?.authorizationFingerprint !==
      previous.authorizationFingerprint ||
    evidence.block?.preflight?.paidSubmissionAttempted !== false ||
    evidence.block?.preflight?.popboomGenerateCalled !== false ||
    evidence.block?.preflight?.popboomUploadCalled !== false ||
    evidence.block?.preflight?.existingLedgerFound !== false
  )
    fail('原付费尝试不是已确认的提交前失败，不允许重新派发');
  const events = (evidence.paid_events || '')
    .trim()
    .split(/\r?\n/)
    .filter(Boolean)
    .map((line) => JSON.parse(line));
  if (
    !events.some(
      (e) => e.type === 'thread.started' && e.thread_id === session.sessionId,
    ) ||
    !events.some((e) => e.type === 'turn.completed') ||
    events.some(
      (e) =>
        e.type === 'turn.failed' ||
        (e.item?.type === 'mcp_tool_call' &&
          /popboom/i.test(JSON.stringify(e.item))),
    )
  )
    fail('原执行记录不完整或存在平台调用，需先核对');
  return { request, claimPath, redemptionPath };
}
