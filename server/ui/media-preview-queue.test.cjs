const assert = require("node:assert/strict");
const test = require("node:test");

const {
  createMediaPreviewQueue,
} = require("./media-preview-queue.js");

function nextTurn() {
  return new Promise((resolve) => setImmediate(resolve));
}

test("cancels queued previews without starting them", async () => {
  const started = [];
  const releases = new Map();
  const queue = createMediaPreviewQueue({
    concurrency: 1,
    run: (target, task) => new Promise((resolve) => {
      started.push(target.id);
      releases.set(target.id, resolve);
      task.cancel = resolve;
    }),
  });
  const first = { id: "first" };
  const second = { id: "second" };

  queue.enqueue(first, {});
  queue.enqueue(second, {});
  assert.deepEqual(queue.status(), { active: 1, queued: 1 });

  assert.equal(queue.cancel(second), true);
  assert.deepEqual(queue.status(), { active: 1, queued: 0 });
  releases.get("first")();
  await nextTurn();

  assert.deepEqual(started, ["first"]);
  assert.deepEqual(queue.status(), { active: 0, queued: 0 });
});

test("cancels an active preview and immediately drains the next one", async () => {
  const started = [];
  const cancelled = [];
  const releases = new Map();
  const queue = createMediaPreviewQueue({
    concurrency: 1,
    run: (target, task) => new Promise((resolve) => {
      started.push(target.id);
      releases.set(target.id, resolve);
      task.cancel = () => {
        cancelled.push(target.id);
        resolve();
      };
    }),
  });
  const first = { id: "first" };
  const second = { id: "second" };

  queue.enqueue(first, {});
  queue.enqueue(second, {});
  assert.equal(queue.cancel(first), true);
  await nextTurn();

  assert.deepEqual(started, ["first", "second"]);
  assert.deepEqual(cancelled, ["first"]);
  assert.deepEqual(queue.status(), { active: 1, queued: 0 });

  releases.get("second")();
  await nextTurn();
  assert.deepEqual(queue.status(), { active: 0, queued: 0 });
});

test("ignores duplicate enqueue and cancellation after completion", async () => {
  let calls = 0;
  const queue = createMediaPreviewQueue({
    concurrency: 2,
    run: async () => {
      calls += 1;
    },
  });
  const target = { id: "only" };

  assert.equal(queue.enqueue(target, {}), true);
  assert.equal(queue.enqueue(target, {}), false);
  await nextTurn();

  assert.equal(calls, 1);
  assert.equal(queue.cancel(target), false);
  assert.deepEqual(queue.status(), { active: 0, queued: 0 });
});
