import assert from 'node:assert/strict';
import test from 'node:test';

import {
  detectImageType,
  parseIntakePayload,
  safeFileStem,
} from '../server/validation.mjs';

const basePayload = {
  sku: 'M4D809-EU',
  modelMode: 'preset',
  modelPreset: '德1',
  variants: [{ id: 'variant-001', name: 'Black' }],
};

test('fixed model preset locks market, locale, asset, and output settings', () => {
  const intake = parseIntakePayload({
    ...basePayload,
    classificationBatchId: 'cls-20260904-0123456789',
    variants: [
      {
        ...basePayload.variants[0],
        colorNameZh: '黑色',
        confidence: 'high',
        classificationReason: '服装主体为黑色',
        autoGrouped: true,
      },
    ],
  });
  assert.equal(intake.model.marketCode, 'DE');
  assert.equal(intake.model.locale, 'de-DE');
  assert.equal(intake.model.assetId, 'asset-20260703091345-n2q24');
  assert.equal(intake.output.resolution, '720p');
  assert.equal(intake.output.platformBrand, '拓展平台');
  assert.equal(intake.classificationBatchId, 'cls-20260904-0123456789');
  assert.equal(intake.variants[0].confidence, 'high');
  assert.equal(intake.variants[0].autoGrouped, true);
});

test('custom model requires a name and deterministic market language', () => {
  const intake = parseIntakePayload({
    ...basePayload,
    modelMode: 'custom',
    customModelName: 'Anna Custom',
    customMarket: 'US',
  });
  assert.equal(intake.model.mode, 'custom');
  assert.equal(intake.model.locale, 'en-US');
  assert.throws(
    () =>
      parseIntakePayload({
        ...basePayload,
        modelMode: 'custom',
        customMarket: 'DE',
      }),
    /模特名称/,
  );
});

test('image signatures are checked independently of filename', () => {
  const png = Buffer.from([0x89, 0x50, 0x4e, 0x47, 0x0d, 0x0a, 0x1a, 0x0a]);
  assert.deepEqual(detectImageType(png), {
    extension: '.png',
    mimeType: 'image/png',
  });
  assert.throws(() => detectImageType(Buffer.from('not-an-image')), /PNG/);
  assert.equal(safeFileStem('../../bad name?.png'), 'bad-name');
});
