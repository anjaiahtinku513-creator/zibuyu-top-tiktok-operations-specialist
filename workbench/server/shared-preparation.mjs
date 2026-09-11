import { createHash } from 'node:crypto';
import { copyFile, lstat, mkdir, readFile, realpath } from 'node:fs/promises';
import path from 'node:path';
import { readJson, writeJsonAtomic } from './state.mjs';
import { readModelBatch } from './model-batches.mjs';

export const SHARED_CONTRACT = 'shared-preparation-v1';
const digest = (value) => createHash('sha256').update(value).digest('hex');
const fingerprint = (value) => digest(JSON.stringify(value));
const canonical = (value) =>
  Array.isArray(value)
    ? value.map(canonical)
    : value && typeof value === 'object'
      ? Object.fromEntries(
          Object.keys(value)
            .sort()
            .map((key) => [key, canonical(value[key])]),
        )
      : value;
const identityFields = [
  'skin_present',
  'face_present',
  'hair_present',
  'neck_chest_collarbone_present',
  'shoulders_arms_wrists_present',
  'hands_fingers_nails_present',
  'tattoos_jewelry_present',
  'person_specific_body_shape_present',
];
const fail = (message) => {
  throw new Error(message);
};

async function safeFile(root, relative) {
  const target = path.resolve(root, relative);
  if (!target.startsWith(path.resolve(root) + path.sep))
    fail('共享产物路径越界');
  const resolved = await realpath(target);
  if (!resolved.startsWith((await realpath(root)) + path.sep))
    fail('共享产物链接越界');
  const stat = await lstat(target);
  if (!stat.isFile() || stat.isSymbolicLink() || stat.size > 50 * 1024 * 1024)
    fail('共享产物文件无效');
  return { target, bytes: await readFile(target) };
}

export async function sharedRequestFor(runDir) {
  const group = await readModelBatch(runDir);
  if (!group) return null;
  const root = path.dirname(runDir);
  const intakes = await Promise.all(
    group.runs.map((child) =>
      readJson(path.join(root, child.runId, 'intake.json')),
    ),
  );
  const first = intakes[0];
  const variants = first.variants.map(({ id, name, images }) => ({
    id,
    name,
    images: images.map(({ sha256, relativePath }) => ({
      sha256,
      relativePath,
    })),
  }));
  for (const intake of intakes) {
    const inputs = intake.variants.map(({ id, name, images }) => ({
      id,
      name,
      images: images.map(({ sha256, relativePath }) => ({
        sha256,
        relativePath,
      })),
    }));
    if (fingerprint(inputs) !== fingerprint(variants))
      fail('同批商品图已变化，不能共享准备');
    for (const variant of intake.variants)
      for (const image of variant.images) {
        const { bytes } = await safeFile(
          path.join(root, intake.runId),
          image.relativePath,
        );
        if (digest(bytes) !== image.sha256)
          fail('商品源图哈希不一致，停止共享准备');
      }
  }
  const markets = [
    ...new Map(
      intakes.map((intake) => {
        const item = {
          marketCode: intake.model.marketCode,
          locale: intake.model.locale,
          sourceUrl: intake.amazonUrl || '',
        };
        return [fingerprint(item), item];
      }),
    ).values(),
  ].sort((a, b) => JSON.stringify(a).localeCompare(JSON.stringify(b)));
  const input = {
    contract: SHARED_CONTRACT,
    sku: first.sku,
    variants,
    markets,
    // Include user constraints and output choices, but never fixed model identity.
    requirements: first.requirements ?? first.notes ?? '',
    output: first.output ?? {},
  };
  const inputFingerprint = fingerprint(input);
  return {
    ...input,
    inputFingerprint,
    batchId: group.batchId,
    sharedDir: path.join(root, '_shared', group.batchId, inputFingerprint),
    sourceRunDir: path.join(root, first.runId),
    runIds: group.runs.map((child) => child.runId),
  };
}

export async function initializeShared(request) {
  await mkdir(request.sharedDir, { recursive: true });
  const { sharedDir, sourceRunDir, ...input } = request;
  await writeJsonAtomic(path.join(sharedDir, 'request.json'), input);
  for (const variant of request.variants)
    for (const image of variant.images) {
      const { target } = await safeFile(sourceRunDir, image.relativePath);
      const destination = path.join(sharedDir, image.relativePath);
      await mkdir(path.dirname(destination), { recursive: true });
      await copyFile(target, destination);
    }
  // Progress is isolated from all model-specific workflow status files.
  await writeJsonAtomic(path.join(sharedDir, 'intake.json'), {
    runId: request.batchId,
    sku: request.sku,
    variants: request.variants,
    model: { name: '公共准备' },
    modelBatchId: request.batchId,
  });
}

export async function sealShared(request, result) {
  if (result.outcome !== 'ready') fail(result.summary || '公共准备未通过');
  if (result.inputFingerprint !== request.inputFingerprint)
    fail('公共准备输入指纹不匹配');
  const files = [];
  async function capture(relative, kind, scope) {
    const { bytes } = await safeFile(request.sharedDir, relative);
    const entry = {
      path: relative,
      sha256: digest(bytes),
      size: bytes.length,
      kind,
      ...scope,
    };
    files.push(entry);
    return {
      entry,
      json: kind === 'image' ? null : JSON.parse(bytes.toString('utf8')),
    };
  }
  const facts = await capture(result.productFactsPath, 'facts', {});
  if (
    facts.json.sku !== request.sku ||
    !Array.isArray(facts.json.facts) ||
    !Array.isArray(facts.json.limitations) ||
    facts.json.model ||
    facts.json.market_lock
  )
    fail('公共商品事实必须不含模特身份');
  if (result.marketAnalyses.length !== request.markets.length)
    fail('市场分析数量不匹配');
  for (const market of request.markets) {
    const matches = result.marketAnalyses.filter(
      (item) =>
        item.marketCode === market.marketCode &&
        item.sourceUrl === market.sourceUrl &&
        item.locale === market.locale,
    );
    if (matches.length !== 1) fail('市场来源或语言不匹配');
    const item = await capture(matches[0].path, 'market', market);
    if (
      item.json.marketCode !== market.marketCode ||
      item.json.sourceUrl !== market.sourceUrl ||
      !['verified', 'partial', 'unverified'].includes(
        item.json.evidenceStatus,
      ) ||
      !Array.isArray(item.json.limitations)
    )
      fail('市场证据边界未记录');
  }
  if (result.threeViews.length !== request.variants.length)
    fail('三视图颜色数量不匹配');
  for (const variant of request.variants) {
    const matches = result.threeViews.filter(
      (item) => item.variantId === variant.id,
    );
    if (matches.length !== 1) fail('三视图颜色标识不匹配');
    const image = await capture(matches[0].path, 'image', {
      variantId: variant.id,
    });
    const audit = await capture(matches[0].qaPath, 'qa', {
      variantId: variant.id,
    });
    if (
      audit.json.variantId !== variant.id ||
      audit.json.passed !== true ||
      audit.json.auditedSha256 !== image.entry.sha256 ||
      audit.json.identityFree !== true ||
      !audit.json.evidence ||
      fingerprint(audit.json.sourceImageHashes) !==
        fingerprint(variant.images.map((item) => item.sha256))
    )
      fail('三视图缺少绑定实际图片和源图的验收记录');
    const cue = audit.json.identityCueAudit;
    if (
      !cue ||
      cue.audited_sha256 !== image.entry.sha256 ||
      cue.passed !== true ||
      cue.audit_version !== 'zero_human_identity_pixels_v1' ||
      cue.inspection_method !== 'full_resolution_visual_inspection' ||
      !cue.reviewed_at ||
      identityFields.some((field) => cue[field] !== false) ||
      fingerprint(canonical(cue)) !== audit.json.identityCueAuditSha256
    )
      fail('三视图人体身份审计不完整');
    if (
      ![
        'front_side_back_order',
        'pure_white_background',
        'same_sku_color_only',
        'human_identity_pixels_absent',
      ].every((key) => audit.json.qc?.[key] === true)
    )
      fail('三视图结构质检不完整');
  }
  if (new Set(files.map((file) => file.path)).size !== files.length)
    fail('共享产物路径重复');
  const manifest = {
    contract: SHARED_CONTRACT,
    state: 'ready',
    batchId: request.batchId,
    executorMode: process.env.ZIBUYU_EXECUTOR_MODE || 'real',
    inputFingerprint: request.inputFingerprint,
    sku: request.sku,
    files,
    summary: result.summary,
    sealedAt: new Date().toISOString(),
  };
  manifest.manifestHash = fingerprint(manifest);
  await writeJsonAtomic(
    path.join(request.sharedDir, 'manifest.json'),
    manifest,
  );
  return manifest;
}

export async function readVerifiedShared(request) {
  const manifest = await readJson(
    path.join(request.sharedDir, 'manifest.json'),
  );
  if (!manifest) return null;
  const { manifestHash, ...body } = manifest;
  if (
    manifest.state !== 'ready' ||
    manifest.executorMode !== (process.env.ZIBUYU_EXECUTOR_MODE || 'real') ||
    manifest.contract !== SHARED_CONTRACT ||
    manifest.inputFingerprint !== request.inputFingerprint ||
    fingerprint(body) !== manifestHash
  )
    fail('共享准备清单已变化');
  for (const file of manifest.files) {
    const { bytes } = await safeFile(request.sharedDir, file.path);
    if (digest(bytes) !== file.sha256 || bytes.length !== file.size)
      fail('共享产物已损坏，停止复用');
  }
  return manifest;
}

export async function attachShared(runDir, request) {
  const manifest = await readVerifiedShared(request);
  if (!manifest) fail('公共准备尚未验收');
  const intake = await readJson(path.join(runDir, 'intake.json'));
  const files = manifest.files.filter(
    (file) =>
      file.kind !== 'market' ||
      (file.marketCode === intake.model.marketCode &&
        file.locale === intake.model.locale &&
        file.sourceUrl === (intake.amazonUrl || '')),
  );
  const folder = path.join(runDir, 'shared-input');
  await mkdir(folder, { recursive: true });
  for (const [index, file] of files.entries()) {
    const { target, bytes } = await safeFile(request.sharedDir, file.path);
    const relative = `shared-input/${index}-${path.basename(file.path)}`;
    await copyFile(target, path.join(runDir, relative));
    if (digest(await readFile(path.join(runDir, relative))) !== digest(bytes))
      fail('共享副本校验失败');
    files[index] = { ...file, sourcePath: file.path, path: relative };
  }
  const receipt = {
    contract: SHARED_CONTRACT,
    inputFingerprint: request.inputFingerprint,
    manifestHash: manifest.manifestHash,
    batchId: request.batchId,
    files,
    attachedAt: new Date().toISOString(),
  };
  await writeJsonAtomic(path.join(folder, 'manifest.json'), receipt);
  return receipt;
}

// Deliberately narrow migration: same input batch, completed model preparation,
// exact source URL/market, verified image bytes and preserved existing audits.
// Never copy a model's scripts, compile output or validation receipt as facts.
export async function seedSharedFromCompletedRun(request) {
  if (request.markets.length !== 1) return false;
  for (const id of request.runIds) {
    const sourceDir = path.join(path.dirname(request.sourceRunDir), id);
    const final = await readJson(
      path.join(sourceDir, 'web', 'result-prepare.json'),
    );
    if (final?.outcome !== 'awaiting_paid_approval') continue;
    const analysis = await readJson(
      path.join(sourceDir, 'product-analysis.json'),
    );
    const ledger = await readJson(
      path.join(sourceDir, 'three-view-ledger.json'),
    );
    const intake = await readJson(path.join(sourceDir, 'intake.json'));
    const market = request.markets[0];
    if (
      !analysis ||
      !ledger ||
      analysis.sku !== request.sku ||
      analysis.source_url !== market.sourceUrl ||
      intake.model.marketCode !== market.marketCode ||
      intake.model.locale !== market.locale ||
      !analysis.garment_baseline
    )
      continue;
    const events = (
      await readFile(
        path.join(sourceDir, 'web', 'codex-events-prepare.jsonl'),
        'utf8',
      )
    ).split(/\r?\n/);
    let historical = null;
    for (const [lineIndex, line] of events.entries()) {
      try {
        const item = JSON.parse(line).item;
        if (
          item?.type !== 'command_execution' ||
          item.status !== 'completed' ||
          item.exit_code !== 0 ||
          !String(item.command).includes('intake.json')
        )
          continue;
        const value = JSON.parse(item.aggregated_output);
        if (
          value.runId === id &&
          value.modelBatchId === request.batchId &&
          value.sku === request.sku &&
          Array.isArray(value.variants)
        ) {
          historical = {
            intake: value,
            eventId: item.id,
            line: lineIndex + 1,
            outputSha256: digest(item.aggregated_output),
          };
          break;
        }
      } catch {
        /* Only exact successful historical intake JSON is eligible. */
      }
    }
    if (!historical) fail('既有素材缺少历史源图哈希快照，不能自动沿用旧验收');
    for (const variant of request.variants) {
      const original = historical.intake.variants.find(
        (item) => item.id === variant.id && item.name === variant.name,
      );
      if (
        !original ||
        fingerprint(
          original.images.map(({ sha256, relativePath }) => ({
            sha256,
            relativePath,
          })),
        ) !== fingerprint(variant.images)
      )
        fail('既有验收的源图或颜色已变化');
      const item = ledger.variants?.find(
        (row) => row.variant_id === variant.id,
      );
      const expectedPaths = original.images.map((image) =>
        path.resolve(sourceDir, image.relativePath),
      );
      const comparePath = (a, b) => a.localeCompare(b);
      if (
        !item ||
        fingerprint(
          item.source_images
            ?.map((file) => path.resolve(file))
            .sort(comparePath),
        ) !== fingerprint([...expectedPaths].sort(comparePath))
      )
        fail('既有三视图源图映射不匹配');
    }
    await initializeShared(request);
    const factsPath = 'facts.json',
      marketPath = 'market.json';
    const limitations = analysis.unsupported_or_unverified ?? [];
    await writeJsonAtomic(path.join(request.sharedDir, factsPath), {
      sku: request.sku,
      facts: Object.entries(analysis.garment_baseline).map(([key, value]) => ({
        key,
        value,
      })),
      limitations: Array.isArray(limitations) ? limitations : [limitations],
      provenance: { runId: id, path: 'product-analysis.json' },
    });
    await writeJsonAtomic(path.join(request.sharedDir, marketPath), {
      ...market,
      // Migration never promotes prior evidence to verified.
      evidenceStatus:
        analysis.amazon_public_web_evidence?.status === 'partial'
          ? 'partial'
          : 'unverified',
      evidence: analysis.amazon_public_web_evidence ?? null,
      reviewAnalysis: analysis.review_analysis ?? null,
      limitations: [
        ...(analysis.amazon_public_web_evidence?.limitations ?? []),
        '共享现有记录；未重新抓取或提升证据可信等级。',
      ],
      provenance: { runId: id, path: 'product-analysis.json' },
    });
    const threeViews = [];
    for (const variant of request.variants) {
      const items = ledger.variants?.filter(
        (item) => item.variant_id === variant.id,
      );
      if (
        items?.length !== 1 ||
        items[0].color_name !== variant.name ||
        items[0].qc?.passed !== true
      )
        fail('既有三视图颜色或验收缺失');
      const item = items[0];
      const { target, bytes } = await safeFile(sourceDir, item.three_view_path);
      if (digest(bytes) !== item.sha256) fail('既有三视图哈希已变化');
      const imagePath = `images/${variant.id}${path.extname(target)}`,
        qaPath = `images/${variant.id}.qa.json`;
      await mkdir(path.join(request.sharedDir, 'images'), { recursive: true });
      await copyFile(target, path.join(request.sharedDir, imagePath));
      await writeJsonAtomic(path.join(request.sharedDir, qaPath), {
        variantId: variant.id,
        passed: true,
        auditedSha256: item.sha256,
        sourceImageHashes: variant.images.map((image) => image.sha256),
        identityFree: item.qc.human_identity_pixels_absent,
        evidence: {
          runId: id,
          ledger: 'three-view-ledger.json',
          notes: item.qc.notes,
          historicalInput: {
            eventId: historical.eventId,
            line: historical.line,
            outputSha256: historical.outputSha256,
          },
        },
        qc: item.qc,
        identityCueAudit: item.identity_cue_audit,
        identityCueAuditSha256: item.identity_cue_audit_sha256,
      });
      threeViews.push({ variantId: variant.id, path: imagePath, qaPath });
    }
    await sealShared(request, {
      inputFingerprint: request.inputFingerprint,
      outcome: 'ready',
      summary:
        '复用本批已完成的商品资料与三视图，保留原始验收和未验证证据边界。',
      productFactsPath: factsPath,
      marketAnalyses: [{ ...market, path: marketPath }],
      threeViews,
    });
    return true;
  }
  return false;
}

export async function verifyAttachedShared(runDir) {
  const receipt = await readJson(
    path.join(runDir, 'shared-input', 'manifest.json'),
  );
  if (!receipt) {
    const preparation = await readJson(
      path.join(runDir, 'web', 'preparation.json'),
    );
    if (preparation?.mode === 'shared')
      fail('本单公共准备引用缺失，不能退回重复生成');
    return null;
  }
  const request = await sharedRequestFor(runDir);
  const manifest = request && (await readVerifiedShared(request));
  if (!manifest || manifest.manifestHash !== receipt.manifestHash)
    fail('本单共享准备来源已失效');
  const intake = await readJson(path.join(runDir, 'intake.json'));
  for (const file of receipt.files) {
    const source = manifest.files.find(
      (item) => item.path === file.sourcePath && item.sha256 === file.sha256,
    );
    if (
      !source ||
      (source.kind === 'market' &&
        (source.marketCode !== intake.model.marketCode ||
          source.sourceUrl !== intake.amazonUrl ||
          source.locale !== intake.model.locale))
    )
      fail('共享副本来源或市场不匹配');
    const { bytes } = await safeFile(runDir, file.path);
    if (digest(bytes) !== file.sha256 || bytes.length !== file.size)
      fail('本单共享准备副本已变化');
  }
  const expected = manifest.files.filter(
    (file) =>
      file.kind !== 'market' ||
      (file.marketCode === intake.model.marketCode &&
        file.locale === intake.model.locale &&
        file.sourceUrl === (intake.amazonUrl || '')),
  );
  if (
    receipt.files.length !== expected.length ||
    new Set(receipt.files.map((file) => file.sourcePath)).size !==
      expected.length
  )
    fail('共享副本不完整');
  return receipt;
}
