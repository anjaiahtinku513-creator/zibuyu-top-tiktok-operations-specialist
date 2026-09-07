import path from 'node:path';

export const MODEL_PRESETS = {
  美1: {
    market: '美国',
    marketCode: 'US',
    locale: 'en-US',
    size: 'S',
    assetId: 'asset-20260810141830-tbcnd',
    fitStats: `5'6" / 115 lb / Size S`,
  },
  美2: {
    market: '美国',
    marketCode: 'US',
    locale: 'en-US',
    size: '2XL',
    assetId: 'asset-20260819170810-kbchw',
    fitStats: `5'6" / 200lb / Size 2XL`,
  },
  美3: {
    market: '美国',
    marketCode: 'US',
    locale: 'en-US',
    size: '2XL',
    assetId: 'asset-20260819170855-8927v',
    fitStats: `5'6" / 200lb / Size 2XL`,
  },
  德1: {
    market: '德国',
    marketCode: 'DE',
    locale: 'de-DE',
    size: 'S',
    assetId: 'asset-20260703091345-n2q24',
    fitStats: '168 cm / 52 kg / Größe S',
  },
  德2: {
    market: '德国',
    marketCode: 'DE',
    locale: 'de-DE',
    size: 'S',
    assetId: 'asset-20260703091441-5rfz2',
    fitStats: '168 cm / 52 kg / Größe S',
  },
  德3: {
    market: '德国',
    marketCode: 'DE',
    locale: 'de-DE',
    size: '2XL',
    assetId: 'asset-20260819170940-ltnxb',
    fitStats: '168 cm / 90 kg / Größe 2XL',
  },
};

const SKU_PATTERN = /^[\p{L}\p{N}][\p{L}\p{N}._-]{0,63}$/u;
const VARIANT_ID_PATTERN = /^[a-zA-Z0-9-]{8,64}$/;
const CLASSIFICATION_BATCH_PATTERN = /^cls-\d{8}-[0-9a-f]{10}$/;
const CONFIDENCE_VALUES = new Set(['high', 'medium', 'low']);

function cleanText(value, maxLength) {
  return String(value ?? '')
    .trim()
    .slice(0, maxLength);
}

function validateUrl(value) {
  if (!value) return '';
  let parsed;
  try {
    parsed = new URL(value);
  } catch {
    throw new Error('Amazon 链接格式不正确');
  }
  if (!['http:', 'https:'].includes(parsed.protocol)) {
    throw new Error('Amazon 链接必须使用 http 或 https');
  }
  return parsed.toString();
}

export function parseIntakePayload(raw) {
  let payload;
  try {
    payload = typeof raw === 'string' ? JSON.parse(raw) : raw;
  } catch {
    throw new Error('制作单参数不是有效 JSON');
  }

  const sku = cleanText(payload?.sku, 64);
  if (!SKU_PATTERN.test(sku)) {
    throw new Error('货号需为 1-64 位字母、数字、点、下划线或连字符');
  }

  const modelMode = payload?.modelMode === 'custom' ? 'custom' : 'preset';
  let model;
  if (modelMode === 'custom') {
    const name = cleanText(payload?.customModelName, 80);
    const marketCode =
      payload?.customMarket === 'US'
        ? 'US'
        : payload?.customMarket === 'DE'
          ? 'DE'
          : '';
    if (!name) throw new Error('请填写 PopBoom 自定义模特名称');
    if (!marketCode) throw new Error('请选择自定义模特的市场');
    model = {
      mode: 'custom',
      name,
      market: marketCode === 'US' ? '美国' : '德国',
      marketCode,
      locale: marketCode === 'US' ? 'en-US' : 'de-DE',
      referenceImage: null,
    };
  } else {
    const preset = cleanText(payload?.modelPreset, 8);
    const selected = MODEL_PRESETS[preset];
    if (!selected) throw new Error('请选择有效的固定模特');
    model = { mode: 'preset', preset, ...selected };
  }

  if (!Array.isArray(payload?.variants) || payload.variants.length < 1) {
    throw new Error('至少需要一个颜色');
  }
  if (payload.variants.length > 24) {
    throw new Error('单个任务最多支持 24 个颜色');
  }

  const classificationBatchId = cleanText(payload?.classificationBatchId, 64);
  if (
    classificationBatchId &&
    !CLASSIFICATION_BATCH_PATTERN.test(classificationBatchId)
  ) {
    throw new Error('颜色识别批次无效');
  }

  const names = new Set();
  const ids = new Set();
  const variants = payload.variants.map((item, index) => {
    const id = cleanText(item?.id, 64);
    const name = cleanText(item?.name, 50);
    if (!VARIANT_ID_PATTERN.test(id) || ids.has(id)) {
      throw new Error(`颜色 ${index + 1} 的内部标识无效`);
    }
    if (!name) throw new Error(`请填写颜色 ${index + 1} 的名称`);
    const nameKey = name.toLocaleLowerCase();
    if (names.has(nameKey)) throw new Error(`颜色名称重复：${name}`);
    ids.add(id);
    names.add(nameKey);
    return {
      id,
      name,
      colorNameZh: cleanText(item?.colorNameZh, 50),
      confidence: CONFIDENCE_VALUES.has(item?.confidence)
        ? item.confidence
        : null,
      classificationReason: cleanText(item?.classificationReason, 500),
      autoGrouped: item?.autoGrouped === true,
      images: [],
    };
  });

  return {
    sku,
    classificationBatchId: classificationBatchId || null,
    amazonUrl: validateUrl(cleanText(payload?.amazonUrl, 1000)),
    notes: cleanText(payload?.notes, 2000),
    model,
    variants,
    output: {
      durationSeconds: 15,
      aspectRatio: '9:16',
      resolution: '720p',
      audio: true,
      watermark: false,
      platformBrand: '拓展平台',
    },
  };
}

export function parseModelBatchPayload(raw) {
  let payload;
  try {
    payload = typeof raw === 'string' ? JSON.parse(raw) : raw;
  } catch {
    throw new Error('制作单参数不是有效 JSON');
  }
  if (payload?.modelPresets === undefined) {
    const intake = parseIntakePayload(payload);
    return {
      ...intake,
      models: [intake.model],
      sourceUrls: { [intake.model.marketCode]: intake.amazonUrl },
    };
  }
  if (payload.modelMode === 'custom')
    throw new Error('自定义模特请单独创建制作单');
  const names = payload.modelPresets;
  if (
    !Array.isArray(names) ||
    names.length < 1 ||
    names.length > 6 ||
    names.some(
      (name) => typeof name !== 'string' || !Object.hasOwn(MODEL_PRESETS, name),
    )
  ) {
    throw new Error('请选择 1–6 位有效固定模特');
  }
  if (new Set(names).size !== names.length) throw new Error('不能重复选择模特');
  if (payload.modelPreset !== undefined && payload.modelPreset !== names[0])
    throw new Error('单模特与多模特参数不一致');
  const markets = [
    ...new Set(names.map((name) => MODEL_PRESETS[name].marketCode)),
  ];
  if (markets.length > 1 && payload.amazonUrl?.trim())
    throw new Error('跨市场制作请分别填写美国和德国商品链接');
  if (
    payload.amazonUrls !== undefined &&
    (!payload.amazonUrls ||
      typeof payload.amazonUrls !== 'object' ||
      Array.isArray(payload.amazonUrls))
  )
    throw new Error('分市场商品链接格式无效');
  const sourceUrls = Object.fromEntries(
    markets.map((market) => [
      market,
      validateUrl(
        cleanText(
          payload.amazonUrls?.[market] ??
            (markets.length === 1 ? payload.amazonUrl : ''),
          1000,
        ),
      ),
    ]),
  );
  const intakes = names.map((name) =>
    parseIntakePayload({
      ...payload,
      modelPreset: name,
      amazonUrl: sourceUrls[MODEL_PRESETS[name].marketCode],
    }),
  );
  return {
    ...intakes[0],
    models: intakes.map((item) => item.model),
    sourceUrls,
  };
}

export function detectImageType(buffer) {
  if (
    buffer.length >= 8 &&
    buffer
      .subarray(0, 8)
      .equals(Buffer.from([0x89, 0x50, 0x4e, 0x47, 0x0d, 0x0a, 0x1a, 0x0a]))
  ) {
    return { extension: '.png', mimeType: 'image/png' };
  }
  if (
    buffer.length >= 3 &&
    buffer[0] === 0xff &&
    buffer[1] === 0xd8 &&
    buffer[2] === 0xff
  ) {
    return { extension: '.jpg', mimeType: 'image/jpeg' };
  }
  if (
    buffer.length >= 12 &&
    buffer.toString('ascii', 0, 4) === 'RIFF' &&
    buffer.toString('ascii', 8, 12) === 'WEBP'
  ) {
    return { extension: '.webp', mimeType: 'image/webp' };
  }
  throw new Error('仅支持真实的 PNG、JPEG 或 WebP 图片');
}

export function safeFileStem(filename) {
  const stem = path.basename(
    String(filename ?? 'image'),
    path.extname(String(filename ?? '')),
  );
  const cleaned = stem
    .normalize('NFKC')
    .replace(/[^\p{L}\p{N}._-]+/gu, '-')
    .replace(/^-+|-+$/g, '')
    .slice(0, 60);
  return cleaned || 'image';
}
