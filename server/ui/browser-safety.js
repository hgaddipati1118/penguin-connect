(function exposePenguinBrowserSafety(root) {
  const QA_MARKER = /(?:^|[-_])(qa|test|expect|automation)(?:[-_]|$)/i;

  function isReadOnlyBrowserSession({
    webdriver = false,
    search = "",
  } = {}) {
    if (webdriver) return true;
    const params = new URLSearchParams(String(search || "").replace(/^\?/, ""));
    const explicit = String(params.get("penguin_readonly") || "").toLowerCase();
    if (["1", "true", "yes"].includes(explicit)) return true;
    for (const [key, value] of params.entries()) {
      if (QA_MARKER.test(key) || QA_MARKER.test(value)) return true;
    }
    return false;
  }

  function requestMethodMutates(method = "GET") {
    return !["GET", "HEAD", "OPTIONS"].includes(
      String(method || "GET").trim().toUpperCase(),
    );
  }

  const api = Object.freeze({
    isReadOnlyBrowserSession,
    requestMethodMutates,
  });
  root.PenguinBrowserSafety = api;
  if (typeof module !== "undefined" && module.exports) {
    module.exports = api;
  }
}(typeof globalThis === "undefined" ? this : globalThis));
