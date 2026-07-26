(function exposePenguinThreadLayout(root) {
  function timestampValue(value) {
    const parsed = Date.parse(String(value || ""));
    return Number.isFinite(parsed) ? parsed : 0;
  }

  function buildSlackReplyTarget({
    nativeMessageId = "",
    threadRootId = "",
    sender = "",
    body = "",
  } = {}) {
    const messageId = String(nativeMessageId || "").trim();
    const threadTs = String(threadRootId || messageId).trim();
    if (!messageId || !threadTs) return null;
    return {
      messageId,
      threadTs,
      provider: "slack",
      sender: String(sender || "").trim(),
      body: String(body || "").trim(),
    };
  }

  function planSlackThreadDefaults(rows, {
    preferredOpenThreadId = "",
  } = {}) {
    const roots = new Map();
    for (const row of rows || []) {
      if (!row || row.isReply) continue;
      const id = String(row.threadRootId || row.id || "").trim();
      if (!id || Number(row.replyCount || 0) <= 0) continue;
      roots.set(id, {
        id,
        activity: timestampValue(row.timestamp),
      });
    }
    for (const row of rows || []) {
      if (!row?.isReply) continue;
      const rootId = String(row.threadRootId || "").trim();
      const thread = roots.get(rootId);
      if (!thread) continue;
      thread.activity = Math.max(thread.activity, timestampValue(row.timestamp));
    }

    const threads = [...roots.values()];
    const preferredId = String(preferredOpenThreadId || "").trim();
    const preferred = preferredId && roots.has(preferredId) ? preferredId : "";
    const mostRecent = threads.reduce((latest, thread) => (
      !latest || thread.activity >= latest.activity ? thread : latest
    ), null);
    const defaultOpenThreadId = preferred || mostRecent?.id || "";

    return {
      defaultOpenThreadId,
      collapsedThreadIds: threads
        .map((thread) => thread.id)
        .filter((id) => id !== defaultOpenThreadId),
    };
  }

  const api = Object.freeze({
    buildSlackReplyTarget,
    planSlackThreadDefaults,
  });
  root.PenguinThreadLayout = api;
  if (typeof module !== "undefined" && module.exports) {
    module.exports = api;
  }
}(typeof globalThis === "undefined" ? this : globalThis));
