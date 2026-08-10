(function exposePenguinMediaPreviewQueue(root) {
  function createMediaPreviewQueue({
    concurrency = 2,
    run,
    onError = () => {},
  } = {}) {
    if (typeof run !== "function") {
      throw new TypeError("A media preview runner is required");
    }
    const maximumActive = Math.max(1, Number(concurrency || 1));
    const queuedTargets = [];
    const liveTargets = new WeakSet();
    const tasks = new WeakMap();
    let active = 0;

    function drain() {
      while (active < maximumActive && queuedTargets.length) {
        const target = queuedTargets.shift();
        if (!liveTargets.has(target)) continue;
        const task = tasks.get(target);
        if (!task) continue;
        active += 1;
        let operation;
        try {
          operation = run(target, task);
        } catch (error) {
          operation = Promise.reject(error);
        }
        Promise.resolve(operation)
          .catch((error) => onError(error, target, task))
          .finally(() => {
            active -= 1;
            if (tasks.get(target) === task) {
              tasks.delete(target);
              liveTargets.delete(target);
            }
            drain();
          });
      }
    }

    function enqueue(target, task = {}) {
      if (
        !target
        || (typeof target !== "object" && typeof target !== "function")
        || liveTargets.has(target)
      ) return false;
      liveTargets.add(target);
      tasks.set(target, task);
      queuedTargets.push(target);
      drain();
      return true;
    }

    function cancel(target) {
      if (!target || !liveTargets.has(target)) return false;
      const task = tasks.get(target);
      liveTargets.delete(target);
      tasks.delete(target);
      for (
        let index = queuedTargets.indexOf(target);
        index >= 0;
        index = queuedTargets.indexOf(target)
      ) {
        queuedTargets.splice(index, 1);
      }
      if (typeof task?.cancel === "function") {
        try {
          task.cancel();
        } catch (_error) {
          // Cancellation is best-effort; the queue still releases its references.
        }
      }
      drain();
      return true;
    }

    function status() {
      return {
        active,
        queued: queuedTargets.length,
      };
    }

    return Object.freeze({
      cancel,
      enqueue,
      status,
    });
  }

  const api = Object.freeze({ createMediaPreviewQueue });
  root.PenguinMediaPreviewQueue = api;
  if (typeof module !== "undefined" && module.exports) {
    module.exports = api;
  }
}(typeof globalThis === "undefined" ? this : globalThis));
