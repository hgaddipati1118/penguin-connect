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

  function firstUnreadMessageId(rows) {
    const unread = (rows || [])
      .filter((row) => (
        row
        && row.isRead === false
        && !row.mine
        && String(row.id || "").trim()
      ))
      .sort((left, right) => timestampValue(left.timestamp) - timestampValue(right.timestamp));
    return String(unread[0]?.id || "").trim();
  }

  function planSlackAuthorGroups(rows, {
    maxGapMs = 5 * 60 * 1000,
  } = {}) {
    const groups = [];
    let previous = null;
    for (const row of rows || []) {
      if (!row) continue;
      const id = String(row.id || "").trim();
      const senderKey = String(row.senderKey || "").trim().toLocaleLowerCase();
      const threadRootId = String(row.threadRootId || "").trim();
      const dateKey = String(row.dateKey || "").trim();
      const isReply = Boolean(row.isReply);
      const startsThread = Boolean(
        isReply
        && (
          !previous?.isReply
          || previous.threadRootId !== threadRootId
        )
      );
      const timestamp = timestampValue(row.timestamp);
      const gap = previous ? timestamp - previous.timestamp : Number.POSITIVE_INFINITY;
      const continuesAuthor = Boolean(
        previous
        && !row.breakBefore
        && !startsThread
        && previous.senderKey === senderKey
        && previous.isReply === isReply
        && previous.threadRootId === threadRootId
        && previous.dateKey === dateKey
        && gap >= 0
        && gap <= maxGapMs
      );
      groups.push({
        id,
        showAuthor: !continuesAuthor,
        continuesAuthor,
        startsThread,
      });
      previous = {
        senderKey,
        threadRootId,
        dateKey,
        isReply,
        timestamp,
      };
    }
    return groups;
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
    firstUnreadMessageId,
    planSlackAuthorGroups,
    planSlackThreadDefaults,
  });
  root.PenguinThreadLayout = api;
  if (typeof module !== "undefined" && module.exports) {
    module.exports = api;
  }
}(typeof globalThis === "undefined" ? this : globalThis));
