const assert = require("node:assert/strict");
const test = require("node:test");

const {
  buildSlackReplyTarget,
  planSlackThreadDefaults,
} = require("./thread-layout.js");

function root(id, timestamp, replyCount = 1) {
  return {
    id,
    threadRootId: id,
    isReply: false,
    replyCount,
    timestamp,
  };
}

function reply(id, threadRootId, timestamp) {
  return {
    id,
    threadRootId,
    isReply: true,
    replyCount: 0,
    timestamp,
  };
}

test("collapses older Slack threads and keeps the most recently active thread open", () => {
  const layout = planSlackThreadDefaults([
    root("thread-a", "2026-07-25T10:00:00Z", 2),
    reply("reply-a1", "thread-a", "2026-07-25T10:02:00Z"),
    root("thread-b", "2026-07-25T10:01:00Z", 1),
    reply("reply-b1", "thread-b", "2026-07-25T10:04:00Z"),
    root("plain-message", "2026-07-25T10:05:00Z", 0),
  ]);

  assert.equal(layout.defaultOpenThreadId, "thread-b");
  assert.deepEqual(layout.collapsedThreadIds, ["thread-a"]);
});

test("keeps a focused reply thread open instead of the newest thread", () => {
  const layout = planSlackThreadDefaults([
    root("thread-a", "2026-07-25T10:00:00Z", 1),
    reply("reply-a1", "thread-a", "2026-07-25T10:02:00Z"),
    root("thread-b", "2026-07-25T10:01:00Z", 1),
    reply("reply-b1", "thread-b", "2026-07-25T10:04:00Z"),
  ], {
    preferredOpenThreadId: "thread-a",
  });

  assert.equal(layout.defaultOpenThreadId, "thread-a");
  assert.deepEqual(layout.collapsedThreadIds, ["thread-b"]);
});

test("does not collapse a single thread or orphan replies", () => {
  const single = planSlackThreadDefaults([
    root("thread-a", "2026-07-25T10:00:00Z", 1),
    reply("reply-a1", "thread-a", "2026-07-25T10:02:00Z"),
    reply("orphan", "missing-root", "2026-07-25T10:03:00Z"),
  ]);

  assert.equal(single.defaultOpenThreadId, "thread-a");
  assert.deepEqual(single.collapsedThreadIds, []);
});

test("targets the Slack thread root while preserving the exact clicked reply", () => {
  const target = buildSlackReplyTarget({
    nativeMessageId: "reply-2",
    threadRootId: "root-1",
    sender: "Taylor Example",
    body: "The nested detail",
  });

  assert.deepEqual(target, {
    messageId: "reply-2",
    threadTs: "root-1",
    provider: "slack",
    sender: "Taylor Example",
    body: "The nested detail",
  });
});

test("uses a standalone Slack message as both the clicked target and thread root", () => {
  const target = buildSlackReplyTarget({
    nativeMessageId: "root-1",
    sender: "Jordan Example",
    body: "Start a thread here",
  });

  assert.equal(target.messageId, "root-1");
  assert.equal(target.threadTs, "root-1");
});
