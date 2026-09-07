'use client';

import { useState } from 'react';
import Image from 'next/image';
import {
  AlertTriangle,
  ArrowLeft,
  Check,
  CheckCircle2,
  ExternalLink,
  FileJson,
  FileText,
  Image as ImageIcon,
  RefreshCw,
  ShieldAlert,
  Video,
} from 'lucide-react';

import {
  AlertDialog,
  AlertDialogAction,
  AlertDialogCancel,
  AlertDialogContent,
  AlertDialogDescription,
  AlertDialogFooter,
  AlertDialogHeader,
  AlertDialogMedia,
  AlertDialogTitle,
} from '@/components/ui/alert-dialog';
import { Badge } from '@/components/ui/badge';
import { Button } from '@/components/ui/button';
import { Progress } from '@/components/ui/progress';
import { Spinner } from '@/components/ui/spinner';
import { UsageSummaryCard } from './usage-summary';
import {
  Artifact,
  formatTime,
  RunDetail,
  RunState,
  runStateLabel,
  WORKFLOW_STAGES,
} from '@/lib/zibuyu';

export function TaskDetailView({
  detail,
  loading,
  onBack,
  onRefresh,
  onApprove,
  onPublish,
  onOpenRun,
}: {
  detail: RunDetail | null;
  loading: boolean;
  onBack: () => void;
  onRefresh: () => void;
  onApprove: (variantIds: string[]) => Promise<void>;
  onPublish: () => void;
  onOpenRun: (runId: string) => void;
}) {
  const [approvalOpen, setApprovalOpen] = useState(false);
  const [approving, setApproving] = useState(false);
  const [approvalError, setApprovalError] = useState('');

  if (!detail) {
    return (
      <div className="grid min-h-72 place-items-center">
        <div className="text-center text-sm text-muted-foreground">
          {loading ? <Spinner className="mx-auto mb-3" /> : null}
          {loading ? '正在读取任务' : '未找到任务'}
        </div>
      </div>
    );
  }

  const { intake, status, prepareResult, paidResult, approval, events } =
    detail;
  const artifacts = [
    ...(prepareResult?.artifacts ?? []),
    ...(paidResult?.artifacts ?? []),
  ];
  const modelName =
    intake.model.mode === 'preset' ? intake.model.preset : intake.model.name;
  const missingInputs = paidResult?.missingInputs?.length
    ? paidResult.missingInputs
    : (prepareResult?.missingInputs ?? []);

  async function approve() {
    setApprovalError('');
    setApproving(true);
    try {
      await onApprove(intake.variants.map((variant) => variant.id));
      setApprovalOpen(false);
    } catch (error) {
      setApprovalError(error instanceof Error ? error.message : '授权提交失败');
    } finally {
      setApproving(false);
    }
  }

  return (
    <div className="mx-auto max-w-7xl">
      <div className="mb-5 flex flex-wrap items-center justify-between gap-3 border-b border-border pb-5">
        <div className="flex min-w-0 items-center gap-3">
          <Button
            variant="ghost"
            size="icon"
            onClick={onBack}
            aria-label="返回任务队列"
            title="返回任务队列"
          >
            <ArrowLeft />
          </Button>
          <div className="min-w-0">
            <p className="text-sm text-muted-foreground">{intake.runId}</p>
            <h1 className="truncate text-[30px] font-semibold tracking-tight">
              {intake.sku}
            </h1>
          </div>
        </div>
        <div className="flex items-center gap-2">
          <StatusBadge state={status.state} />
          <Button
            variant="outline"
            size="icon"
            onClick={onRefresh}
            disabled={loading}
            aria-label="刷新任务"
            title="刷新任务"
          >
            <RefreshCw className={loading ? 'animate-spin' : ''} />
          </Button>
        </div>
      </div>

      {status.state === 'delivered' ? (
        <div className="surface-card mb-5 flex flex-wrap items-center justify-between gap-4 p-5">
          <div>
            <p className="font-semibold">
              {detail.modelBatch && !detail.modelBatch.allDelivered
                ? '本模特已交付，同批其他模特仍在制作'
                : '成片已交付，下一步安排发布'}
            </p>
            <p className="mt-1 text-sm text-muted-foreground">
              核对整批成片与文案后，准备账号、商品和当地发布时间。
            </p>
          </div>
          <Button
            onClick={onPublish}
            disabled={Boolean(
              detail.modelBatchError ||
              (detail.modelBatch && !detail.modelBatch.allDelivered),
            )}
          >
            进入发布中心
            <ArrowLeft className="rotate-180" />
          </Button>
        </div>
      ) : null}

      {detail.modelBatch ? (
        <section className="surface-card mb-5 p-5">
          <div className="flex flex-wrap items-center justify-between gap-2">
            <h2 className="font-semibold">同批模特</h2>
            <p className="text-sm text-muted-foreground">
              {detail.modelBatch.colorCount} 色 × {detail.modelBatch.modelCount}{' '}
              位 = {detail.modelBatch.plannedVideoCount} 条 ·{' '}
              {
                detail.modelBatch.runs.filter(
                  (run) => run.status?.state === 'delivered',
                ).length
              }
              /{detail.modelBatch.modelCount} 位已交付
            </p>
          </div>
          <div className="mt-4 grid gap-3 sm:grid-cols-2 lg:grid-cols-3">
            {detail.modelBatch.runs.map((run) => (
              <button
                key={run.runId}
                type="button"
                onClick={() => onOpenRun(run.runId)}
                aria-current={run.runId === intake.runId ? 'page' : undefined}
                className={`rounded-xl border p-3 text-left focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring ${run.runId === intake.runId ? 'border-[#0071e3] bg-blue-50' : 'border-border hover:bg-muted/40'}`}
              >
                <p className="font-semibold">
                  {run.modelPreset}{' '}
                  <span className="font-normal text-muted-foreground">
                    · {run.locale}
                  </span>
                </p>
                <p className="mt-2 text-sm text-muted-foreground">
                  {run.status?.stageLabel ?? '状态未提供'} ·{' '}
                  {run.status ? runStateLabel(run.status.state) : '待核对'}
                </p>
              </button>
            ))}
          </div>
          <p className="mt-3 text-sm text-muted-foreground">
            每位模特分别审核、确认生成与质检；全批交付后再安排发布。
          </p>
        </section>
      ) : null}

      {detail.modelBatchError ? (
        <p
          role="alert"
          className="mb-5 rounded-xl border border-amber-200 bg-amber-50 p-4 text-sm text-amber-900"
        >
          {detail.modelBatchError}
          。仍可查看本任务产物与回执，新的生成和发布需先核对批次。
        </p>
      ) : null}

      {status.state === 'submission_unknown' ? (
        <div className="mb-5 flex gap-3 border-l-2 border-red-600 bg-red-50 px-4 py-3 text-sm text-red-800">
          <ShieldAlert className="mt-0.5 size-5 shrink-0" />
          <div>
            <p className="font-semibold">不要重复提交</p>
            <p className="mt-1 leading-5">
              当前无法确认 PopBoom 是否已接收任务，需先核对平台记录和任务 ID。
            </p>
          </div>
        </div>
      ) : null}

      {status.state === 'awaiting_paid_approval' ? (
        <div className="mb-5 flex flex-col justify-between gap-4 border border-[#d8c6a5] bg-[#fffaf0] px-4 py-4 sm:flex-row sm:items-center">
          <div>
            <p className="font-semibold text-[#6d5120]">准备阶段已完成</p>
            <p className="mt-1 text-sm leading-5 text-[#826a3e]">
              确认后将为 {modelName} 的 {intake.variants.length} 个颜色执行
              PopBoom 生成与交付质检。
            </p>
          </div>
          <Button
            className="shrink-0 bg-[#0071e3] hover:bg-[#005bb8]"
            onClick={() => setApprovalOpen(true)}
            disabled={Boolean(detail.modelBatchError)}
          >
            <CheckCircle2 />
            审核并确认生成
          </Button>
        </div>
      ) : null}

      <div className="grid gap-5 lg:grid-cols-[minmax(0,1fr)_340px]">
        <div className="min-w-0 space-y-5">
          <section className="rounded-2xl border border-border bg-white px-4 py-5 sm:px-5">
            <div className="flex flex-wrap items-start justify-between gap-4">
              <div>
                <p className="text-xs text-muted-foreground">当前流程</p>
                <h2 className="mt-1 text-lg font-semibold">
                  {status.currentTask}
                </h2>
                {status.note ? (
                  <p className="mt-2 text-sm leading-6 text-muted-foreground">
                    {status.note}
                  </p>
                ) : null}
              </div>
              <div className="text-right">
                <p className="text-xs text-muted-foreground">预计剩余</p>
                <p className="mt-1 font-semibold tabular-nums">
                  {status.etaLabel}
                </p>
              </div>
            </div>
            <div className="mt-5 flex items-center gap-3">
              <Progress
                value={status.progress}
                className="flex-1 [&_[data-slot=progress-track]]:h-1.5 [&_[data-slot=progress-indicator]]:bg-[#0071e3]"
              />
              <span className="w-10 text-right text-sm font-semibold tabular-nums">
                {status.progress}%
              </span>
            </div>
          </section>

          <section className="rounded-2xl border border-border bg-white px-4 py-5 sm:px-5">
            <h2 className="text-base font-semibold">执行流程</h2>
            <ol className="mt-4 grid gap-x-4 gap-y-2 sm:grid-cols-2">
              {WORKFLOW_STAGES.map((stage, index) => {
                const number = index + 1;
                const complete =
                  status.state === 'delivered' || number < status.stageIndex;
                const current =
                  number === status.stageIndex && status.state !== 'delivered';
                const failed =
                  current &&
                  ['failed', 'blocked', 'submission_unknown'].includes(
                    status.state,
                  );
                return (
                  <li
                    key={stage.key}
                    className={`flex min-h-12 items-center gap-3 rounded-md border px-3 py-2 text-sm ${
                      current
                        ? failed
                          ? 'border-red-200 bg-red-50'
                          : 'border-emerald-200 bg-emerald-50'
                        : 'border-transparent'
                    }`}
                  >
                    <span
                      className={`grid size-7 shrink-0 place-items-center rounded-full border text-xs font-semibold ${
                        complete
                          ? 'border-emerald-700 bg-emerald-700 text-white'
                          : current
                            ? failed
                              ? 'border-red-500 text-red-700'
                              : 'border-emerald-700 text-emerald-800'
                            : 'border-border text-muted-foreground'
                      }`}
                    >
                      {complete ? <Check className="size-4" /> : number}
                    </span>
                    <span
                      className={
                        complete
                          ? 'text-foreground'
                          : current
                            ? 'font-medium'
                            : 'text-muted-foreground'
                      }
                    >
                      {stage.label}
                    </span>
                  </li>
                );
              })}
            </ol>
          </section>

          {paidResult || prepareResult ? (
            <section className="rounded-2xl border border-border bg-white px-4 py-5 sm:px-5">
              <h2 className="text-base font-semibold">阶段结果</h2>
              <p className="mt-3 text-sm leading-6 text-muted-foreground">
                {(paidResult || prepareResult)?.summary}
              </p>
              {(paidResult || prepareResult)?.nextAction ? (
                <div className="mt-4 border-l-2 border-[#0071e3] pl-3 text-sm">
                  <span className="text-muted-foreground">下一步：</span>
                  {(paidResult || prepareResult)?.nextAction}
                </div>
              ) : null}
              {missingInputs.length ? (
                <div className="mt-4 bg-amber-50 px-3 py-3 text-sm text-amber-800">
                  <p className="font-medium">待补资料</p>
                  <ul className="mt-2 space-y-1">
                    {missingInputs.map((item) => (
                      <li key={item}>{item}</li>
                    ))}
                  </ul>
                </div>
              ) : null}
            </section>
          ) : null}

          <section className="rounded-2xl border border-border bg-white px-4 py-5 sm:px-5">
            <div className="flex items-center justify-between gap-3">
              <h2 className="text-base font-semibold">产物</h2>
              <span className="text-xs text-muted-foreground">
                {artifacts.length} 项
              </span>
            </div>
            {artifacts.length ? (
              <div className="mt-4 grid gap-3 sm:grid-cols-2">
                {artifacts.map((artifact, index) => (
                  <ArtifactItem
                    key={`${artifact.path}-${index}`}
                    artifact={artifact}
                    runId={intake.runId}
                  />
                ))}
              </div>
            ) : (
              <div className="mt-4 border border-dashed border-border py-8 text-center text-sm text-muted-foreground">
                产物会在 Codex 完成阶段后出现
              </div>
            )}
          </section>
          <UsageSummaryCard
            usage={detail.usage}
            classification={detail.classificationUsage}
          />
        </div>

        <aside className="min-w-0 space-y-5">
          <section className="rounded-2xl border border-border bg-white px-4 py-5">
            <h2 className="text-sm font-semibold">制作单</h2>
            <dl className="mt-3 divide-y divide-border text-sm">
              <InfoRow label="货号" value={intake.sku} />
              <InfoRow label="市场" value={intake.model.market} />
              <InfoRow label="语言" value={intake.model.locale} />
              <InfoRow label="模特" value={modelName} />
              {intake.execution ? (
                <InfoRow
                  label="Codex"
                  value={`${intake.execution.model} · ${intake.execution.reasoningEffort === 'high' ? '高推理' : intake.execution.reasoningEffort}`}
                />
              ) : null}
              {intake.execution ? (
                <InfoRow label="会话" value="每批独立" />
              ) : null}
              <InfoRow
                label="颜色"
                value={intake.variants.map((item) => item.name).join(' / ')}
              />
              <InfoRow label="创建" value={formatTime(intake.createdAt)} />
            </dl>
          </section>

          <section className="rounded-2xl border border-border bg-white px-4 py-5">
            <h2 className="text-sm font-semibold">最近记录</h2>
            <div className="mt-4 space-y-4">
              {events
                .slice(-8)
                .reverse()
                .map((event, index) => (
                  <div
                    key={`${event.at}-${index}`}
                    className="flex gap-3 text-sm"
                  >
                    <span className="mt-1.5 size-1.5 shrink-0 rounded-full bg-[#0071e3]" />
                    <div className="min-w-0">
                      <p className="font-medium">
                        {event.currentTask || event.stageLabel}
                      </p>
                      <p className="mt-0.5 text-xs text-muted-foreground">
                        {formatTime(event.at)}
                      </p>
                    </div>
                  </div>
                ))}
            </div>
          </section>

          {approval ? (
            <section className="rounded-2xl border border-border bg-white px-4 py-5">
              <h2 className="text-sm font-semibold">授权回执</h2>
              <p className="mt-3 text-xs text-muted-foreground">
                {formatTime(approval.approvedAt)}
              </p>
              <code className="mt-2 block break-all bg-muted px-2 py-2 text-xs leading-4">
                {approval.authorizationFingerprint}
              </code>
            </section>
          ) : null}
        </aside>
      </div>

      <AlertDialog open={approvalOpen} onOpenChange={setApprovalOpen}>
        <AlertDialogContent>
          <AlertDialogHeader>
            <AlertDialogMedia className="bg-amber-50 text-amber-700">
              <AlertTriangle />
            </AlertDialogMedia>
            <AlertDialogTitle>确认开始 PopBoom 生成</AlertDialogTitle>
            <AlertDialogDescription>
              本次授权仅覆盖 {modelName} 的 {intake.variants.length} 个颜色，共{' '}
              {intake.variants.length}{' '}
              条视频。同批其他模特需分别确认。操作可能消耗 PopBoom
              额度，授权回执会在提交前落盘。
            </AlertDialogDescription>
          </AlertDialogHeader>
          {approvalError ? (
            <p className="text-sm text-destructive">{approvalError}</p>
          ) : null}
          <AlertDialogFooter>
            <AlertDialogCancel disabled={approving}>取消</AlertDialogCancel>
            <AlertDialogAction
              className="bg-[#0071e3] hover:bg-[#005bb8]"
              onClick={approve}
              disabled={approving}
            >
              {approving ? <Spinner /> : null}
              {approving ? '正在记录授权' : '确认并开始'}
            </AlertDialogAction>
          </AlertDialogFooter>
        </AlertDialogContent>
      </AlertDialog>
    </div>
  );
}

export function StatusBadge({ state }: { state: RunState }) {
  const classes = {
    queued: 'border-neutral-300 bg-neutral-50 text-neutral-700',
    running: 'border-emerald-200 bg-emerald-50 text-emerald-800',
    submitting: 'border-[#d8c6a5] bg-[#fffaf0] text-[#6d5120]',
    awaiting_paid_approval: 'border-[#d8c6a5] bg-[#fffaf0] text-[#6d5120]',
    needs_input: 'border-amber-200 bg-amber-50 text-amber-800',
    blocked: 'border-neutral-300 bg-neutral-100 text-neutral-700',
    failed: 'border-red-200 bg-red-50 text-red-700',
    delivered: 'border-emerald-200 bg-emerald-50 text-emerald-800',
    submission_unknown: 'border-red-300 bg-red-50 text-red-800',
  }[state];
  return (
    <Badge variant="outline" className={`h-7 rounded-md px-2.5 ${classes}`}>
      {runStateLabel(state)}
    </Badge>
  );
}

function ArtifactItem({
  artifact,
  runId,
}: {
  artifact: Artifact;
  runId: string;
}) {
  const localUrl = `/api/runs/${encodeURIComponent(runId)}/artifact?path=${encodeURIComponent(artifact.path)}`;
  const isExternal = /^https?:\/\//i.test(artifact.path);
  const href = isExternal ? artifact.path : localUrl;
  const icon = {
    image: <ImageIcon />,
    video: <Video />,
    json: <FileJson />,
    text: <FileText />,
    link: <ExternalLink />,
    other: <FileText />,
  }[artifact.kind];

  return (
    <div className="overflow-hidden rounded-md border border-border bg-muted/20">
      {artifact.kind === 'image' ? (
        <a
          href={href}
          target="_blank"
          rel="noreferrer"
          className="block aspect-[4/3] bg-muted"
        >
          <span className="relative block size-full">
            <Image
              src={href}
              alt={artifact.label}
              fill
              sizes="(max-width: 640px) 100vw, 400px"
              unoptimized
              className="object-cover"
            />
          </span>
        </a>
      ) : null}
      {artifact.kind === 'video' ? (
        <video
          controls
          preload="metadata"
          className="aspect-video w-full bg-black"
        >
          <source src={href} />
        </video>
      ) : null}
      <a
        href={href}
        target="_blank"
        rel="noreferrer"
        className="flex min-h-12 items-center gap-2 px-3 py-2 text-sm hover:bg-white"
      >
        <span className="text-muted-foreground [&_svg]:size-4">{icon}</span>
        <span className="min-w-0 flex-1 truncate font-medium">
          {artifact.label}
        </span>
        <ExternalLink className="size-3.5 text-muted-foreground" />
      </a>
    </div>
  );
}

function InfoRow({ label, value }: { label: string; value: string }) {
  return (
    <div className="flex items-start justify-between gap-3 py-3">
      <dt className="shrink-0 text-muted-foreground">{label}</dt>
      <dd className="min-w-0 break-words text-right font-medium">{value}</dd>
    </div>
  );
}
