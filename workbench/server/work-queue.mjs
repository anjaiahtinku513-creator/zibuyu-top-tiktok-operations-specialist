// Bounded workers with promise deduplication and resource exclusion.
export function createWorkQueue({ concurrency = 3 } = {}) {
  if (!Number.isInteger(concurrency) || concurrency < 1)
    throw new Error('Invalid concurrency');
  const jobs = new Map(),
    pending = [],
    active = new Map(),
    resources = new Set();
  function drain() {
    while (active.size < concurrency) {
      const index = pending.findIndex((job) => !resources.has(job.resource));
      if (index < 0) return;
      const job = pending.splice(index, 1)[0];
      active.set(job.key, job);
      resources.add(job.resource);
      void Promise.resolve()
        .then(job.task)
        .then(job.resolve, job.reject)
        .finally(() => {
          active.delete(job.key);
          resources.delete(job.resource);
          jobs.delete(job.key);
          drain();
        });
    }
  }
  return {
    enqueue(key, task, resource = key) {
      if (jobs.has(key)) return jobs.get(key).promise;
      let resolve, reject;
      const promise = new Promise((yes, no) => {
        resolve = yes;
        reject = no;
      });
      const job = { key, resource, task, promise, resolve, reject };
      jobs.set(key, job);
      pending.push(job);
      drain();
      return promise;
    },
    snapshot() {
      return {
        concurrency,
        active: [...active.keys()],
        queued: pending.map((job) => job.key),
      };
    },
  };
}
