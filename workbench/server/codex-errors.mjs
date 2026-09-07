// Only terminal process errors belong in the failure shown to the user.
// item.completed errors can be nonfatal startup/skill warnings.
export function codexFailureFromEvent(event) {
  if (!['error', 'turn.failed'].includes(event?.type)) return null;
  let detail = event.error ?? event.message;
  for (let depth = 0; depth < 4; depth += 1) {
    if (typeof detail === 'string') {
      try {
        detail = JSON.parse(detail);
        continue;
      } catch {
        break;
      }
    }
    if (detail?.error) {
      detail = detail.error;
      continue;
    }
    if (typeof detail?.message === 'string' && !detail.code) {
      detail = detail.message;
      continue;
    }
    break;
  }
  const message = String(
    typeof detail === 'string' ? detail : (detail?.message ?? ''),
  )
    .replace(/\s+/g, ' ')
    .trim()
    .slice(0, 500);
  if (!message) return null;
  const code = /^[a-z0-9_]+$/i.test(detail?.code ?? '') ? detail.code : null;
  return { code, message };
}

export function classificationFailureText(execution) {
  if (execution.failure?.code === 'invalid_json_schema') {
    return '颜色识别的结果格式配置不兼容，服务拒绝了请求；请更新工作台后重新识别。';
  }
  if (execution.failure?.message) {
    return `颜色识别未完成：${execution.failure.message}`;
  }
  return `颜色识别中断（Codex 退出码 ${execution.exitCode}${execution.signal ? `，信号 ${execution.signal}` : ''}），请重试。`;
}
