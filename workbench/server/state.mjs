import {
  appendFile,
  mkdir,
  readFile,
  rename,
  writeFile,
} from 'node:fs/promises';
import path from 'node:path';

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
  const temporary = `${filePath}.${process.pid}.${Date.now()}.tmp`;
  await writeFile(temporary, `${JSON.stringify(value, null, 2)}\n`, 'utf8');
  await rename(temporary, filePath);
}

export async function appendEvent(runDir, event) {
  const eventPath = path.join(runDir, 'web', 'events.jsonl');
  await mkdir(path.dirname(eventPath), { recursive: true });
  await appendFile(eventPath, `${JSON.stringify(event)}\n`, 'utf8');
}

export async function readStatus(runDir) {
  return readJson(path.join(runDir, 'web', 'status.json'));
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
  const remainingSeconds =
    state === 'delivered'
      ? 0
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
    etaLabel: etaLabel(remainingSeconds, waitingForApproval),
    variantCount,
    startedAt: current.startedAt ?? now,
    stageStartedAt:
      current.stageKey === stage.key ? (current.stageStartedAt ?? now) : now,
    updatedAt: now,
    completedAt: state === 'delivered' ? now : (current.completedAt ?? null),
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
