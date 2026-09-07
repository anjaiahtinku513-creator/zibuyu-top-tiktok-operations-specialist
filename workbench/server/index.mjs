import { createHash, randomBytes, timingSafeEqual } from 'node:crypto';
import { createReadStream } from 'node:fs';
import {
  mkdir,
  open,
  readFile,
  readdir,
  rm,
  stat,
  writeFile,
} from 'node:fs/promises';
import os from 'node:os';
import path from 'node:path';
import { fileURLToPath } from 'node:url';
import { readUsage } from './usage.mjs';
import {
  persistModelRuns,
  modelBatchForDisplay,
  recoverIncompleteModelSubmissions,
} from './model-batches.mjs';

import multipart from '@fastify/multipart';
import Fastify from 'fastify';
import {
  PUBLISH_ACCOUNTS,
  CAPTION_TRANSPORT,
  publishingDetail,
  saveIntent,
  freshHandoff,
  reviewDelivery,
  preparePublishing,
  approvePublishing,
  reconcilePublishing,
  recoverPublishing,
} from './publishing.mjs';

import {
  CODEX_EXECUTION_POLICY,
  authorizationFingerprint,
  discoverCodexCli,
  enqueueColorClassification,
  enqueueCodexPhase,
  getExecutorInfo,
} from './codex-runner.mjs';
import {
  readJson,
  readStatus,
  transitionStatus,
  writeJsonAtomic,
} from './state.mjs';
import {
  detectImageType,
  parseModelBatchPayload,
  safeFileStem,
} from './validation.mjs';

const serverDir = path.dirname(fileURLToPath(import.meta.url));
const projectRoot = path.dirname(serverDir);
const runsRoot = path.resolve(
  process.env.ZIBUYU_RUNS_ROOT ||
    path.join(os.homedir(), '.codex', 'zibuyu-runs'),
);
const classificationsRoot = path.join(runsRoot, '_classifications');
const host = '127.0.0.1';
const port = Number(process.env.PORT || 4319);
const sessionToken = randomBytes(32).toString('hex');
const allowedOrigins = new Set([
  `http://127.0.0.1:${port}`,
  `http://localhost:${port}`,
  'http://127.0.0.1:4318',
  'http://localhost:4318',
]);

const app = Fastify({
  logger: {
    level: process.env.LOG_LEVEL || 'info',
  },
  bodyLimit: 2 * 1024 * 1024,
});

await app.register(multipart, {
  limits: {
    fieldNameSize: 100,
    fieldSize: 128 * 1024,
    fields: 4,
    fileSize: 20 * 1024 * 1024,
    files: 61,
    parts: 66,
  },
});

function httpError(statusCode, message) {
  const error = new Error(message);
  error.statusCode = statusCode;
  return error;
}

function tokenMatches(value) {
  const candidate = Buffer.from(String(value || ''));
  const expected = Buffer.from(sessionToken);
  return (
    candidate.length === expected.length && timingSafeEqual(candidate, expected)
  );
}

function requireBrowserOrigin(request) {
  const origin = request.headers.origin;
  if (origin && !allowedOrigins.has(origin)) {
    throw httpError(403, '该请求不来自本地 Zibuyu 制作台');
  }
}

function requireMutationToken(request) {
  requireBrowserOrigin(request);
  if (!tokenMatches(request.headers['x-zibuyu-token'])) {
    throw httpError(403, '本地会话已失效，请刷新页面');
  }
}

function cookieValue(request, name) {
  const cookies = String(request.headers.cookie || '').split(';');
  for (const cookie of cookies) {
    const [key, ...value] = cookie.trim().split('=');
    if (key === name) return decodeURIComponent(value.join('='));
  }
  return '';
}

function requireSessionCookie(request) {
  requireBrowserOrigin(request);
  if (!tokenMatches(cookieValue(request, 'zibuyu_session'))) {
    throw httpError(403, '本地会话已失效，请刷新页面');
  }
}

function createRunId() {
  const day = new Date().toISOString().slice(0, 10).replaceAll('-', '');
  return `zb-${day}-${randomBytes(5).toString('hex')}`;
}

function createClassificationId() {
  const day = new Date().toISOString().slice(0, 10).replaceAll('-', '');
  return `cls-${day}-${randomBytes(5).toString('hex')}`;
}

function getRunDir(runId) {
  if (!/^zb-\d{8}-[0-9a-f]{10}$/.test(String(runId))) {
    throw httpError(404, '任务不存在');
  }
  const resolved = path.resolve(runsRoot, runId);
  if (!resolved.startsWith(`${runsRoot}${path.sep}`)) {
    throw httpError(404, '任务不存在');
  }
  return resolved;
}

function getClassificationDir(batchId) {
  if (!/^cls-\d{8}-[0-9a-f]{10}$/.test(String(batchId))) {
    throw httpError(404, '颜色识别批次不存在');
  }
  const resolved = path.resolve(classificationsRoot, batchId);
  if (!resolved.startsWith(`${classificationsRoot}${path.sep}`)) {
    throw httpError(404, '颜色识别批次不存在');
  }
  return resolved;
}

async function removeIncompleteRun(runDir) {
  const resolved = path.resolve(runDir);
  if (resolved.startsWith(`${runsRoot}${path.sep}`)) {
    await rm(resolved, { recursive: true, force: true });
  }
}

async function readEvents(runDir, limit = 200) {
  try {
    const content = await readFile(
      path.join(runDir, 'web', 'events.jsonl'),
      'utf8',
    );
    return content
      .split(/\r?\n/)
      .filter(Boolean)
      .slice(-limit)
      .map((line) => JSON.parse(line));
  } catch (error) {
    if (error?.code === 'ENOENT') return [];
    throw error;
  }
}

async function getRunDetail(runId) {
  const runDir = getRunDir(runId);
  const intake = await readJson(path.join(runDir, 'intake.json'));
  if (!intake) throw httpError(404, '任务不存在');
  const [status, prepareResult, paidResult, approval, events, usage] =
    await Promise.all([
      readStatus(runDir),
      readJson(path.join(runDir, 'web', 'result-prepare.json')),
      readJson(path.join(runDir, 'web', 'result-paid.json')),
      readJson(path.join(runDir, 'approval.json')),
      readEvents(runDir),
      readUsage(runDir),
    ]);
  const classificationUsage = intake.classificationBatchId
    ? await readUsage(getClassificationDir(intake.classificationBatchId))
    : null;
  return {
    intake,
    status,
    prepareResult,
    paidResult,
    approval,
    events,
    usage,
    classificationUsage,
    ...(await modelBatchForDisplay(runDir)),
  };
}

async function listRuns() {
  const entries = await readdir(runsRoot, { withFileTypes: true }).catch(
    () => [],
  );
  const statuses = await Promise.all(
    entries
      .filter(
        (entry) =>
          entry.isDirectory() && /^zb-\d{8}-[0-9a-f]{10}$/.test(entry.name),
      )
      .map(async (entry) => {
        const dir = path.join(runsRoot, entry.name);
        const status = await readStatus(dir);
        if (status?.modelBatchId) {
          const { modelBatchError } = await modelBatchForDisplay(dir);
          return { ...status, modelBatchError };
        }
        return status;
      }),
  );
  return statuses
    .filter(Boolean)
    .sort((left, right) =>
      String(right.updatedAt).localeCompare(String(left.updatedAt)),
    );
}

async function getClassificationDetail(batchId) {
  const batchDir = getClassificationDir(batchId);
  const request = await readJson(path.join(batchDir, 'request.json'));
  if (!request) throw httpError(404, '颜色识别批次不存在');
  const [status, result] = await Promise.all([
    readJson(path.join(batchDir, 'status.json')),
    readJson(path.join(batchDir, 'result.json')),
  ]);
  return { request, status, result, usage: await readUsage(batchDir) };
}

function parseClassificationPayload(value) {
  let payload;
  try {
    payload = value ? JSON.parse(value) : {};
  } catch {
    throw httpError(400, '颜色识别参数不是有效 JSON');
  }
  return {
    sku: String(payload?.sku ?? '')
      .trim()
      .slice(0, 64),
  };
}

async function saveMultipartClassification(request) {
  let batch = null;
  let batchDir = null;

  try {
    for await (const part of request.parts()) {
      if (part.type === 'field') {
        if (part.fieldname !== 'payload') continue;
        if (batch) throw httpError(400, '颜色识别参数重复');
        const payload = parseClassificationPayload(part.value);
        const batchId = createClassificationId();
        batchDir = getClassificationDir(batchId);
        await mkdir(batchDir, { recursive: false });
        batch = {
          batchId,
          sku: payload.sku,
          createdAt: new Date().toISOString(),
          source: 'zibuyu-local-web',
          images: [],
        };
        continue;
      }

      if (!batch || !batchDir) {
        part.file.resume();
        throw httpError(400, '请先提交颜色识别参数，再上传图片');
      }
      if (part.fieldname !== 'image') {
        part.file.resume();
        throw httpError(400, '颜色识别图片字段无效');
      }
      if (batch.images.length >= 60) {
        part.file.resume();
        throw httpError(400, '单次最多识别 60 张商品图');
      }

      const chunks = [];
      let size = 0;
      for await (const chunk of part.file) {
        size += chunk.length;
        chunks.push(chunk);
      }
      if (part.file.truncated) throw httpError(413, '单张图片不能超过 20 MB');
      if (!size) throw httpError(400, '上传的图片为空文件');

      const buffer = Buffer.concat(chunks, size);
      let detected;
      try {
        detected = detectImageType(buffer);
      } catch (error) {
        throw httpError(400, `${part.filename || '商品图'}：${error.message}`);
      }

      const imageId = `img-${String(batch.images.length + 1).padStart(3, '0')}`;
      const imageDir = path.join(batchDir, 'images');
      await mkdir(imageDir, { recursive: true });
      const filename = `${String(batch.images.length + 1).padStart(3, '0')}-${safeFileStem(part.filename)}${detected.extension}`;
      const absolutePath = path.join(imageDir, filename);
      await writeFile(absolutePath, buffer, { flag: 'wx' });
      batch.images.push({
        id: imageId,
        originalName: String(part.filename || filename).slice(0, 255),
        relativePath: path.relative(batchDir, absolutePath),
        mimeType: detected.mimeType,
        size,
        sha256: createHash('sha256').update(buffer).digest('hex'),
      });
    }

    if (!batch || !batchDir) throw httpError(400, '缺少颜色识别参数');
    if (!batch.images.length) throw httpError(400, '至少需要上传一张商品图');

    await writeJsonAtomic(path.join(batchDir, 'request.json'), batch);
    const status = {
      batchId: batch.batchId,
      state: 'queued',
      currentTask: '等待 Codex 识别颜色',
      note: `已接收 ${batch.images.length} 张商品图`,
      imageCount: batch.images.length,
      createdAt: batch.createdAt,
      startedAt: null,
      updatedAt: batch.createdAt,
      completedAt: null,
      error: null,
    };
    await writeJsonAtomic(path.join(batchDir, 'status.json'), status);
    return { batchId: batch.batchId, batchDir, status };
  } catch (error) {
    if (batchDir) await removeIncompleteRun(batchDir).catch(() => {});
    throw error;
  }
}

async function sha256File(filePath) {
  const hash = createHash('sha256');
  for await (const chunk of createReadStream(filePath)) hash.update(chunk);
  return hash.digest('hex');
}

async function buildArtifactManifest(runDir, prepareResult) {
  if (!prepareResult?.artifacts?.length) {
    throw httpError(409, '准备阶段未产生可授权的本地产物');
  }

  const manifest = [];
  for (const artifact of prepareResult.artifacts) {
    if (artifact.kind === 'link' && /^https?:\/\//i.test(artifact.path)) {
      manifest.push({
        kind: artifact.kind,
        label: artifact.label,
        path: artifact.path,
        external: true,
      });
      continue;
    }
    const absolute = path.resolve(
      path.isAbsolute(artifact.path)
        ? artifact.path
        : path.join(runDir, artifact.path),
    );
    if (!absolute.startsWith(`${runDir}${path.sep}`)) {
      throw httpError(409, `准备产物不在当前任务目录内：${artifact.label}`);
    }
    const fileStat = await stat(absolute).catch(() => null);
    if (!fileStat?.isFile()) {
      throw httpError(409, `准备产物缺失：${artifact.label}`);
    }
    manifest.push({
      kind: artifact.kind,
      label: artifact.label,
      path: path.relative(runDir, absolute),
      size: fileStat.size,
      sha256: await sha256File(absolute),
    });
  }
  if (!manifest.some((item) => !item.external)) {
    throw httpError(409, '付费授权前至少需要一个本地可验证产物');
  }
  return manifest;
}

async function saveMultipartRun(request) {
  let intake = null;
  let runId = null;
  let runDir = null;
  let ownsRunDir = false;
  let totalFiles = 0;
  let productFileCount = 0;
  const perVariantCount = new Map();

  try {
    for await (const part of request.parts()) {
      if (part.type === 'field') {
        if (part.fieldname !== 'payload') continue;
        if (intake) throw httpError(400, '制作单参数重复');
        intake = parseModelBatchPayload(part.value);
        runId = createRunId();
        runDir = getRunDir(runId);
        await mkdir(runDir, { recursive: false });
        ownsRunDir = true;
        continue;
      }

      if (!intake || !runDir) {
        part.file.resume();
        throw httpError(400, '请先提交制作单参数，再上传图片');
      }
      if (!part.fieldname.startsWith('image__')) {
        if (
          part.fieldname !== 'model__reference' ||
          intake.model.mode !== 'custom'
        ) {
          part.file.resume();
          throw httpError(400, '图片字段无效');
        }

        const chunks = [];
        let size = 0;
        for await (const chunk of part.file) {
          size += chunk.length;
          chunks.push(chunk);
        }
        if (part.file.truncated)
          throw httpError(413, '模特参考图不能超过 20 MB');
        if (!size) throw httpError(400, '模特参考图为空文件');
        if (intake.model.referenceImage)
          throw httpError(400, '自定义模特只能上传一张参考图');

        const buffer = Buffer.concat(chunks, size);
        let detected;
        try {
          detected = detectImageType(buffer);
        } catch (error) {
          throw httpError(400, `模特参考图：${error.message}`);
        }
        const modelDir = path.join(runDir, 'model');
        await mkdir(modelDir, { recursive: true });
        const filename = `reference-${safeFileStem(part.filename)}${detected.extension}`;
        const absolutePath = path.join(modelDir, filename);
        await writeFile(absolutePath, buffer, { flag: 'wx' });
        intake.model.referenceImage = {
          originalName: String(part.filename || filename).slice(0, 255),
          relativePath: path.relative(runDir, absolutePath),
          mimeType: detected.mimeType,
          size,
          sha256: createHash('sha256').update(buffer).digest('hex'),
          usage: 'model_identity_only_never_garment_reference',
        };
        totalFiles += 1;
        continue;
      }

      const variantId = part.fieldname.slice('image__'.length);
      const variant = intake.variants.find((item) => item.id === variantId);
      if (!variant) {
        part.file.resume();
        throw httpError(400, '图片与颜色不匹配');
      }

      const count = (perVariantCount.get(variantId) ?? 0) + 1;
      if (count > 60 || productFileCount >= 60) {
        part.file.resume();
        throw httpError(400, '单个制作单最多上传 60 张商品图');
      }

      const chunks = [];
      let size = 0;
      for await (const chunk of part.file) {
        size += chunk.length;
        chunks.push(chunk);
      }
      if (part.file.truncated) throw httpError(413, '单张图片不能超过 20 MB');
      if (!size) throw httpError(400, '上传的图片为空文件');

      const buffer = Buffer.concat(chunks, size);
      let detected;
      try {
        detected = detectImageType(buffer);
      } catch (error) {
        throw httpError(400, `${variant.name}：${error.message}`);
      }

      const uploadDir = path.join(runDir, 'uploads', variantId);
      await mkdir(uploadDir, { recursive: true });
      const filename = `${String(count).padStart(2, '0')}-${safeFileStem(part.filename)}${detected.extension}`;
      const absolutePath = path.join(uploadDir, filename);
      await writeFile(absolutePath, buffer, { flag: 'wx' });
      variant.images.push({
        originalName: String(part.filename || filename).slice(0, 255),
        relativePath: path.relative(runDir, absolutePath),
        mimeType: detected.mimeType,
        size,
        sha256: createHash('sha256').update(buffer).digest('hex'),
      });
      perVariantCount.set(variantId, count);
      totalFiles += 1;
      productFileCount += 1;
    }

    if (!intake || !runDir || !runId) throw httpError(400, '缺少制作单参数');
    if (!totalFiles) throw httpError(400, '至少需要上传一张商品图');
    if (intake.model.mode === 'custom' && !intake.model.referenceImage) {
      throw httpError(400, '自定义模特必须上传一张身份参考图');
    }
    for (const variant of intake.variants) {
      if (!variant.images.length) {
        throw httpError(400, `请为 ${variant.name} 上传至少一张商品图`);
      }
    }

    return await persistModelRuns({
      root: runsRoot,
      primaryRunId: runId,
      intake,
      policy: CODEX_EXECUTION_POLICY,
      requestId: request.headers['x-zibuyu-request-id'] || null,
    });
  } catch (error) {
    if (ownsRunDir) await removeIncompleteRun(runDir).catch(() => {});
    throw error;
  }
}

async function writeApprovalExclusive(filePath, value) {
  const handle = await open(filePath, 'wx');
  try {
    await handle.writeFile(`${JSON.stringify(value, null, 2)}\n`, 'utf8');
  } finally {
    await handle.close();
  }
}

async function recoverInterruptedRuns() {
  const statuses = await listRuns();
  for (const status of statuses) {
    if (!['queued', 'running', 'submitting'].includes(status.state)) continue;
    const runDir = getRunDir(status.runId);
    const uncertain = status.state === 'submitting';
    await transitionStatus(runDir, {
      stageKey: status.stageKey,
      state: uncertain ? 'submission_unknown' : 'blocked',
      currentTask: uncertain
        ? '需人工核对 PopBoom 提交状态'
        : '本地服务曾中断，需要人工续跑',
      note: uncertain
        ? '服务在提交阶段重启，为避免重复扣费已停止自动重试'
        : '已保留现有产物和 Codex 会话记录',
    });
  }
}

async function recoverInterruptedClassifications() {
  const entries = await readdir(classificationsRoot, {
    withFileTypes: true,
  }).catch(() => []);
  for (const entry of entries) {
    if (!entry.isDirectory() || !/^cls-\d{8}-[0-9a-f]{10}$/.test(entry.name))
      continue;
    const batchDir = getClassificationDir(entry.name);
    const status = await readJson(path.join(batchDir, 'status.json'));
    if (!['queued', 'running'].includes(status?.state)) continue;
    await writeJsonAtomic(path.join(batchDir, 'status.json'), {
      ...status,
      state: 'queued',
      currentTask: '等待 Codex 重新识别颜色',
      note: '本地服务重启，安全重排只读颜色识别任务',
      updatedAt: new Date().toISOString(),
      error: null,
    });
    void enqueueColorClassification(batchDir).catch((error) => {
      app.log.error(
        { error, batchId: entry.name },
        'Recovered color classification failed',
      );
    });
  }
}

app.addHook('onRequest', async (request) => {
  if (request.url.startsWith('/api/')) requireBrowserOrigin(request);
});

app.get('/api/health', async () => {
  let codexAvailable = false;
  let codexPath = null;
  try {
    codexPath = await discoverCodexCli();
    codexAvailable = true;
  } catch {
    codexAvailable = false;
  }
  return {
    ok: true,
    service: 'zibuyu-local-bridge',
    codexAvailable,
    codexPath,
    ...getExecutorInfo(),
  };
});

app.get('/api/session', async (_request, reply) => {
  reply.header(
    'Set-Cookie',
    `zibuyu_session=${encodeURIComponent(sessionToken)}; HttpOnly; SameSite=Strict; Path=/api`,
  );
  return { token: sessionToken };
});

app.get('/api/runs', async () => ({ runs: await listRuns() }));

app.get('/api/publishing', async (request) => {
  requireSessionCookie(request);
  const runs = await listRuns();
  return {
    accounts: PUBLISH_ACCOUNTS,
    captionTransport: CAPTION_TRANSPORT,
    batches: await Promise.all(
      runs.map(async (run) => {
        const detail = await publishingDetail(getRunDir(run.runId));
        return {
          runId: run.runId,
          sku: run.sku,
          status: detail.status,
          actions: detail.actions,
        };
      }),
    ),
  };
});

app.get('/api/runs/:runId/publishing', async (request) => {
  requireSessionCookie(request);
  await getRunDetail(request.params.runId);
  return publishingDetail(getRunDir(request.params.runId));
});

app.put('/api/runs/:runId/publishing/intent', async (request) => {
  requireMutationToken(request);
  await getRunDetail(request.params.runId);
  return saveIntent(getRunDir(request.params.runId), request.body);
});

app.post('/api/runs/:runId/publishing/snapshot', async (request) => {
  requireMutationToken(request);
  return freshHandoff(getRunDir(request.params.runId));
});

app.post('/api/runs/:runId/publishing/review', async (request) => {
  requireMutationToken(request);
  return reviewDelivery(
    getRunDir(request.params.runId),
    request.body?.handoffHash,
  );
});

app.post('/api/runs/:runId/publishing/prepare', async (request, reply) => {
  requireMutationToken(request);
  return reply
    .code(202)
    .send(await preparePublishing(getRunDir(request.params.runId), runsRoot));
});

app.post('/api/runs/:runId/publishing/approve', async (request, reply) => {
  requireMutationToken(request);
  return reply
    .code(202)
    .send(
      await approvePublishing(
        getRunDir(request.params.runId),
        runsRoot,
        request.body,
      ),
    );
});

app.post('/api/runs/:runId/publishing/reconcile', async (request, reply) => {
  requireMutationToken(request);
  return reply
    .code(202)
    .send(await reconcilePublishing(getRunDir(request.params.runId)));
});

app.post('/api/classifications', async (request, reply) => {
  requireMutationToken(request);
  if (!request.isMultipart())
    throw httpError(415, '请使用 multipart/form-data 上传商品图');
  const created = await saveMultipartClassification(request);
  void enqueueColorClassification(created.batchDir).catch((error) => {
    app.log.error(
      { error, batchId: created.batchId },
      'Color classification failed',
    );
  });
  return reply
    .code(202)
    .send({ batchId: created.batchId, status: created.status });
});

app.get('/api/classifications/:batchId', async (request) =>
  getClassificationDetail(request.params.batchId),
);

app.get('/api/runs/:runId', async (request) =>
  getRunDetail(request.params.runId),
);

app.post('/api/runs', async (request, reply) => {
  requireMutationToken(request);
  if (!request.isMultipart())
    throw httpError(415, '请使用 multipart/form-data 上传制作单');
  const created = await saveMultipartRun(request);
  if (!created.idempotent)
    for (const run of created.runs) {
      void enqueueCodexPhase(run.runDir, 'prepare').catch((error) => {
        app.log.error({ error, runId: run.runId }, 'Codex preparation failed');
      });
    }
  return reply.code(202).send({
    runId: created.runId,
    status: created.status,
    batchId: created.batchId,
    runs: created.runs.map(({ runId, status }) => ({ runId, status })),
    idempotent: created.idempotent,
  });
});

app.post('/api/runs/:runId/approve', async (request, reply) => {
  requireMutationToken(request);
  const runDir = getRunDir(request.params.runId);
  const detail = await getRunDetail(request.params.runId);
  if (detail.approval) {
    return reply.send({
      approval: detail.approval,
      status: detail.status,
      idempotent: true,
    });
  }
  if (detail.status?.state !== 'awaiting_paid_approval') {
    throw httpError(409, '当前任务尚未进入付费确认阶段');
  }
  if (detail.modelBatchError) throw httpError(409, detail.modelBatchError);
  if (request.body?.confirm !== true) {
    throw httpError(400, '必须明确确认付费生成');
  }

  const allowedIds = new Set(
    detail.intake.variants.map((variant) => variant.id),
  );
  const requestedIds = Array.isArray(request.body?.variantIds)
    ? [...new Set(request.body.variantIds.map(String))]
    : [];
  if (!requestedIds.length || requestedIds.some((id) => !allowedIds.has(id))) {
    throw httpError(400, '付费授权的颜色范围无效');
  }

  const approval = {
    version: 1,
    runId: detail.intake.runId,
    scope: 'popboom_generation_and_delivery_qa',
    modelPreset: detail.intake.model.preset ?? detail.intake.model.name,
    modelBatchId: detail.intake.modelBatchId ?? null,
    plannedVideoCount: requestedIds.length,
    variantIds: requestedIds,
    variants: detail.intake.variants
      .filter((variant) => requestedIds.includes(variant.id))
      .map(({ id, name }) => ({ id, name })),
    approvedAt: new Date().toISOString(),
    approvedBy: 'local-web-user',
    statement:
      '用户已在 Zibuyu 本地制作台确认对列明颜色执行 PopBoom 生成与交付质检。',
    artifactManifest: await buildArtifactManifest(runDir, detail.prepareResult),
  };
  approval.authorizationFingerprint = authorizationFingerprint({
    intake: detail.intake,
    prepareResult: detail.prepareResult,
    variantIds: requestedIds,
    artifactManifest: approval.artifactManifest,
  });

  try {
    await writeApprovalExclusive(path.join(runDir, 'approval.json'), approval);
  } catch (error) {
    if (error?.code !== 'EEXIST') throw error;
    return reply.send({
      approval: await readJson(path.join(runDir, 'approval.json')),
      status: await readStatus(runDir),
      idempotent: true,
    });
  }

  const status = await transitionStatus(runDir, {
    stageKey: 'popboom_generation',
    state: 'queued',
    currentTask: '已授权，等待 Codex 执行',
    note: `已授权 ${requestedIds.length} 个颜色，授权回执已落盘`,
  });
  void enqueueCodexPhase(runDir, 'paid').catch((error) => {
    app.log.error(
      { error, runId: detail.intake.runId },
      'Codex paid phase failed',
    );
  });
  return reply.code(202).send({ approval, status });
});

app.get('/api/runs/:runId/events', async (request, reply) => {
  requireSessionCookie(request);
  const runDir = getRunDir(request.params.runId);
  if (!(await readStatus(runDir))) throw httpError(404, '任务不存在');

  reply.hijack();
  reply.raw.writeHead(200, {
    'Content-Type': 'text/event-stream; charset=utf-8',
    'Cache-Control': 'no-cache, no-transform',
    Connection: 'keep-alive',
    'X-Accel-Buffering': 'no',
  });

  let closed = false;
  let lastUpdatedAt = '';
  let lastHeartbeat = Date.now();
  const push = async () => {
    if (closed) return;
    const status = await readStatus(runDir);
    if (!status) return;
    if (status.updatedAt !== lastUpdatedAt) {
      lastUpdatedAt = status.updatedAt;
      reply.raw.write(`event: status\ndata: ${JSON.stringify(status)}\n\n`);
    } else if (Date.now() - lastHeartbeat > 15000) {
      lastHeartbeat = Date.now();
      reply.raw.write(': keep-alive\n\n');
    }
  };

  await push();
  const timer = setInterval(() => void push().catch(() => {}), 1000);
  request.raw.on('close', () => {
    closed = true;
    clearInterval(timer);
  });
});

app.get('/api/runs/:runId/artifact', async (request, reply) => {
  requireSessionCookie(request);
  const runDir = getRunDir(request.params.runId);
  const requested = String(request.query?.path || '');
  if (!requested) throw httpError(400, '缺少产物路径');
  const absolute = path.resolve(
    path.isAbsolute(requested) ? requested : path.join(runDir, requested),
  );
  if (!absolute.startsWith(`${runDir}${path.sep}`)) {
    throw httpError(403, '只能读取当前任务的产物');
  }
  const fileStat = await stat(absolute).catch(() => null);
  if (!fileStat?.isFile()) throw httpError(404, '产物文件不存在');

  const mimeByExtension = {
    '.png': 'image/png',
    '.jpg': 'image/jpeg',
    '.jpeg': 'image/jpeg',
    '.webp': 'image/webp',
    '.mp4': 'video/mp4',
    '.json': 'application/json; charset=utf-8',
    '.txt': 'text/plain; charset=utf-8',
    '.md': 'text/markdown; charset=utf-8',
  };
  reply.type(
    mimeByExtension[path.extname(absolute).toLowerCase()] ||
      'application/octet-stream',
  );
  reply.header('Cache-Control', 'private, max-age=60');
  return reply.send(createReadStream(absolute));
});

app.setErrorHandler((error, request, reply) => {
  const statusCode =
    error.statusCode || (error.code === 'FST_REQ_FILE_TOO_LARGE' ? 413 : 500);
  if (statusCode >= 500)
    request.log.error({ error }, 'Unhandled local bridge error');
  reply.code(statusCode).send({
    error: statusCode >= 500 ? '本地服务执行失败' : error.message,
    detail: statusCode >= 500 ? error.message : undefined,
  });
});

await mkdir(classificationsRoot, { recursive: true });
await recoverIncompleteModelSubmissions(runsRoot);
await recoverInterruptedRuns();
await recoverPublishing(runsRoot);
await recoverInterruptedClassifications();
await app.listen({ host, port });

const shutdown = async () => {
  await app.close();
  process.exit(0);
};
process.once('SIGINT', shutdown);
process.once('SIGTERM', shutdown);

app.log.info(
  { url: `http://${host}:${port}`, projectRoot, runsRoot },
  'Zibuyu local bridge ready',
);
