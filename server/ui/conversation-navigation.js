(function exposePenguinConversationNavigation(root) {
  function createConversationNavigationCoordinator({
    scheduleCommit,
    cancelCommit,
    onPreview = () => {},
    onCommit = () => {},
    onCancel = () => {},
  } = {}) {
    const schedule = scheduleCommit || ((callback) => (
      typeof root.requestAnimationFrame === "function"
        ? root.requestAnimationFrame(callback)
        : root.setTimeout(callback, 16)
    ));
    const cancelSchedule = cancelCommit || ((timer) => (
      typeof root.cancelAnimationFrame === "function"
        ? root.cancelAnimationFrame(timer)
        : root.clearTimeout(timer)
    ));
    let commitTimer = 0;
    let pending = null;

    function conversationId(conversation) {
      return String(conversation?.conversation_id || "");
    }

    function flush() {
      commitTimer = 0;
      const next = pending;
      pending = null;
      if (next?.conversation) onCommit(next.conversation);
    }

    function move(rows, selectedId, offset) {
      const conversations = Array.isArray(rows) ? rows : [];
      if (!conversations.length) return null;

      const pendingId = conversationId(pending?.conversation);
      let currentIndex = pendingId
        ? conversations.findIndex((row) => conversationId(row) === pendingId)
        : conversations.findIndex((row) => conversationId(row) === selectedId);
      if (currentIndex < 0) {
        currentIndex = offset > 0 ? -1 : conversations.length;
      }

      const nextIndex = Math.max(
        0,
        Math.min(conversations.length - 1, currentIndex + offset),
      );
      const next = conversations[nextIndex];
      const currentId = pendingId || String(selectedId || "");
      if (conversationId(next) === currentId && !pending) return next;

      const previous = pending?.conversation || conversations[currentIndex] || null;
      pending = { conversation: next };
      onPreview(next, previous);
      if (commitTimer) cancelSchedule(commitTimer);
      commitTimer = schedule(flush);
      return next;
    }

    function cancel() {
      if (!pending) return false;
      const canceled = pending.conversation;
      pending = null;
      if (commitTimer) cancelSchedule(commitTimer);
      commitTimer = 0;
      onCancel(canceled);
      return true;
    }

    return Object.freeze({
      cancel,
      move,
    });
  }

  const api = Object.freeze({ createConversationNavigationCoordinator });
  root.PenguinConversationNavigation = api;
  if (typeof module !== "undefined" && module.exports) {
    module.exports = api;
  }
}(typeof globalThis === "undefined" ? this : globalThis));
