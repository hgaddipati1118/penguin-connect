(function exposePenguinComposerMentions(root) {
  function cleanCandidate(candidate) {
    const label = String(candidate?.label || candidate?.display_name || "").trim();
    const handle = String(candidate?.handle || candidate?.id || "").trim();
    if (!label || !handle) return null;
    return {
      label,
      handle,
      avatarUrl: String(candidate?.avatarUrl || candidate?.avatar_url || "").trim(),
      isSelf: Boolean(candidate?.isSelf || candidate?.is_self),
    };
  }

  function mergeMentionCandidates(primary = [], fallback = []) {
    const merged = [];
    const seen = new Set();
    for (const rawCandidate of [...primary, ...fallback]) {
      const candidate = cleanCandidate(rawCandidate);
      if (!candidate || candidate.isSelf) continue;
      const key = candidate.handle.toLowerCase();
      if (seen.has(key)) continue;
      seen.add(key);
      merged.push(candidate);
    }
    return merged;
  }

  function escapeRegex(value) {
    return String(value).replace(/[.*+?^${}()|[\]\\]/g, "\\$&");
  }

  function encodeProviderMentions(text, provider, candidates = []) {
    let encoded = String(text || "");
    if (String(provider || "").toLowerCase() !== "slack") return encoded;
    const known = mergeMentionCandidates(candidates)
      .filter((candidate) => /^[UW][A-Z0-9]+$/i.test(candidate.handle))
      .sort((left, right) => right.label.length - left.label.length);
    for (const candidate of known) {
      const pattern = new RegExp(
        `(^|[\\s([{\"'])@${escapeRegex(candidate.label)}(?=$|[\\s.,!?;:)}\\]\"'])`,
        "gi",
      );
      encoded = encoded.replace(pattern, `$1<@${candidate.handle}>`);
    }
    return encoded;
  }

  function renderProviderMentions(text, provider, candidates = []) {
    let rendered = String(text || "");
    if (String(provider || "").toLowerCase() !== "slack") return rendered;
    const known = mergeMentionCandidates(candidates);
    for (const candidate of known) {
      rendered = rendered.replace(
        new RegExp(`<@${escapeRegex(candidate.handle)}>`, "gi"),
        `@${candidate.label}`,
      );
    }
    return rendered;
  }

  const api = Object.freeze({
    encodeProviderMentions,
    mergeMentionCandidates,
    renderProviderMentions,
  });
  root.PenguinComposerMentions = api;
  if (typeof module !== "undefined" && module.exports) {
    module.exports = api;
  }
}(typeof globalThis === "undefined" ? this : globalThis));
