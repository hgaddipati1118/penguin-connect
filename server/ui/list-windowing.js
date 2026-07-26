(function exposePenguinListWindowing(root) {
  const DEFAULT_RENDER_WINDOWS = Object.freeze({
    conversations: 48,
    files: 24,
  });

  function nextVisibleCount(current, total, batch) {
    const loaded = Math.max(0, Number(total || 0));
    const visible = Math.max(0, Number(current || 0));
    const windowSize = Math.max(1, Number(batch || 1));
    return Math.min(loaded, visible + windowSize);
  }

  const api = Object.freeze({
    DEFAULT_RENDER_WINDOWS,
    nextVisibleCount,
  });
  root.PenguinListWindowing = api;
  if (typeof module !== "undefined" && module.exports) {
    module.exports = api;
  }
}(typeof globalThis === "undefined" ? this : globalThis));
