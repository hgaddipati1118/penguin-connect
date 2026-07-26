const assert = require("node:assert/strict");
const test = require("node:test");

const {
  createConversationNavigationCoordinator,
} = require("./conversation-navigation.js");

function createFrameHarness() {
  let callback = null;
  let canceled = false;
  let cancelCount = 0;
  let scheduleCount = 0;
  return {
    cancelCommit() {
      canceled = true;
      cancelCount += 1;
      callback = null;
    },
    flush() {
      const pending = callback;
      callback = null;
      pending?.();
    },
    get pending() {
      return Boolean(callback);
    },
    get canceled() {
      return canceled;
    },
    get cancelCount() {
      return cancelCount;
    },
    get scheduleCount() {
      return scheduleCount;
    },
    scheduleCommit(next) {
      scheduleCount += 1;
      callback = next;
      return scheduleCount;
    },
  };
}

const rows = [
  { conversation_id: "a" },
  { conversation_id: "b" },
  { conversation_id: "c" },
  { conversation_id: "d" },
];

test("coalesces rapid moves into one commit for the final conversation", () => {
  const frames = createFrameHarness();
  const previews = [];
  const commits = [];
  const coordinator = createConversationNavigationCoordinator({
    scheduleCommit: frames.scheduleCommit,
    cancelCommit: frames.cancelCommit,
    onPreview: (next) => previews.push(next.conversation_id),
    onCommit: (next) => commits.push(next.conversation_id),
  });

  coordinator.move(rows, "a", 1);
  coordinator.move(rows, "a", 1);
  coordinator.move(rows, "a", 1);

  assert.deepEqual(previews, ["b", "c", "d"]);
  assert.deepEqual(commits, []);
  assert.equal(frames.pending, true);
  assert.equal(frames.scheduleCount, 3);
  assert.equal(frames.cancelCount, 2);

  frames.flush();

  assert.deepEqual(commits, ["d"]);
  assert.equal(frames.pending, false);
});

test("applies direction changes to the pending selection", () => {
  const frames = createFrameHarness();
  const commits = [];
  const coordinator = createConversationNavigationCoordinator({
    scheduleCommit: frames.scheduleCommit,
    cancelCommit: frames.cancelCommit,
    onCommit: (next) => commits.push(next.conversation_id),
  });

  coordinator.move(rows, "a", 1);
  coordinator.move(rows, "a", 1);
  coordinator.move(rows, "a", -1);
  frames.flush();

  assert.deepEqual(commits, ["b"]);
});

test("does not schedule a redundant commit at a list boundary", () => {
  const frames = createFrameHarness();
  const commits = [];
  const coordinator = createConversationNavigationCoordinator({
    scheduleCommit: frames.scheduleCommit,
    cancelCommit: frames.cancelCommit,
    onCommit: (next) => commits.push(next.conversation_id),
  });

  assert.equal(coordinator.move(rows, "d", 1).conversation_id, "d");
  assert.equal(frames.pending, false);
  frames.flush();

  assert.deepEqual(commits, []);
});

test("cancel prevents a stale keyboard selection from committing", () => {
  const frames = createFrameHarness();
  const canceled = [];
  const commits = [];
  const coordinator = createConversationNavigationCoordinator({
    scheduleCommit: frames.scheduleCommit,
    cancelCommit: frames.cancelCommit,
    onCancel: (pending) => canceled.push(pending.conversation_id),
    onCommit: (next) => commits.push(next.conversation_id),
  });

  coordinator.move(rows, "a", 1);
  assert.equal(coordinator.cancel(), true);
  frames.flush();

  assert.equal(frames.canceled, true);
  assert.deepEqual(canceled, ["b"]);
  assert.deepEqual(commits, []);
});
