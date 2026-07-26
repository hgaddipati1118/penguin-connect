(function exposePenguinRefreshCoordinator(root) {
  function createRefreshCoordinator({
    cooldownMs = 1200,
    now = () => Date.now(),
  } = {}) {
    const inFlight = new Map();
    const completedAt = new Map();

    async function run(key, task, { force = false } = {}) {
      const normalizedKey = String(key || "").trim();
      if (!normalizedKey || typeof task !== "function") {
        throw new TypeError("A refresh key and task are required");
      }
      if (inFlight.has(normalizedKey)) return inFlight.get(normalizedKey);
      const lastCompletedAt = completedAt.get(normalizedKey);
      if (
        !force
        && Number.isFinite(lastCompletedAt)
        && now() - lastCompletedAt < cooldownMs
      ) {
        return { skipped: "fresh" };
      }

      const refresh = Promise.resolve()
        .then(task)
        .then((result) => {
          completedAt.set(normalizedKey, now());
          return result;
        })
        .finally(() => {
          if (inFlight.get(normalizedKey) === refresh) {
            inFlight.delete(normalizedKey);
          }
        });
      inFlight.set(normalizedKey, refresh);
      return refresh;
    }

    return Object.freeze({ run });
  }

  function createCompletionGate() {
    const completed = new Set();
    const inFlight = new Map();

    async function run(key, task) {
      const normalizedKey = String(key || "").trim();
      if (!normalizedKey || typeof task !== "function") {
        throw new TypeError("A completion key and task are required");
      }
      if (completed.has(normalizedKey)) return { skipped: "complete" };
      if (inFlight.has(normalizedKey)) return inFlight.get(normalizedKey);

      const operation = Promise.resolve()
        .then(task)
        .then((result) => {
          if (result?.completed === true) completed.add(normalizedKey);
          return result;
        })
        .finally(() => {
          if (inFlight.get(normalizedKey) === operation) {
            inFlight.delete(normalizedKey);
          }
        });
      inFlight.set(normalizedKey, operation);
      return operation;
    }

    return Object.freeze({ run });
  }

  const api = Object.freeze({
    createCompletionGate,
    createRefreshCoordinator,
  });
  root.PenguinRefreshCoordinator = api;
  if (typeof module !== "undefined" && module.exports) {
    module.exports = api;
  }
}(typeof globalThis === "undefined" ? this : globalThis));
