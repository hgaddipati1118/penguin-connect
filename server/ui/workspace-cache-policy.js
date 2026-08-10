(function exposePenguinWorkspaceCachePolicy(root) {
  function planWorkspaceHydration(conversationIds, {
    rememberedConversationId = "",
    eagerLimit = 12,
    totalLimit = 160,
  } = {}) {
    const seen = new Set();
    const orderedIds = [];
    for (const value of conversationIds || []) {
      const id = String(value || "").trim();
      if (!id || seen.has(id)) continue;
      seen.add(id);
      orderedIds.push(id);
    }
    const boundedTotal = Math.max(1, Math.floor(Number(totalLimit) || 1));
    const boundedEager = Math.max(
      1,
      Math.min(boundedTotal, Math.floor(Number(eagerLimit) || 1)),
    );
    const rememberedId = String(rememberedConversationId || "").trim();
    const immediateIds = rememberedId && seen.has(rememberedId)
      ? [rememberedId]
      : [];
    const remainingIds = orderedIds
      .filter((id) => id !== rememberedId)
      .slice(0, boundedTotal - immediateIds.length);
    const eagerIds = remainingIds.slice(
      0,
      Math.max(0, boundedEager - immediateIds.length),
    );
    const backgroundIds = remainingIds.slice(eagerIds.length);
    return {
      immediateIds,
      eagerIds,
      backgroundIds,
    };
  }

  const api = Object.freeze({
    planWorkspaceHydration,
  });
  root.PenguinWorkspaceCachePolicy = api;
  if (typeof module !== "undefined" && module.exports) {
    module.exports = api;
  }
}(typeof globalThis === "undefined" ? this : globalThis));
