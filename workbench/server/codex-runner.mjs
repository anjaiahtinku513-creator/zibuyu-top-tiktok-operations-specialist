import { spawn } from 'node:child_process';
import { createHash, randomUUID } from 'node:crypto';
import { createWriteStream } from 'node:fs';
import { access, mkdir, open, readFile, readdir } from 'node:fs/promises';
import os from 'node:os';
import path from 'node:path';
import readline from 'node:readline';
import { fileURLToPath } from 'node:url';
import { createWorkQueue } from './work-queue.mjs';
import { archivePublishEvent } from './publish-evidence.mjs';
import { validatePreparationCompiles } from './compile-validation.mjs';
import { verifyAuditedPaidRecovery } from './paid-recovery.mjs';
import {
  attemptOutputPath,
  reconcilePaidRun,
} from './production-reconciliation.mjs';
import {
  sharedRequestFor,
  initializeShared,
  sealShared,
  readVerifiedShared,
  attachShared,
  verifyAttachedShared,
  seedSharedFromCompletedRun,
} from './shared-preparation.mjs';
import { startUsageAttempt } from './usage.mjs';
import {
  codexFailureFromEvent,
  classificationFailureText,
} from './codex-errors.mjs';

import {
  mockClassify,
  validateColorClassification,
} from './color-classification.mjs';
import {
  readJson,
  readStatus,
  transitionStatus,
  writeJsonAtomic,
} from './state.mjs';

const serverDir = path.dirname(fileURLToPath(import.meta.url));
const projectRoot = path.dirname(serverDir);
const schemaPath = path.join(
  projectRoot,
  'contracts',
  'codex-result.schema.json',
);
const classificationSchemaPath = path.join(
  projectRoot,
  'contracts',
  'color-classification.schema.json',
);
const progressCliPath = path.join(serverDir, 'progress-cli.mjs');
const runsRoot = path.resolve(
  process.env.ZIBUYU_RUNS_ROOT ||
    path.join(os.homedir(), '.codex', 'zibuyu-runs'),
);
const workspaceRoot = path.resolve(
  process.env.ZIBUYU_CODEX_WORKSPACE || path.dirname(projectRoot),
);
const executorMode = process.env.ZIBUYU_EXECUTOR_MODE || 'real';

export const CODEX_EXECUTION_POLICY = Object.freeze({
  model: 'gpt-5.5',
  reasoningEffort: 'high',
  conversationPolicy: 'new-session-per-production-batch',
});

const preparationQueue = createWorkQueue({ concurrency: 3 });
const paidQueue = createWorkQueue({ concurrency: 1 });
const sharedQueue = createWorkQueue({ concurrency: 1 });
const runQueue = createWorkQueue({ concurrency: 4 });
const preparationJobs = new Map();

const UUID_PATTERN =
  /^[0-9a-f]{8}-[0-9a-f]{4}-[1-8][0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$/i;

function sleep(milliseconds) {
  return new Promise((resolve) => setTimeout(resolve, milliseconds));
}

async function exists(filePath) {
  try {
    await access(filePath);
    return true;
  } catch {
    return false;
  }
}

function versionParts(name) {
  return (name.match(/\d+(?:\.\d+)*/) ?? ['0'])[0]
    .split('.')
    .map((part) => Number(part));
}

function compareVersionsDescending(left, right) {
  const a = versionParts(left);
  const b = versionParts(right);
  const length = Math.max(a.length, b.length);
  for (let index = 0; index < length; index += 1) {
    const difference = (b[index] ?? 0) - (a[index] ?? 0);
    if (difference) return difference;
  }
  return 0;
}

export async function discoverCodexCli() {
  if (
    process.env.CODEX_CLI_PATH &&
    (await exists(process.env.CODEX_CLI_PATH))
  ) {
    return process.env.CODEX_CLI_PATH;
  }

  const base = path.join(
    process.env.LOCALAPPDATA || path.join(os.homedir(), 'AppData', 'Local'),
    'OpenAI',
    'Codex',
  );
  const entries = await readdir(base, { withFileTypes: true }).catch(() => []);
  const versions = entries
    .filter(
      (entry) => entry.isDirectory() && entry.name.startsWith('npm-stable-'),
    )
    .map((entry) => entry.name)
    .sort(compareVersionsDescending);

  for (const version of versions) {
    const root = path.join(base, version);
    const candidates = [
      path.join(root, 'node_modules', '.bin', 'codex.ps1'),
      path.join(root, 'codex.ps1'),
    ];
    for (const candidate of candidates) {
      if (await exists(candidate)) return candidate;
    }
  }
  throw new Error('未找到本机 Codex CLI，请先在 Codex 桌面端完成登录');
}

function findSessionId(value, keyHint = '') {
  if (typeof value === 'string') {
    if (UUID_PATTERN.test(value) && /(thread|session)/i.test(keyHint))
      return value;
    return null;
  }
  if (!value || typeof value !== 'object') return null;
  for (const [key, nested] of Object.entries(value)) {
    const match = findSessionId(nested, key);
    if (match) return match;
  }
  return null;
}

function cleanChildEnvironment() {
  const env = { ...process.env };
  delete env.CODEX_THREAD_ID;
  delete env.CODEX_SESSION_ID;
  delete env.CODEX_APP_TOOLS_PIPE_PATH;
  delete env.CODEX_PERMISSION_PROFILE;
  delete env.CODEX_SHELL;
  return env;
}

function processInvocation(cliPath, codexArgs) {
  if (path.extname(cliPath).toLowerCase() === '.exe') {
    return { command: cliPath, args: codexArgs };
  }
  return {
    command: 'powershell.exe',
    args: [
      '-NoLogo',
      '-NoProfile',
      '-NonInteractive',
      '-ExecutionPolicy',
      'Bypass',
      '-File',
      cliPath,
      ...codexArgs,
    ],
  };
}

function progressCommand(runDir, stageKey, task) {
  const quote = (value) => `"${String(value).replaceAll('"', '\\"')}"`;
  return `${quote(process.execPath)} ${quote(progressCliPath)} --run-dir ${quote(runDir)} --stage ${stageKey} --state running --task ${quote(task)}`;
}

export function preparationPrompt(runDir, intake, shared = null) {
  const commands = [
    ['intake_validation', '校验用户资料、市场、语言和商品身份'],
    ['product_analysis', '分析商品属性与可验证卖点'],
    ['three_view_generation', '生成每个颜色的白底无人体三视图'],
    ['three_view_quality', '核对颜色、轮廓和结构细节'],
    ['script_and_voice', '生成转化导向的导演包、口播与中文对照'],
    ['preflight_validation', '核对固定模特、市场、引用素材和待提交参数'],
  ].filter(
    ([stage]) =>
      !shared || ['script_and_voice', 'preflight_validation'].includes(stage),
  );
  const progressInstructions = commands
    .map(
      ([stage, task], index) =>
        `${index + 1}. 进入 ${stage} 前先执行：${progressCommand(runDir, stage, task)}`,
    )
    .join('\n');

  return `
你正在执行一个由 Zibuyu 本地制作台提交的真实生产任务。

必须遵循已安装的 skill：zibuyu-top-tiktok-operations-specialist:zibuyu-top-tiktok-operations-specialist，并在需要时使用 clothing-three-view 与 seedance-ugc-cn-director 子 skill。

单据号：${intake.runId}
货号：${intake.sku}
任务目录：${runDir}
唯一输入真值：${path.join(runDir, 'intake.json')}

会话隔离：这是单据 ${intake.runId} 独占的新 Codex 会话。不得引用、猜测或继承其他制作单的上下文。
${shared ? `公共准备已经完成。先读取 ${path.join(runDir, 'shared-input', 'manifest.json')} 及其中每份产物，这是本批经过哈希校验的明确数据交接，允许复用而非继承其他会话上下文。商品事实、同市场来源/评论边界、每色三视图和实际图片QA只读取已有结果；不得重复访问来源做同一研究，不得重新生成三视图，也不得修改 shared-input 文件。缺失/不一致必须 blocked。保留未验证/partial/blocked的证据限制，不能升级为已验证。只为 intake.model 制作差异化场景、动作、买家切入点、口播、中文对照与独立编译/模特绑定/生成前校验。相同商品实物事实可复用，不得复制其他模特的batch-compile、timeline、文案或preflight回执。保持每色三视图原始字节；需要插件布局时复制已有图片与审计，保留来源哈希。不要使用任何 _fixture 函数或测试样例制造真实研究、视觉QA、时间戳或收据。` : ''}
${intake.modelBatchId ? `本单属于多模特批次 ${intake.modelBatchId}，只制作本 intake.model 指定的 ${intake.model.preset ?? intake.model.name}；市场 ${intake.model.marketCode}，口播 ${intake.model.locale}，计划 ${intake.variants.length} 条视频。每个颜色为本模特独立编译和质检，不得生成同组其他模特，也不得将其他市场的来源/评论视为本市场证据。批次编号仅用于网站归组，不是插件 streaming batch-plan。` : ''}

执行边界：
- 这一轮只执行准备阶段：资料校验、商品分析、三视图、质检、导演包/口播和生成前校验。
- 绝对不得调用 PopBoom 提交、generate_video、扣费、发布、上传或其他外部付费动作。
- 模特照不得当作服装参考图；商品身份、颜色、市场和语言必须锁定。
- 无法读取的 Amazon 或评论证据要明确标记为未验证，不得编造。
- 所有产物写入任务目录内，保留可追溯的中间件和质检结果。
- 必须保持 intake.variants[].id 作为编译产物中的 variant_id，不可另起颜色标识；每个颜色只对应本 intake.model 指定的一位模特。
- 输出文案时遵循当前插件的标签规则：每个颜色的文案与恰好 5 个不重复标签合在同一段，并服从 intake.json 中的单次覆盖要求。

进度回写：每进入下一个流程前，先执行对应命令，成功后再做该阶段工作。
${progressInstructions}

完成准备阶段后，立即停止。最终只返回符合结构化 schema 的 JSON。正常成功时 outcome 必须为 awaiting_paid_approval，stageKey 必须为 paid_approval；不要在最终 JSON 之外加 Markdown。
`.trim();
}

function paidPrompt(runDir, intake, approval) {
  return `
继续单据 ${intake.runId} 的 Zibuyu 生产任务。这是同一个 Codex 会话的付费执行阶段。

先读取：
- ${path.join(runDir, 'intake.json')}
- ${path.join(runDir, 'approval.json')}
- ${path.join(runDir, 'web', 'final-prepare.json')}

用户在本地网页上明确授权的范围仅为：
- 动作：${approval.scope}
- 颜色 ID：${approval.variantIds.join(', ')}
- 本次模特：${intake.model.preset ?? intake.model.name}；${intake.model.marketCode} / ${intake.model.locale}；本次最多 ${approval.variantIds.length} 条。授权不包含同批其他模特。
- 授权指纹：${approval.authorizationFingerprint}
- 授权时间：${approval.approvedAt}

必须遵循已安装的 zibuyu-top-tiktok-operations-specialist:popboom skill 以及主工作流。

强制规则：
- 不得超出 approval.json 的颜色和动作范围。
- 在提交前完成身份、市场、语言、固定模特、引用图和参数校验。
- 进入 PopBoom 生成前执行：${progressCommand(runDir, 'popboom_generation', '提交并监控已授权的 PopBoom 任务')}
- 进入质检与交付前执行：${progressCommand(runDir, 'quality_and_delivery', '下载、核验并整理最终交付')}
- 每个已接受的提交都要先持久化任务 ID 和证据，不得重复提交。
- 如果提交时超时、连接断开或无法确认是否已接受，必须返回 submission_unknown，不得自动重试。
- 平台显示完成不等于交付通过；必须下载并核验商品一致性、媒体参数和文件哈希。
- 本阶段止于整批成片、真实 QA、完整文案与五标签交付；不得查询发布账号/商品、准备排期或发布。网页随后显示交付，再独立执行发布交接；制作授权不包含发布。
- 必须完成主插件的生产台账与交付包字段，保留 build_publish_handoff.py 可校验的真实证据；不能为通过校验编造 completion_reported。

最终只返回符合 schema 的 JSON。全部验收通过时 outcome 为 delivered，stageKey 为 quality_and_delivery；不要在 JSON 之外加 Markdown。
`.trim();
}

function classificationPrompt(batchDir, request) {
  const imageIndex = request.images
    .map(
      (image, index) =>
        `${index + 1}. ${image.id} | ${image.originalName} | ${path.join(batchDir, image.relativePath)}`,
    )
    .join('\n');

  return `
你正在执行 Zibuyu 本地制作台的“商品图颜色自动归类”任务。这是正式制作前的只读视觉识别步骤。

批次号：${request.batchId}
货号提示：${request.sku || '未填写'}
请求文件：${path.join(batchDir, 'request.json')}

图片按下列顺序作为 --image 附件传入；必须使用对应 imageId 返回结果：
${imageIndex}

归类标准：
- 只观察用户要售卖的服装主体颜色，忽略背景、模特肤色、模特自身其他衣物、道具、光线、姿势和文件顺序。
- 同一件商品的前面、侧面、背面、细节图，只要服装商业颜色相同，就归入同一组。
- 不要把接近但不同的商业颜色强行合并，例如 Black/Navy Blue、White/Cream、Beige/Khaki、Red/Burgundy。
- colorName 使用简洁的英文商业颜色名；colorNameZh 给出对应中文名。
- confidence 只能是 high、medium 或 low。遮挡、偏色、滤镜或无法确认时使用 low，并在 reason 中说明。
- 每张图片必须且只能出现一次：要么放进一个 groups[].imageIds，要么放进 unassigned。
- 如果无法可靠识别服装主体或颜色，放入 unassigned，不得猜测。

安全边界：
- 本任务只能做视觉归类，不得生成三视图、脚本、视频或其他素材。
- 不得调用 PopBoom、不得提交付费任务、不得上传到外部平台、不得发布，也不得修改请求图片。
- 最终只返回符合结构化 schema 的 JSON，不要附加 Markdown 或解释。
`.trim();
}

export function buildCodexArguments({
  phase,
  imagePaths = [],
  sessionId = null,
  outputSchemaPath = schemaPath,
  outputPath,
}) {
  const resumesBatchSession = phase === 'paid' || phase === 'prepare-resume';
  if (resumesBatchSession && !sessionId) {
    throw new Error('续跑阶段缺少该批次的 Codex 会话 ID');
  }
  if (!resumesBatchSession && sessionId) {
    throw new Error('新批次禁止复用已有 Codex 会话');
  }

  const args = [
    'exec',
    '--model',
    CODEX_EXECUTION_POLICY.model,
    '--config',
    `model_reasoning_effort="${CODEX_EXECUTION_POLICY.reasoningEffort}"`,
    '--color',
    'never',
    '-C',
    workspaceRoot,
    '--add-dir',
    runsRoot,
  ];
  // --approve-for-me already selects workspace-write; the CLI rejects both.
  if (!['paid', 'publish-submit'].includes(phase))
    args.push('--sandbox', 'workspace-write');
  if (resumesBatchSession) {
    if (phase === 'paid') args.push('--approve-for-me');
    args.push('resume', '--all');
  }
  if (phase === 'publish-submit') args.push('--approve-for-me');
  args.push(
    '--skip-git-repo-check',
    '--json',
    '--output-schema',
    outputSchemaPath,
    '--output-last-message',
    outputPath,
  );
  for (const imagePath of imagePaths) args.push('--image', imagePath);
  if (resumesBatchSession) args.push(sessionId);
  args.push('-');
  return args;
}

export async function runCodexProcess({
  runDir,
  phase,
  prompt,
  imagePaths,
  sessionId,
  outputSchemaPath = schemaPath,
  processSpawner = spawn,
}) {
  const cliPath = await discoverCodexCli();
  const codexDir = phase.startsWith('publish-')
    ? path.join(runDir, 'publishing', 'web')
    : path.join(runDir, 'web');
  await mkdir(codexDir, { recursive: true });
  const attemptId = randomUUID();
  const outputPath = attemptOutputPath(codexDir, phase, attemptId);
  await mkdir(path.dirname(outputPath), { recursive: true });
  const eventsPath = path.join(codexDir, `codex-events-${phase}.jsonl`);
  const stderrPath = path.join(codexDir, `codex-${phase}.stderr.log`);

  const args = buildCodexArguments({
    phase,
    imagePaths,
    sessionId,
    outputSchemaPath,
    outputPath,
  });

  const invocation = processInvocation(cliPath, args);
  const usage = await startUsageAttempt(runDir, phase, CODEX_EXECUTION_POLICY);
  const eventsStream = createWriteStream(eventsPath, { flags: 'a' });
  const stderrStream = createWriteStream(stderrPath, { flags: 'a' });

  return new Promise((resolve, reject) => {
    let discoveredSessionId = sessionId ?? null;
    let persistedSessionId = sessionId ?? null;
    let spawned = false;
    let spawnError = null;
    let failure = null;
    let writes = Promise.resolve();
    let lastRuntimeWrite = 0;
    const runtime = {
      attemptId,
      phase,
      source: 'exec',
      state: 'starting',
      pid: null,
      startedAt: new Date().toISOString(),
      lastEventAt: null,
      currentTask: null,
    };
    const persist = (file, value) => {
      const snapshot = structuredClone(value);
      writes = writes.then(() =>
        writeJsonAtomic(path.join(codexDir, file), snapshot),
      );
      // Keep failures observable on close without an unhandled rejection.
      writes.catch(() => {});
    };
    const child = processSpawner(invocation.command, invocation.args, {
      cwd: workspaceRoot,
      env: cleanChildEnvironment(),
      windowsHide: true,
      stdio: ['pipe', 'pipe', 'pipe'],
    });

    child.once('spawn', () => {
      spawned = true;
      Object.assign(runtime, { state: 'running', pid: child.pid ?? null });
      persist('runtime.json', runtime);
      child.stdin.end(prompt, 'utf8');
    });
    child.once('error', (error) => {
      spawnError = error;
      error.processStarted = spawned;
    });

    child.stderr.pipe(stderrStream);
    const lines = readline.createInterface({ input: child.stdout });
    lines.on('line', (line) => {
      let event;
      try { event = JSON.parse(line); } catch { /* Preserve non-JSON output below. */ }
      const receivedAt = new Date().toISOString();
      eventsStream.write(`${event && phase.startsWith('publish-') ? JSON.stringify(archivePublishEvent(event, phase, attemptId, receivedAt)) : line}\n`);
      try {
        if (!event) return;
        runtime.lastEventAt = receivedAt;
        if (event.item?.type === 'agent_message' && event.item.text) {
          let message = event.item.text;
          try {
            const structured = JSON.parse(message);
            message = structured.currentTask || structured.summary || message;
          } catch {
            /* Plain commentary. */
          }
          runtime.currentTask = String(message).slice(0, 1200);
        }
        if (
          Date.now() - lastRuntimeWrite > 1500 ||
          event.type === 'thread.started'
        ) {
          lastRuntimeWrite = Date.now();
          persist('runtime.json', runtime);
        }
        usage.observe(event);
        failure = codexFailureFromEvent(event) ?? failure;
        const found = findSessionId(event);
        if (found) discoveredSessionId = found;
        if (found && found !== persistedSessionId) {
          persistedSessionId = found;
          persist('session.json', {
            sessionId: found,
            model: CODEX_EXECUTION_POLICY.model,
            reasoningEffort: CODEX_EXECUTION_POLICY.reasoningEffort,
            conversationPolicy: CODEX_EXECUTION_POLICY.conversationPolicy,
            sessionMode: sessionId ? 'resumed' : 'new',
            updatedAt: new Date().toISOString(),
          });
        }
      } catch {
        // Raw output is preserved even when a line is not a JSON event.
      }
    });

    child.once('close', async (code, signal) => {
      try {
        lines.close();
        await Promise.all([
          new Promise((done) => eventsStream.end(done)),
          new Promise((done) => stderrStream.end(done)),
        ]);
        Object.assign(runtime, {
          state: code === 0 ? 'completed' : 'failed',
          exitCode: code ?? -1,
          finishedAt: new Date().toISOString(),
          sessionId: discoveredSessionId,
        });
        persist('runtime.json', runtime);
        await writes;
        const usageResult = await usage.finish({
          exitCode: code ?? -1,
          signal,
          sessionId: discoveredSessionId,
          processStarted: spawned,
        });
        if (spawnError) {
          Object.assign(spawnError, usageResult);
          reject(spawnError);
          return;
        }
        resolve({
          attemptId,
          exitCode: code ?? -1,
          signal,
          sessionId: discoveredSessionId,
          outputPath,
          processStarted: spawned,
          failure,
          ...usageResult,
        });
      } catch (error) {
        error.processStarted = spawned;
        reject(error);
      }
    });
  });
}

async function updateClassificationStatus(batchDir, patch) {
  const request = await readJson(path.join(batchDir, 'request.json'), {});
  const current = await readJson(path.join(batchDir, 'status.json'), {});
  const now = new Date().toISOString();
  const terminal = ['completed', 'failed'].includes(patch.state);
  const status = {
    ...current,
    ...patch,
    batchId: request.batchId ?? current.batchId,
    imageCount: request.images?.length ?? current.imageCount ?? 0,
    startedAt: current.startedAt ?? (patch.state === 'running' ? now : null),
    updatedAt: now,
    completedAt: terminal ? now : (current.completedAt ?? null),
    error: patch.error ?? (patch.state === 'failed' ? current.error : null),
  };
  await writeJsonAtomic(path.join(batchDir, 'status.json'), status);
  return status;
}

async function executeColorClassification(batchDir) {
  const request = await readJson(path.join(batchDir, 'request.json'));
  if (!request) throw new Error('颜色识别请求不存在');

  await updateClassificationStatus(batchDir, {
    state: 'running',
    currentTask: 'Codex 正在识别服装颜色',
    note: `正在分析 ${request.images.length} 张商品图`,
  });

  if (executorMode === 'mock') {
    await sleep(150);
    const result = mockClassify(request);
    await writeJsonAtomic(path.join(batchDir, 'result.json'), result);
    await updateClassificationStatus(batchDir, {
      state: 'completed',
      currentTask: '颜色归类完成',
      note: `识别出 ${result.groups.length} 个颜色，${result.unassigned.length} 张待确认`,
    });
    return;
  }

  let execution;
  try {
    execution = await runCodexProcess({
      runDir: batchDir,
      phase: 'classify',
      prompt: classificationPrompt(batchDir, request),
      imagePaths: request.images.map((image) =>
        path.join(batchDir, image.relativePath),
      ),
      sessionId: null,
      outputSchemaPath: classificationSchemaPath,
    });
  } catch (error) {
    await updateClassificationStatus(batchDir, {
      state: 'failed',
      currentTask: '颜色识别启动失败',
      note: error.message,
      error: error.message,
    });
    return;
  }

  if (execution.exitCode !== 0) {
    const message = classificationFailureText(execution);
    await updateClassificationStatus(batchDir, {
      state: 'failed',
      currentTask: '颜色识别中断',
      note: message,
      error: message,
    });
    return;
  }

  try {
    const rawResult = JSON.parse(await readFile(execution.outputPath, 'utf8'));
    const result = validateColorClassification(request, rawResult);
    await writeJsonAtomic(path.join(batchDir, 'result.json'), result);
    await updateClassificationStatus(batchDir, {
      state: 'completed',
      currentTask: '颜色归类完成',
      note: `识别出 ${result.groups.length} 个颜色，${result.unassigned.length} 张待确认`,
    });
  } catch (error) {
    await updateClassificationStatus(batchDir, {
      state: 'failed',
      currentTask: '颜色识别结果无效',
      note: error.message,
      error: error.message,
    });
  }
}

async function applyStructuredResult(runDir, phase, result, sessionId) {
  const normalized = {
    ...result,
    attemptId:
      (await readJson(path.join(runDir, 'web/runtime.json')))?.attemptId ??
      null,
    runId: (await readJson(path.join(runDir, 'intake.json'))).runId,
  };
  if (
    phase === 'prepare' &&
    normalized.outcome === 'awaiting_paid_approval' &&
    executorMode !== 'mock'
  ) {
    try {
      await validatePreparationCompiles(runDir, normalized);
    } catch (error) {
      normalized.outcome = 'blocked';
      normalized.stageKey = 'preflight_validation';
      normalized.currentTask = '正式编译校验未通过，需要修复准备包';
      normalized.summary = error.message;
      normalized.nextAction = '修复正式编译包并重新运行当前插件校验';
    }
  }
  await writeJsonAtomic(
    path.join(runDir, 'web', `result-${phase}.json`),
    normalized,
  );

  const stateByOutcome = {
    awaiting_paid_approval: 'awaiting_paid_approval',
    needs_input: 'needs_input',
    blocked: 'blocked',
    needs_review: 'needs_review',
    failed: 'failed',
    delivered: 'delivered',
    submission_unknown: 'submission_unknown',
  };
  return transitionStatus(runDir, {
    stageKey: normalized.stageKey,
    state: stateByOutcome[normalized.outcome] ?? 'failed',
    currentTask: normalized.currentTask,
    note: normalized.summary,
    sessionId,
    forceProgress: normalized.outcome === 'delivered' ? 100 : undefined,
  });
}

async function runMockPhase(runDir, phase, shared = null) {
  const intake = await readJson(path.join(runDir, 'intake.json'));
  const stageKeys =
    phase === 'prepare'
      ? [
          'intake_validation',
          'product_analysis',
          'three_view_generation',
          'three_view_quality',
          'script_and_voice',
          'preflight_validation',
        ]
      : ['popboom_generation', 'quality_and_delivery'];
  for (const stageKey of stageKeys) {
    if (
      shared &&
      ![
        'script_and_voice',
        'preflight_validation',
        'popboom_generation',
        'quality_and_delivery',
      ].includes(stageKey)
    )
      continue;
    await transitionStatus(runDir, {
      stageKey,
      state: 'running',
      currentTask: `模拟执行：${stageKey}`,
      note: '本地验收模式，未调用外部生成服务',
      sessionId: `mock-${intake.runId}`,
    });
    await sleep(120);
  }
  const result = {
    runId: intake.runId,
    phase,
    outcome: phase === 'prepare' ? 'awaiting_paid_approval' : 'delivered',
    stageKey: phase === 'prepare' ? 'paid_approval' : 'quality_and_delivery',
    currentTask: phase === 'prepare' ? '等待付费确认' : '模拟交付完成',
    summary: '本地验收模式已完成，没有调用真实生成服务。',
    nextAction: phase === 'prepare' ? '在网页上审核后确认' : '查看交付物',
    missingInputs: [],
    artifacts: [],
  };
  return applyStructuredResult(runDir, phase, result, `mock-${intake.runId}`);
}

export async function verifyPaidAuthorization(runDir, approval) {
  const intake = await readJson(path.join(runDir, 'intake.json'));
  const prepareResult = await readJson(
    path.join(runDir, 'web', 'result-prepare.json'),
  );
  if (
    !approval ||
    approval.runId !== intake.runId ||
    !approval.variantIds?.length ||
    approval.variantIds.some(
      (id) => !intake.variants.some((variant) => variant.id === id),
    )
  )
    throw new Error('付费授权范围不匹配');
  if (
    authorizationFingerprint({
      intake,
      prepareResult,
      variantIds: approval.variantIds,
      artifactManifest: approval.artifactManifest,
    }) !== approval.authorizationFingerprint
  )
    throw new Error('付费授权后资料已变化，请重新核对');
  for (const file of approval.artifactManifest) {
    if (file.external) continue;
    const absolute = path.resolve(runDir, file.path);
    if (!absolute.startsWith(path.resolve(runDir) + path.sep))
      throw new Error('授权产物路径越界');
    const bytes = await readFile(absolute);
    if (
      bytes.length !== file.size ||
      createHash('sha256').update(bytes).digest('hex') !== file.sha256
    )
      throw new Error('授权产物已变化，已阻止提交');
  }
  await verifyAttachedShared(runDir);
}

async function executePhase(runDir, phase, options = {}) {
  const intake = await readJson(path.join(runDir, 'intake.json'));
  const status = await readStatus(runDir);
  const sessionRecord = await readJson(
    path.join(runDir, 'web', 'session.json'),
  );
  const approval =
    phase === 'paid'
      ? await readJson(path.join(runDir, 'approval.json'))
      : null;

  if (phase === 'paid' && !approval) {
    throw new Error('付费阶段缺少 approval.json');
  }
  if (phase === 'paid') await verifyPaidAuthorization(runDir, approval);
  const recovery =
    phase === 'paid' && options.recoveryId
      ? await verifyAuditedPaidRecovery(runDir, options.recoveryId, approval)
      : null;
  const compileValidation =
    phase === 'paid' && executorMode !== 'mock'
      ? await validatePreparationCompiles(
          runDir,
          await readJson(path.join(runDir, 'web/result-prepare.json')),
          { intake, variantIds: approval.variantIds },
        )
      : null;
  const shared =
    phase === 'prepare' ? await verifyAttachedShared(runDir) : null;

  await transitionStatus(runDir, {
    stageKey:
      phase === 'prepare'
        ? shared
          ? 'script_and_voice'
          : 'intake_validation'
        : 'popboom_generation',
    state: phase === 'prepare' ? 'running' : 'submitting',
    currentTask:
      phase === 'prepare'
        ? shared
          ? '并行准备本模特脚本与口播'
          : '启动 Codex 资料校验'
        : '启动已授权的 PopBoom 提交',
    note: phase === 'prepare' ? '正在创建本地 Codex 会话' : '已记录付费授权',
  });

  if (phase === 'paid') {
    // Validation may take time; another valid package must not replace the
    // authorized bytes while Python is running.
    await verifyPaidAuthorization(runDir, approval);
    if (recovery) {
      await verifyAuditedPaidRecovery(runDir, options.recoveryId, approval);
      await mkdir(path.dirname(recovery.redemptionPath), { recursive: true });
      const redemption = await open(recovery.redemptionPath, 'wx');
      try {
        await redemption.writeFile(
          JSON.stringify({
            recoveryId: options.recoveryId,
            claimedAt: new Date().toISOString(),
          }),
        );
      } finally {
        await redemption.close();
      }
    }
    const claimPath =
      recovery?.claimPath || path.join(runDir, 'web', 'paid-dispatch.json');
    await mkdir(path.dirname(claimPath), { recursive: true });
    const claim = await open(claimPath, 'wx');
    try {
      await claim.writeFile(
        JSON.stringify({
          runId: intake.runId,
          authorizationFingerprint: approval.authorizationFingerprint,
          ...(recovery
            ? {
                recoveryId: options.recoveryId,
                previousAuthorizationFingerprint:
                  recovery.request.previousAuthorizationFingerprint,
              }
            : {}),
          claimedAt: new Date().toISOString(),
        }),
      );
    } finally {
      await claim.close();
    }
    if (recovery)
      await writeJsonAtomic(path.join(runDir, 'web/paid-recovery.json'), {
        ...recovery.request,
        consumedAt: new Date().toISOString(),
      });
  }
  if (executorMode === 'mock') return runMockPhase(runDir, phase, shared);

  const imagePaths = intake.variants.flatMap((variant) =>
    variant.images.map((image) => path.join(runDir, image.relativePath)),
  );
  const prompt =
    phase === 'prepare'
      ? preparationPrompt(runDir, intake, shared) +
        (options.resume
          ? '\n这是本单准备阶段恢复，继续现有产物和会话；不得重复生成已有图片，先核对明确交接的 shared-input；本轮仍不得上传、付费生成或发布。'
          : '')
      : paidPrompt(runDir, intake, approval) +
        (compileValidation
          ? `\n服务器已独立复验本次正式编译包，以下是唯一允许用于本次提交的 compile 路径：\n${compileValidation.compilePaths.join('\n')}\n只消费这些正式编译包中的授权颜色。其他旧根目录、旧release、history或recovery/original下的包均为历史证据，不得替换或回退。服务器完整校验记录：${compileValidation.receiptPath}`
          : '') +
        (recovery
          ? `\n本轮是用户明确要求“${recovery.request.userInstruction}”后的首次视频提交续跑。旧paid调用已独立审核为本地编译校验失败，没有PopBoom上传或生成；旧approval/paid-dispatch/失败日志完整保留，新授权指纹已绑定修复后的正式包。只按上面列出的新compile和当前approval继续，不能因旧历史根compile错误而回退或重建共享素材。每色单独保留其正式compile、ledger、实际视频QA与完整文案交付。根目录与旧release是失败历史；inspect_run对旧根的结果不能替代本次列明包。若出现任何已接受record_id则该颜色只查询/下载/验收，严禁再提交。本任务只制作并交付，不发布。`
          : '');

  let execution;
  try {
    execution = await runCodexProcess({
      runDir,
      phase: options.resume ? 'prepare-resume' : phase,
      prompt,
      imagePaths: phase === 'prepare' && !shared ? imagePaths : [],
      sessionId:
        phase === 'paid' || options.resume
          ? (sessionRecord?.sessionId ?? status?.sessionId)
          : null,
    });
  } catch (error) {
    const uncertain = phase === 'paid' && error.processStarted;
    if (uncertain && (await reconcilePaidRun(runDir))) return;
    await transitionStatus(runDir, {
      stageKey:
        phase === 'prepare' ? 'intake_validation' : 'popboom_generation',
      state: uncertain ? 'submission_unknown' : 'failed',
      currentTask: uncertain ? '需人工核对 PopBoom 提交状态' : 'Codex 启动失败',
      note: error.message,
      error: error.message,
    });
    return;
  }

  if (execution.exitCode !== 0) {
    if (phase === 'paid' && (await reconcilePaidRun(runDir))) return;
    const state = phase === 'paid' ? 'submission_unknown' : 'failed';
    await transitionStatus(runDir, {
      stageKey:
        phase === 'paid'
          ? 'popboom_generation'
          : (await readStatus(runDir))?.stageKey,
      state,
      currentTask:
        phase === 'paid' ? '需人工核对 PopBoom 提交状态' : 'Codex 执行中断',
      note: `Codex 退出码 ${execution.exitCode}${execution.signal ? `，信号 ${execution.signal}` : ''}`,
      sessionId: execution.sessionId,
      error: `codex_exit_${execution.exitCode}`,
    });
    return;
  }

  let result;
  try {
    result = JSON.parse(await readFile(execution.outputPath, 'utf8'));
    if (shared) {
      // Bind copied common assets into the same review/authorization manifest.
      await verifyAttachedShared(runDir);
      const artifacts = [
        {
          kind: 'json',
          label: '公共准备来源与校验',
          path: path.join(runDir, 'shared-input', 'manifest.json'),
        },
        ...shared.files.map((file) => ({
          kind: file.kind === 'image' ? 'image' : 'json',
          label: `共享${file.variantId || file.marketCode || '商品事实'} · ${file.kind}`,
          path: path.join(runDir, file.path),
        })),
      ];
      const seen = new Set(
        result.artifacts.map((item) => path.resolve(runDir, item.path)),
      );
      result.artifacts.push(
        ...artifacts.filter((item) => !seen.has(path.resolve(item.path))),
      );
    }
    if (phase === 'prepare')
      await writeJsonAtomic(
        path.join(runDir, 'web', 'final-prepare.json'),
        result,
      );
  } catch (error) {
    if (phase === 'paid' && (await reconcilePaidRun(runDir))) return;
    await transitionStatus(runDir, {
      stageKey:
        phase === 'paid'
          ? 'popboom_generation'
          : (await readStatus(runDir))?.stageKey,
      state: phase === 'paid' ? 'submission_unknown' : 'failed',
      currentTask: phase === 'paid' ? '需人工核对提交状态' : '结果解析失败',
      note: `Codex 未返回有效结构化结果：${error.message}`,
      sessionId: execution.sessionId,
      error: error.message,
    });
    return;
  }

  if (
    phase === 'paid' &&
    ['blocked', 'failed', 'submission_unknown'].includes(result.outcome) &&
    (await reconcilePaidRun(runDir))
  )
    return;
  await applyStructuredResult(runDir, phase, result, execution.sessionId);
}

export function enqueueCodexPhase(runDir, phase, options = {}) {
  const queue = phase === 'paid' ? paidQueue : preparationQueue;
  return queue.enqueue(
    `${runDir}:${phase}`,
    () =>
      runQueue.enqueue(
        `${runDir}:${phase}`,
        async () => {
          try {
            const current = await readStatus(runDir);
            if (
              phase === 'prepare' &&
              ['awaiting_paid_approval', 'delivered'].includes(current?.state)
            )
              return;
            const runtime = current?.execution;
            if (runtime?.state === 'running' && isProcessAlive(runtime.pid))
              throw new Error('本单已有 Codex 进程运行，已阻止重复启动');
            if (phase === 'prepare')
              await writeJsonAtomic(
                path.join(runDir, 'web', 'preparation.json'),
                {
                  ...current?.preparation,
                  state: 'running',
                  mode: current?.preparation?.mode ?? 'independent',
                },
              );
            await executePhase(runDir, phase, options);
            if (phase === 'prepare') {
              const latest = await readStatus(runDir);
              await writeJsonAtomic(
                path.join(runDir, 'web', 'preparation.json'),
                {
                  ...latest?.preparation,
                  state: ['awaiting_paid_approval', 'delivered'].includes(
                    latest?.state,
                  )
                    ? 'completed'
                    : latest?.state,
                },
              );
            }
          } catch (error) {
            await transitionStatus(runDir, {
              state: 'blocked',
              currentTask: '提交前检查未通过',
              note: error.message,
              error: error.message,
            });
            throw error;
          }
        },
        runDir,
      ),
    runDir,
  );
}

export function isProcessAlive(pid) {
  if (!Number.isInteger(pid) || pid <= 0) return false;
  try {
    process.kill(pid, 0);
    return true;
  } catch (error) {
    return error.code !== 'ESRCH';
  }
}

export async function hasPaidDispatchEvidence(runDir) {
  for (const file of [
    'paid-dispatch.json',
    'codex-events-paid.jsonl',
    'codex-paid.stderr.log',
    'final-paid.json',
    'result-paid.json',
  ]) {
    if (await exists(path.join(runDir, 'web', file))) return true;
  }
  const entries = await readdir(path.join(runDir, 'web', 'usage')).catch(
    () => [],
  );
  for (const file of entries)
    if (file.endsWith('.json')) {
      const attempt = await readJson(path.join(runDir, 'web', 'usage', file));
      if (attempt?.phase !== 'paid') continue;
      if (
        attempt.exitCode === 2 &&
        attempt.completedTurns === 0 &&
        attempt.localRejectionEvidence
      ) {
        const folder = path.resolve(runDir, attempt.localRejectionEvidence);
        const allowed =
          path.join(path.resolve(runDir), 'web', 'history') + path.sep;
        if (folder.startsWith(allowed)) {
          const [stderr, events] = await Promise.all([
            readFile(path.join(folder, 'codex-paid.stderr.log'), 'utf8').catch(
              () => '',
            ),
            readFile(
              path.join(folder, 'codex-events-paid.jsonl'),
              'utf8',
            ).catch(() => null),
          ]);
          if (
            events !== null &&
            !events.trim() &&
            stderr.includes(
              "error: the argument '--sandbox <SANDBOX_MODE>' cannot be used with '--approve-for-me'",
            )
          )
            continue;
        }
      }
      return true;
    }
  return false;
}

export function enqueueColorClassification(batchDir) {
  return preparationQueue.enqueue(
    `${batchDir}:classification`,
    () => executeColorClassification(batchDir),
    batchDir,
  );
}

export function sharedPreparationPrompt(request) {
  return `这是子不语多模特批次的公共准备，批次 ${request.batchId}。只做一次商品分析和每色三视图，不制作任何模特脚本或视频。
任务目录：${request.sharedDir}
唯一输入：${path.join(request.sharedDir, 'request.json')}，输入指纹：${request.inputFingerprint}。
按已安装 clothing-three-view 与主插件的商品证据规则执行；读取必要规则即可，禁止用 _fixture 或测试数据生成真实事实、研究、视觉QA或收据。
先验证SKU、颜色和源图，再生成每色一张白底无人体身份的正侧背三视图，实际查看源图和生成图验收；严禁只凭提示词或文件存在宣称通过。
商品事实不包含任何模特身份/身材/账号，保留推断与不可验证项。各市场和来源单独研究一次；amazon.com不能冒充DE评论，无法读取必须partial/unverified，不得编造买家评论、成分或效果。
不得调用PopBoom提交、上传、付费视频生成或发布。文件全部写在任务目录内，用相对路径返回。
产物要求：
1. productFactsPath 指向JSON：{sku, facts:[], limitations:[]}，只包含实物事实与证据。
2. marketAnalyses 每个 request.markets 对应一个JSON，包含 marketCode, locale, sourceUrl, evidenceStatus(verified/partial/unverified), limitations:[], evidence及评论边界；保存必要引用证据供模特编译复用。
3. threeViews 每个颜色包含variantId、path图片和qaPath审计JSON。审计必须包含variantId, passed, auditedSha256(实际图片SHA256), sourceImageHashes(按request图片顺序), identityFree, evidence(实际对照观察记录), qc，以及 identityCueAudit 和 identityCueAuditSha256。qc必含 front_side_back_order/pure_white_background/same_sku_color_only/human_identity_pixels_absent 全true。
identityCueAudit使用插件zero_human_identity_pixels_v1完整结构：audit_version, audited_sha256, inspection_method=full_resolution_visual_inspection, reviewed_at, passed，以及skin_present/face_present/hair_present/neck_chest_collarbone_present/shoulders_arms_wrists_present/hands_fingers_nails_present/tattoos_jewelry_present/person_specific_body_shape_present八项明确false。identityCueAuditSha256必须是完整审计JSON按键排序、UTF8、紧凑分隔符的SHA256。只能基于实际查看记录这些结论。
进度每个阶段进入前更新：
${['intake_validation', 'product_analysis', 'three_view_generation', 'three_view_quality'].map((stage) => progressCommand(request.sharedDir, stage, '公共准备：' + stage)).join('\n')}
任何图片或QA缺失返回blocked，不让三个模特各自重新做。完成后只返回符合schema的JSON，outcome=ready，inputFingerprint保持输入原值。`;
}

async function ensureShared(request) {
  return sharedQueue.enqueue(request.sharedDir, async () => {
    if (await readVerifiedShared(request)) return;
    if (await seedSharedFromCompletedRun(request)) return;
    const runtime = await readJson(
      path.join(request.sharedDir, 'web', 'runtime.json'),
    );
    if (runtime) {
      // A prior attempt may have accepted image generation. Reconcile its saved
      // output rather than starting another producer automatically.
      const final = await readJson(
        attemptOutputPath(
          path.join(request.sharedDir, 'web'),
          'shared',
          runtime.attemptId,
        ),
      );
      if (runtime.state === 'completed' && runtime.exitCode === 0 && final) {
        await sealShared(request, final);
        return;
      }
      throw new Error(
        '公共准备已有执行记录，需要核对已生成产物；不会自动重复生成',
      );
    }
    await initializeShared(request);
    let result;
    if (executorMode === 'mock') {
      const { createMockSharedResult } =
        await import('./shared-preparation-mock.mjs');
      result = await createMockSharedResult(request);
    } else {
      const execution = await runCodexProcess({
        runDir: request.sharedDir,
        phase: 'shared',
        prompt: sharedPreparationPrompt(request),
        imagePaths: request.variants.flatMap((variant) =>
          variant.images.map((image) =>
            path.join(request.sharedDir, image.relativePath),
          ),
        ),
        outputSchemaPath: path.join(
          projectRoot,
          'contracts',
          'shared-result.schema.json',
        ),
      });
      if (execution.exitCode !== 0)
        throw new Error(
          execution.failure?.message || '公共准备执行未完成，现有产物已保留',
        );
      result = JSON.parse(await readFile(execution.outputPath, 'utf8'));
    }
    await sealShared(request, result);
  });
}

export function enqueuePreparationBatch(runs, options = {}) {
  const key = runs
    .map((run) => path.resolve(run.runDir))
    .sort()
    .join('|');
  if (preparationJobs.has(key)) return preparationJobs.get(key);
  const work = (async () => {
    const eligible = [];
    for (const run of runs) {
      const status = await readStatus(run.runDir);
      if (
        [
          'awaiting_paid_approval',
          'delivered',
          'submission_unknown',
          'submitting',
        ].includes(status?.state)
      )
        continue;
      if (await readJson(path.join(run.runDir, 'approval.json'))) continue;
      eligible.push(run);
    }
    if (!eligible.length) return;
    const request = await sharedRequestFor(eligible[0].runDir);
    if (request) {
      try {
        for (const run of eligible) {
          await writeJsonAtomic(
            path.join(run.runDir, 'web', 'preparation.json'),
            {
              mode: 'shared',
              state: 'waiting_shared',
              sharedDir: request.sharedDir,
              batchId: request.batchId,
            },
          );
          await transitionStatus(run.runDir, {
            stageKey: 'product_analysis',
            state: 'queued',
            currentTask: '等待本批公共商品分析与三视图',
            note: '公共素材验收后，最多三位模特并行准备',
            forceProgress: 0,
          });
        }
        await ensureShared(request);
        for (const run of eligible) {
          await attachShared(run.runDir, request);
          await writeJsonAtomic(
            path.join(run.runDir, 'web', 'preparation.json'),
            {
              mode: 'shared',
              state: 'ready',
              sharedDir: request.sharedDir,
              batchId: request.batchId,
              manifestHash: (await readVerifiedShared(request)).manifestHash,
            },
          );
          await transitionStatus(run.runDir, {
            stageKey: 'script_and_voice',
            state: 'queued',
            currentTask: '公共素材已就绪，等待模特准备槽位',
            note: '商品分析与每色三视图已复用',
          });
        }
      } catch (error) {
        for (const run of eligible) {
          await transitionStatus(run.runDir, {
            state: 'blocked',
            currentTask: '公共准备需要核对',
            note: error.message,
            error: error.message,
          });
          await writeJsonAtomic(
            path.join(run.runDir, 'web', 'preparation.json'),
            {
              mode: 'shared',
              state: 'blocked',
              sharedDir: request.sharedDir,
              batchId: request.batchId,
              error: error.message,
            },
          );
        }
        throw error;
      }
    }
    const results = await Promise.allSettled(
      eligible.map((run) =>
        enqueueCodexPhase(run.runDir, 'prepare', {
          resume: options.resumeRunIds?.includes(run.runId),
        }),
      ),
    );
    const failed = results.find((result) => result.status === 'rejected');
    if (failed) throw failed.reason;
  })();
  preparationJobs.set(key, work);
  work.finally(() => preparationJobs.delete(key)).catch(() => {});
  return work;
}

export function getExecutorInfo() {
  const preparation = preparationQueue.snapshot(),
    paid = paidQueue.snapshot(),
    shared = sharedQueue.snapshot();
  return {
    mode: executorMode,
    codexModel: CODEX_EXECUTION_POLICY.model,
    reasoningEffort: CODEX_EXECUTION_POLICY.reasoningEffort,
    conversationPolicy: CODEX_EXECUTION_POLICY.conversationPolicy,
    workspaceRoot,
    runsRoot,
    queuedJobs:
      preparation.queued.length +
      preparation.active.length +
      paid.queued.length +
      paid.active.length +
      shared.queued.length +
      shared.active.length,
    preparationConcurrency: preparation.concurrency,
    activePreparationJobs: preparation.active.length,
    waitingPreparationJobs: preparation.queued.length,
    activeSharedJobs: shared.active.length,
    activePaidJobs: paid.active.length,
    waitingPaidJobs: paid.queued.length,
    paidConcurrency: 1,
    workflowVersion: 'shared-preparation-v1',
  };
}

export function authorizationFingerprint({
  intake,
  prepareResult,
  variantIds,
  artifactManifest,
}) {
  return createHash('sha256')
    .update(
      JSON.stringify({ intake, prepareResult, variantIds, artifactManifest }),
    )
    .digest('hex');
}
