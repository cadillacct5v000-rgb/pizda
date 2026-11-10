import {AppError} from './core.mjs';

export function createGenerationQueue({name, limit}) {
  const jobs = new Map(), pending = []; let active = 0, closed = false;
  const pump = () => {
    while (!closed && active < limit && pending.length) {
      const job = pending.shift();
      if (job.controller.signal.aborted) continue;
      active++; job.state = 'running';
      Promise.resolve().then(() => job.run(job.controller.signal)).then(job.resolve, job.reject).finally(() => {
        active--; if (jobs.get(job.key) === job) jobs.delete(job.key); pump();
      });
    }
  };
  return {
    enqueue(key, run) {
      if (closed) throw new AppError(503, 'Очередь закрывается');
      if (jobs.has(key)) return jobs.get(key).promise;
      const controller = new AbortController();
      const job = {key, run, controller, state: 'queued'};
      job.promise = new Promise((resolve, reject) => { job.resolve = resolve; job.reject = reject; });
      // Jobs may outlive the request that enqueued them. The returned promise still rejects for callers.
      job.promise.catch(() => {});
      jobs.set(key, job); pending.push(job); pump(); return job.promise;
    },
    cancel(key) {
      const job = jobs.get(key); if (!job) return false;
      job.controller.abort(new DOMException('Отменено', 'AbortError'));
      if (job.state === 'queued') { pending.splice(pending.indexOf(job), 1); jobs.delete(key); job.reject(job.controller.signal.reason); }
      return true;
    },
    stats: () => ({name, limit, active, pending: pending.length, keys: [...jobs.keys()]}),
    async close() { closed = true; const outstanding = [...jobs.values()].map((job) => job.promise); for (const key of jobs.keys()) this.cancel(key); await Promise.allSettled(outstanding); },
  };
}
