// Audit query evidence, never the publish response or the local request echo.
import { verifiedPublishCall } from './publish-evidence.mjs';
const aliases = {
  scheduleId: ['schedule_id', 'scheduleId', '排期 ID', '排期ID'],
  logId: ['log_id', 'logId', '发布记录 ID', '发布记录ID'],
  channelId: ['channel_id', 'channelId', '渠道 ID', '渠道ID'],
  productId: ['product_id', 'productId', '商品 ID', '商品ID', 'PID'],
  caption: ['video_title', 'videoTitle', 'caption', '标题', '视频标题'],
  scheduledTime: ['scheduled_time', 'scheduledTime', '计划发布时间'],
  fileId: ['tiktok_file_id', 'file_id', 'TikTok 文件 ID'],
  videoId: ['tiktok_video_id', 'TikTok 视频 ID'],
  videoUrl: ['video_url', '视频 URL'],
  cover: ['cover_timestamp_ms'],
  music: ['music_id'],
  ai: ['is_ai_generated'],
  status: ['status', '状态', '定时发布状态', '发布任务状态'],
};
const lookup = new Map(
  Object.entries(aliases).flatMap(([field, names]) =>
    names.map((name) => [name, field]),
  ),
);
const fieldIcons = {
  scheduleId: '📋',
  logId: '📋',
  channelId: '📺',
  productId: '🛍',
  caption: '🎬',
  scheduledTime: '🕐',
  fileId: '📎',
  videoId: '🆔',
  videoUrl: '🎥',
  status: '📌⏳🎉❌📤🔍',
};
const states = {
  pending: 'pending',
  scheduled: 'scheduled',
  published: 'published',
  failed: 'failed',
  cancelled: 'cancelled',
  uploading: 'uploading',
  uploaded: 'uploaded',
  prechecking: 'prechecking',
  publishing: 'publishing',
  等待发布: 'pending',
  已排期: 'scheduled',
  已发布: 'published',
  失败: 'failed',
  已取消: 'cancelled',
  上传中: 'uploading',
  已上传: 'uploaded',
  预检中: 'prechecking',
  发布中: 'publishing',
};

export function explicitInstant(value) {
  if (typeof value !== 'string') return null;
  const match = value.match(
    /^(\d{4}-\d{2}-\d{2})[T ](\d{2}:\d{2}:\d{2})(\.\d{1,3})?\s*(Z|[+-]\d{2}:?\d{2})$/i,
  );
  if (!match) return null;
  const [, date, time, fraction = '', zone] = match;
  const calendar = Date.parse(`${date}T${time}Z`);
  if (
    !Number.isFinite(calendar) ||
    new Date(calendar).toISOString().slice(0, 19) !== `${date}T${time}`
  )
    return null;
  const normalizedZone =
    zone.toUpperCase() === 'Z'
      ? 'Z'
      : zone.replace(/([+-]\d{2}):?(\d{2})/, '$1:$2');
  if (normalizedZone === '-00:00') return null;
  if (normalizedZone !== 'Z') {
    const hour = Number(normalizedZone.slice(1, 3));
    const minute = Number(normalizedZone.slice(4));
    if (hour > 14 || minute > 59 || (hour === 14 && minute !== 0)) return null;
  }
  const milliseconds = Date.parse(
    `${date}T${time}${fraction}${normalizedZone}`,
  );
  return Number.isFinite(milliseconds)
    ? { milliseconds, iso: new Date(milliseconds).toISOString() }
    : null;
}

export function normalizeCheckPublishResult(result) {
  const values = {};
  const issues = [];
  function add(field, value) {
    if (value === undefined || value === null) return;
    if (typeof value === 'number' && !Number.isSafeInteger(value)) {
      issues.push(`${field} 数字超出安全整数范围，无法精确核对`);
      return;
    }
    if (!['string', 'number', 'boolean'].includes(typeof value)) return;
    const string = field === 'caption' ? String(value) : String(value).trim();
    const normalized =
      field === 'status'
        ? states[string.toLowerCase()] || string.toLowerCase()
        : string;
    (values[field] ||= []).push(normalized);
  }
  function text(value) {
    try {
      walk(JSON.parse(value));
      return;
    } catch {
      /* Human-readable MCP content. */
    }
    const lines = value.replace(/\r\n/g, '\n').split('\n');
    let caption = null;
    function flush() {
      if (caption !== null) {
        while (caption.at(-1) === '') caption.pop();
        add('caption', caption.join('\n'));
      }
      caption = null;
    }
    for (const line of lines) {
      const label = line.match(/^[^\p{L}\p{N}_]*([^:：]+)[:：] ?(.*)$/u);
      const candidate = label && lookup.get(label[1].trim());
      // Known platform field markers are required. Bare text inside a caption
      // such as "计划发布时间: ..." must never become query metadata.
      const field =
        candidate &&
        [...(fieldIcons[candidate] || '')].some(
          (icon) =>
            /\p{Extended_Pictographic}/u.test(icon) &&
            line.trimStart().startsWith(icon),
        )
          ? candidate
          : null;
      const boundary =
        field ||
        (label &&
          /^[^\p{L}\p{N}]*\p{Extended_Pictographic}/u.test(line) &&
          /^(商品标题|TikTok 视频 ID|TikTok 文件 ID|创建时间|发布时间|错误信息)$/.test(
            label[1].trim(),
          )) ||
        /^\s*💡/.test(line);
      if (boundary) flush();
      if (field === 'caption') caption = [label[2]];
      else if (field) add(field, label[2]);
      else if (caption !== null) {
        caption.push(line);
      }
    }
    // Text output uses blank lines as section separators. Interior newlines stay intact.
    if (caption !== null) flush();
  }
  function walk(value) {
    if (typeof value === 'string') return text(value);
    if (!value || typeof value !== 'object' || Array.isArray(value)) return;
    if (value.isError === true) issues.push('check_publish 返回错误');
    for (const [name, field] of lookup)
      if (Object.hasOwn(value, name)) add(field, value[name]);
    for (const wrapper of ['result', 'data', 'structuredContent'])
      if (value[wrapper]) walk(value[wrapper]);
    if (Array.isArray(value.content))
      for (const block of value.content)
        if (block.type === 'text') text(block.text);
  }
  walk(result);
  const observed = {};
  for (const [field, candidates] of Object.entries(values)) {
    const unique = [...new Set(candidates)];
    observed[field] = unique[0];
    if (
      unique.length > 1 &&
      !(
        field === 'scheduledTime' &&
        unique.every(
          (value) =>
            explicitInstant(value)?.iso &&
            explicitInstant(value).iso === explicitInstant(unique[0])?.iso,
        )
      )
    )
      issues.push(`${field} 回读包含互相冲突的值`);
  }
  return { observed, issues };
}

export function auditSchedule(action, now = Date.now(), events = []) {
  const evidence = action.verificationEvidence;
  const tool = evidence?.tool || evidence?.toolName || evidence?.tool_name;
  const queryEvent = verifiedPublishCall(action, evidence, events);
  const query =
    ['check_publish', 'mcp__PopBoom__check_publish'].includes(tool) &&
    Boolean(queryEvent);
  const parsed = normalizeCheckPublishResult(
    evidence?.result ?? evidence?.data ?? evidence,
  );
  const observed = parsed.observed;
  const expected = {
    scheduleId: action.scheduleId || action.schedule_id || null,
    logId: action.logId || action.log_id || null,
    scheduledTime: action.request?.scheduled_time ?? null,
    channelId: action.request?.channel_id ?? null,
    productId: action.request?.product_id ?? null,
    caption: action.request?.video_title ?? null,
    videoUrl: action.request?.video_url ?? null,
  };
  const checks = [];
  const check = (field, status, message) =>
    checks.push({ field, status, message });
  check(
    'source',
    query ? 'passed' : 'needs_review',
    query
      ? '已匹配运行器归档的 check_publish 调用、查询ID与完整结果摘要'
      : '未匹配运行器独立 check_publish 完成事件；自填来源、创建回显和本地清单不能作为证据',
  );
  const queriedAt = queryEvent?._zibuyuReceipt?.receivedAt || null;
  const timestamp = explicitInstant(queriedAt);
  const requiredValue =
    action.verificationRequiredAfter ||
    action.dispatchStartedAt ||
    action.submittedAt ||
    action.claimedAt;
  const requiredAt = explicitInstant(requiredValue);
  const notFuture = timestamp && timestamp.milliseconds <= now;
  const fresh =
    notFuture &&
    (!requiredValue ||
      (requiredAt && timestamp.milliseconds >= requiredAt.milliseconds));
  check(
    'queriedAt',
    fresh ? 'passed' : 'needs_review',
    fresh
      ? '查询时间有效；审核结论对应此次回读时间'
      : !timestamp
        ? '查询时间缺失、无时区或无效'
        : !notFuture
          ? '查询时间在未来'
          : !requiredAt
            ? '本次提交或核查起点时间无效'
            : '查询早于本次提交或本次核查，需重新只读回查',
  );
  let matchingId = false;
  let conflictingId = false;
  for (const field of ['scheduleId', 'logId']) {
    if (expected[field] != null && observed[field] != null) {
      if (String(expected[field]) === observed[field]) matchingId = true;
      else conflictingId = true;
    }
  }
  check(
    'id',
    conflictingId ? 'mismatch' : matchingId ? 'passed' : 'needs_review',
    conflictingId
      ? '回读 ID 与本次动作不匹配'
      : matchingId
        ? '回读 ID 与本次动作一致'
        : '未取得与本次动作匹配的回读 ID',
  );
  const expectedInstant = explicitInstant(expected.scheduledTime);
  // PopBoom operational contract confirmed by user on 2026-09-11.
  // Apply only to genuine check_publish evidence and explicit Beijing requests.
  const beijingReadback =
    query &&
    (expected.scheduledTime || '').endsWith('+08:00') &&
    /^\d{4}-\d{2}-\d{2}[T ]\d{2}:\d{2}:\d{2}$/.test(
      observed.scheduledTime || '',
    );
  const observedInstant =
    explicitInstant(observed.scheduledTime) ||
    (beijingReadback
      ? explicitInstant(observed.scheduledTime + '+08:00')
      : null);
  observed.timezoneBasis = beijingReadback
    ? 'popboom_beijing_caption_v1:user_confirmed'
    : 'explicit_offset';
  expected.scheduledInstant = expectedInstant?.iso || null;
  observed.scheduledInstant = observedInstant?.iso || null;
  check(
    'scheduledTime',
    !expectedInstant || !observedInstant
      ? 'needs_review'
      : expectedInstant.milliseconds === observedInstant.milliseconds
        ? 'passed'
        : 'mismatch',
    !expectedInstant || !observedInstant
      ? '计划发布时间缺失、无明确 UTC offset/Z 或无效；不能套用主机或账号时区'
      : expectedInstant.milliseconds === observedInstant.milliseconds
        ? '计划发布时间对应同一实际时刻'
        : '实际计划发布时间与授权时间不一致',
  );
  for (const field of ['channelId', 'productId', 'caption']) {
    const missing =
      expected[field] == null ||
      expected[field] === '' ||
      observed[field] == null ||
      observed[field] === '';
    const equal = !missing && String(expected[field]) === observed[field];
    check(
      field,
      missing ? 'needs_review' : equal ? 'passed' : 'mismatch',
      missing
        ? `${field} 缺少完整回读，不能用请求补齐`
        : equal
          ? `${field} 与授权请求完全一致`
          : `${field} 与授权请求不匹配`,
    );
  }
  const creationEvent = verifiedPublishCall(
    action,
    action.creationEvidence,
    events,
    'publish_video',
  );
  const creation = creationEvent
    ? normalizeCheckPublishResult(creationEvent.item.result)
    : null;
  const creationTime = explicitInstant(
    creationEvent?._zibuyuReceipt?.receivedAt,
  );
  const creationMatchesId =
    creation &&
    ['scheduleId', 'logId'].some(
      (field) =>
        expected[field] != null &&
        creation.observed[field] === String(expected[field]),
    ) &&
    !['scheduleId', 'logId'].some(
      (field) =>
        expected[field] != null &&
        creation.observed[field] != null &&
        creation.observed[field] !== String(expected[field]),
    );
  const mapped =
    creationMatchesId &&
    !creation.issues.length &&
    creationTime &&
    timestamp &&
    creationTime.milliseconds <= timestamp.milliseconds;
  if (mapped) {
    expected.fileId = creation.observed.fileId || null;
    expected.videoId = creation.observed.videoId || null;
  }
  const videoPairs = ['videoUrl', 'fileId', 'videoId'].filter(
    (field) => expected[field] && observed[field],
  );
  const videoMismatch = videoPairs.some(
    (field) => expected[field] !== observed[field],
  );
  check(
    'videoIdentity',
    videoMismatch ? 'mismatch' : videoPairs.length ? 'passed' : 'needs_review',
    videoMismatch
      ? '回读视频身份与授权视频或对应创建回执不匹配'
      : videoPairs.length
        ? '视频URL或同类平台视频ID与本条授权链一致'
        : '缺少视频URL或对应上传/创建回执的平台视频ID，不能用标题或颜色代替',
  );
  for (const [field, key] of [
    ['cover', 'cover_timestamp_ms'],
    ['music', 'music_id'],
    ['ai', 'is_ai_generated'],
  ]) {
    if (observed[field] == null)
      check(field, 'not_returned', `${field} 平台未返回`);
    else if (action.request?.[key] == null)
      check(field, 'not_returned', `${field} 无同类授权字段可比较`);
    else
      check(
        field,
        observed[field] === String(action.request[key]) ? 'passed' : 'mismatch',
        `${field} 与授权参数${observed[field] === String(action.request[key]) ? '一致' : '不一致'}`,
      );
  }
  const ready = ['pending', 'scheduled', 'published'].includes(observed.status);
  check(
    'platformStatus',
    ready
      ? 'passed'
      : ['failed', 'cancelled'].includes(observed.status)
        ? 'mismatch'
        : 'needs_review',
    ready
      ? '平台已接受排期或已发布'
      : `平台状态尚未通过审核：${observed.status || '缺失'}`,
  );
  for (const issue of parsed.issues) check('response', 'needs_review', issue);
  const status = checks.some((item) => item.status === 'mismatch')
    ? 'mismatch'
    : checks.every((item) => ['passed', 'not_returned'].includes(item.status))
      ? 'passed'
      : 'needs_review';
  // A stale query can still establish the historical fact of publication. Its audit remains pending.
  const platformState =
    query &&
    matchingId &&
    !conflictingId &&
    notFuture &&
    !parsed.issues.some((issue) =>
      /^(scheduleId|logId|status|check_publish)/.test(issue),
    )
      ? observed.status || null
      : null;
  return {
    status,
    checkedAt: new Date(now).toISOString(),
    queriedAt,
    platformState,
    expected,
    observed,
    checks,
    reasons: checks
      .filter((item) => ['needs_review', 'mismatch'].includes(item.status))
      .map((item) => item.message),
  };
}

export function observedAction(action, now = Date.now(), events = []) {
  const postScheduleAudit = auditSchedule(action, now, events);
  const platformState = postScheduleAudit.platformState;
  const hasId = Boolean(
    action.scheduleId || action.logId || action.schedule_id || action.log_id,
  );
  const state =
    platformState === 'published'
      ? 'published'
      : ['pending', 'scheduled'].includes(platformState)
        ? 'scheduled'
        : ['failed', 'cancelled'].includes(platformState)
          ? 'publish_failed'
          : hasId
            ? 'receipt_received'
            : ['claimed', 'not_dispatched'].includes(action.state)
              ? action.state
              : 'submission_unknown';
  return {
    ...action,
    scheduleId: action.scheduleId || action.schedule_id || null,
    logId: action.logId || action.log_id || null,
    state,
    platformState,
    postScheduleAudit,
  };
}

export function summarizePublishingActions(actions) {
  const passed = actions.filter(
    (action) => action.postScheduleAudit?.status === 'passed',
  ).length;
  const mismatch = actions.filter(
    (action) => action.postScheduleAudit?.status === 'mismatch',
  ).length;
  const auditStatus = mismatch
    ? 'mismatch'
    : actions.length && passed === actions.length
      ? 'passed'
      : 'needs_review';
  const published = actions.filter(
    (action) => action.state === 'published',
  ).length;
  let state = 'receipt_received';
  if (actions.length && published === actions.length) state = 'published';
  else if (mismatch) state = 'schedule_mismatch';
  else if (actions.some((action) => action.state === 'submission_unknown'))
    state = 'submission_unknown';
  else if (actions.some((action) => action.state === 'publish_failed'))
    state = 'publish_failed';
  else if (
    auditStatus === 'passed' &&
    actions.every((action) => ['scheduled', 'published'].includes(action.state))
  )
    state = 'scheduled';
  else if (actions.length) state = 'schedule_needs_review';
  const auditNote =
    auditStatus === 'passed'
      ? '排期已审核'
      : auditStatus === 'mismatch'
        ? '排期不匹配，已停止后续提交，仅继续只读核查'
        : '排期待审核，保留现有回执，不会自动重发';
  return {
    state,
    note: `${published ? `${published}/${actions.length} 条已由平台确认发布；` : ''}${auditNote}`,
    postScheduleAudit: {
      status: auditStatus,
      passed,
      mismatch,
      needsReview: actions.length - passed - mismatch,
      total: actions.length,
    },
  };
}

export function canContinueScheduling(actions, now = Date.now(), events = []) {
  return actions.every(
    (action) =>
      (['claimed', 'not_dispatched'].includes(action.state) &&
        !action.scheduleId &&
        !action.logId &&
        !action.schedule_id &&
        !action.log_id) ||
      observedAction(action, now, events).postScheduleAudit.status === 'passed',
  );
}
