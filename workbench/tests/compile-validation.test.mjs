import assert from 'node:assert/strict';
import { createHash } from 'node:crypto';
import { mkdtemp, readFile, rm, writeFile } from 'node:fs/promises';
import os from 'node:os';
import path from 'node:path';
import test from 'node:test';
import { validatePreparationCompiles } from '../server/compile-validation.mjs';
import { writeJsonAtomic } from '../server/state.mjs';

const sha = (bytes) => createHash('sha256').update(bytes).digest('hex');
const intake = {
  runId: 'unit-only',
  model: { preset: '德2', marketCode: 'DE', locale: 'de-DE' },
  variants: [{ id: 'black' }, { id: 'white' }],
  output: { durationSeconds: 15, aspectRatio: '9:16', resolution: '720p' },
};
async function fixture(action) {
  const root = await mkdtemp(path.join(os.tmpdir(), 'zb-compile-gate-'));
  try {
    const validatorPath = path.join(root, 'fake-validator.py');
    await writeFile(validatorPath, '# test process is injected; never invoked');
    const document = {
      batch_key: {
        model_preset: '德2',
        market: 'DE',
        voiceover_language: 'de-DE',
        duration_seconds: 15,
        aspect_ratio: '9:16',
        resolution: '720p',
      },
      variants: ['black', 'white'].map((id) => ({
        variant_id: id,
        caption: 'Offene Front für deinen Alltag.',
        hashtags: ['#Cardigan', '#Outfit', '#Strick', '#Alltag', '#Layering'],
      })),
    };
    const compilePath = path.join(root, 'batch-compile.json');
    await writeJsonAtomic(compilePath, document);
    const result = { artifacts: [{ kind: 'json', path: compilePath }] };
    const options = {
      intake,
      validatorPath,
      pythonPath: 'unit-only-not-invoked',
      processRunner: async (_p, _v, file) => {
        const bytes = await readFile(file);
        return {
          exitCode: 0,
          stdout: JSON.stringify({
            valid: true,
            eligible_for_new_submission: true,
            batch_compile_sha256: sha(bytes),
            director_receipts: ['black', 'white'].map((id) => ({
              variant_id: id,
              director_valid: true,
              eligible_for_new_submission: true,
              batch_compile_sha256: sha(bytes),
              market: 'DE',
              voiceover_language: 'de-DE',
            })),
          }),
          stderr: '',
        };
      },
    };
    await action({ root, compilePath, document, result, options });
  } finally {
    await rm(root, { recursive: true, force: true });
  }
}

test('formal compiler gate ignores self-declared preflight and checks every listed compile', async () =>
  fixture(async ({ root, result, options }) => {
    const valid = await validatePreparationCompiles(root, result, options);
    assert.equal(valid.compilePaths.length, 1);
    const receipt = JSON.parse(await readFile(valid.receiptPath, 'utf8'));
    assert.equal(receipt.valid, true);
    const bad = path.join(root, 'releases/white/batch-compile.json');
    await writeJsonAtomic(bad, { schema_version: '1.4', status: 'passed' });
    await writeJsonAtomic(path.join(root, 'preflight-validation.json'), {
      status: 'passed',
    });
    result.artifacts.push({ kind: 'json', path: bad });
    const original = options.processRunner;
    options.processRunner = async (...args) =>
      args[2] === bad
        ? {
            exitCode: 1,
            stdout: JSON.stringify({
              valid: false,
              errors: [{ code: 'VARIANTS_INVALID' }],
            }),
            stderr: '',
          }
        : original(...args);
    await assert.rejects(
      validatePreparationCompiles(root, result, options),
      /VARIANTS_INVALID/,
    );
  }));

test('historical eligibility, non-JSON, timeout, wrong scope and changed compile bytes fail closed', async () => {
  for (const mode of [
    'historical',
    'invalid-json',
    'timeout',
    'scope',
    'changed',
    'duplicate-caption-tags',
  ]) {
    await fixture(async ({ root, compilePath, document, result, options }) => {
      if (mode === 'scope') {
        document.batch_key.model_preset = '德3';
        await writeJsonAtomic(compilePath, document);
      }
      if (mode === 'duplicate-caption-tags') {
        document.variants[0].caption += ' #Cardigan';
        await writeJsonAtomic(compilePath, document);
      }
      const original = options.processRunner;
      options.processRunner = async (...args) => {
        const output = await original(...args);
        if (mode === 'invalid-json') output.stdout = 'not-json';
        if (mode === 'timeout') output.timedOut = true;
        if (mode === 'historical') {
          const body = JSON.parse(output.stdout);
          body.eligible_for_new_submission = false;
          output.stdout = JSON.stringify(body);
        }
        if (mode === 'changed') await writeFile(compilePath, '{}');
        return output;
      };
      await assert.rejects(validatePreparationCompiles(root, result, options));
    });
  }
});

test('missing compile or missing authorized colors cannot open paid execution', async () =>
  fixture(async ({ root, result, options, document, compilePath }) => {
    await assert.rejects(
      validatePreparationCompiles(root, { artifacts: [] }, options),
      /缺少正式/,
    );
    document.variants = [document.variants[0]];
    await writeJsonAtomic(compilePath, document);
    await assert.rejects(
      validatePreparationCompiles(root, result, options),
      /全部颜色/,
    );
  }));
