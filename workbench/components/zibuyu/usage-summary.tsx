import type { UsageSummary } from '@/lib/zibuyu';

const phaseNames: Record<string, string> = {
  prepare: '制作准备',
  paid: '生成与质检',
  classify: '颜色识别',
  'publish-prepare': '排期准备',
  'publish-submit': '发布提交',
  'publish-reconcile': '发布核对',
};
const count = (value: number | null | undefined) =>
  value == null ? '未提供' : value.toLocaleString('zh-CN');
const receipts = (
  attempt: NonNullable<UsageSummary>['attempts'][number],
  key: 'inputTokens' | 'cachedInputTokens' | 'outputTokens',
) =>
  attempt.reportedUsage?.length
    ? attempt.reportedUsage.map((row) => count(row[key])).join(' / ')
    : '未提供';

export function UsageSummaryCard({
  usage,
  classification,
}: {
  usage?: UsageSummary;
  classification?: UsageSummary | null;
}) {
  return (
    <section className="rounded-2xl border border-border bg-white px-4 py-5 sm:px-5">
      <h2 className="text-base font-semibold">Token 用量</h2>
      {!usage?.attemptCount ? (
        <p className="mt-3 text-sm text-muted-foreground">
          新执行阶段结束后会记录用量。历史任务没有记录时，不估算补填。
        </p>
      ) : (
        <>
          <p className="mt-2 text-sm text-muted-foreground">
            {usage.attemptCount} 次执行（含失败尝试）。
            {usage.usageComplete
              ? '已收到各次执行的用量回执。'
              : '仍有执行未结束或用量未提供，当前记录不完整。'}
          </p>
          <div className="mt-4 overflow-x-auto">
            <table className="w-full min-w-[620px] text-left text-sm tabular-nums">
              <thead>
                <tr className="border-b border-border text-muted-foreground">
                  <th className="pb-3 font-medium">阶段 / 模型</th>
                  <th className="pb-3 font-medium">输入</th>
                  <th className="pb-3 font-medium">其中缓存</th>
                  <th className="pb-3 font-medium">输出</th>
                  <th className="pb-3 font-medium">耗时</th>
                </tr>
              </thead>
              <tbody>
                {usage.attempts.map((attempt) => (
                  <tr
                    key={attempt.attemptId}
                    className="border-b border-border last:border-0"
                  >
                    <td className="py-3 pr-3">
                      <p>
                        {phaseNames[attempt.phase] ?? attempt.phase} ·{' '}
                        {attempt.state === 'running'
                          ? '执行中'
                          : attempt.state === 'failed'
                            ? '失败'
                            : '结束'}
                      </p>
                      <p className="mt-1 text-xs text-muted-foreground">
                        {attempt.model} / {attempt.reasoningEffort}
                      </p>
                    </td>
                    <td className="py-3 pr-3">
                      {receipts(attempt, 'inputTokens')}
                    </td>
                    <td className="py-3 pr-3">
                      {receipts(attempt, 'cachedInputTokens')}
                    </td>
                    <td className="py-3 pr-3">
                      {receipts(attempt, 'outputTokens')}
                    </td>
                    <td className="py-3">
                      {attempt.durationMs == null
                        ? '未结束'
                        : `${Math.round(attempt.durationMs / 1000)} 秒`}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
          {usage.unreadableAttempts > 0 ? (
            <p className="mt-2 text-sm text-destructive">
              有 {usage.unreadableAttempts} 份用量记录无法读取。
            </p>
          ) : null}
        </>
      )}
      {classification?.attemptCount ? (
        <div className="mt-3 text-sm text-muted-foreground">
          关联颜色识别（独立记录）：
          {classification.attempts.map((attempt) => (
            <p key={attempt.attemptId}>
              输入 {receipts(attempt, 'inputTokens')}，输出{' '}
              {receipts(attempt, 'outputTokens')} Token。
            </p>
          ))}
        </div>
      ) : null}
      <p className="mt-3 text-xs leading-5 text-muted-foreground">
        显示 CLI 回报值；多条回执用 /
        分隔，缓存属于输入。续跑的累计口径及子代理覆盖尚未核实，因此暂不合计为批次消耗，也不换算订阅额度或账单。
      </p>
    </section>
  );
}
