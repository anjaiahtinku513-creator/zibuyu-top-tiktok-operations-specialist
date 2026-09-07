const CONFIDENCE_VALUES = new Set(['high', 'medium', 'low']);

const MOCK_COLORS = [
  {
    colorName: 'Army Green',
    colorNameZh: '军绿色',
    tokens: ['army green', 'army-green', 'army_green', '军绿色', '军绿'],
  },
  {
    colorName: 'Navy Blue',
    colorNameZh: '藏青色',
    tokens: ['navy blue', 'navy-blue', 'navy_blue', 'navy', '藏青', '深蓝'],
  },
  {
    colorName: 'Burgundy',
    colorNameZh: '酒红色',
    tokens: ['burgundy', 'wine red', 'wine-red', '酒红'],
  },
  {
    colorName: 'Apricot',
    colorNameZh: '杏色',
    tokens: ['apricot', '杏色', '杏'],
  },
  { colorName: 'Khaki', colorNameZh: '卡其色', tokens: ['khaki', '卡其'] },
  { colorName: 'Beige', colorNameZh: '米色', tokens: ['beige', '米色'] },
  { colorName: 'Coffee', colorNameZh: '咖啡色', tokens: ['coffee', '咖啡'] },
  { colorName: 'Brown', colorNameZh: '棕色', tokens: ['brown', '棕色', '棕'] },
  {
    colorName: 'Purple',
    colorNameZh: '紫色',
    tokens: ['purple', 'violet', '紫色', '紫'],
  },
  {
    colorName: 'Orange',
    colorNameZh: '橙色',
    tokens: ['orange', '橙色', '橙'],
  },
  {
    colorName: 'Yellow',
    colorNameZh: '黄色',
    tokens: ['yellow', '黄色', '黄'],
  },
  {
    colorName: 'Gray',
    colorNameZh: '灰色',
    tokens: ['gray', 'grey', '灰色', '灰'],
  },
  { colorName: 'Green', colorNameZh: '绿色', tokens: ['green', '绿色', '绿'] },
  {
    colorName: 'Pink',
    colorNameZh: '粉色',
    tokens: ['pink', 'rose', '粉色', '粉'],
  },
  { colorName: 'Red', colorNameZh: '红色', tokens: ['red', '红色', '红'] },
  { colorName: 'Blue', colorNameZh: '蓝色', tokens: ['blue', '蓝色', '蓝'] },
  {
    colorName: 'Cream',
    colorNameZh: '奶油色',
    tokens: ['cream', '奶油色', '奶油'],
  },
  {
    colorName: 'Ivory',
    colorNameZh: '象牙白',
    tokens: ['ivory', '象牙白', '象牙'],
  },
  {
    colorName: 'White',
    colorNameZh: '白色',
    tokens: ['white', '白色', '白'],
  },
  { colorName: 'Black', colorNameZh: '黑色', tokens: ['black', '黑色', '黑'] },
];

function cleanText(value, maxLength, fallback = '') {
  const cleaned = String(value ?? '')
    .trim()
    .slice(0, maxLength);
  return cleaned || fallback;
}

function uniqueColorName(baseName, usedNames) {
  const base = cleanText(baseName, 50, 'Unknown');
  let candidate = base;
  let suffix = 2;
  while (usedNames.has(candidate.toLocaleLowerCase())) {
    const ending = ` ${suffix}`;
    candidate = `${base.slice(0, 50 - ending.length)}${ending}`;
    suffix += 1;
  }
  usedNames.add(candidate.toLocaleLowerCase());
  return candidate;
}

export function validateColorClassification(request, rawResult) {
  if (!request?.batchId || !Array.isArray(request.images)) {
    throw new Error('颜色识别请求无效');
  }
  if (!rawResult || typeof rawResult !== 'object') {
    throw new Error('颜色识别结果不是对象');
  }

  const validIds = new Set(request.images.map((image) => image.id));
  const seen = new Set();
  const usedNames = new Set();
  const groups = [];

  for (const rawGroup of Array.isArray(rawResult.groups)
    ? rawResult.groups
    : []) {
    const imageIds = Array.isArray(rawGroup?.imageIds)
      ? rawGroup.imageIds.map(String)
      : [];
    if (!imageIds.length) continue;
    for (const imageId of imageIds) {
      if (!validIds.has(imageId))
        throw new Error(`颜色识别返回了未知图片：${imageId}`);
      if (seen.has(imageId)) throw new Error(`图片被重复归类：${imageId}`);
      seen.add(imageId);
    }

    const colorName = uniqueColorName(rawGroup?.colorName, usedNames);
    groups.push({
      id: `color-${String(groups.length + 1).padStart(3, '0')}`,
      colorName,
      colorNameZh: cleanText(rawGroup?.colorNameZh, 50, colorName),
      confidence: CONFIDENCE_VALUES.has(rawGroup?.confidence)
        ? rawGroup.confidence
        : 'low',
      imageIds,
      reason: cleanText(rawGroup?.reason, 500, '根据服装主体颜色归组'),
    });
  }

  const unassigned = [];
  for (const rawItem of Array.isArray(rawResult.unassigned)
    ? rawResult.unassigned
    : []) {
    const imageId = String(rawItem?.imageId ?? '');
    if (!validIds.has(imageId))
      throw new Error(`颜色识别返回了未知图片：${imageId}`);
    if (seen.has(imageId)) throw new Error(`图片被重复归类：${imageId}`);
    seen.add(imageId);
    unassigned.push({
      imageId,
      reason: cleanText(rawItem?.reason, 500, '服装颜色无法可靠判断'),
    });
  }

  for (const image of request.images) {
    if (seen.has(image.id)) continue;
    unassigned.push({
      imageId: image.id,
      reason: 'Codex 未返回该图片的归类结果，已转入待确认',
    });
  }

  return {
    batchId: request.batchId,
    groups,
    unassigned,
    warnings: (Array.isArray(rawResult.warnings) ? rawResult.warnings : [])
      .map((warning) => cleanText(warning, 500))
      .filter(Boolean),
  };
}

export function mockClassify(request) {
  const grouped = new Map();
  const unassigned = [];

  for (const image of request.images) {
    const searchable = String(image.originalName ?? '')
      .normalize('NFKC')
      .toLocaleLowerCase();
    const color = MOCK_COLORS.find((candidate) =>
      candidate.tokens.some((token) => searchable.includes(token)),
    );
    if (!color) {
      unassigned.push({
        imageId: image.id,
        reason: '验收模式无法从文件名识别颜色',
      });
      continue;
    }
    const current = grouped.get(color.colorName) ?? {
      colorName: color.colorName,
      colorNameZh: color.colorNameZh,
      confidence: 'high',
      imageIds: [],
      reason: '验收模式按文件名中的颜色词归组',
    };
    current.imageIds.push(image.id);
    grouped.set(color.colorName, current);
  }

  return validateColorClassification(request, {
    batchId: request.batchId,
    groups: [...grouped.values()],
    unassigned,
    warnings: [],
  });
}
