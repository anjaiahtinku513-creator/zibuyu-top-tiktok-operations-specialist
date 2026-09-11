import {
  appendFile,
  mkdir,
  readFile,
  rename,
  writeFile,
} from 'node:fs/promises';
import path from 'node:path';
import { randomUUID } from 'node:crypto';

import {
  WORKFLOW_STAGES,
  estimateRemainingSeconds,
  etaLabel,
  getProgress,
  getStage,
} from './workflow.mjs';

export async function readJson(filePath, fallback = null) {
  try {
    return JSON.parse(await readFile(filePath, 'utf8'));
  } catch (error) {
    if (error?.code === 'ENOENT') return fallback;
    throw error;
  }
}

export async function writeJsonAtomic(filePath, value) {
  await mkdir(path.dirname(filePath), { recursive: true });
  const temporary = `${filePath}.${process.pid}.${randomUUID()}.tmp`;
  await writeFile(temporary, `${JSON.stringify(value, null, 2)}\n`, 'utf8');
  for (let attempt = 0; ; attempt++) {
    try {
      await rename(temporary, filePath);
      break;
    } catch (error) {
      if (attempt >= 5 || !['EPERM', 'EACCES', 'EBUSY'].includes(error.code))
        throw error;
      await new Promise((resolve) => setTimeout(resolve, 20 * (attempt + 1)));
    }
  }
}

export async function appendEvent(runDir, event) {
  const eventPath = path.join(runDir, 'web', 'events.jsonl');
  await mkdir(path.dirname(eventPath), { recursive: true });
  await appendFile(eventPath, `${JSON.stringify(event)}\n`, 'utf8');
}

export async function readStatus(runDir) {
  const [status, session, runtime, preparation] = await Promise.all([
    readJson(path.join(runDir, 'web', 'status.json')),
    readJson(path.join(runDir, 'web', 'session.json')),
    readJson(path.join(runDir, 'web', 'runtime.json')),
    readJson(path.join(runDir, 'web', 'preparation.json')),
  ]);
  if (!status) return null;
  let shared = null;
  if (
    preparation?.sharedDir &&
    path
      .resolve(preparation.sharedDir)
      .startsWith(path.join(path.dirname(runDir), '_shared') + path.sep)
  ) {
    const [sharedStatus, sharedSession, manifest] = await Promise.all([
      readJson(path.join(preparation.sharedDir, 'web', 'status.json')),
      readJson(path.join(preparation.sharedDir, 'web', 'session.json')),
      readJson(path.join(preparation.sharedDir, 'manifest.json')),
    ]);
    shared = {
      sessionId: sharedSession?.sessionId ?? null,
      stageLabel:
        manifest?.state === 'ready'
          ? '公共准备已完成'
          : (sharedStatus?.stageLabel ?? '等待公共准备'),
      state:
        manifest?.state === 'ready'
          ? 'ready'
          : (sharedStatus?.state ?? 'queued'),
      summary: manifest?.summary ?? sharedStatus?.currentTask ?? null,
    };
  }
  return {
    ...status,
    sessionId: session?.sessionId ?? status.sessionId ?? null,
    execution: runtime ?? null,
    preparation: preparation ? { ...preparation, shared } : null,
  };
}

export async function transitionStatus(
  runDir,
  {
    stageKey,
    state = 'running',
    currentTask,
    note = '',
    sessionId,
    error,
    forceProgress,
  },
) {
  const now = new Date().toISOString();
  const current = (await readStatus(runDir)) ?? {};
  const intake = await readJson(path.join(runDir, 'intake.json'), {});
  const stage = getStage(stageKey ?? current.stageKey ?? 'intake_validation');
  const variantCount = intake?.variants?.length ?? current.variantCount ?? 1;
  const waitingForApproval = state === 'awaiting_paid_approval';
  const paused = [
    'needs_review',
    'needs_input',
    'blocked',
    'failed',
    'submission_unknown',
  ].includes(state);
  const remainingSeconds =
    state === 'delivered'
      ? 0
      : paused
        ? null
        : estimateRemainingSeconds(stage.key, variantCount);
  const status = {
    ...current,
    runId: intake.runId ?? current.runId,
    sku: intake.sku ?? current.sku,
    modelPreset:
      intake.model?.preset ?? intake.model?.name ?? current.modelPreset,
    modelBatchId: intake.modelBatchId ?? current.modelBatchId ?? null,
    state,
    stageKey: stage.key,
    stageLabel: stage.label,
    stageIndex: stage.index + 1,
    totalStages: WORKFLOW_STAGES.length,
    currentTask: currentTask || stage.label,
    note,
    progress: forceProgress ?? getProgress(stage.key, state),
    estimatedRemainingSeconds: remainingSeconds,
    etaLabel: paused
      ? state === 'needs_review'
        ? '待验收'
        : '待处理'
      : etaLabel(remainingSeconds, waitingForApproval),
    variantCount,
    startedAt: current.startedAt ?? now,
    stageStartedAt:
      current.stageKey === stage.key ? (current.stageStartedAt ?? now) : now,
    updatedAt: now,
    completedAt: state === 'delivered' ? (current.completedAt ?? now) : null,
    sessionId: sessionId ?? current.sessionId ?? null,
    error: error ?? null,
  };

  await writeJsonAtomic(path.join(runDir, 'web', 'status.json'), status);
  await appendEvent(runDir, {
    type: current.stageKey === stage.key ? 'status_updated' : 'stage_changed',
    at: now,
    state,
    stageKey: stage.key,
    stageLabel: stage.label,
    currentTask: status.currentTask,
    progress: status.progress,
    etaLabel: status.etaLabel,
    note,
  });
  return status;
}
