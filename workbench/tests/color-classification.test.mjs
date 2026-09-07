import assert from 'node:assert/strict';
import test from 'node:test';
import { readFile } from 'node:fs/promises';

import {
  mockClassify,
  validateColorClassification,
} from '../server/color-classification.mjs';

const request = {
  batchId: 'cls-20260904-0123456789',
  images: [
    { id: 'img-001', originalName: 'black-front.png' },
    { id: 'img-002', originalName: 'black-back.png' },
    { id: 'img-003', originalName: 'red-front.png' },
    { id: 'img-004', originalName: 'detail.png' },
  ],
};

test('classification output schema stays within the supported structured-output subset', async () => {
  const schema = JSON.parse(
    await readFile(
      new URL('../contracts/color-classification.schema.json', import.meta.url),
      'utf8',
    ),
  );
  const supported = new Set([
    '$schema',
    'type',
    'additionalProperties',
    'required',
    'properties',
    'items',
    'enum',
  ]);
  function check(node) {
    for (const key of Object.keys(node))
      assert.ok(supported.has(key), `Unsupported keyword: ${key}`);
    if (node.type === 'object') {
      assert.equal(node.additionalProperties, false);
      assert.deepEqual(
        [...node.required].sort(),
        Object.keys(node.properties).sort(),
      );
      Object.values(node.properties).forEach(check);
    }
    if (node.items) check(node.items);
  }
  check(schema);
});

test('duplicate image IDs within a color remain rejected locally without schema uniqueItems', () => {
  assert.throws(
    () =>
      validateColorClassification(request, {
        groups: [{ colorName: 'Black', imageIds: ['img-001', 'img-001'] }],
      }),
    /重复归类/,
  );
});

test('duplicate maximum-length color names get distinct bounded names', () => {
  const name = 'a'.repeat(50);
  const result = validateColorClassification(request, {
    groups: [
      { colorName: name, imageIds: ['img-001'] },
      { colorName: name, imageIds: ['img-002'] },
    ],
  });
  assert.equal(result.groups[0].colorName, name);
  assert.equal(result.groups[1].colorName.length, 50);
  assert.ok(result.groups[1].colorName.endsWith(' 2'));
});

test('classification result enforces one assignment per image and preserves missing images', () => {
  const result = validateColorClassification(request, {
    batchId: 'wrong-batch-is-normalized',
    groups: [
      {
        colorName: 'Black',
        colorNameZh: '黑色',
        confidence: 'high',
        imageIds: ['img-001', 'img-002'],
        reason: '服装主体为黑色',
      },
    ],
    unassigned: [{ imageId: 'img-003', reason: '光线偏色' }],
    warnings: [],
  });

  assert.equal(result.batchId, request.batchId);
  assert.deepEqual(result.groups[0].imageIds, ['img-001', 'img-002']);
  assert.deepEqual(
    result.unassigned.map((item) => item.imageId),
    ['img-003', 'img-004'],
  );
  assert.match(result.unassigned[1].reason, /未返回/);
});

test('classification rejects duplicate and unknown image assignments', () => {
  assert.throws(
    () =>
      validateColorClassification(request, {
        groups: [
          {
            colorName: 'Black',
            colorNameZh: '黑色',
            confidence: 'high',
            imageIds: ['img-001'],
            reason: '黑色',
          },
        ],
        unassigned: [{ imageId: 'img-001', reason: '重复' }],
      }),
    /重复归类/,
  );
  assert.throws(
    () =>
      validateColorClassification(request, {
        groups: [
          {
            colorName: 'Blue',
            colorNameZh: '蓝色',
            confidence: 'high',
            imageIds: ['img-999'],
            reason: '蓝色',
          },
        ],
      }),
    /未知图片/,
  );
});

test('mock classifier groups multiple views by filename color token', () => {
  const result = mockClassify(request);
  const black = result.groups.find((group) => group.colorName === 'Black');
  const red = result.groups.find((group) => group.colorName === 'Red');

  assert.deepEqual(black.imageIds, ['img-001', 'img-002']);
  assert.deepEqual(red.imageIds, ['img-003']);
  assert.deepEqual(
    result.unassigned.map((item) => item.imageId),
    ['img-004'],
  );
});
