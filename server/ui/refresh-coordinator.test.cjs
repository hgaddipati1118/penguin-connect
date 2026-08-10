const assert = require("node:assert/strict");
const test = require("node:test");

const {
  createCompletionGate,
  createDurableUndoQueue,
  createRefreshCoordinator,
  mergeRefreshedMessages,
  settleOptimisticMessage,
} = require("./refresh-coordinator.js");

test("coalesces concurrent refreshes for the same conversation and mode", async () => {
  const coordinator = createRefreshCoordinator({ cooldownMs: 1200 });
  let calls = 0;
  let release;
  const pending = new Promise((resolve) => {
    release = resolve;
  });
  const task = async () => {
    calls += 1;
    await pending;
    return "fresh";
  };

  const first = coordinator.run("conversation-a:incremental", task);
  const second = coordinator.run("conversation-a:incremental", task);
  release();

  assert.equal(await first, "fresh");
  assert.equal(await second, "fresh");
  assert.equal(calls, 1);
});

test("skips a duplicate successful refresh inside the cooldown", async () => {
  let now = 1000;
  const coordinator = createRefreshCoordinator({
    cooldownMs: 1200,
    now: () => now,
  });
  let calls = 0;
  const task = async () => {
    calls += 1;
    return calls;
  };

  assert.equal(await coordinator.run("conversation-a:full", task), 1);
  now += 500;
  assert.deepEqual(
    await coordinator.run("conversation-a:full", task),
    { skipped: "fresh" },
  );
  assert.equal(calls, 1);
  now += 1200;
  assert.equal(await coordinator.run("conversation-a:full", task), 2);
});

test("does not share cooldowns between conversations or refresh modes", async () => {
  const coordinator = createRefreshCoordinator({ cooldownMs: 1200 });
  let calls = 0;
  const task = async () => {
    calls += 1;
    return calls;
  };

  await coordinator.run("conversation-a:incremental", task);
  await coordinator.run("conversation-b:incremental", task);
  await coordinator.run("conversation-a:full", task);

  assert.equal(calls, 3);
});

test("retries after a failed refresh instead of cooling it down", async () => {
  const coordinator = createRefreshCoordinator({ cooldownMs: 1200 });
  let calls = 0;
  const task = async () => {
    calls += 1;
    if (calls === 1) throw new Error("temporary");
    return "recovered";
  };

  await assert.rejects(
    coordinator.run("conversation-a:incremental", task),
    /temporary/,
  );
  assert.equal(
    await coordinator.run("conversation-a:incremental", task),
    "recovered",
  );
  assert.equal(calls, 2);
});

test("allows explicit refreshes to bypass the cooldown", async () => {
  const coordinator = createRefreshCoordinator({ cooldownMs: 1200 });
  let calls = 0;
  const task = async () => {
    calls += 1;
    return calls;
  };

  assert.equal(await coordinator.run("conversation-a:full", task), 1);
  assert.equal(
    await coordinator.run("conversation-a:full", task, { force: true }),
    2,
  );
});

test("stops completed history repairs for the rest of the session", async () => {
  const gate = createCompletionGate();
  let calls = 0;
  const task = async () => {
    calls += 1;
    return { completed: true, imported: 120 };
  };

  assert.deepEqual(await gate.run("conversation-a", task), {
    completed: true,
    imported: 120,
  });
  assert.deepEqual(await gate.run("conversation-a", task), {
    skipped: "complete",
  });
  assert.equal(calls, 1);
});

test("keeps incomplete history repairs eligible for another pass", async () => {
  const gate = createCompletionGate();
  let calls = 0;
  const task = async () => {
    calls += 1;
    return { completed: calls === 2 };
  };

  assert.deepEqual(await gate.run("conversation-a", task), {
    completed: false,
  });
  assert.deepEqual(await gate.run("conversation-a", task), {
    completed: true,
  });
  assert.equal(calls, 2);
});

test("coalesces concurrent history repairs before marking them complete", async () => {
  const gate = createCompletionGate();
  let calls = 0;
  let release;
  const pending = new Promise((resolve) => {
    release = resolve;
  });
  const task = async () => {
    calls += 1;
    await pending;
    return { completed: true };
  };

  const first = gate.run("conversation-a", task);
  const second = gate.run("conversation-a", task);
  release();
  await Promise.all([first, second]);

  assert.equal(calls, 1);
});

test("keeps optimistic sends when a refresh returns a larger message page", () => {
  const optimistic = {
    provider_message_id: "optimistic:send-1",
    body_text: "Queued reply",
    metadata: { pending_send: true },
  };
  const refreshed = Array.from({ length: 3 }, (_, index) => ({
    provider_message_id: `server-${index + 1}`,
    body_text: `Server message ${index + 1}`,
    metadata: {},
  }));

  const merged = mergeRefreshedMessages([optimistic], refreshed);

  assert.equal(merged.length, 4);
  assert.equal(merged.at(-1), optimistic);
});

test("preserves loaded history without letting it overwrite refreshed rows", () => {
  const older = {
    provider_message_id: "older",
    body_text: "Older cached message",
    metadata: {},
  };
  const stale = {
    provider_message_id: "shared",
    body_text: "Stale body",
    metadata: {},
  };
  const fresh = {
    provider_message_id: "shared",
    body_text: "Edited body",
    metadata: { provider_edited: true },
  };

  const merged = mergeRefreshedMessages([older, stale], [fresh]);

  assert.deepEqual(merged, [older, fresh]);
});

test("settles an optimistic send under the canonical server message id", () => {
  const optimistic = {
    provider_message_id: "optimistic:send-1",
    body_text: "Sent reply",
    metadata: {
      pending_send: true,
      pending_status: "Sending…",
      pending_failed: false,
    },
  };

  const settled = settleOptimisticMessage(optimistic, {
    providerMessageId: "manual:canonical-1",
  });

  assert.equal(settled.provider_message_id, "manual:canonical-1");
  assert.equal(settled.metadata.pending_send, false);
  assert.equal(settled.metadata.optimistic_send, true);
  assert.equal(settled.metadata.pending_status, "Sent");
  assert.equal("pending_failed" in settled.metadata, false);
  assert.equal(optimistic.provider_message_id, "optimistic:send-1");
});

test("keeps a settled optimistic send until its canonical row is refreshed", () => {
  const settled = settleOptimisticMessage({
    provider_message_id: "optimistic:send-1",
    body_text: "Sent reply",
    metadata: { pending_send: true },
  }, {
    providerMessageId: "manual:canonical-1",
  });

  const merged = mergeRefreshedMessages(
    [settled],
    [{ provider_message_id: "server-1", metadata: {} }],
  );

  assert.equal(merged.at(-1), settled);
});

test("replaces a settled optimistic send with its refreshed canonical row", () => {
  const settled = settleOptimisticMessage({
    provider_message_id: "optimistic:send-1",
    body_text: "Optimistic body",
    metadata: { pending_send: true },
  }, {
    providerMessageId: "manual:canonical-1",
  });
  const canonical = {
    provider_message_id: "manual:canonical-1",
    body_text: "Canonical body",
    metadata: {},
  };

  const merged = mergeRefreshedMessages([settled], [canonical]);

  assert.deepEqual(merged, [canonical]);
});

test("queues an undoable send durably before the undo window expires", async () => {
  const scheduled = {
    scheduled_message: {
      scheduled_id: "scheduled-1",
      scheduled_at: "2026-07-26T12:00:15Z",
    },
  };
  let scheduleCalls = 0;
  const queue = createDurableUndoQueue({
    schedule: async (payload) => {
      scheduleCalls += 1;
      assert.deepEqual(payload, { body: "Queued message" });
      return scheduled;
    },
    cancel: async () => {
      throw new Error("not expected");
    },
  });

  const result = await queue.enqueue({ body: "Queued message" });

  assert.equal(scheduleCalls, 1);
  assert.equal(result, scheduled);
  assert.equal(queue.undoRequested, false);
});

test("waits for durable creation before cancelling an immediate undo", async () => {
  let resolveSchedule;
  const scheduling = new Promise((resolve) => {
    resolveSchedule = resolve;
  });
  let cancelledId = "";
  const queue = createDurableUndoQueue({
    schedule: async () => scheduling,
    cancel: async (scheduled) => {
      cancelledId = scheduled.scheduled_message.scheduled_id;
      return { success: true };
    },
  });

  const enqueue = queue.enqueue({});
  const undo = queue.undo();
  assert.equal(queue.undoRequested, true);
  assert.equal(cancelledId, "");
  resolveSchedule({
    scheduled_message: { scheduled_id: "scheduled-race" },
  });

  await Promise.all([enqueue, undo]);
  assert.equal(cancelledId, "scheduled-race");
  assert.equal(queue.cancelled, true);
});

test("coalesces repeated undo clicks into one scheduler cancellation", async () => {
  let cancelCalls = 0;
  const queue = createDurableUndoQueue({
    schedule: async () => ({
      scheduled_message: { scheduled_id: "scheduled-1" },
    }),
    cancel: async () => {
      cancelCalls += 1;
      return { success: true };
    },
  });
  await queue.enqueue({});

  await Promise.all([queue.undo(), queue.undo(), queue.undo()]);

  assert.equal(cancelCalls, 1);
  assert.equal(queue.cancelled, true);
});

test("allows undo cancellation to retry after a transient failure", async () => {
  let cancelCalls = 0;
  const queue = createDurableUndoQueue({
    schedule: async () => ({
      scheduled_message: { scheduled_id: "scheduled-1" },
    }),
    cancel: async () => {
      cancelCalls += 1;
      if (cancelCalls === 1) throw new Error("temporary");
      return { success: true };
    },
  });
  await queue.enqueue({});

  await assert.rejects(queue.undo(), /temporary/);
  assert.equal(queue.undoRequested, false);
  await queue.undo();

  assert.equal(cancelCalls, 2);
  assert.equal(queue.cancelled, true);
});
