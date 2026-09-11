// Isolated integration-test executor. Never imported for real production.
import { createHash } from 'node:crypto';
import { copyFile, readFile } from 'node:fs/promises';
import path from 'node:path';
import { writeJsonAtomic } from './state.mjs';

export async function createMockSharedResult(request) {
  if (process.env.ZIBUYU_EXECUTOR_MODE !== 'mock')
    throw new Error('Mock shared producer requires mock mode');
  const sha = (data) => createHash('sha256').update(data).digest('hex');
  await writeJsonAtomic(path.join(request.sharedDir, 'facts.json'), {
    sku: request.sku,
    facts: [],
    limitations: ['模拟验收，不是真实商品分析'],
  });
  const marketAnalyses = [];
  for (const [index, market] of request.markets.entries()) {
    const file = `market-${index}.json`;
    await writeJsonAtomic(path.join(request.sharedDir, file), {
      ...market,
      evidenceStatus: 'unverified',
      limitations: ['mock'],
    });
    marketAnalyses.push({ ...market, path: file });
  }
  const threeViews = [];
  for (const variant of request.variants) {
    const file = `${variant.id}.png`,
      qaPath = `${variant.id}.qa.json`;
    await copyFile(
      path.join(request.sharedDir, variant.images[0].relativePath),
      path.join(request.sharedDir, file),
    );
    const auditedSha256 = sha(
      await readFile(path.join(request.sharedDir, file)),
    );
    const cue = {
      audit_version: 'zero_human_identity_pixels_v1',
      audited_sha256: auditedSha256,
      inspection_method: 'full_resolution_visual_inspection',
      reviewed_at: new Date().toISOString(),
      passed: true,
    };
    for (const field of [
      'skin_present',
      'face_present',
      'hair_present',
      'neck_chest_collarbone_present',
      'shoulders_arms_wrists_present',
      'hands_fingers_nails_present',
      'tattoos_jewelry_present',
      'person_specific_body_shape_present',
    ])
      cue[field] = false;
    await writeJsonAtomic(path.join(request.sharedDir, qaPath), {
      variantId: variant.id,
      passed: true,
      auditedSha256,
      sourceImageHashes: variant.images.map((image) => image.sha256),
      identityFree: true,
      evidence: 'MOCK ONLY — no real visual inspection',
      qc: {
        front_side_back_order: true,
        pure_white_background: true,
        same_sku_color_only: true,
        human_identity_pixels_absent: true,
      },
      identityCueAudit: cue,
      identityCueAuditSha256: sha(
        JSON.stringify(
          Object.fromEntries(
            Object.entries(cue).sort(([a], [b]) => a.localeCompare(b)),
          ),
        ),
      ),
    });
    threeViews.push({ variantId: variant.id, path: file, qaPath });
  }
  return {
    inputFingerprint: request.inputFingerprint,
    outcome: 'ready',
    summary: '公共准备模拟完成，未调用外部服务',
    productFactsPath: 'facts.json',
    marketAnalyses,
    threeViews,
  };
}
