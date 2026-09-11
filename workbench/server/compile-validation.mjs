import { spawn } from 'node:child_process';
import { createHash, randomUUID } from 'node:crypto';
import { access, mkdir, readFile, readdir } from 'node:fs/promises';
import os from 'node:os';
import path from 'node:path';
import { readJson, writeJsonAtomic } from './state.mjs';

const digest = (bytes) => createHash('sha256').update(bytes).digest('hex');
const reject = (message) => {
  throw Object.assign(new Error(message), { statusCode: 409 });
};

async function installedValidator() {
  const base = path.join(
    os.homedir(),
    '.codex/plugins/cache/personal/zibuyu-top-tiktok-operations-specialist',
  );
  for (const version of (await readdir(base)).sort().reverse()) {
    const candidate = path.join(
      base,
      version,
      'skills/seedance-ugc-cn-director/scripts/validate_batch_compile.py',
    );
    try {
      await access(candidate);
      return candidate;
    } catch {
      /* next installed version */
    }
  }
  reject('未找到当前插件的正式编译校验器');
}

async function pythonRuntime() {
  const candidate =
    process.env.ZIBUYU_PYTHON ||
    path.join(
      os.homedir(),
      '.cache/codex-runtimes/codex-primary-runtime/dependencies/python/python.exe',
    );
  await access(candidate);
  return candidate;
}

function runValidator(python, validator, compilePath) {
  return new Promise((resolve, rejectRun) => {
    const child = spawn(python, [validator, compilePath], {
      windowsHide: true,
      stdio: ['ignore', 'pipe', 'pipe'],
      env: { ...process.env, PYTHONUTF8: '1' },
    });
    let stdout = '',
      stderr = '',
      timedOut = false;
    const timer = setTimeout(() => {
      timedOut = true;
      child.kill();
    }, 60000);
    child.stdout.on('data', (data) => {
      stdout += data;
    });
    child.stderr.on('data', (data) => {
      stderr += data;
    });
    child.once('error', (error) => {
      clearTimeout(timer);
      rejectRun(error);
    });
    child.once('close', (exitCode) => {
      clearTimeout(timer);
      resolve({ exitCode, stdout, stderr, timedOut });
    });
  });
}

// Validate the exact formal compile artifacts that the paid prompt will consume.
// Old/history packages outside this approved list are never a fallback source.
export async function validatePreparationCompiles(
  runDir,
  prepareResult,
  options = {},
) {
  const intake =
    options.intake || (await readJson(path.join(runDir, 'intake.json')));
  const expectedIds = new Set(
    options.variantIds || intake.variants.map((v) => v.id),
  );
  const paths = [
    ...new Set(
      (prepareResult?.artifacts || [])
        .filter(
          (a) =>
            path.basename(a.path.replaceAll('\\', '/')) ===
            'batch-compile.json',
        )
        .map((a) => path.resolve(runDir, a.path)),
    ),
  ];
  if (!paths.length)
    reject('准备阶段缺少正式 batch-compile.json，不能授权生成');
  if (paths.some((p) => !p.startsWith(path.resolve(runDir) + path.sep)))
    reject('正式编译包必须位于当前任务目录');
  const validatorPath = options.validatorPath || (await installedValidator());
  const python = options.pythonPath || (await pythonRuntime());
  const validatorSha256 = digest(await readFile(validatorPath));
  const receiptPath = path.join(
    runDir,
    'web/compile-validations',
    randomUUID(),
    'receipt.json',
  );
  await mkdir(path.dirname(receiptPath), { recursive: true });
  const receipt = {
    version: 1,
    runId: intake.runId,
    checkedAt: new Date().toISOString(),
    validatorPath,
    validatorSha256,
    valid: false,
    records: [],
  };
  const covered = new Set();
  try {
    for (const compilePath of paths) {
      const before = await readFile(compilePath);
      const sha256 = digest(before);
      const execution = await (options.processRunner || runValidator)(
        python,
        validatorPath,
        compilePath,
      );
      const record = { compilePath, sha256, ...execution };
      receipt.records.push(record);
      let result;
      try {
        result = JSON.parse(execution.stdout.trim());
      } catch {
        reject(
          `正式编译校验未返回有效结果：${path.relative(runDir, compilePath)}`,
        );
      }
      record.result = result;
      const errorCodes = (result.errors || [])
        .slice(0, 4)
        .map((e) => e.code || e.message || String(e))
        .join('、');
      if (
        execution.timedOut ||
        execution.exitCode !== 0 ||
        result.valid !== true ||
        result.eligible_for_new_submission !== true
      )
        reject(
          `正式编译校验未通过：${path.relative(runDir, compilePath)}${errorCodes ? `（${errorCodes}）` : ''}`,
        );
      if (
        digest(await readFile(compilePath)) !== sha256 ||
        result.batch_compile_sha256 !== sha256
      )
        reject('编译包在校验期间变化，不能授权生成');
      const document = JSON.parse(before.toString('utf8'));
      const key = document.batch_key;
      if (
        key?.model_preset !== (intake.model.preset || intake.model.name) ||
        key?.market !== intake.model.marketCode ||
        key?.voiceover_language !== intake.model.locale ||
        key?.duration_seconds !== intake.output.durationSeconds ||
        key?.aspect_ratio !== intake.output.aspectRatio ||
        key?.resolution !== intake.output.resolution
      )
        reject('正式编译包的模特、市场、语言或输出规格与制作单不一致');
      const ids = document.variants?.map((v) => v.variant_id);
      if (
        !ids?.length ||
        new Set(ids).size !== ids.length ||
        ids.some((id) => !intake.variants.some((v) => v.id === id))
      )
        reject('正式编译包的颜色范围与制作单不一致');
      for (const id of ids) {
        const variant = document.variants.find((v) => v.variant_id === id);
        const tags = variant.hashtags;
        if (
          typeof variant.caption !== 'string' ||
          !variant.caption.trim() ||
          /#[\p{L}\p{N}_]+/u.test(variant.caption) ||
          !Array.isArray(tags) ||
          tags.length !== 5 ||
          tags.some(
            (tag) => typeof tag !== 'string' || !/^#[\p{L}\p{N}_]+$/u.test(tag),
          ) ||
          new Set(tags.map((tag) => tag.toLowerCase())).size !== 5 ||
          /#imily\s*bela/i.test(tags.join(' '))
        )
          reject(`最终文案或五个标签不规范，需在生成前修复：${id}`);
        const proof = result.director_receipts?.find(
          (r) => r.variant_id === id,
        );
        if (
          !proof ||
          proof.director_valid !== true ||
          proof.eligible_for_new_submission !== true ||
          proof.batch_compile_sha256 !== sha256 ||
          proof.market !== intake.model.marketCode ||
          proof.voiceover_language !== intake.model.locale
        )
          reject(`正式编译包缺少当前颜色的有效导演回执：${id}`);
        covered.add(id);
      }
    }
    if ([...expectedIds].some((id) => !covered.has(id)))
      reject('正式编译包未覆盖本次授权的全部颜色');
    if (digest(await readFile(validatorPath)) !== validatorSha256)
      reject('校验器在检查期间更新，请重新校验');
    receipt.valid = true;
  } catch (error) {
    receipt.error = error.message;
    throw Object.assign(error, {
      statusCode: 409,
      validationReceiptPath: receiptPath,
    });
  } finally {
    await writeJsonAtomic(receiptPath, receipt);
  }
  return {
    receiptPath,
    receiptSha256: digest(await readFile(receiptPath)),
    compilePaths: paths,
    records: receipt.records.map((r) => ({
      compilePath: r.compilePath,
      sha256: r.sha256,
    })),
  };
}
