import assert from 'node:assert/strict';
import test from 'node:test';

import {
  WORKFLOW_STAGES,
  estimateRemainingSeconds,
  etaLabel,
  getProgress,
} from '../server/workflow.mjs';

test('workflow exposes the nine visible stages in order', () => {
  assert.equal(WORKFLOW_STAGES.length, 9);
  assert.equal(WORKFLOW_STAGES[0].key, 'intake_validation');
  assert.equal(WORKFLOW_STAGES[6].key, 'paid_approval');
  assert.equal(WORKFLOW_STAGES[8].key, 'quality_and_delivery');
});

test('progress pauses at 70 percent for paid approval', () => {
  assert.equal(getProgress('intake_validation', 'running'), 0);
  assert.equal(getProgress('paid_approval', 'awaiting_paid_approval'), 70);
  assert.equal(getProgress('popboom_generation', 'running'), 70);
  assert.equal(getProgress('quality_and_delivery', 'running'), 90);
  assert.equal(getProgress('quality_and_delivery', 'delivered'), 100);
});

test('remaining estimate scales with color count and labels approval waits', () => {
  assert.ok(
    estimateRemainingSeconds('intake_validation', 3) >
      estimateRemainingSeconds('intake_validation', 1),
  );
  assert.match(etaLabel(0, true), /待确认/);
  assert.equal(etaLabel(null), '--');
});
