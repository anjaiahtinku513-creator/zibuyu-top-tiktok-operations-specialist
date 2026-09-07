import type { RunStatus } from './zibuyu';

export type PublishAccount = {
  code: string;
  username: string;
  market: string;
  timezone: string;
};
export type PublishState =
  | 'production_not_ready'
  | 'production_only'
  | 'awaiting_delivery_review'
  | 'awaiting_publish_input'
  | 'preparing'
  | 'blocked'
  | 'awaiting_publish_confirmation'
  | 'submitting'
  | 'reconciling'
  | 'receipt_received'
  | 'scheduled'
  | 'published'
  | 'publish_failed'
  | 'submission_unknown';
export type PublishStatus = {
  state: PublishState;
  note: string;
  updatedAt: string;
};
export type PublishIntent = {
  accountCode: string;
  pid: string;
  date: string;
  mode: 'schedule' | 'production_only';
  spreadAcrossDays?: boolean;
};
export type DeliveryItem = {
  variant_id: string;
  color_name: string;
  record_id: number;
  video_url: string | null;
  local_video_path: string | null;
  copy_ready_caption: string;
  creative_quality_verdict: string;
  model_preset: string;
};
export type PublishRow = {
  actionId: string;
  variantId: string;
  recordId: number;
  colorName: string;
  accountCode: string;
  username: string;
  timezone: string;
  localDate: string;
  localTime: string;
  pid: string;
  captionFinal: string;
  request: {
    channel_id: string;
    video_url: string;
    video_title: string;
    product_id: string;
    product_title: string;
    scheduled_time: string;
    run_precheck: boolean;
    is_ai_generated: boolean;
    cover_timestamp_ms: number;
  };
};
export type PublishAction = PublishRow & {
  state: string;
  scheduleId?: string | number | null;
  logId?: string | number | null;
  observedAt?: string;
};
export type PublishManifest = {
  manifestHash: string;
  captionMapping: {
    field: string;
    evidenceSource: string;
    evidenceExcerpt: string;
    maxLength: number;
  };
  rows: PublishRow[];
};
export type PublishingDetail = {
  runId: string;
  production: RunStatus;
  intent: PublishIntent | null;
  snapshot: { handoff_sha256: string; items: DeliveryItem[] } | null;
  review: { handoffHash: string; reviewedAt: string } | null;
  manifest: PublishManifest | null;
  approval: { manifestHash: string; approvedAt: string } | null;
  actions: PublishAction[];
  status: PublishStatus;
};
export type PublishOverview = {
  accounts: PublishAccount[];
  captionTransport: { verified: boolean; reason: string };
  batches: {
    runId: string;
    sku: string;
    status: PublishStatus;
    actions: PublishAction[];
  }[];
};
export const publishLabel = (state: string) =>
  ({
    production_not_ready: '等待成片',
    production_only: '仅交付',
    awaiting_delivery_review: '待核对交付',
    awaiting_publish_input: '待准备排期',
    preparing: '准备中',
    blocked: '待处理',
    awaiting_publish_confirmation: '待你确认',
    submitting: '提交中',
    reconciling: '核对中',
    receipt_received: '回执待确认',
    scheduled: '已排期',
    published: '已发布',
    publish_failed: '发布失败',
    submission_unknown: '结果待核对',
    claimed: '已记录提交范围',
    dispatching: '正在提交',
  })[state] || state;
