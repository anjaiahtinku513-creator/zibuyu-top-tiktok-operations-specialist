'use client';

import { useCallback, useEffect, useRef, useState } from 'react';
import {
  ArrowRight,
  CalendarClock,
  Check,
  CheckCircle2,
  Clock3,
  Copy,
  Film,
  Globe2,
  RefreshCw,
  Send,
  ShieldCheck,
  AlertCircle,
} from 'lucide-react';
import { Button } from '@/components/ui/button';
import { Input } from '@/components/ui/input';
import { Label } from '@/components/ui/label';
import { Spinner } from '@/components/ui/spinner';
import {
  AlertDialog,
  AlertDialogContent,
  AlertDialogHeader,
  AlertDialogTitle,
  AlertDialogDescription,
  AlertDialogFooter,
  AlertDialogCancel,
} from '@/components/ui/alert-dialog';
import { formatTime, RunStatus } from '@/lib/zibuyu';
import {
  DeliveryItem,
  PublishIntent,
  PublishOverview,
  PublishingDetail,
  PublishRow,
  publishLabel,
} from '@/lib/publishing';

async function json<T>(response: Response): Promise<T> {
  const body = (await response.json()) as T & {
    error?: string;
    detail?: string;
  };
  if (!response.ok) throw new Error(body.error || body.detail || '操作未完成');
  return body as T;
}

export function PublishingCenter({
  token,
  runs,
  initialRunId,
  onProduction,
}: {
  token: string | null;
  runs: RunStatus[];
  initialRunId: string | null;
  onProduction: (id: string) => void;
}) {
  const [overview, setOverview] = useState<PublishOverview | null>(null);
  const [selected, setSelected] = useState(initialRunId || '');
  const [detail, setDetail] = useState<PublishingDetail | null>(null);
  const [intent, setIntent] = useState<PublishIntent>({
    accountCode: '美1',
    pid: '',
    date: '',
    mode: 'schedule',
  });
  const [busy, setBusy] = useState('');
  const [error, setError] = useState('');
  const [notice, setNotice] = useState('');
  const [approveOpen, setApproveOpen] = useState(false);
  const loadedIntent = useRef('');
  const attemptedSnapshot = useRef(new Set<string>());
  const selectionRef = useRef(selected);
  const api = `/api/runs/${encodeURIComponent(selected)}/publishing`;

  const refresh = useCallback(async () => {
    if (!token) return;
    const all = await json<PublishOverview>(await fetch('/api/publishing'));
    setOverview(all);
    if (!selected) return;
    const batch = await json<PublishingDetail>(
      await fetch(`/api/runs/${encodeURIComponent(selected)}/publishing`),
    );
    if (selectionRef.current !== selected) return;
    setDetail(batch);
    if (loadedIntent.current !== selected) {
      loadedIntent.current = selected;
      setIntent(
        batch.intent || {
          accountCode:
            batch.production.modelPreset?.match(/^[美德][123]$/)?.[0] || '美1',
          pid: '',
          date: '',
          mode: 'schedule',
        },
      );
    }
  }, [selected, token]);

  useEffect(() => {
    const first = window.setTimeout(
      () => void refresh().catch((cause) => setError(cause.message)),
      0,
    );
    const timer = window.setInterval(
      () => void refresh().catch(() => {}),
      5000,
    );
    return () => {
      window.clearTimeout(first);
      window.clearInterval(timer);
    };
  }, [refresh]);

  const mutate = useCallback(
    async (
      action: string,
      body: unknown = {},
      method: 'POST' | 'PUT' = 'POST',
    ) => {
      if (!token) throw new Error('本地服务尚未连接');
      return json(
        await fetch(`${api}/${action}`, {
          method,
          headers: {
            'content-type': 'application/json',
            'x-zibuyu-token': token,
          },
          body: JSON.stringify(body),
        }),
      );
    },
    [api, token],
  );

  const perform = useCallback(
    async (name: string, action: () => Promise<unknown>) => {
      setBusy(name);
      setError('');
      setNotice('');
      try {
        await action();
        await refresh();
      } catch (cause) {
        setError(cause instanceof Error ? cause.message : '操作未完成');
      } finally {
        setBusy('');
      }
    },
    [refresh],
  );

  useEffect(() => {
    if (
      !detail ||
      detail.runId !== selected ||
      detail.production.state !== 'delivered' ||
      detail.snapshot ||
      !token ||
      attemptedSnapshot.current.has(selected)
    )
      return;
    attemptedSnapshot.current.add(selected);
    const timer = window.setTimeout(
      () => void perform('snapshot', () => mutate('snapshot')),
      0,
    );
    return () => window.clearTimeout(timer);
  }, [detail, selected, token, perform, mutate]);

  const current = detail?.runId === selected ? detail : null;
  const account = overview?.accounts.find(
    (item) => item.code === intent.accountCode,
  );
  const actions = overview?.batches.flatMap((batch) => batch.actions) || [];
  const running = ['preparing', 'submitting', 'reconciling'].includes(
    current?.status.state || '',
  );
  const disabled = Boolean(busy || running || !token);
  const frozen = disabled || Boolean(current?.actions.length);
  const reviewed = Boolean(
    current?.snapshot &&
    current.review?.handoffHash === current.snapshot.handoff_sha256,
  );
  const canPrepare =
    current?.production.state === 'delivered' &&
    current?.snapshot &&
    intent.pid &&
    intent.date &&
    intent.mode === 'schedule' &&
    !current.actions.length;

  async function prepare() {
    await mutate('intent', intent, 'PUT');
    if (!reviewed)
      await mutate('review', {
        handoffHash: current?.snapshot?.handoff_sha256,
      });
    await mutate('prepare');
  }

  function chooseRun(id: string) {
    if (busy) return;
    selectionRef.current = id;
    setSelected(id);
    setDetail(null);
    setError('');
    setNotice('');
    setApproveOpen(false);
  }

  return (
    <div className="mx-auto max-w-[1440px] space-y-6">
      <div className="flex flex-wrap items-end justify-between gap-4">
        <div>
          <p className="mb-2 text-sm font-medium text-primary">从成片到观众</p>
          <h1 className="text-[30px] font-semibold tracking-tight">发布中心</h1>
        </div>
        <Button
          variant="outline"
          className="rounded-full bg-white/70"
          onClick={() => void perform('refresh', refresh)}
          disabled={Boolean(busy)}
        >
          <RefreshCw className={busy === 'refresh' ? 'animate-spin' : ''} />
          刷新状态
        </Button>
      </div>

      <div className="grid grid-cols-1 gap-3 sm:grid-cols-3">
        <Metric
          label="等待确认"
          value={
            overview?.batches.filter(
              (batch) => batch.status.state === 'awaiting_publish_confirmation',
            ).length || 0
          }
          icon={<ShieldCheck />}
        />
        <Metric
          label="已排期"
          value={
            actions.filter((action) => action.state === 'scheduled').length
          }
          icon={<CalendarClock />}
        />
        <Metric
          label="已发布"
          value={
            actions.filter((action) => action.state === 'published').length
          }
          icon={<CheckCircle2 />}
        />
      </div>

      {overview && !overview.captionTransport.verified ? (
        <div className="flex items-start gap-3 rounded-2xl border border-blue-200/70 bg-blue-50/80 p-4 text-sm leading-6 text-blue-900">
          <ShieldCheck className="mt-0.5 size-5 shrink-0" />
          <p>{overview.captionTransport.reason}</p>
        </div>
      ) : null}

      <div className="grid gap-6 xl:grid-cols-[260px_minmax(0,1fr)]">
        <aside className="space-y-4">
          <div className="flex items-center justify-between">
            <h2 className="font-semibold">制作批次</h2>
            <span className="text-sm text-muted-foreground">{runs.length}</span>
          </div>
          <div className="flex gap-2 overflow-x-auto pb-1 xl:max-h-[520px] xl:flex-col xl:overflow-y-auto">
            {runs.map((run) => {
              const batch = overview?.batches.find(
                (item) => item.runId === run.runId,
              );
              return (
                <button
                  type="button"
                  key={run.runId}
                  disabled={Boolean(busy)}
                  onClick={() => chooseRun(run.runId)}
                  aria-pressed={selected === run.runId}
                  className={`min-w-52 rounded-2xl border p-4 text-left transition-colors xl:min-w-0 ${selected === run.runId ? 'border-blue-200 bg-white shadow-sm' : 'border-transparent bg-white/40 hover:bg-white/80'}`}
                >
                  <span className="flex items-center justify-between gap-2">
                    <span className="truncate font-semibold">
                      {run.sku} · {run.modelPreset}
                    </span>
                    {selected === run.runId ? (
                      <span className="size-2 rounded-full bg-primary" />
                    ) : null}
                  </span>
                  <span className="mt-1.5 block text-sm text-muted-foreground">
                    {run.modelPreset} · {run.variantCount} 个颜色
                  </span>
                  <span className="mt-3 block text-sm text-primary">
                    {publishLabel(
                      batch?.status.state || 'production_not_ready',
                    )}
                  </span>
                </button>
              );
            })}
            {!runs.length ? (
              <p className="rounded-2xl border border-dashed p-5 text-sm leading-6 text-muted-foreground">
                暂无制作批次。完成制作与验收后，成片会在这里进入发布流程。
              </p>
            ) : null}
          </div>
          <div className="rounded-2xl border border-white bg-white/50 p-4 text-sm leading-6 text-muted-foreground">
            <Globe2 className="mb-2 size-5 text-primary" />
            按账号当地时间排期
            <div className="mt-1 font-medium text-foreground">
              07:00 · 12:00 · 18:00
            </div>
            <p className="mt-2">
              每个账号每天最多 3 条，自动校对夏令时及已有排期。
            </p>
          </div>
        </aside>

        <div className="min-w-0 space-y-5">
          {error ? (
            <div
              role="alert"
              className="flex gap-3 rounded-2xl border border-amber-200 bg-amber-50 p-4 text-sm leading-6 text-amber-900"
            >
              <AlertCircle className="mt-0.5 size-5 shrink-0" />
              <span>{error}</span>
            </div>
          ) : null}
          {notice ? (
            <output className="block text-sm text-primary">{notice}</output>
          ) : null}
          {!selected ? (
            <div className="surface-card grid min-h-80 place-items-center p-8 text-center">
              <div>
                <span className="mx-auto grid size-16 place-items-center rounded-2xl bg-blue-50 text-primary">
                  <CalendarClock className="size-8" />
                </span>
                <h2 className="mt-5 text-xl font-semibold">
                  选择一个批次，安排下一次发布
                </h2>
                <p className="mx-auto mt-2 max-w-md text-sm leading-6 text-muted-foreground">
                  先交付整批成片与文案，再核对账号、商品和时间。最终发布表由你确认。
                </p>
              </div>
            </div>
          ) : !current ? (
            <output className="surface-card flex min-h-48 items-center justify-center gap-3 text-muted-foreground">
              <Spinner />
              正在读取批次
            </output>
          ) : (
            <>
              <div className="surface-card p-5 sm:p-6">
                <div className="flex flex-wrap items-center justify-between gap-3">
                  <div className="flex items-center gap-3">
                    <span className="grid size-11 place-items-center rounded-2xl bg-blue-50 text-primary">
                      <Film />
                    </span>
                    <div>
                      <h2 className="text-xl font-semibold">
                        {current.production.sku}
                      </h2>
                      <p className="mt-1 text-sm text-muted-foreground">
                        {current.production.variantCount} 个颜色 ·{' '}
                        {current.production.modelPreset}
                      </p>
                    </div>
                  </div>
                  <span className="rounded-full bg-blue-50 px-3 py-1.5 text-sm font-medium text-blue-700">
                    {publishLabel(current.status.state)}
                  </span>
                </div>
                <ol className="mt-6 grid grid-cols-2 gap-3 border-t border-border pt-5 sm:grid-cols-4">
                  {['成片交付', '账号与日期', '最终审核', '排期结果'].map(
                    (label, index) => {
                      const complete = [
                        reviewed,
                        Boolean(current.manifest),
                        Boolean(current.approval),
                        ['scheduled', 'published'].includes(
                          current.status.state,
                        ),
                      ][index];
                      return (
                        <li
                          key={label}
                          className="flex items-center gap-2 text-sm"
                        >
                          <span
                            className={`grid size-6 shrink-0 place-items-center rounded-full text-xs ${complete ? 'bg-primary text-white' : 'bg-slate-100 text-slate-600'}`}
                          >
                            {complete ? (
                              <Check className="size-3.5" />
                            ) : (
                              index + 1
                            )}
                          </span>
                          {label}
                        </li>
                      );
                    },
                  )}
                </ol>
                <output className="mt-4 flex items-start gap-2 text-sm leading-6 text-muted-foreground">
                  {running ? (
                    <Spinner className="mt-1" />
                  ) : (
                    <Clock3 className="mt-1 size-4 shrink-0" />
                  )}
                  {current.status.note}
                </output>
                {current.production.state !== 'delivered' ? (
                  <Button
                    variant="outline"
                    className="mt-4"
                    onClick={() => onProduction(selected)}
                  >
                    查看制作进度
                    <ArrowRight />
                  </Button>
                ) : null}
              </div>

              <section className="surface-card p-5 sm:p-6">
                <div className="mb-5 flex items-center justify-between">
                  <h2 className="text-lg font-semibold">账号与发布日期</h2>
                  <span className="text-sm text-muted-foreground">
                    可提前保存
                  </span>
                </div>
                <div className="grid gap-4 md:grid-cols-3">
                  <div className="space-y-2">
                    <Label htmlFor="publish-account">发布账号</Label>
                    <select
                      id="publish-account"
                      value={intent.accountCode}
                      onChange={(event) =>
                        setIntent({
                          ...intent,
                          accountCode: event.target.value,
                        })
                      }
                      disabled={frozen}
                      className="h-11 w-full rounded-xl border bg-white px-3"
                    >
                      {overview?.accounts.map((item) => (
                        <option key={item.code} value={item.code}>
                          {item.code} · {item.username}
                        </option>
                      ))}
                    </select>
                  </div>
                  <div className="space-y-2">
                    <Label htmlFor="publish-pid">TikTok 商品 PID</Label>
                    <Input
                      id="publish-pid"
                      placeholder="商品 PID，与制作货号不同"
                      value={intent.pid}
                      disabled={frozen}
                      onChange={(event) =>
                        setIntent({ ...intent, pid: event.target.value })
                      }
                      className="h-11"
                      maxLength={100}
                    />
                  </div>
                  <div className="space-y-2">
                    <Label htmlFor="publish-date">账号当地日期</Label>
                    <Input
                      id="publish-date"
                      type="date"
                      value={intent.date}
                      disabled={frozen}
                      onChange={(event) =>
                        setIntent({ ...intent, date: event.target.value })
                      }
                      className="h-11"
                    />
                  </div>
                </div>
                <div className="mt-3 flex flex-wrap gap-2 text-sm text-muted-foreground">
                  <Globe2 className="size-4" />
                  <span>
                    {account?.timezone || '请选择账号'} ·
                    准备排期时核验账号身份和商品
                  </span>
                </div>
                {(current.snapshot?.items.length ||
                  current.production.variantCount) > 3 ? (
                  <label className="mt-4 flex cursor-pointer items-start gap-2 rounded-xl bg-blue-50 p-3 text-sm leading-6">
                    <input
                      type="checkbox"
                      className="mt-1 size-4 shrink-0 accent-blue-600"
                      checked={Boolean(intent.spreadAcrossDays)}
                      disabled={frozen}
                      onChange={(event) =>
                        setIntent({
                          ...intent,
                          spreadAcrossDays: event.target.checked,
                        })
                      }
                    />
                    <span>
                      同意分多日发布：每天 3 条，剩余视频按顺序延续到下一天的
                      07:00、12:00、18:00，最终日期在发布表中确认。
                    </span>
                  </label>
                ) : null}
                <div className="mt-5 flex flex-wrap items-center justify-between gap-4 border-t border-border pt-5">
                  <label className="flex cursor-pointer items-center gap-2 text-sm">
                    <input
                      type="checkbox"
                      className="size-4 accent-blue-600"
                      checked={intent.mode === 'production_only'}
                      disabled={frozen}
                      onChange={(event) =>
                        setIntent({
                          ...intent,
                          mode: event.target.checked
                            ? 'production_only'
                            : 'schedule',
                        })
                      }
                    />
                    本批仅交付，不安排发布
                  </label>
                  <Button
                    variant="outline"
                    onClick={() =>
                      void perform('intent', async () => {
                        await mutate('intent', intent, 'PUT');
                        setNotice('发布意向已保存');
                      })
                    }
                    disabled={frozen}
                  >
                    {busy === 'intent' ? <Spinner /> : null}保存意向
                  </Button>
                </div>
              </section>

              {current.production.state === 'delivered' ? (
                <section className="surface-card p-5 sm:p-6">
                  <div className="mb-5 flex flex-wrap items-center justify-between gap-3">
                    <div>
                      <h2 className="text-lg font-semibold">整批成片与文案</h2>
                      <p className="mt-1 text-sm text-muted-foreground">
                        核对所有颜色的最终视频、验收结论和完整发布文字。
                      </p>
                    </div>
                    <Button
                      variant="ghost"
                      disabled={disabled}
                      onClick={() =>
                        void perform('snapshot', () => mutate('snapshot'))
                      }
                    >
                      {busy === 'snapshot' ? <Spinner /> : <RefreshCw />}
                      重新校验交付
                    </Button>
                  </div>
                  {current.snapshot ? (
                    <div className="space-y-5">
                      {current.snapshot.items.map((item) => (
                        <DeliveryCard
                          key={item.variant_id}
                          item={item}
                          runId={selected}
                        />
                      ))}
                    </div>
                  ) : (
                    <p className="rounded-xl bg-slate-50 p-5 text-sm leading-6 text-muted-foreground">
                      {busy === 'snapshot'
                        ? '正在核对插件的整批验收与交付记录…'
                        : '等待整批交付校验通过。已有文件不代表成片验收完成。'}
                    </p>
                  )}
                  <div className="mt-5 flex flex-wrap items-center justify-between gap-4 border-t border-border pt-5">
                    <p className="text-sm text-muted-foreground">
                      {reviewed
                        ? '你已确认收到这版整批交付'
                        : '核对后开始准备，提交发布前还有最终确认。'}
                    </p>
                    <Button
                      disabled={disabled || !canPrepare}
                      onClick={() => void perform('prepare', prepare)}
                    >
                      {busy === 'prepare' ? <Spinner /> : <ShieldCheck />}
                      {reviewed ? '准备排期' : '核对完成，准备排期'}
                    </Button>
                  </div>
                </section>
              ) : null}

              {current.manifest ? (
                <section className="surface-card overflow-hidden">
                  <div className="flex flex-wrap items-center justify-between gap-3 border-b border-border p-5 sm:p-6">
                    <div>
                      <h2 className="text-lg font-semibold">最终发布表</h2>
                      <p className="mt-1 text-sm text-muted-foreground">
                        {current.manifest.rows.length} 条视频 · 封面取首帧 ·
                        不添加配乐
                      </p>
                    </div>
                    <Button
                      disabled={
                        disabled ||
                        current.status.state !== 'awaiting_publish_confirmation'
                      }
                      onClick={() => setApproveOpen(true)}
                    >
                      <Send />
                      审核并确认排期
                    </Button>
                  </div>
                  <div className="divide-y divide-border">
                    {current.manifest.rows.map((row) => (
                      <ManifestRow key={row.actionId} row={row} />
                    ))}
                  </div>
                  <details className="border-t border-border p-5 text-sm">
                    <summary className="cursor-pointer text-muted-foreground">
                      发布参数与文案传输依据
                    </summary>
                    <p className="mt-3 break-words leading-6">
                      {current.manifest.captionMapping.evidenceExcerpt}
                    </p>
                    <p className="mt-2 break-all text-muted-foreground">
                      {current.manifest.captionMapping.evidenceSource}
                    </p>
                    <pre className="mt-3 max-h-64 overflow-auto rounded-xl bg-slate-50 p-4 text-xs">
                      {JSON.stringify(current.manifest, null, 2)}
                    </pre>
                  </details>
                </section>
              ) : null}

              {current.actions.length ? (
                <section className="surface-card p-5 sm:p-6">
                  <div className="mb-5 flex flex-wrap items-center justify-between gap-3">
                    <h2 className="text-lg font-semibold">平台结果</h2>
                    <Button
                      variant="outline"
                      disabled={
                        disabled ||
                        !current.actions.some(
                          (action) => action.scheduleId || action.logId,
                        )
                      }
                      onClick={() =>
                        void perform('reconcile', () => mutate('reconcile'))
                      }
                    >
                      <RefreshCw />
                      核对发布结果
                    </Button>
                  </div>
                  <div className="space-y-3">
                    {current.actions.map((action) => (
                      <div
                        key={action.actionId}
                        className="flex flex-wrap items-center justify-between gap-3 rounded-xl bg-slate-50 p-4"
                      >
                        <div>
                          <p className="font-medium">
                            {action.colorName} · {action.accountCode}
                          </p>
                          <p className="mt-1 text-sm text-muted-foreground">
                            {action.localDate} {action.localTime} ·{' '}
                            {action.timezone}
                          </p>
                          <p className="mt-1 break-all text-xs text-muted-foreground">
                            回执{' '}
                            {action.scheduleId || action.logId || '尚未取得'}
                            {action.observedAt
                              ? ` · ${formatTime(action.observedAt)} 核对`
                              : ''}
                          </p>
                        </div>
                        <span className="text-sm font-medium text-primary">
                          {publishLabel(action.state)}
                        </span>
                      </div>
                    ))}
                  </div>
                </section>
              ) : null}
            </>
          )}
        </div>
      </div>

      <AlertDialog open={approveOpen} onOpenChange={setApproveOpen}>
        <AlertDialogContent className="max-h-[85svh] overflow-y-auto sm:max-w-2xl">
          <AlertDialogHeader>
            <AlertDialogTitle>确认这份发布排期</AlertDialogTitle>
            <AlertDialogDescription>
              将把以下视频安排到指定 TikTok
              账号。此次确认仅覆盖你正在查看的完整发布表。
            </AlertDialogDescription>
          </AlertDialogHeader>
          <div className="space-y-3">
            {current?.manifest?.rows.map((row) => (
              <div key={row.actionId} className="rounded-xl border p-4 text-sm">
                <p className="font-semibold">
                  {row.colorName} → @{row.username}
                </p>
                <p className="mt-1">
                  {row.localDate} {row.localTime} · {row.timezone}
                </p>
                <p className="mt-1 text-muted-foreground">
                  {row.request.product_title} · PID {row.pid}
                </p>
                <p className="mt-3 whitespace-pre-wrap break-words leading-6">
                  {row.captionFinal}
                </p>
              </div>
            ))}
          </div>
          <p className="break-all text-xs text-muted-foreground">
            发布表版本：{current?.manifest?.manifestHash}
          </p>
          {error ? (
            <p role="alert" className="text-sm text-destructive">
              {error}
            </p>
          ) : null}
          <AlertDialogFooter>
            <AlertDialogCancel disabled={Boolean(busy)}>
              返回检查
            </AlertDialogCancel>
            <Button
              disabled={disabled}
              onClick={() =>
                void perform('approve', async () => {
                  await mutate('approve', {
                    confirm: true,
                    manifestHash: current?.manifest?.manifestHash,
                  });
                  setApproveOpen(false);
                })
              }
            >
              {busy === 'approve' ? <Spinner /> : <Send />}确认并提交排期
            </Button>
          </AlertDialogFooter>
        </AlertDialogContent>
      </AlertDialog>
    </div>
  );
}

function Metric({
  label,
  value,
  icon,
}: {
  label: string;
  value: number;
  icon: React.ReactNode;
}) {
  return (
    <div className="surface-card flex items-center justify-between px-5 py-4">
      <div>
        <p className="text-sm text-muted-foreground">{label}</p>
        <p className="mt-1 text-3xl font-semibold tabular-nums tracking-tight">
          {value}
        </p>
      </div>
      <span className="grid size-11 place-items-center rounded-2xl bg-blue-50 text-primary [&_svg]:size-5">
        {icon}
      </span>
    </div>
  );
}
function DeliveryCard({ item, runId }: { item: DeliveryItem; runId: string }) {
  const [copyState, setCopyState] = useState('');
  const url = item.local_video_path
    ? `/api/runs/${encodeURIComponent(runId)}/artifact?path=${encodeURIComponent(item.local_video_path)}`
    : /^https?:\/\//i.test(item.video_url || '')
      ? item.video_url!
      : undefined;
  return (
    <article className="grid gap-4 rounded-2xl border bg-slate-50/40 p-4 sm:grid-cols-[128px_minmax(0,1fr)]">
      <div className="overflow-hidden rounded-xl bg-slate-950">
        {url ? (
          <video
            src={url}
            controls
            preload="metadata"
            className="aspect-[9/16] max-h-72 w-full object-contain"
            aria-label={`${item.color_name} 最终成片`}
          />
        ) : (
          <div className="grid aspect-[9/16] place-items-center text-sm text-white">
            视频地址待补齐
          </div>
        )}
      </div>
      <div className="min-w-0">
        <div className="flex flex-wrap items-center justify-between gap-2">
          <h3 className="font-semibold">{item.color_name}</h3>
          <span className="flex items-center gap-1 text-sm text-emerald-700">
            <CheckCircle2 className="size-4" />
            已验收
          </span>
        </div>
        <p className="mt-1 text-xs text-muted-foreground">
          {item.model_preset} · 视频 {item.record_id}
        </p>
        <p className="mt-4 whitespace-pre-wrap break-words text-sm leading-7">
          {item.copy_ready_caption}
        </p>
        <Button
          variant="ghost"
          size="sm"
          className="mt-3"
          onClick={() =>
            void navigator.clipboard
              .writeText(item.copy_ready_caption)
              .then(() => setCopyState('已复制'))
              .catch(() => setCopyState('复制失败，请手动选择文字'))
          }
        >
          <Copy />
          {copyState || '复制文案与标签'}
        </Button>
      </div>
    </article>
  );
}
function ManifestRow({ row }: { row: PublishRow }) {
  return (
    <article className="grid gap-4 p-5 sm:grid-cols-[92px_minmax(0,1fr)] sm:p-6">
      <div>
        <p className="text-2xl font-semibold tracking-tight">{row.localTime}</p>
        <p className="mt-1 text-xs text-muted-foreground">{row.localDate}</p>
      </div>
      <div className="min-w-0">
        <div className="flex flex-wrap justify-between gap-2">
          <h3 className="font-semibold">
            {row.colorName} · {row.accountCode}
          </h3>
          <span className="text-sm text-muted-foreground">@{row.username}</span>
        </div>
        <p className="mt-1 text-sm text-muted-foreground">
          {row.request.product_title} · PID {row.pid}
        </p>
        <p className="mt-3 whitespace-pre-wrap break-words text-sm leading-7">
          {row.captionFinal}
        </p>
        <p className="mt-3 text-xs text-muted-foreground">
          {row.timezone} · {row.request.scheduled_time}
        </p>
      </div>
    </article>
  );
}
