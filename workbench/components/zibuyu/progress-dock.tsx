import { Clock3, RefreshCw } from 'lucide-react';

import { Progress } from '@/components/ui/progress';
import { RunStatus, WORKFLOW_STAGES } from '@/lib/zibuyu';

export function ProgressDock({ status }: { status: RunStatus | null }) {
  const progress = Math.max(0, Math.min(100, status?.progress ?? 0));
  const delivered = status?.state === 'delivered';

  return (
    <footer className="glass-bar glass-dock z-30 shrink-0">
      <div className="grid min-h-27 items-center gap-3 px-4 py-3 sm:min-h-19 sm:grid-cols-[minmax(180px,0.9fr)_minmax(280px,2fr)_auto] sm:px-5 lg:px-6">
        <div className="min-w-0">
          <p className="text-[11px] uppercase text-slate-500">
            {status ? `货号 ${status.sku}` : '当前任务'}
          </p>
          <p className="mt-1 truncate text-sm font-semibold">
            {status?.currentTask ?? '等待提交制作单'}
          </p>
        </div>

        <div className="min-w-0">
          <div className="mb-2.5 flex items-center justify-between gap-3 text-xs">
            <span className="truncate text-slate-600">
              {status?.stageLabel ?? '尚未进入执行流程'}
            </span>
            <span className="shrink-0 font-semibold tabular-nums text-slate-900">
              {delivered ? WORKFLOW_STAGES.length : (status?.stageIndex ?? 0)} /{' '}
              {WORKFLOW_STAGES.length}
            </span>
          </div>
          <Progress
            value={progress}
            aria-label="任务总进度"
            className="[&_[data-slot=progress-track]]:h-1.5 [&_[data-slot=progress-track]]:bg-blue-100 [&_[data-slot=progress-indicator]]:bg-[#0071e3]"
          />
        </div>

        <div className="flex min-w-43 items-center gap-2 border-slate-200 text-sm sm:border-l sm:pl-5">
          {status ? (
            <Clock3 className="size-4 shrink-0 text-[#0071e3]" />
          ) : (
            <RefreshCw className="size-4 shrink-0 text-slate-400" />
          )}
          <span className="text-slate-500">预计剩余</span>
          <span className="font-semibold tabular-nums">
            {status?.etaLabel ?? '--'}
          </span>
        </div>
      </div>
    </footer>
  );
}
