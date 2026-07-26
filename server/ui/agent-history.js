(function agentHistoryModule(root, factory) {
  const api = factory();
  if (typeof module === "object" && module.exports) module.exports = api;
  if (root) root.PenguinAgentHistory = api;
}(typeof window !== "undefined" ? window : globalThis, () => {
  const HISTORY_LIMIT = 50;
  const ACTIVITY_LIMIT = 60;
  const REFERENCE_LIMIT = 8;

  function cleanText(value, limit = 10000) {
    return String(value || "").trim().slice(0, limit);
  }

  function normalizedTimestamp(value, fallback = 0) {
    const number = Number(value);
    return Number.isFinite(number) && number > 0 ? number : fallback;
  }

  function normalizeAgentSession(record) {
    const id = cleanText(record?.id, 160);
    if (!id) return null;
    const history = (Array.isArray(record?.history) ? record.history : [])
      .filter((item) => ["user", "assistant"].includes(item?.role))
      .map((item) => ({
        role: item.role,
        text: cleanText(item.text),
        timestamp: normalizedTimestamp(item.timestamp),
        error: Boolean(item.error),
      }))
      .filter((item) => item.text)
      .slice(-HISTORY_LIMIT);
    const firstQuestion = history.find((item) => item.role === "user")?.text;
    const createdAt = normalizedTimestamp(record?.createdAt, Date.now());
    return {
      id,
      title: cleanText(record?.title || firstQuestion || "New chat", 72),
      createdAt,
      updatedAt: normalizedTimestamp(record?.updatedAt, createdAt),
      conversationId: cleanText(record?.conversationId, 300),
      conversationName: cleanText(record?.conversationName, 200),
      provider: cleanText(record?.provider, 40),
      mode: ["read", "ask", "yolo"].includes(record?.mode) ? record.mode : "read",
      answer: cleanText(record?.answer),
      lastQuestion: cleanText(record?.lastQuestion),
      history,
      activity: (Array.isArray(record?.activity) ? record.activity : [])
        .map((item) => ({
          id: cleanText(item?.id, 200),
          kind: cleanText(item?.kind, 40),
          text: cleanText(item?.text, 2000),
          status: cleanText(item?.status, 40),
        }))
        .filter((item) => item.text)
        .slice(-ACTIVITY_LIMIT),
      references: (Array.isArray(record?.references) ? record.references : [])
        .map((item) => ({
          conversationId: cleanText(item?.conversationId, 300),
          label: cleanText(item?.label, 200),
          provider: cleanText(item?.provider, 40),
          reason: cleanText(item?.reason, 500),
        }))
        .filter((item) => item.conversationId && item.label)
        .slice(-REFERENCE_LIMIT),
    };
  }

  function recentAgentSessions(records, { limit = 40 } = {}) {
    const byId = new Map();
    for (const record of records || []) {
      const session = normalizeAgentSession(record);
      if (!session) continue;
      const previous = byId.get(session.id);
      if (!previous || session.updatedAt >= previous.updatedAt) byId.set(session.id, session);
    }
    return [...byId.values()]
      .sort((a, b) => b.updatedAt - a.updatedAt)
      .slice(0, Math.max(0, limit));
  }

  function sessionTranscript(history, { excludeTrailingUser = false, limit = 16 } = {}) {
    const items = (Array.isArray(history) ? history : [])
      .filter((item) => ["user", "assistant"].includes(item?.role) && cleanText(item?.text));
    if (excludeTrailingUser && items.at(-1)?.role === "user") items.pop();
    return items
      .slice(-Math.max(0, limit))
      .map((item) => `${item.role.toUpperCase()}: ${cleanText(item.text, 2000)}`)
      .join("\n");
  }

  return {
    normalizeAgentSession,
    recentAgentSessions,
    sessionTranscript,
  };
}));
