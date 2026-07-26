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

  function clampWindowStart(start, total, windowSize) {
    const loaded = Math.max(0, Math.floor(Number(total || 0)));
    const size = Math.max(1, Math.floor(Number(windowSize || 1)));
    const maximum = Math.max(0, loaded - size);
    return Math.min(
      maximum,
      Math.max(0, Math.floor(Number(start || 0))),
    );
  }

  function windowStartForIndex(currentStart, targetIndex, total, windowSize) {
    const loaded = Math.max(0, Math.floor(Number(total || 0)));
    if (!loaded) return 0;
    const size = Math.max(1, Math.floor(Number(windowSize || 1)));
    const current = clampWindowStart(currentStart, loaded, size);
    const target = Math.max(
      0,
      Math.min(loaded - 1, Math.floor(Number(targetIndex || 0))),
    );
    if (target < current) return clampWindowStart(target, loaded, size);
    if (target >= current + size) {
      return clampWindowStart(target - size + 1, loaded, size);
    }
    return current;
  }

  function windowStartForScroll(
    scrollTop,
    rowHeight,
    total,
    windowSize,
    overscan = 0,
  ) {
    const height = Math.max(1, Number(rowHeight || 1));
    const firstVisible = Math.floor(Math.max(0, Number(scrollTop || 0)) / height);
    const padding = Math.max(0, Math.floor(Number(overscan || 0)));
    return clampWindowStart(
      firstVisible - padding,
      total,
      windowSize,
    );
  }

  const api = Object.freeze({
    clampWindowStart,
    DEFAULT_RENDER_WINDOWS,
    nextVisibleCount,
    windowStartForIndex,
    windowStartForScroll,
  });
  root.PenguinListWindowing = api;
  if (typeof module !== "undefined" && module.exports) {
    module.exports = api;
  }
}(typeof globalThis === "undefined" ? this : globalThis));
