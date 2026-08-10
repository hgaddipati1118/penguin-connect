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

  function buildMessageReplyTarget({
    provider = "",
    providerMessageId = "",
    nativeMessageId = "",
    sender = "",
    body = "",
  } = {}) {
    const normalizedProvider = String(provider || "").trim().toLowerCase();
    const isNativeReply = normalizedProvider === "whatsapp";
    const messageId = String(
      isNativeReply ? nativeMessageId : providerMessageId,
    ).trim();
    if (!messageId || !["imessage", "whatsapp"].includes(normalizedProvider)) {
      return null;
    }
    return {
      messageId,
      threadTs: "",
      provider: normalizedProvider,
      sender: String(sender || "").trim(),
      body: String(body || "").trim(),
      native: isNativeReply,
    };
  }

  function providerSupportsReply(provider = "") {
    const normalized = String(provider || "").trim().toLowerCase();
    return [
      "apple_messages",
      "imessage",
      "rcs",
      "slack",
      "sms",
      "whatsapp",
    ].includes(normalized);
  }

  function planNativeReplySummaries(rows) {
    const summaries = new Map();
    for (const row of rows || []) {
      if (!row || row.isSlackThreadReply || row.isReaction) continue;
      const id = String(row.id || "").trim();
      const parentId = String(row.parentId || "").trim();
      if (!id || !parentId) continue;
      const timestamp = timestampValue(row.timestamp);
      const current = summaries.get(parentId) || {
        count: 0,
        latestReplyId: "",
        latestTimestamp: 0,
      };
      current.count += 1;
      if (!current.latestReplyId || timestamp >= current.latestTimestamp) {
        current.latestReplyId = id;
        current.latestTimestamp = timestamp;
      }
      summaries.set(parentId, current);
    }
    return summaries;
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

  function planSlackThreadWindow(rows, {
    visibleCount = 60,
  } = {}) {
    const contentRows = (rows || []).filter((row) => (
      row
      && !row.isReaction
      && String(row.id || "").trim()
    ));
    const limit = Math.max(1, Math.floor(Number(visibleCount) || 1));
    const visibleRows = contentRows.slice(-limit);
    const visibleMessageIds = visibleRows.map((row) => String(row.id).trim());
    const visibleIds = new Set(visibleMessageIds);
    const availableRootIds = new Set(
      contentRows
        .filter((row) => !row.isReply)
        .map((row) => String(row.threadRootId || row.id || "").trim())
        .filter(Boolean),
    );
    const contextRootIds = [];
    const seenContextRoots = new Set();
    for (const row of visibleRows) {
      if (!row.isReply) continue;
      const rootId = String(row.threadRootId || "").trim();
      if (
        !rootId
        || visibleIds.has(rootId)
        || seenContextRoots.has(rootId)
        || !availableRootIds.has(rootId)
      ) continue;
      seenContextRoots.add(rootId);
      contextRootIds.push(rootId);
    }
    return {
      visibleMessageIds,
      contextRootIds,
      hasHiddenMessages: contentRows.length > visibleRows.length,
    };
  }

  const api = Object.freeze({
    buildMessageReplyTarget,
    buildSlackReplyTarget,
    firstUnreadMessageId,
    planNativeReplySummaries,
    planSlackAuthorGroups,
    planSlackThreadDefaults,
    planSlackThreadWindow,
    providerSupportsReply,
  });
  root.PenguinThreadLayout = api;
  if (typeof module !== "undefined" && module.exports) {
    module.exports = api;
  }
}(typeof globalThis === "undefined" ? this : globalThis));
