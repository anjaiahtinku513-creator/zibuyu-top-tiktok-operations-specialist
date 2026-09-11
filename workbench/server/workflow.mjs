export const WORKFLOW_STAGES = [
  {
    key: 'intake_validation',
    label: '资料校验',
    baseSeconds: 45,
    perVariantSeconds: 5,
    weight: 5,
  },
  {
    key: 'product_analysis',
    label: '商品分析',
    baseSeconds: 240,
    perVariantSeconds: 20,
    weight: 10,
  },
  {
    key: 'three_view_generation',
    label: '生成三视图',
    baseSeconds: 60,
    perVariantSeconds: 240,
    weight: 20,
  },
  {
    key: 'three_view_quality',
    label: '三视图质检',
    baseSeconds: 45,
    perVariantSeconds: 100,
    weight: 10,
  },
  {
    key: 'script_and_voice',
    label: '脚本与配音',
    baseSeconds: 240,
    perVariantSeconds: 120,
    weight: 15,
  },
  {
    key: 'preflight_validation',
    label: '生成前校验',
    baseSeconds: 120,
    perVariantSeconds: 30,
    weight: 10,
  },
  {
    key: 'paid_approval',
    label: '等待付费确认',
    baseSeconds: 0,
    perVariantSeconds: 0,
    weight: 0,
  },
  {
    key: 'popboom_generation',
    label: 'PopBoom 生成',
    baseSeconds: 900,
    perVariantSeconds: 20,
    weight: 20,
  },
  {
    key: 'quality_and_delivery',
    label: '成片质检与交付',
    baseSeconds: 180,
    perVariantSeconds: 150,
    weight: 10,
  },
];

export const TOTAL_WEIGHT = WORKFLOW_STAGES.reduce(
  (total, stage) => total + stage.weight,
  0,
);

export function getStage(stageKey) {
  const index = WORKFLOW_STAGES.findIndex((stage) => stage.key === stageKey);
  if (index === -1) {
    throw new Error(`Unknown workflow stage: ${stageKey}`);
  }
  return { ...WORKFLOW_STAGES[index], index };
}

export function getProgress(stageKey, state) {
  if (state === 'delivered') return 100;
  const stage = getStage(stageKey);
  const completedWeight = WORKFLOW_STAGES.slice(0, stage.index).reduce(
    (total, item) => total + item.weight,
    0,
  );
  return Math.round((completedWeight / TOTAL_WEIGHT) * 100);
}

export function estimateRemainingSeconds(stageKey, variantCount) {
  const stage = getStage(stageKey);
  const safeCount = Math.max(1, Number(variantCount) || 1);
  return WORKFLOW_STAGES.slice(stage.index).reduce((total, item) => {
    if (item.key === 'paid_approval') return total;
    return total + item.baseSeconds + item.perVariantSeconds * safeCount;
  }, 0);
}

export function etaLabel(seconds, waitingForApproval = false) {
  if (seconds == null) return waitingForApproval ? '等待你确认' : '--';
  if (seconds === 0 && !waitingForApproval) return '已完成';
  const minutes = Math.max(1, Math.ceil(seconds / 60));
  if (minutes < 60) {
    return `约 ${minutes} 分钟${waitingForApproval ? ' + 待确认' : ''}`;
  }
  const hours = Math.floor(minutes / 60);
  const rest = minutes % 60;
  return `约 ${hours} 小时${rest ? ` ${rest} 分钟` : ''}${
    waitingForApproval ? ' + 待确认' : ''
  }`;
}
