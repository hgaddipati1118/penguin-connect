const assert = require("node:assert/strict");
const test = require("node:test");

const {
  buildSlackReplyTarget,
  firstUnreadMessageId,
  planSlackAuthorGroups,
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
