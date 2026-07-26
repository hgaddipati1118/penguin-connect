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

  function mergeRefreshedMessages(currentMessages, refreshedMessages) {
    const current = Array.isArray(currentMessages) ? currentMessages : [];
    const refreshed = Array.isArray(refreshedMessages) ? refreshedMessages : [];
    const preserveLoadedHistory = current.length > refreshed.length;
    const merged = new Map();

    if (preserveLoadedHistory) {
      for (const message of current) {
        const messageId = String(message?.provider_message_id || "").trim();
        if (messageId) merged.set(messageId, message);
      }
    }
    for (const message of refreshed) {
      const messageId = String(message?.provider_message_id || "").trim();
      if (messageId) merged.set(messageId, message);
    }
    for (const message of current) {
      if (
        !message?.metadata?.pending_send
        && !message?.metadata?.optimistic_send
      ) continue;
      const messageId = String(message.provider_message_id || "").trim();
      if (messageId && !merged.has(messageId)) merged.set(messageId, message);
    }
    return [...merged.values()];
  }

  function settleOptimisticMessage(message, {
    providerMessageId = "",
    status = "Sent",
  } = {}) {
    if (!message) return null;
    const metadata = {
      ...(message.metadata || {}),
      pending_send: false,
      pending_status: String(status || "Sent"),
      optimistic_send: true,
    };
    delete metadata.pending_failed;
    return {
      ...message,
      provider_message_id: (
        String(providerMessageId || "").trim()
        || String(message.provider_message_id || "").trim()
      ),
      metadata,
    };
  }

  const api = Object.freeze({
    createCompletionGate,
    createRefreshCoordinator,
    mergeRefreshedMessages,
    settleOptimisticMessage,
  });
  root.PenguinRefreshCoordinator = api;
  if (typeof module !== "undefined" && module.exports) {
    module.exports = api;
  }
}(typeof globalThis === "undefined" ? this : globalThis));
