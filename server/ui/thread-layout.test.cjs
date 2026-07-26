const assert = require("node:assert/strict");
const test = require("node:test");

const {
  buildMessageReplyTarget,
  buildSlackReplyTarget,
  firstUnreadMessageId,
  planNativeReplySummaries,
  planSlackAuthorGroups,
  planSlackThreadDefaults,
  planSlackThreadWindow,
  providerSupportsReply,
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

test("bounds an expanded Slack thread to the visible message window", () => {
  const rows = [
    root("thread-a", "2026-07-25T10:00:00Z", 5),
    reply("reply-1", "thread-a", "2026-07-25T10:01:00Z"),
    reply("reply-2", "thread-a", "2026-07-25T10:02:00Z"),
    reply("reply-3", "thread-a", "2026-07-25T10:03:00Z"),
    reply("reply-4", "thread-a", "2026-07-25T10:04:00Z"),
    reply("reply-5", "thread-a", "2026-07-25T10:05:00Z"),
  ];

  const window = planSlackThreadWindow(rows, { visibleCount: 3 });

  assert.deepEqual(window.visibleMessageIds, ["reply-3", "reply-4", "reply-5"]);
  assert.deepEqual(window.contextRootIds, ["thread-a"]);
  assert.equal(window.hasHiddenMessages, true);
});

test("does not let hidden Slack reactions consume the visible message window", () => {
  const window = planSlackThreadWindow([
    root("message-1", "2026-07-25T10:00:00Z", 0),
    {
      id: "reaction-event",
      isReaction: true,
      timestamp: "2026-07-25T10:01:00Z",
    },
    root("message-2", "2026-07-25T10:02:00Z", 0),
  ], { visibleCount: 2 });

  assert.deepEqual(window.visibleMessageIds, ["message-1", "message-2"]);
  assert.deepEqual(window.contextRootIds, []);
  assert.equal(window.hasHiddenMessages, false);
});

test("adds only missing roots as context for visible Slack replies", () => {
  const window = planSlackThreadWindow([
    root("thread-a", "2026-07-25T10:00:00Z", 2),
    reply("reply-a", "thread-a", "2026-07-25T10:01:00Z"),
    root("thread-b", "2026-07-25T10:02:00Z", 1),
    reply("reply-b", "thread-b", "2026-07-25T10:03:00Z"),
  ], { visibleCount: 3 });

  assert.deepEqual(window.visibleMessageIds, ["reply-a", "thread-b", "reply-b"]);
  assert.deepEqual(window.contextRootIds, ["thread-a"]);
});

test("groups consecutive Slack messages from the same author", () => {
  const groups = planSlackAuthorGroups([
    {
      id: "message-1",
      senderKey: "member-a",
      timestamp: "2026-07-25T10:00:00Z",
    },
    {
      id: "message-2",
      senderKey: "member-a",
      timestamp: "2026-07-25T10:02:00Z",
    },
  ]);

  assert.deepEqual(groups, [
    {
      id: "message-1",
      showAuthor: true,
      continuesAuthor: false,
      startsThread: false,
    },
    {
      id: "message-2",
      showAuthor: false,
      continuesAuthor: true,
      startsThread: false,
    },
  ]);
});

test("starts a new Slack author group when identity, time, or thread changes", () => {
  const groups = planSlackAuthorGroups([
    {
      id: "root",
      senderKey: "member-a",
      timestamp: "2026-07-25T10:00:00Z",
    },
    {
      id: "reply-1",
      senderKey: "member-a",
      threadRootId: "root",
      isReply: true,
      timestamp: "2026-07-25T10:01:00Z",
    },
    {
      id: "reply-2",
      senderKey: "member-b",
      threadRootId: "root",
      isReply: true,
      timestamp: "2026-07-25T10:02:00Z",
    },
    {
      id: "reply-3",
      senderKey: "member-b",
      threadRootId: "root",
      isReply: true,
      timestamp: "2026-07-25T10:10:00Z",
    },
  ]);

  assert.deepEqual(groups.map((group) => ({
    showAuthor: group.showAuthor,
    startsThread: group.startsThread,
  })), [
    { showAuthor: true, startsThread: false },
    { showAuthor: true, startsThread: true },
    { showAuthor: true, startsThread: false },
    { showAuthor: true, startsThread: false },
  ]);
});

test("keeps consecutive replies by one author visually grouped inside one thread", () => {
  const groups = planSlackAuthorGroups([
    {
      id: "reply-1",
      senderKey: "member-a",
      threadRootId: "root",
      isReply: true,
      timestamp: "2026-07-25T10:00:00Z",
    },
    {
      id: "reply-2",
      senderKey: "member-a",
      threadRootId: "root",
      isReply: true,
      timestamp: "2026-07-25T10:01:00Z",
    },
  ]);

  assert.equal(groups[0].startsThread, true);
  assert.equal(groups[0].showAuthor, true);
  assert.equal(groups[1].continuesAuthor, true);
  assert.equal(groups[1].showAuthor, false);
});

test("repeats the author after a date or unread boundary", () => {
  const groups = planSlackAuthorGroups([
    {
      id: "before-midnight",
      senderKey: "member-a",
      dateKey: "Jul 25",
      timestamp: "2026-07-25T23:59:00Z",
    },
    {
      id: "after-midnight",
      senderKey: "member-a",
      dateKey: "Jul 26",
      timestamp: "2026-07-26T00:01:00Z",
    },
    {
      id: "after-unread",
      senderKey: "member-a",
      dateKey: "Jul 26",
      breakBefore: true,
      timestamp: "2026-07-26T00:02:00Z",
    },
  ]);

  assert.deepEqual(groups.map((group) => group.showAuthor), [true, true, true]);
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

test("keeps iMessage reply context local while targeting the stored parent", () => {
  const target = buildMessageReplyTarget({
    provider: "imessage",
    providerMessageId: "imessage:parent-guid",
    nativeMessageId: "parent-guid",
    sender: "Taylor Example",
    body: "The original iMessage",
  });

  assert.deepEqual(target, {
    messageId: "imessage:parent-guid",
    threadTs: "",
    provider: "imessage",
    sender: "Taylor Example",
    body: "The original iMessage",
    native: false,
  });
});

test("uses the native WhatsApp id for quoted replies", () => {
  const target = buildMessageReplyTarget({
    provider: "whatsapp",
    providerMessageId: "whatsapp:stored-id",
    nativeMessageId: "native-id",
    sender: "Jordan Example",
    body: "The original WhatsApp message",
  });

  assert.equal(target.messageId, "native-id");
  assert.equal(target.provider, "whatsapp");
  assert.equal(target.native, true);
});

test("supports reply shortcuts across every rendered messaging provider", () => {
  assert.equal(providerSupportsReply("iMessage"), true);
  assert.equal(providerSupportsReply("apple_messages"), true);
  assert.equal(providerSupportsReply("WhatsApp"), true);
  assert.equal(providerSupportsReply("Slack"), true);
  assert.equal(providerSupportsReply("telegram"), false);
  assert.equal(providerSupportsReply(""), false);
});

test("summarizes direct native replies and identifies the latest child", () => {
  const summaries = planNativeReplySummaries([
    {
      id: "reply-new",
      parentId: "parent-guid",
      timestamp: "2026-07-26T10:04:00Z",
    },
    {
      id: "reply-old",
      parentId: "parent-guid",
      timestamp: "2026-07-26T10:02:00Z",
    },
    {
      id: "nested-reply",
      parentId: "reply-old",
      timestamp: "2026-07-26T10:03:00Z",
    },
    {
      id: "slack-thread-reply",
      parentId: "slack-root",
      timestamp: "2026-07-26T10:05:00Z",
      isSlackThreadReply: true,
    },
    {
      id: "hidden-reaction-event",
      parentId: "parent-guid",
      timestamp: "2026-07-26T10:06:00Z",
      isReaction: true,
    },
  ]);

  assert.deepEqual(summaries.get("parent-guid"), {
    count: 2,
    latestReplyId: "reply-new",
    latestTimestamp: Date.parse("2026-07-26T10:04:00Z"),
  });
  assert.deepEqual(summaries.get("reply-old"), {
    count: 1,
    latestReplyId: "nested-reply",
    latestTimestamp: Date.parse("2026-07-26T10:03:00Z"),
  });
  assert.equal(summaries.has("slack-root"), false);
});

test("finds the earliest unread incoming message regardless of input order", () => {
  const boundary = firstUnreadMessageId([
    {
      id: "later-unread",
      timestamp: "2026-07-25T10:04:00Z",
      isRead: false,
      mine: false,
    },
    {
      id: "outgoing",
      timestamp: "2026-07-25T10:01:00Z",
      isRead: false,
      mine: true,
    },
    {
      id: "earlier-unread",
      timestamp: "2026-07-25T10:02:00Z",
      isRead: false,
      mine: false,
    },
    {
      id: "read",
      timestamp: "2026-07-25T10:00:00Z",
      isRead: true,
      mine: false,
    },
  ]);

  assert.equal(boundary, "earlier-unread");
});

test("returns no unread boundary when every incoming message is read", () => {
  const boundary = firstUnreadMessageId([
    { id: "read", timestamp: "2026-07-25T10:00:00Z", isRead: true, mine: false },
    { id: "mine", timestamp: "2026-07-25T10:01:00Z", isRead: false, mine: true },
  ]);

  assert.equal(boundary, "");
});
