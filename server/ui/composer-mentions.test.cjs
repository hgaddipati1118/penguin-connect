const assert = require("node:assert/strict");
const test = require("node:test");

const {
  encodeProviderMentions,
  mergeMentionCandidates,
  renderProviderMentions,
} = require("./composer-mentions.js");

test("encodes known Slack display mentions with native user ids", () => {
  const text = encodeProviderMentions(
    "Can you check this @Anh and @Dhruv Roongta?",
    "slack",
    [
      { label: "Anh", handle: "UANH" },
      { label: "Dhruv Roongta", handle: "UDHRUV" },
    ],
  );

  assert.equal(text, "Can you check this <@UANH> and <@UDHRUV>?");
});

test("preserves unknown mentions and non-Slack message text", () => {
  const candidates = [{ label: "Anh", handle: "UANH" }];

  assert.equal(
    encodeProviderMentions("Hi @Unknown", "slack", candidates),
    "Hi @Unknown",
  );
  assert.equal(
    encodeProviderMentions("Hi @Anh", "whatsapp", candidates),
    "Hi @Anh",
  );
});

test("matches the longest Slack display name first and respects token boundaries", () => {
  const text = encodeProviderMentions(
    "@Taylor Example please pair with @Taylor, not email@Taylor.com",
    "slack",
    [
      { label: "Taylor", handle: "UTAYLOR" },
      { label: "Taylor Example", handle: "UTAYLOREXAMPLE" },
    ],
  );

  assert.equal(
    text,
    "<@UTAYLOREXAMPLE> please pair with <@UTAYLOR>, not email@Taylor.com",
  );
});

test("merges live channel members with message-history senders without duplicates", () => {
  const merged = mergeMentionCandidates(
    [
      { label: "Anh", handle: "UANH", avatarUrl: "member-avatar" },
      { label: "Harsha", handle: "USELF", isSelf: true },
    ],
    [
      { label: "Anh Updated", handle: "UANH", avatarUrl: "history-avatar" },
      { label: "Dhruv", handle: "UDHRUV" },
    ],
  );

  assert.deepEqual(merged, [
    { label: "Anh", handle: "UANH", avatarUrl: "member-avatar", isSelf: false },
    { label: "Dhruv", handle: "UDHRUV", avatarUrl: "", isSelf: false },
  ]);
});

test("renders queued native Slack mentions as human-readable names", () => {
  assert.equal(
    renderProviderMentions(
      "Queued for <@UANH>",
      "slack",
      [{ label: "Anh", handle: "UANH" }],
    ),
    "Queued for @Anh",
  );
});
