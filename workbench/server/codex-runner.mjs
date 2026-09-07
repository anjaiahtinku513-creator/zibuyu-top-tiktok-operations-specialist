import { spawn } from 'node:child_process';
import { createHash } from 'node:crypto';
import { createWriteStream } from 'node:fs';
import { access, mkdir, readFile, readdir } from 'node:fs/promises';
import os from 'node:os';
import path from 'node:path';
import readline from 'node:readline';
import { fileURLToPath } from 'node:url';
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

const pending = [];
const queuedKeys = new Set();
let draining = false;

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

function preparationPrompt(runDir, intake) {
  const commands = [
    ['intake_validation', '校验用户资料、市场、语言和商品身份'],
    ['product_analysis', '分析商品属性与可验证卖点'],
    ['three_view_generation', '生成每个颜色的白底无人体三视图'],
    ['three_view_quality', '核对颜色、轮廓和结构细节'],
    ['script_and_voice', '生成转化导向的导演包、口播与中文对照'],
    ['preflight_validation', '核对固定模特、市场、引用素材和待提交参数'],
  ];
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
  const resumesBatchSession = phase === 'paid';
  if (resumesBatchSession && !sessionId) {
    throw new Error('付费阶段缺少该批次的 Codex 会话 ID');
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
    '--sandbox',
    'workspace-write',
    '--color',
    'never',
    '-C',
    workspaceRoot,
    '--add-dir',
    runsRoot,
  ];
  if (resumesBatchSession) args.push('--approve-for-me', 'resume', '--all');
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
  const outputPath = path.join(codexDir, `final-${phase}.json`);
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
    const child = processSpawner(invocation.command, invocation.args, {
      cwd: workspaceRoot,
      env: cleanChildEnvironment(),
      windowsHide: true,
      stdio: ['pipe', 'pipe', 'pipe'],
    });

    child.once('spawn', () => {
      spawned = true;
      child.stdin.end(prompt, 'utf8');
    });
    child.once('error', (error) => {
      spawnError = error;
      error.processStarted = spawned;
    });

    child.stderr.pipe(stderrStream);
    const lines = readline.createInterface({ input: child.stdout });
    lines.on('line', (line) => {
      eventsStream.write(`${line}\n`);
      try {
        const event = JSON.parse(line);
        usage.observe(event);
        failure = codexFailureFromEvent(event) ?? failure;
        const found = findSessionId(event);
        if (found) discoveredSessionId = found;
        if (found && found !== persistedSessionId) {
          persistedSessionId = found;
          void writeJsonAtomic(path.join(codexDir, 'session.json'), {
            sessionId: found,
            model: CODEX_EXECUTION_POLICY.model,
            reasoningEffort: CODEX_EXECUTION_POLICY.reasoningEffort,
            conversationPolicy: CODEX_EXECUTION_POLICY.conversationPolicy,
            sessionMode: sessionId ? 'resumed' : 'new',
            updatedAt: new Date().toISOString(),
          }).catch(() => {});
        }
      } catch {
        // Raw output is preserved even when a line is not a JSON event.
      }
    });

    child.once('close', async (code, signal) => {
      lines.close();
      eventsStream.end();
      stderrStream.end();
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
        exitCode: code ?? -1,
        signal,
        sessionId: discoveredSessionId,
        outputPath,
        processStarted: spawned,
        failure,
        ...usageResult,
      });
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
    runId: (await readJson(path.join(runDir, 'intake.json'))).runId,
  };
  await writeJsonAtomic(
    path.join(runDir, 'web', `result-${phase}.json`),
    normalized,
  );

  const stateByOutcome = {
    awaiting_paid_approval: 'awaiting_paid_approval',
    needs_input: 'needs_input',
    blocked: 'blocked',
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

async function runMockPhase(runDir, phase) {
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

async function executePhase(runDir, phase) {
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

  await transitionStatus(runDir, {
    stageKey: phase === 'prepare' ? 'intake_validation' : 'popboom_generation',
    state: phase === 'prepare' ? 'running' : 'submitting',
    currentTask:
      phase === 'prepare' ? '启动 Codex 资料校验' : '启动已授权的 PopBoom 提交',
    note: phase === 'prepare' ? '正在创建本地 Codex 会话' : '已记录付费授权',
  });

  if (executorMode === 'mock') return runMockPhase(runDir, phase);

  const imagePaths = intake.variants.flatMap((variant) =>
    variant.images.map((image) => path.join(runDir, image.relativePath)),
  );
  const prompt =
    phase === 'prepare'
      ? preparationPrompt(runDir, intake)
      : paidPrompt(runDir, intake, approval);

  let execution;
  try {
    execution = await runCodexProcess({
      runDir,
      phase,
      prompt,
      imagePaths: phase === 'prepare' ? imagePaths : [],
      sessionId:
        phase === 'paid'
          ? (sessionRecord?.sessionId ?? status?.sessionId)
          : null,
    });
  } catch (error) {
    const uncertain = phase === 'paid' && error.processStarted;
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
  } catch (error) {
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

  await applyStructuredResult(runDir, phase, result, execution.sessionId);
}

async function drainQueue() {
  if (draining) return;
  draining = true;
  while (pending.length) {
    const job = pending.shift();
    try {
      if (job.kind === 'classification') {
        await executeColorClassification(job.runDir);
      } else {
        await executePhase(job.runDir, job.phase);
      }
      job.resolve();
    } catch (error) {
      if (job.kind === 'classification') {
        await updateClassificationStatus(job.runDir, {
          state: 'failed',
          currentTask: '颜色识别失败',
          note: error.message,
          error: error.message,
        }).catch(() => {});
      } else {
        await transitionStatus(job.runDir, {
          stageKey:
            job.phase === 'paid' ? 'popboom_generation' : 'intake_validation',
          state: job.phase === 'paid' ? 'submission_unknown' : 'failed',
          currentTask: '任务执行失败',
          note: error.message,
          error: error.message,
        }).catch(() => {});
      }
      job.reject(error);
    } finally {
      queuedKeys.delete(job.key);
    }
  }
  draining = false;
}

export function enqueueCodexPhase(runDir, phase) {
  const key = `${runDir}:${phase}`;
  if (queuedKeys.has(key)) return Promise.resolve();
  queuedKeys.add(key);
  const promise = new Promise((resolve, reject) => {
    pending.push({ kind: 'workflow', key, runDir, phase, resolve, reject });
  });
  void drainQueue();
  return promise;
}

export function enqueueColorClassification(batchDir) {
  const key = `${batchDir}:classification`;
  if (queuedKeys.has(key)) return Promise.resolve();
  queuedKeys.add(key);
  const promise = new Promise((resolve, reject) => {
    pending.push({
      kind: 'classification',
      key,
      runDir: batchDir,
      resolve,
      reject,
    });
  });
  void drainQueue();
  return promise;
}

export function getExecutorInfo() {
  return {
    mode: executorMode,
    codexModel: CODEX_EXECUTION_POLICY.model,
    reasoningEffort: CODEX_EXECUTION_POLICY.reasoningEffort,
    conversationPolicy: CODEX_EXECUTION_POLICY.conversationPolicy,
    workspaceRoot,
    runsRoot,
    queuedJobs: pending.length + (draining ? 1 : 0),
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
