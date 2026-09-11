import path from 'node:path';
import { readFile, stat } from 'node:fs/promises';
import { createHash } from 'node:crypto';
import { readJson, writeJsonAtomic, transitionStatus } from './state.mjs';

const hash = (bytes) => createHash('sha256').update(bytes).digest('hex');
export function attemptOutputPath(codexDir, phase, attemptId) {
  if (attemptId && !/^[a-f0-9-]{36}$/.test(attemptId))
    throw new Error('执行标识无效');
  return attemptId
    ? path.join(codexDir, 'attempts', attemptId, `final-${phase}.json`)
    : path.join(codexDir, `final-${phase}.json`);
}
const localPath = (runDir, file) => {
  const resolved = path.resolve(runDir, file);
  if (!resolved.startsWith(path.resolve(runDir) + path.sep))
    throw new Error('生产证据路径越界');
  return resolved;
};

// Evidence is selected only by the current authorization, never by newest-file
// searches. This reader cannot dispatch or retry a paid action.
export async function readProductionEvidence(runDir) {
  const [intake, approval, prepareResult] = await Promise.all([
    readJson(path.join(runDir, 'intake.json')),
    readJson(path.join(runDir, 'approval.json')),
    readJson(path.join(runDir, 'web/result-prepare.json')),
  ]);
  if (!approval || approval.runId !== intake?.runId) return null;
  const { variantIds, artifactManifest } = approval;
  if (!variantIds?.length || new Set(variantIds).size !== variantIds.length)
    return null;
  if (
    hash(
      JSON.stringify({ intake, prepareResult, variantIds, artifactManifest }),
    ) !== approval.authorizationFingerprint
  )
    return null;
  const compiles = artifactManifest.filter(
    (file) =>
      !file.external && path.basename(file.path) === 'batch-compile.json',
  );
  if (!compiles.length) return null;
  const jobs = [];
  for (const file of compiles) {
    const compilePath = localPath(runDir, file.path);
    const bytes = await readFile(compilePath);
    if (hash(bytes) !== file.sha256 || bytes.length !== file.size) return null;
    const compile = JSON.parse(bytes);
    const ledgerPath = path.join(path.dirname(compilePath), 'ledger.json');
    const ledger = await readJson(ledgerPath);
    if (
      !ledger ||
      ledger.batch_compile_sha256 !== file.sha256 ||
      ledger.batch_compile_id !== compile.batch_compile_id ||
      !compile.sku_family_id ||
      ledger.sku_family_id !== compile.sku_family_id ||
      !(
        ledger.run_id === intake.runId ||
        variantIds.some((id) => ledger.run_id === `${intake.runId}:${id}`)
      )
    )
      return null;
    for (const job of ledger.jobs ?? []) {
      if (
        !variantIds.includes(job.variant_id) ||
        !compile.variants?.some((v) => v.variant_id === job.variant_id) ||
        job.model_name !== (intake.model.preset ?? intake.model.name)
      )
        return null;
      let verifiedLocalPath = null;
      if (job.download?.state === 'succeeded' && job.download.local_path) {
        try {
          const videoPath = localPath(runDir, job.download.local_path);
          const video = await readFile(videoPath);
          if (
            video.length > 0 &&
            video.length === job.download.bytes &&
            hash(video) === job.download.sha256
          )
            verifiedLocalPath = videoPath;
        } catch {
          /* Missing or changed media remains a delivery block. */
        }
      }
      jobs.push({
        variantId: job.variant_id,
        color: job.color_name,
        state: job.state,
        recordId: job.record_id,
        verifiedLocalPath,
        compilePath,
        ledgerPath,
      });
    }
  }
  if (
    jobs.length !== variantIds.length ||
    new Set(jobs.map((j) => j.variantId)).size !== variantIds.length
  )
    return null;
  return {
    runId: intake.runId,
    authorizationFingerprint: approval.authorizationFingerprint,
    jobs,
  };
}

export function reconcilePaidOutcome(evidence) {
  if (
    !evidence?.jobs?.length ||
    evidence.jobs.some(
      (j) =>
        !Number.isInteger(j.recordId) ||
        j.recordId <= 0 ||
        ['submission_unknown', 'submission_started', 'submitting'].includes(
          j.state,
        ),
    )
  )
    return null;
  const allSucceeded = evidence.jobs.every((j) => j.state === 'succeeded');
  const allLocal = evidence.jobs.every((j) => j.verifiedLocalPath);
  const state = allSucceeded && allLocal ? 'needs_review' : 'blocked';
  const currentTask = allSucceeded
    ? allLocal
      ? `${evidence.jobs.length} 条成片已生成，待验收`
      : '生成已完成，待补齐成片文件'
    : evidence.jobs.some((j) => j.state === 'failed')
      ? '平台任务已返回失败，待处理'
      : '平台已接收任务，待查询结果';
  return {
    runId: evidence.runId,
    phase: 'paid',
    outcome: state,
    stageKey: allSucceeded ? 'quality_and_delivery' : 'popboom_generation',
    currentTask,
    summary: `${currentTask}。已核对记录 ${evidence.jobs.map((j) => j.recordId).join(' / ')}。`,
    nextAction: allSucceeded
      ? '核对实际画面、口播、口型及完整交付包。'
      : '只查询现有记录并处理实际结果。',
    missingInputs: [],
    artifacts: evidence.jobs
      .filter((j) => j.verifiedLocalPath)
      .map((j) => ({
        kind: 'video',
        label: `${j.color || j.variantId} · 成片待验收 · ${j.recordId}`,
        path: j.verifiedLocalPath,
      })),
    productionEvidence: evidence,
  };
}

export async function readCurrentPhaseResult(runDir, phase) {
  const resultPath = path.join(runDir, 'web', `result-${phase}.json`);
  const [result, runtime] = await Promise.all([
    readJson(resultPath),
    readJson(path.join(runDir, 'web/runtime.json')),
  ]);
  if (!result || runtime?.phase !== phase) return result;
  if (runtime.attemptId)
    return result.attemptId === runtime.attemptId ? result : null;
  if (
    runtime.startedAt &&
    (await stat(resultPath)).mtimeMs < Date.parse(runtime.startedAt)
  )
    return null;
  return result;
}

export async function reconcilePaidRun(runDir) {
  const result = reconcilePaidOutcome(
    await readProductionEvidence(runDir).catch(() => null),
  );
  if (!result) return null;
  const runtime = await readJson(path.join(runDir, 'web/runtime.json'));
  const current = await readJson(path.join(runDir, 'web/result-paid.json'));
  if (current)
    await writeJsonAtomic(
      path.join(runDir, 'web/history', `result-paid-${Date.now()}.json`),
      current,
    );
  await writeJsonAtomic(path.join(runDir, 'web/result-paid.json'), {
    ...result,
    attemptId: runtime?.attemptId ?? null,
    reconciledAt: new Date().toISOString(),
  });
  await transitionStatus(runDir, {
    state: result.outcome,
    stageKey: result.stageKey,
    currentTask: result.currentTask,
    note: result.summary,
  });
  return result;
}
