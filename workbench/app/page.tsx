'use client';

import type { CSSProperties } from 'react';
import { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import Image from 'next/image';
import {
  Activity,
  CalendarClock,
  Archive,
  ChevronRight,
  CircleUserRound,
  LayoutDashboard,
  Plus,
  RefreshCw,
  Sparkles,
  WifiOff,
} from 'lucide-react';

import { PublishingCenter } from '@/components/zibuyu/publishing-center';
import { NewTaskForm } from '@/components/zibuyu/new-task-form';
import { ProgressDock } from '@/components/zibuyu/progress-dock';
import { StatusBadge, TaskDetailView } from '@/components/zibuyu/task-detail';
import { Badge } from '@/components/ui/badge';
import { Button } from '@/components/ui/button';
import { Progress } from '@/components/ui/progress';
import {
  Sidebar,
  SidebarContent,
  SidebarFooter,
  SidebarGroup,
  SidebarGroupContent,
  SidebarGroupLabel,
  SidebarHeader,
  SidebarInset,
  SidebarMenu,
  SidebarMenuBadge,
  SidebarMenuButton,
  SidebarMenuItem,
  SidebarProvider,
  SidebarRail,
  SidebarTrigger,
  useSidebar,
} from '@/components/ui/sidebar';
import { formatTime, Health, MODELS, RunDetail, RunStatus } from '@/lib/zibuyu';

type View = 'new' | 'queue' | 'models' | 'history' | 'detail' | 'publishing';

const VIEW_LABELS: Record<View, string> = {
  publishing: '发布中心',
  new: '新建制作',
  queue: '任务队列',
  models: '模特库',
  history: '历史交付',
  detail: '任务详情',
};

async function readJsonResponse<T>(response: Response): Promise<T> {
  const body = (await response.json()) as T & {
    error?: string;
    detail?: string;
  };
  if (!response.ok)
    throw new Error(body.error || body.detail || '请求执行失败');
  return body as T;
}

export default function Home() {
  const [view, setView] = useState<View>('new');
  const [publishRunId, setPublishRunId] = useState<string | null>(null);
  const [token, setToken] = useState<string | null>(null);
  const [health, setHealth] = useState<Health | null>(null);
  const [runs, setRuns] = useState<RunStatus[]>([]);
  const [activeRunId, setActiveRunId] = useState<string | null>(null);
  const activeRunRef = useRef<string | null>(null);
  const detailRequestRef = useRef(0);
  const [detail, setDetail] = useState<RunDetail | null>(null);
  const [loadingDetail, setLoadingDetail] = useState(false);
  const [initializing, setInitializing] = useState(true);
  const [serviceError, setServiceError] = useState('');

  const refreshRuns = useCallback(async () => {
    const result = await readJsonResponse<{ runs: RunStatus[] }>(
      await fetch('/api/runs'),
    );
    setRuns(result.runs);
    return result.runs;
  }, []);

  const loadRun = useCallback(async (runId: string, showLoading = true) => {
    if (activeRunRef.current !== runId) return;
    const requestNumber = ++detailRequestRef.current;
    if (showLoading) setLoadingDetail(true);
    try {
      const result = await readJsonResponse<RunDetail>(
        await fetch(`/api/runs/${encodeURIComponent(runId)}`),
      );
      if (
        activeRunRef.current === runId &&
        detailRequestRef.current === requestNumber
      )
        setDetail(result);
      return result;
    } finally {
      if (
        activeRunRef.current === runId &&
        detailRequestRef.current === requestNumber
      )
        setLoadingDetail(false);
    }
  }, []);

  const initialize = useCallback(async () => {
    setServiceError('');
    setInitializing(true);
    try {
      const session = await readJsonResponse<{ token: string }>(
        await fetch('/api/session'),
      );
      setToken(session.token);
      const [healthResult] = await Promise.all([
        readJsonResponse<Health>(await fetch('/api/health')),
        refreshRuns(),
      ]);
      setHealth(healthResult);
    } catch (error) {
      const message =
        error instanceof Error ? error.message : '本地服务连接失败';
      setServiceError(
        /failed to fetch|fetch failed|networkerror/i.test(message)
          ? '本地桥接未启动，请双击 start-workbench.cmd 重新启动'
          : message,
      );
      setHealth(null);
    } finally {
      setInitializing(false);
    }
  }, [refreshRuns]);

  useEffect(() => {
    if (!activeRunId || view !== 'detail') return;
    const timer = window.setInterval(() => {
      void loadRun(activeRunId, false).catch(() => {});
    }, 5000);
    return () => window.clearInterval(timer);
  }, [activeRunId, view, loadRun]);

  useEffect(() => {
    const timer = window.setTimeout(() => {
      if (window.location.hash === '#publishing') setView('publishing');
      void initialize();
    }, 0);
    return () => window.clearTimeout(timer);
  }, [initialize]);

  useEffect(() => {
    if (health?.ok) return;
    const timer = window.setInterval(() => void initialize(), 5000);
    return () => window.clearInterval(timer);
  }, [health?.ok, initialize]);

  useEffect(() => {
    const timer = window.setInterval(() => {
      void refreshRuns().catch(() => {});
    }, 5000);
    return () => window.clearInterval(timer);
  }, [refreshRuns]);

  useEffect(() => {
    if (!activeRunId || !token) return;
    const source = new EventSource(
      `/api/runs/${encodeURIComponent(activeRunId)}/events`,
    );
    const onStatus = (event: MessageEvent<string>) => {
      const status = JSON.parse(event.data) as RunStatus;
      if (activeRunRef.current !== status.runId) return;
      setDetail((current) =>
        current?.intake.runId === status.runId
          ? { ...current, status }
          : current,
      );
      setRuns((current) => {
        const exists = current.some((item) => item.runId === status.runId);
        const next = exists
          ? current.map((item) => (item.runId === status.runId ? status : item))
          : [status, ...current];
        return next.sort((left, right) =>
          right.updatedAt.localeCompare(left.updatedAt),
        );
      });
      void loadRun(activeRunId, false).catch(() => {});
    };
    source.addEventListener('status', onStatus as EventListener);
    source.addEventListener('error', () => {
      // Restarting the local bridge rotates its session cookie/token.
      // Renew it so an already-open workbench reconnects automatically.
      void initialize();
    });
    return () => source.close();
  }, [activeRunId, token, loadRun, initialize]);

  const dockStatus = useMemo(() => {
    if (detail?.status && detail.status.runId === activeRunId)
      return detail.status;
    return (
      runs.find((run) =>
        ['running', 'submitting', 'queued'].includes(run.state),
      ) ??
      runs.find((run) => run.state === 'awaiting_paid_approval') ??
      runs[0] ??
      null
    );
  }, [activeRunId, detail, runs]);

  const openRun = useCallback(
    async (runId: string) => {
      activeRunRef.current = runId;
      setActiveRunId(runId);
      setView('detail');
      setDetail(null);
      try {
        await loadRun(runId);
      } catch (error) {
        setServiceError(
          error instanceof Error ? error.message : '任务读取失败',
        );
      }
    },
    [loadRun],
  );

  function handleCreated(
    runId: string,
    status: RunStatus,
    createdRuns = [{ runId, status }],
  ) {
    const ids = new Set(createdRuns.map((run) => run.runId));
    setRuns((current) => [
      ...createdRuns.map((run) => run.status),
      ...current.filter((item) => !ids.has(item.runId)),
    ]);
    if (createdRuns.length > 1) {
      activeRunRef.current = null;
      setActiveRunId(null);
      setDetail(null);
      setView('queue');
      void refreshRuns().catch(() => {});
      return;
    }
    activeRunRef.current = runId;
    setActiveRunId(runId);
    setView('detail');
    void loadRun(runId).catch((error) => {
      setServiceError(error instanceof Error ? error.message : '任务读取失败');
    });
  }

  async function approvePaidPhase(reviewedRunId: string, variantIds: string[]) {
    if (!token || !activeRunId) throw new Error('本地会话已失效，请刷新页面');
    if (reviewedRunId !== activeRunRef.current)
      throw new Error('当前模特已切换，请重新核对后确认');
    const response = await fetch(
      `/api/runs/${encodeURIComponent(reviewedRunId)}/approve`,
      {
        method: 'POST',
        headers: {
          'content-type': 'application/json',
          'x-zibuyu-token': token,
        },
        body: JSON.stringify({ confirm: true, variantIds }),
      },
    );
    await readJsonResponse(response);
    await Promise.all([loadRun(reviewedRunId, false), refreshRuns()]);
  }

  const queueCount = runs.filter((run) => run.state !== 'delivered').length;
  const currentLabel =
    view === 'detail' && detail ? detail.intake.sku : VIEW_LABELS[view];

  return (
    <SidebarProvider
      defaultOpen
      className="workbench-shell h-svh min-h-0 overflow-hidden"
      style={
        {
          '--sidebar-width': '15rem',
          '--sidebar-width-icon': '4rem',
        } as CSSProperties
      }
    >
      <WorkspaceSidebar
        view={view}
        queueCount={queueCount}
        status={dockStatus}
        onView={setView}
      />

      <SidebarInset className="workspace-inset h-svh min-h-0 min-w-0 overflow-hidden">
        <header className="glass-bar flex h-17 shrink-0 items-center justify-between border-b px-3 sm:px-5">
          <div className="flex min-w-0 items-center gap-2.5">
            <SidebarTrigger className="shrink-0 text-muted-foreground" />
            <span className="h-5 w-px shrink-0 bg-border" />
            <div className="flex min-w-0 items-center gap-2 text-sm">
              <span className="hidden font-medium text-muted-foreground sm:inline">
                Zibuyu
              </span>
              <ChevronRight className="hidden size-3.5 text-muted-foreground/55 sm:block" />
              <span className="truncate font-semibold">{currentLabel}</span>
            </div>
          </div>
          <div className="flex items-center gap-1.5">
            <ConnectionBadge
              health={health}
              initializing={initializing}
              error={serviceError}
            />
            <Button
              variant="ghost"
              size="icon"
              className="text-muted-foreground"
              onClick={() => void initialize()}
              aria-label="重连本地服务"
              title="重连本地服务"
            >
              <RefreshCw className={initializing ? 'animate-spin' : ''} />
            </Button>
          </div>
        </header>

        <div className="min-h-0 flex-1 overflow-y-auto overscroll-contain">
          <div className="workbench-content px-4 py-6 sm:px-6 lg:px-8 lg:py-7">
            {serviceError ? (
              <div className="mx-auto mb-5 flex max-w-7xl items-center gap-3 rounded-md border border-red-200 bg-red-50 px-4 py-3 text-sm text-red-800">
                <WifiOff className="size-4 shrink-0" />
                <span className="min-w-0 flex-1">{serviceError}</span>
                <button
                  type="button"
                  onClick={() => setServiceError('')}
                  className="shrink-0 text-xs font-semibold"
                >
                  关闭
                </button>
              </div>
            ) : null}

            {view === 'new' ? (
              <div className="mx-auto max-w-[1440px]">
                <NewTaskForm
                  token={token}
                  codexAvailable={Boolean(health?.codexAvailable)}
                  onCreated={handleCreated}
                />
              </div>
            ) : null}

            {view === 'queue' ? (
              <RunList
                title="任务队列"
                description="本地制作单与实时执行状态"
                runs={runs}
                onOpen={openRun}
                onNew={() => setView('new')}
              />
            ) : null}

            {view === 'history' ? (
              <RunList
                title="历史交付"
                description="已完成质检和交付的制作单"
                runs={runs.filter((run) => run.state === 'delivered')}
                onOpen={openRun}
                onNew={() => setView('new')}
              />
            ) : null}

            {view === 'models' ? <ModelLibrary /> : null}

            {view === 'publishing' ? (
              <PublishingCenter
                token={token}
                runs={runs}
                initialRunId={publishRunId}
                onProduction={openRun}
              />
            ) : null}

            {view === 'detail' ? (
              <TaskDetailView
                key={activeRunId}
                onOpenRun={openRun}
                detail={detail?.intake.runId === activeRunId ? detail : null}
                loading={loadingDetail}
                onBack={() => setView('queue')}
                onRefresh={() =>
                  activeRunId && void loadRun(activeRunId).catch(() => {})
                }
                onApprove={approvePaidPhase}
                onPublish={() => {
                  setPublishRunId(activeRunId);
                  setView('publishing');
                }}
              />
            ) : null}
          </div>
        </div>

        <ProgressDock status={dockStatus} />
      </SidebarInset>
    </SidebarProvider>
  );
}

function WorkspaceSidebar({
  view,
  queueCount,
  status,
  onView,
}: {
  view: View;
  queueCount: number;
  status: RunStatus | null;
  onView: (view: View) => void;
}) {
  const { isMobile, setOpenMobile } = useSidebar();
  const items = [
    { view: 'new' as const, label: '新建制作', icon: Sparkles },
    { view: 'queue' as const, label: '任务队列', icon: LayoutDashboard },
    { view: 'publishing' as const, label: '发布中心', icon: CalendarClock },
    { view: 'models' as const, label: '模特库', icon: CircleUserRound },
    { view: 'history' as const, label: '历史交付', icon: Archive },
  ];

  function navigate(nextView: View) {
    onView(nextView);
    if (isMobile) setOpenMobile(false);
  }

  return (
    <Sidebar collapsible="icon" className="border-r-0">
      <SidebarHeader className="h-17 justify-center border-b border-sidebar-border p-0">
        <button
          type="button"
          onClick={() => navigate('new')}
          className="flex h-full w-full items-center px-3 text-left group-data-[collapsible=icon]:justify-center group-data-[collapsible=icon]:px-2"
          aria-label="打开新建制作"
        >
          <span className="hidden size-8 place-items-center rounded-md bg-white text-sm font-black text-[#1c1d20] group-data-[collapsible=icon]:grid">
            Z
          </span>
          <span className="flex min-w-0 items-center gap-2.5 group-data-[collapsible=icon]:hidden">
            <Image
              src="/zibuyu-logo.png"
              alt="Zibuyu"
              width={84}
              height={32}
              priority
              className="h-7 w-auto shrink-0 object-contain"
            />
            <span className="border-l border-slate-300 pl-2 text-xs font-semibold uppercase leading-3 text-slate-500">
              Studio
            </span>
          </span>
        </button>
      </SidebarHeader>

      <SidebarContent className="px-2 py-3">
        <SidebarGroup className="p-0">
          <SidebarGroupLabel className="px-2 text-xs uppercase text-slate-500 group-data-[collapsible=icon]:hidden">
            工作区
          </SidebarGroupLabel>
          <SidebarGroupContent>
            <SidebarMenu className="gap-1">
              {items.map((item) => {
                const Icon = item.icon;
                const active =
                  item.view === 'queue'
                    ? view === 'queue' || view === 'detail'
                    : view === item.view;
                return (
                  <SidebarMenuItem key={item.view}>
                    <SidebarMenuButton
                      tooltip={item.label}
                      isActive={active}
                      onClick={() => navigate(item.view)}
                      className="h-10 text-slate-600 hover:text-slate-950 data-active:text-blue-700"
                    >
                      <Icon />
                      <span>{item.label}</span>
                    </SidebarMenuButton>
                    {item.view === 'queue' && queueCount ? (
                      <SidebarMenuBadge className="text-slate-500 peer-data-active/menu-button:text-[#0071e3]">
                        {queueCount}
                      </SidebarMenuBadge>
                    ) : null}
                  </SidebarMenuItem>
                );
              })}
            </SidebarMenu>
          </SidebarGroupContent>
        </SidebarGroup>
      </SidebarContent>

      <SidebarFooter className="border-t border-sidebar-border p-3 group-data-[collapsible=icon]:p-2">
        <div className="group-data-[collapsible=icon]:hidden">
          <div className="flex items-center justify-between gap-3 text-xs text-slate-500">
            <span>当前任务</span>
            <span className="tabular-nums">
              {status ? `${status.progress}%` : '--'}
            </span>
          </div>
          <p className="mt-1.5 truncate text-xs font-semibold text-slate-800">
            {status?.sku ?? '等待制作单'}
          </p>
          <p className="mt-1 truncate text-xs text-slate-500">
            {status?.stageLabel ?? '尚未进入执行流程'}
          </p>
          <Progress
            value={status?.progress ?? 0}
            className="mt-3 [&_[data-slot=progress-track]]:bg-white/10 [&_[data-slot=progress-indicator]]:bg-[#0071e3]"
          />
        </div>
        <div className="hidden h-8 items-center justify-center group-data-[collapsible=icon]:flex">
          <Activity
            className={`size-4 ${status ? 'text-[#0071e3]' : 'text-slate-400'}`}
          />
        </div>
      </SidebarFooter>
      <SidebarRail />
    </Sidebar>
  );
}

function ConnectionBadge({
  health,
  initializing,
  error,
}: {
  health: Health | null;
  initializing: boolean;
  error: string;
}) {
  const bridgeReady = Boolean(health?.ok);
  const connected = bridgeReady && health?.codexAvailable;
  const label = initializing
    ? '连接中'
    : connected
      ? health?.mode === 'mock'
        ? '本地验收模式'
        : '本地服务已连接'
      : bridgeReady
        ? 'Codex CLI 未就绪'
        : error
          ? '桥接未启动'
          : '桥接未连接';
  return (
    <Badge
      variant="outline"
      title={connected ? '每批制作创建独立 Codex 会话' : undefined}
      className="h-7 rounded-md border-border bg-white px-2.5 text-muted-foreground"
    >
      <span
        className={`size-1.5 rounded-full ${
          connected
            ? 'bg-emerald-600'
            : initializing || bridgeReady
              ? 'bg-amber-500'
              : 'bg-red-500'
        }`}
      />
      <span className="hidden sm:inline">{label}</span>
    </Badge>
  );
}

function RunList({
  title,
  description,
  runs,
  onOpen,
  onNew,
}: {
  title: string;
  description: string;
  runs: RunStatus[];
  onOpen: (runId: string) => void;
  onNew: () => void;
}) {
  const activeCount = runs.filter((run) =>
    ['queued', 'running', 'submitting'].includes(run.state),
  ).length;
  const approvalCount = runs.filter(
    (run) => run.state === 'awaiting_paid_approval',
  ).length;
  const deliveredCount = runs.filter((run) => run.state === 'delivered').length;
  const groups = new Map<string, RunStatus[]>();
  for (const run of runs) {
    const key = run.modelBatchId || run.runId;
    groups.set(key, [...(groups.get(key) ?? []), run]);
  }

  return (
    <div className="mx-auto max-w-7xl">
      <div className="mb-5 flex items-end justify-between gap-4">
        <div>
          <p className="text-sm text-muted-foreground">{description}</p>
          <h1 className="mt-1 text-[30px] font-semibold tracking-tight">
            {title}
          </h1>
        </div>
        <Button className="bg-[#0071e3] hover:bg-[#005bb8]" onClick={onNew}>
          <Plus />
          新建制作
        </Button>
      </div>

      <div className="mb-5 grid overflow-hidden rounded-2xl border border-border bg-white sm:grid-cols-3 sm:divide-x sm:divide-border">
        <Metric label="执行中" value={activeCount} tone="brand" />
        <Metric label="待确认" value={approvalCount} tone="warning" />
        <Metric label="已交付" value={deliveredCount} tone="success" />
      </div>

      {runs.length ? (
        <div className="overflow-hidden rounded-2xl border border-border bg-white">
          <div className="hidden grid-cols-[1.25fr_1.15fr_140px_130px_24px] gap-4 border-b border-border bg-muted/55 px-4 py-2.5 text-xs font-semibold text-muted-foreground md:grid">
            <span>货号 / 任务</span>
            <span>当前流程</span>
            <span>更新时间</span>
            <span>状态</span>
            <span />
          </div>
          {[...groups.entries()].map(([key, members]) => (
            <div key={key}>
              {members[0].modelBatchId ? (
                <div className="flex flex-wrap items-center justify-between gap-2 border-b border-border bg-blue-50/60 px-4 py-3 text-sm">
                  <span className="font-semibold">
                    {members[0].sku} · 多模特批次
                  </span>
                  <span className="text-muted-foreground">
                    {members.map((member) => member.modelPreset).join('、')} ·
                    本列表{' '}
                    {members.reduce(
                      (count, member) => count + member.variantCount,
                      0,
                    )}{' '}
                    条视频
                  </span>
                </div>
              ) : null}
              {members.map((run) => (
                <button
                  key={run.runId}
                  type="button"
                  onClick={() => onOpen(run.runId)}
                  className="grid w-full gap-3 border-b border-border px-4 py-4 text-left transition-colors last:border-b-0 hover:bg-muted/35 md:grid-cols-[1.25fr_1.15fr_140px_130px_24px] md:items-center md:gap-4"
                >
                  <span className="min-w-0">
                    <span className="block truncate font-semibold">
                      {run.sku} · {run.modelPreset}
                    </span>
                    <span className="mt-1 block truncate font-mono text-xs text-muted-foreground">
                      {run.runId}
                    </span>
                  </span>
                  <span className="min-w-0 text-sm">
                    <span className="block truncate font-medium">
                      {run.stageLabel}
                    </span>
                    <span className="mt-2 flex items-center gap-2">
                      <span className="h-1 min-w-0 flex-1 overflow-hidden rounded-full bg-muted">
                        <span
                          className="block h-full bg-[#0071e3]"
                          style={{
                            width: `${Math.max(0, Math.min(100, run.progress))}%`,
                          }}
                        />
                      </span>
                      <span className="shrink-0 text-xs tabular-nums text-muted-foreground">
                        {run.stageIndex}/{run.totalStages}
                      </span>
                    </span>
                  </span>
                  <span className="text-sm text-muted-foreground">
                    {formatTime(run.updatedAt)}
                  </span>
                  <span>
                    <StatusBadge state={run.state} />
                  </span>
                  <ChevronRight className="hidden size-4 text-muted-foreground md:block" />
                </button>
              ))}
            </div>
          ))}
        </div>
      ) : (
        <div className="grid min-h-64 place-items-center rounded-md border border-dashed border-border bg-white text-center">
          <div>
            <LayoutDashboard className="mx-auto size-6 text-muted-foreground" />
            <p className="mt-3 text-sm font-medium">暂无制作单</p>
          </div>
        </div>
      )}
    </div>
  );
}

function Metric({
  label,
  value,
  tone,
}: {
  label: string;
  value: number;
  tone: 'brand' | 'warning' | 'success';
}) {
  const dot = {
    brand: 'bg-[#0071e3]',
    warning: 'bg-amber-500',
    success: 'bg-emerald-600',
  }[tone];
  return (
    <div className="flex min-h-20 items-center justify-between border-b border-border px-4 last:border-b-0 sm:border-b-0">
      <span className="flex items-center gap-2 text-sm text-muted-foreground">
        <span className={`size-2 rounded-full ${dot}`} />
        {label}
      </span>
      <strong className="text-[30px] font-semibold tracking-tight tabular-nums">
        {value}
      </strong>
    </div>
  );
}

function ModelLibrary() {
  return (
    <div className="mx-auto max-w-7xl">
      <div className="mb-5">
        <p className="text-sm text-muted-foreground">PopBoom 固定模特预设</p>
        <h1 className="mt-1 text-[30px] font-semibold tracking-tight">
          模特库
        </h1>
      </div>
      <div className="grid gap-3 sm:grid-cols-2 xl:grid-cols-3">
        {MODELS.map((model) => (
          <article
            key={model.id}
            className="overflow-hidden rounded-2xl border border-border bg-white transition-colors hover:border-[#9ec7f4]"
          >
            <div className="h-1 bg-[#0071e3]" />
            <div className="p-4">
              <div className="flex items-start justify-between gap-3">
                <div className="grid size-11 place-items-center rounded-md bg-[#0071e3] text-base font-semibold text-white">
                  {model.id}
                </div>
                <Badge variant="outline" className="rounded-md bg-muted/45">
                  {model.marketCode}
                </Badge>
              </div>
              <h2 className="mt-4 font-semibold">
                {model.id} · {model.tone}
              </h2>
              <p className="mt-2 text-sm text-muted-foreground">
                {model.fitStats}
              </p>
              <div className="mt-4 flex items-center justify-between border-t border-border pt-3 text-xs text-muted-foreground">
                <span>{model.locale}</span>
                <span>Size {model.size}</span>
              </div>
            </div>
          </article>
        ))}
      </div>
    </div>
  );
}
