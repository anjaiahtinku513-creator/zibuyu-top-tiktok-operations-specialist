export const MODELS = [
  {
    id: '美1',
    market: '美国',
    marketCode: 'US',
    locale: 'en-US',
    size: 'S',
    tone: '轻盈自然',
    fitStats: `5'6" / 115 lb / Size S`,
  },
  {
    id: '美2',
    market: '美国',
    marketCode: 'US',
    locale: 'en-US',
    size: '2XL',
    tone: '亲和真实',
    fitStats: `5'6" / 200lb / Size 2XL`,
  },
  {
    id: '美3',
    market: '美国',
    marketCode: 'US',
    locale: 'en-US',
    size: '2XL',
    tone: '明快有力',
    fitStats: `5'6" / 200lb / Size 2XL`,
  },
  {
    id: '德1',
    market: '德国',
    marketCode: 'DE',
    locale: 'de-DE',
    size: 'S',
    tone: '克制日常',
    fitStats: '168 cm / 52 kg / Größe S',
  },
  {
    id: '德2',
    market: '德国',
    marketCode: 'DE',
    locale: 'de-DE',
    size: 'S',
    tone: '利落通勤',
    fitStats: '168 cm / 52 kg / Größe S',
  },
  {
    id: '德3',
    market: '德国',
    marketCode: 'DE',
    locale: 'de-DE',
    size: '2XL',
    tone: '从容可信',
    fitStats: '168 cm / 90 kg / Größe 2XL',
  },
] as const;

export const WORKFLOW_STAGES = [
  { key: 'intake_validation', label: '资料校验' },
  { key: 'product_analysis', label: '商品分析' },
  { key: 'three_view_generation', label: '生成三视图' },
  { key: 'three_view_quality', label: '三视图质检' },
  { key: 'script_and_voice', label: '脚本与配音' },
  { key: 'preflight_validation', label: '生成前校验' },
  { key: 'paid_approval', label: '等待付费确认' },
  { key: 'popboom_generation', label: 'PopBoom 生成' },
  { key: 'quality_and_delivery', label: '成片质检与交付' },
] as const;

export type RunState =
  | 'queued'
  | 'running'
  | 'submitting'
  | 'awaiting_paid_approval'
  | 'needs_input'
  | 'needs_review'
  | 'blocked'
  | 'failed'
  | 'delivered'
  | 'submission_unknown';

export type RunStatus = {
  runId: string;
  sku: string;
  modelPreset: string;
  modelBatchId?: string | null;
  modelBatchError?: string | null;
  state: RunState;
  stageKey: string;
  stageLabel: string;
  stageIndex: number;
  totalStages: number;
  currentTask: string;
  note: string;
  progress: number;
  estimatedRemainingSeconds: number | null;
  etaLabel: string;
  variantCount: number;
  startedAt: string;
  stageStartedAt: string;
  updatedAt: string;
  completedAt: string | null;
  sessionId: string | null;
  execution?: {
    source: string;
    phase: string;
    state: string;
    pid: number | null;
    startedAt: string;
    lastEventAt?: string | null;
    currentTask?: string | null;
  } | null;
  preparation?: {
    mode: string;
    state: string;
    batchId?: string;
    error?: string;
    shared?: {
      sessionId: string | null;
      stageLabel: string;
      state: string;
      summary?: string | null;
    } | null;
  } | null;
  error: string | null;
};

export type StoredImage = {
  originalName: string;
  relativePath: string;
  mimeType: string;
  size: number;
  sha256: string;
};

export type ClassificationConfidence = 'high' | 'medium' | 'low';

export type ClassificationGroup = {
  id: string;
  colorName: string;
  colorNameZh: string;
  confidence: ClassificationConfidence;
  imageIds: string[];
  reason: string;
};

export type ClassificationResult = {
  batchId: string;
  groups: ClassificationGroup[];
  unassigned: Array<{
    imageId: string;
    reason: string;
  }>;
  warnings: string[];
};

export type ClassificationStatus = {
  batchId: string;
  state: 'queued' | 'running' | 'completed' | 'failed';
  currentTask: string;
  note: string;
  imageCount: number;
  createdAt: string;
  startedAt: string | null;
  updatedAt: string;
  completedAt: string | null;
  error: string | null;
};

export type ClassificationDetail = {
  request: {
    batchId: string;
    sku: string;
    createdAt: string;
    images: Array<StoredImage & { id: string }>;
  };
  status: ClassificationStatus;
  result: ClassificationResult | null;
};

export type Intake = {
  runId: string;
  sku: string;
  modelBatchId?: string;
  classificationBatchId: string | null;
  amazonUrl: string;
  notes: string;
  createdAt: string;
  execution?: {
    model: string;
    reasoningEffort: string;
    conversationPolicy: string;
  };
  model:
    | {
        mode: 'preset';
        preset: string;
        market: string;
        marketCode: string;
        locale: string;
        size: string;
        fitStats: string;
      }
    | {
        mode: 'custom';
        name: string;
        market: string;
        marketCode: string;
        locale: string;
        referenceImage: StoredImage;
      };
  variants: Array<{
    id: string;
    name: string;
    colorNameZh: string;
    confidence: ClassificationConfidence | null;
    classificationReason: string;
    autoGrouped: boolean;
    images: StoredImage[];
  }>;
  output: {
    durationSeconds: number;
    aspectRatio: string;
    resolution: string;
  };
};

export type Artifact = {
  kind: 'image' | 'video' | 'json' | 'text' | 'link' | 'other';
  label: string;
  path: string;
};

export type PhaseResult = {
  runId: string;
  phase: 'prepare' | 'paid';
  outcome: string;
  stageKey: string;
  currentTask: string;
  summary: string;
  nextAction: string;
  missingInputs: string[];
  artifacts: Artifact[];
};

export type RunEvent = {
  type: string;
  at: string;
  state: RunState;
  stageKey: string;
  stageLabel: string;
  currentTask: string;
  progress: number;
  etaLabel: string;
  note: string;
};

export type Approval = {
  approvedAt: string;
  scope: string;
  variantIds: string[];
  authorizationFingerprint: string;
};

export type RunDetail = {
  intake: Intake;
  status: RunStatus;
  prepareResult: PhaseResult | null;
  paidResult: PhaseResult | null;
  approval: Approval | null;
  events: RunEvent[];
  usage?: UsageSummary;
  classificationUsage?: UsageSummary | null;
  modelBatch?: ModelBatch | null;
  modelBatchError?: string | null;
};

export function isPaidApprovalReady(detail: RunDetail) {
  const { intake, status, prepareResult, approval, modelBatchError } = detail;
  return Boolean(
    !approval &&
    !modelBatchError &&
    status.state === 'awaiting_paid_approval' &&
    status.execution?.state !== 'running' &&
    status.execution?.state !== 'failed' &&
    prepareResult?.runId === intake.runId &&
    prepareResult.phase === 'prepare' &&
    prepareResult.outcome === 'awaiting_paid_approval' &&
    prepareResult.artifacts.some(
      (artifact) => artifact.path && !/^https?:\/\//i.test(artifact.path),
    ),
  );
}

export type ModelBatch = {
  batchId: string;
  modelCount: number;
  colorCount: number;
  plannedVideoCount: number;
  allDelivered: boolean;
  runs: Array<{
    runId: string;
    modelPreset: string;
    marketCode: string;
    locale: string;
    status: RunStatus | null;
  }>;
};

export type UsageAttempt = {
  attemptId: string;
  phase: string;
  model: string;
  reasoningEffort: string;
  state: 'running' | 'completed' | 'failed';
  startedAt: string;
  durationMs: number | null;
  inputTokens: number | null;
  cachedInputTokens: number | null;
  outputTokens: number | null;
  reasoningTokens: number | null;
  observedToolCalls: number | null;
  usageComplete: boolean;
  reportedUsage: Array<{
    eventIndex: number;
    inputTokens: number | null;
    cachedInputTokens: number | null;
    outputTokens: number | null;
  }>;
};

export type UsageSummary = {
  attempts: UsageAttempt[];
  totals: Pick<
    UsageAttempt,
    | 'inputTokens'
    | 'cachedInputTokens'
    | 'outputTokens'
    | 'reasoningTokens'
    | 'durationMs'
    | 'observedToolCalls'
  >;
  attemptCount: number;
  unreadableAttempts: number;
  usageComplete: boolean;
  subagentUsageCoverage: 'unverified';
};

export type Health = {
  preparationConcurrency?: number;
  activePreparationJobs?: number;
  waitingPreparationJobs?: number;
  activeSharedJobs?: number;
  activePaidJobs?: number;
  waitingPaidJobs?: number;
  workflowVersion?: string;
  ok: boolean;
  codexAvailable: boolean;
  mode: 'real' | 'mock';
  codexModel: string;
  reasoningEffort: string;
  conversationPolicy: string;
  workspaceRoot: string;
  runsRoot: string;
  queuedJobs: number;
};

export function runStateLabel(state: RunState) {
  return {
    queued: '排队中',
    running: '执行中',
    submitting: '提交中',
    awaiting_paid_approval: '待付费确认',
    needs_input: '待补资料',
    needs_review: '待验收',
    blocked: '已暂停',
    failed: '执行失败',
    delivered: '已交付',
    submission_unknown: '提交状态待核对',
  }[state];
}

export function isTerminalState(state: RunState) {
  return [
    'needs_review',
    'needs_input',
    'blocked',
    'failed',
    'delivered',
    'submission_unknown',
  ].includes(state);
}

export function formatTime(value?: string | null) {
  if (!value) return '--';
  return new Intl.DateTimeFormat('zh-CN', {
    month: '2-digit',
    day: '2-digit',
    hour: '2-digit',
    minute: '2-digit',
    hour12: false,
  }).format(new Date(value));
}
