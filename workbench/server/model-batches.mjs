import { createHash, randomBytes } from 'node:crypto';
import { constants } from 'node:fs';
import {
  copyFile,
  lstat,
  mkdir,
  open,
  readFile,
  readdir,
  rm,
} from 'node:fs/promises';
import path from 'node:path';
import {
  readJson,
  readStatus,
  transitionStatus,
  writeJsonAtomic,
} from './state.mjs';

const runPattern = /^zb-\d{8}-[0-9a-f]{10}$/;
const batchPattern = /^mb-\d{8}-[0-9a-f]{10}$/;
const requestPattern =
  /^[0-9a-f]{8}-[0-9a-f]{4}-[1-8][0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$/i;
const fail = (message) => {
  throw Object.assign(new Error(message), { statusCode: 409 });
};
const hash = (value) =>
  createHash('sha256').update(JSON.stringify(value)).digest('hex');
export function modelRunId() {
  return `zb-${new Date().toISOString().slice(0, 10).replaceAll('-', '')}-${randomBytes(5).toString('hex')}`;
}
function childDir(root, id) {
  if (!runPattern.test(id)) fail('制作任务标识无效');
  return path.join(path.resolve(root), id);
}
function batchFile(root, id) {
  if (!batchPattern.test(id)) fail('多模特批次标识无效');
  return path.join(root, '_model-batches', `${id}.json`);
}
async function removeOwned(root, dir) {
  const target = path.resolve(dir);
  if (
    path.dirname(target) !== path.resolve(root) ||
    !runPattern.test(path.basename(target))
  )
    fail('不能清理制作目录范围之外的文件');
  await rm(target, { recursive: true, force: true });
}

// Called only after the complete multipart upload has passed validation. No
// executor is started here; the caller enqueues only after this commit returns.
export async function persistModelRuns({
  root,
  primaryRunId,
  intake,
  policy,
  requestId = null,
  newRunId = modelRunId,
  copy = copyFile,
}) {
  const primary = childDir(root, primaryRunId);
  const owned = [primary]; // Caller owns this successfully-created upload dir.
  let receiptPath = null,
    ownsReceipt = false,
    groupPath = null,
    ownsGroup = false;
  try {
    if (requestId && !requestPattern.test(requestId)) fail('制作请求标识无效');
    const { models, sourceUrls, ...common } = intake;
    if (!Array.isArray(models) || !models.length || models.length > 6)
      fail('制作模特范围无效');
    const requestHash = hash(intake);
    const ids = [primaryRunId, ...models.slice(1).map(() => newRunId())];
    if (new Set(ids).size !== ids.length) fail('制作任务标识冲突');
    const batchId =
      models.length > 1 ? primaryRunId.replace(/^zb-/, 'mb-') : null;
    const pendingReceipt = {
      state: 'pending',
      requestHash,
      ownerProcessId: process.pid,
      plannedRunIds: ids,
      ownedRunIds: [primaryRunId],
      batchId,
      createdAt: new Date().toISOString(),
    };
    if (requestId) {
      receiptPath = path.join(
        root,
        '_intake-requests',
        `${requestId.toLowerCase()}.json`,
      );
      await mkdir(path.dirname(receiptPath), { recursive: true });
      let handle;
      try {
        handle = await open(receiptPath, 'wx');
      } catch (error) {
        if (error.code !== 'EEXIST') throw error;
        const previous = await readJson(receiptPath).catch(() => null);
        if (previous?.requestHash !== requestHash)
          fail('同一制作请求的内容已变化，请重新提交');
        if (previous.state !== 'committed')
          fail('该制作请求尚未确认完成，请核对已有任务后继续');
        const runs = await Promise.all(
          previous.runIds.map(async (id) => {
            const runDir = childDir(root, id);
            const status = await readStatus(runDir);
            if (!status) fail('已有制作请求的任务记录缺失');
            return { runId: id, runDir, status };
          }),
        );
        await removeOwned(root, primary);
        return {
          ...runs[0],
          runs,
          batchId: previous.batchId,
          idempotent: true,
        };
      }
      ownsReceipt = true;
      try {
        await handle.writeFile(JSON.stringify(pendingReceipt));
      } finally {
        await handle.close();
      }
    }
    const variants = intake.variants.map((variant) => ({
      id: variant.id,
      name: variant.name,
    }));
    const children = models.map((model, index) => ({
      runId: ids[index],
      modelPreset: model.preset ?? model.name,
      marketCode: model.marketCode,
      locale: model.locale,
    }));
    const scope = {
      batchId,
      sku: intake.sku,
      variants,
      children,
      plannedVideoCount: models.length * variants.length,
    };
    const now = new Date().toISOString();
    const runs = [];
    for (const [index, model] of models.entries()) {
      const runId = ids[index],
        runDir = childDir(root, runId);
      if (index) {
        await mkdir(runDir, { recursive: false });
        owned.push(runDir);
        pendingReceipt.ownedRunIds.push(runId);
        if (receiptPath) await writeJsonAtomic(receiptPath, pendingReceipt);
        for (const variant of intake.variants)
          for (const image of variant.images) {
            const source = path.resolve(primary, image.relativePath),
              destination = path.resolve(runDir, image.relativePath);
            if (
              !source.startsWith(primary + path.sep) ||
              !destination.startsWith(runDir + path.sep)
            )
              fail('商品图片路径无效');
            await mkdir(path.dirname(destination), { recursive: true });
            // Copy-on-write where supported; never hard-link mutable run inputs.
            await copy(
              source,
              destination,
              constants.COPYFILE_EXCL | constants.COPYFILE_FICLONE,
            );
            const bytes = await readFile(destination);
            if (
              createHash('sha256').update(bytes).digest('hex') !== image.sha256
            )
              fail('商品图片复制校验失败');
          }
      }
      const stored = {
        ...common,
        model: structuredClone(model),
        amazonUrl: sourceUrls[model.marketCode] ?? '',
        runId,
        createdAt: now,
        source: 'zibuyu-local-web',
        execution: policy,
        ...(batchId
          ? { modelBatchId: batchId, modelBatchScopeHash: hash(scope) }
          : {}),
      };
      await writeJsonAtomic(path.join(runDir, 'intake.json'), stored);
      const status = await transitionStatus(runDir, {
        stageKey: 'intake_validation',
        state: 'queued',
        currentTask: '等待 Codex 处理',
        note: `已接收 ${variants.length} 个颜色 · ${model.preset ?? model.name} · 计划 ${variants.length} 条视频`,
      });
      runs.push({ runId, runDir, status });
    }
    if (batchId) {
      groupPath = batchFile(root, batchId);
      await mkdir(path.dirname(groupPath), { recursive: true });
      const handle = await open(groupPath, 'wx');
      ownsGroup = true;
      try {
        await handle.writeFile(
          JSON.stringify({
            ...scope,
            scopeHash: hash(scope),
            createdAt: now,
            committed: true,
          }),
        );
      } finally {
        await handle.close();
      }
    }
    if (receiptPath)
      await writeJsonAtomic(receiptPath, {
        state: 'committed',
        requestHash,
        batchId,
        runIds: ids,
      });
    return { ...runs[0], runs, batchId, idempotent: false };
  } catch (error) {
    // No work was queued yet. Only directories created by this request are owned.
    for (const dir of owned.reverse())
      await removeOwned(root, dir).catch(() => {});
    if (ownsGroup) await rm(groupPath, { force: true }).catch(() => {});
    if (ownsReceipt) await rm(receiptPath, { force: true }).catch(() => {});
    throw error;
  }
}

export async function readModelBatch(runDir) {
  const intake = await readJson(path.join(runDir, 'intake.json'));
  const persistedStatus = await readStatus(runDir);
  if (
    persistedStatus?.modelBatchId &&
    persistedStatus.modelBatchId !== intake?.modelBatchId
  )
    fail('制作单的多模特批次关联缺失或变化');
  if (!intake?.modelBatchId) return null;
  const root = path.dirname(runDir);
  const manifest = await readJson(batchFile(root, intake.modelBatchId));
  if (
    !manifest?.committed ||
    !Array.isArray(manifest.children) ||
    manifest.children.length < 2 ||
    manifest.children.length > 6 ||
    !Array.isArray(manifest.variants)
  )
    fail('多模特批次尚未完整保存');
  const scope = {
    batchId: manifest.batchId,
    sku: manifest.sku,
    variants: manifest.variants,
    children: manifest.children,
    plannedVideoCount: manifest.plannedVideoCount,
  };
  if (
    hash(scope) !== manifest.scopeHash ||
    manifest.scopeHash !== intake.modelBatchScopeHash ||
    manifest.batchId !== intake.modelBatchId ||
    new Set(manifest.children.map((child) => child.runId)).size !==
      manifest.children.length ||
    !manifest.children.some((child) => child.runId === intake.runId)
  )
    fail('多模特批次范围校验失败');
  const runs = await Promise.all(
    manifest.children.map(async (child) => {
      const dir = childDir(root, child.runId);
      const item = await readJson(path.join(dir, 'intake.json'));
      if (
        !item ||
        item.modelBatchScopeHash !== manifest.scopeHash ||
        item.modelBatchId !== manifest.batchId ||
        item.sku !== manifest.sku ||
        item.model?.preset !== child.modelPreset ||
        item.model.marketCode !== child.marketCode ||
        item.model.locale !== child.locale ||
        hash(item.variants.map(({ id, name }) => ({ id, name }))) !==
          hash(manifest.variants)
      )
        fail('同批模特或颜色范围发生变化');
      return { ...child, status: await readStatus(dir) };
    }),
  );
  return {
    batchId: manifest.batchId,
    scopeHash: manifest.scopeHash,
    modelCount: runs.length,
    colorCount: manifest.variants.length,
    variantIds: manifest.variants.map((variant) => variant.id),
    plannedVideoCount: manifest.plannedVideoCount,
    runs,
    allDelivered: runs.every((run) => run.status?.state === 'delivered'),
  };
}

export async function modelBatchForDisplay(runDir) {
  try {
    return { modelBatch: await readModelBatch(runDir), modelBatchError: null };
  } catch (error) {
    return {
      modelBatch: null,
      modelBatchError:
        error.statusCode === 409
          ? error.message
          : '多模特批次记录需要核对；现有产物和回执仍保留',
    };
  }
}

// Startup-only recovery. Pending receipts precede every enqueue. Remove only
// journal-owned staging; preserve anything showing execution or unknown files.
export async function recoverIncompleteModelSubmissions(root) {
  const folder = path.join(root, '_intake-requests');
  const entries = await readdir(folder).catch(() => []);
  const recovered = [];
  async function stagingOnly(dir, prefix = '', budget = { remaining: 1000 }) {
    const stat = await lstat(dir).catch((error) =>
      error.code === 'ENOENT' ? null : Promise.reject(error),
    );
    if (!stat) return true;
    if (!stat.isDirectory() || stat.isSymbolicLink()) return false;
    for (const entry of await readdir(dir, { withFileTypes: true })) {
      if (--budget.remaining < 0 || entry.isSymbolicLink()) return false;
      const relative = prefix + entry.name;
      if (entry.isDirectory()) {
        if (
          !['uploads', 'model', 'web'].includes(relative) &&
          !relative.startsWith('uploads/')
        )
          return false;
        if (
          !(await stagingOnly(
            path.join(dir, entry.name),
            relative + '/',
            budget,
          ))
        )
          return false;
      } else if (
        !entry.isFile() ||
        !(
          ['intake.json', 'web/status.json', 'web/events.jsonl'].includes(
            relative,
          ) || /^(uploads|model)\/.+\.(png|jpg|webp)$/i.test(relative)
        )
      )
        return false;
    }
    const status = !prefix ? await readStatus(dir) : null;
    return !status || (status.state === 'queued' && !status.sessionId);
  }
  for (const name of entries) {
    if (!name.endsWith('.json') || !requestPattern.test(name.slice(0, -5)))
      continue;
    const file = path.join(folder, name);
    const receipt = await readJson(file).catch(() => null);
    if (
      receipt?.state !== 'pending' ||
      !Array.isArray(receipt.ownedRunIds) ||
      !receipt.ownedRunIds.length ||
      receipt.ownedRunIds.length > 6 ||
      !Array.isArray(receipt.plannedRunIds)
    )
      continue;
    if (
      Number.isInteger(receipt.ownerProcessId) &&
      receipt.ownerProcessId > 0
    ) {
      try {
        process.kill(receipt.ownerProcessId, 0);
        continue;
      } catch (error) {
        if (error.code !== 'ESRCH') continue;
      }
    }
    try {
      const ids = receipt.ownedRunIds;
      if (
        new Set(ids).size !== ids.length ||
        ids.some(
          (id) => !runPattern.test(id) || !receipt.plannedRunIds.includes(id),
        )
      )
        continue;
      let safe = true;
      for (const id of ids)
        if (!(await stagingOnly(childDir(root, id)))) safe = false;
      const groupPath = receipt.batchId
        ? batchFile(root, receipt.batchId)
        : null;
      const group = groupPath ? await readJson(groupPath) : null;
      if (
        group &&
        (!Array.isArray(group.children) ||
          group.children.some((child) => !ids.includes(child.runId)) ||
          group.children.length !== ids.length)
      )
        safe = false;
      if (!safe) continue;
      for (const id of ids) await removeOwned(root, childDir(root, id));
      if (group) await rm(groupPath, { force: true });
      await rm(file, { force: true });
      recovered.push(name.slice(0, -5));
    } catch {
      /* Preserve anything not proven to be unfinished staging. */
    }
  }
  return recovered;
}
